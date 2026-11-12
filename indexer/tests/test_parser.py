"""Parser do parse-worker. Só roda onde o Docling está instalado (grupo "parse")."""
from types import SimpleNamespace

import pytest

pytest.importorskip("docling")

from docling_core.types.doc import (
    BoundingBox,
    DocItemLabel,
    ListItem,
    TableCell,
    TableData,
    TextItem,
)

from app.pipeline import parser
from app.schemas import ParsedItem, ParsedPage


def test_linux_without_system_fonts_fails_fast(monkeypatch, tmp_path):
    """Sem fontes, o layout degrada em silêncio (páginas viram "figura"); melhor não subir."""
    monkeypatch.setattr(parser.sys, "platform", "linux")
    monkeypatch.setattr(parser, "_LINUX_FONT_DIRS", (str(tmp_path / "nao-existe"), str(tmp_path)))
    with pytest.raises(RuntimeError, match="fonte de sistema"):
        parser.check_system_fonts()

    font_dir = tmp_path / "truetype" / "liberation"
    font_dir.mkdir(parents=True)
    (font_dir / "LiberationSans-Regular.ttf").write_bytes(b"")
    parser.check_system_fonts()  # com uma fonte instalada, passa


def test_font_check_is_skipped_outside_linux(monkeypatch, tmp_path):
    monkeypatch.setattr(parser.sys, "platform", "win32")
    monkeypatch.setattr(parser, "_LINUX_FONT_DIRS", (str(tmp_path),))
    parser.check_system_fonts()


def test_enumerated_list_item_keeps_its_marker():
    """O Docling separa "3.1" do texto do item de lista; sem o marcador a seção se perde."""
    def item(cls, text, **extra):
        label = DocItemLabel.LIST_ITEM if cls is ListItem else DocItemLabel.TEXT
        return cls(self_ref="#/texts/0", orig=text, text=text, label=label, **extra)

    numbered = item(ListItem, "Políticas contábeis", enumerated=True, marker="3.1")
    assert parser._item_text(numbered) == "3.1 Políticas contábeis"
    already = item(ListItem, "b) Receitas de contratos", enumerated=True, marker="b)")
    assert parser._item_text(already) == "b) Receitas de contratos"
    bullet = item(ListItem, "for mantido para negociação;", enumerated=False, marker="-")
    assert parser._item_text(bullet) == "for mantido para negociação;"
    assert parser._item_text(item(TextItem, "Parágrafo comum.")) == "Parágrafo comum."


def test_paragraph_that_crosses_a_page_break_keeps_both_pages():
    """O Docling junta o parágrafo das páginas 27 e 28 num item só, com uma procedência por página."""
    def prov(*pages):
        return [SimpleNamespace(page_no=page) for page in pages]

    assert parser._page_span(prov(27, 28)) == (27, 28)
    assert parser._page_span(prov(27)) == (27, None)
    assert parser._page_span(prov(27, 27)) == (27, None)


def cell(text, row, col_start, col_end, left, right, header=True) -> TableCell:
    return TableCell(
        bbox=BoundingBox(l=left, t=10 * row, r=right, b=10 * row + 8), text=text, column_header=header,
        row_span=1, col_span=col_end - col_start, start_row_offset_idx=row, end_row_offset_idx=row + 1,
        start_col_offset_idx=col_start, end_col_offset_idx=col_end)


def header_table(unaudited_span: tuple[int, int]) -> TableData:
    """Cabeçalho da DRE da Brasal: grupo, ano e, só sob 2021, "(não auditado)"."""
    return TableData(num_rows=4, num_cols=3, table_cells=[
        cell("Controladora", 0, 1, 3, 357.6, 406.3),  # grupo: centrado entre as duas colunas
        cell("2022", 1, 1, 2, 342.7, 359.7),
        cell("2021", 1, 2, 3, 403.2, 420.4),
        cell("(não auditado)", 2, *unaudited_span, 385.0, 438.4),  # escrito em cima de 2021
        cell("Receita operacional líquida", 3, 0, 1, 50.0, 200.0, header=False),
        cell("1.325.059", 3, 1, 2, 320.0, 359.7, header=False),
        cell("1.061.410", 3, 2, 3, 380.0, 420.4, header=False),
    ])


def header_texts(data: TableData) -> list[list[str]]:
    return [[cell.text for cell in row] for row in data.grid[:3]]


def test_header_cell_stretched_over_two_columns_goes_back_to_its_own():
    """O modelo de tabelas deu a "(não auditado)" a largura de 2022 e 2021; só 2021 tem a marca."""
    data = header_table(unaudited_span=(1, 3))
    assert header_texts(data)[2] == ["", "(não auditado)", "(não auditado)"]
    assert parser.fix_header_spans(data) == 1
    assert header_texts(data) == [
        ["", "Controladora", "Controladora"],  # o grupo, que fica acima das colunas, não muda
        ["", "2022", "2021"],
        ["", "", "(não auditado)"],
    ]
    assert parser.fix_header_spans(data) == 0  # nada mais a corrigir


def test_header_cell_already_on_one_column_is_left_alone():
    data = header_table(unaudited_span=(2, 3))
    assert parser.fix_header_spans(data) == 0
    assert header_texts(data)[2] == ["", "", "(não auditado)"]


def test_group_header_of_a_second_block_and_wide_captions_keep_their_span():
    """Tabela com dois blocos empilhados: o "Consolidado" do segundo bloco tem linhas de dados
    logo acima, não cabeçalhos. E um texto largo sob os anos cobre mesmo as duas colunas."""
    data = TableData(num_rows=5, num_cols=3, table_cells=[
        cell("Controladora", 0, 1, 3, 357.6, 406.3),
        cell("2022", 1, 1, 2, 342.7, 359.7),
        cell("2021", 1, 2, 3, 403.2, 420.4),
        cell("(em milhares de reais, exceto lucro por ação)", 2, 1, 3, 300.0, 440.0),  # largo
        cell("Caixa", 3, 0, 1, 50.0, 90.0, header=False),
        cell("96.453", 3, 1, 2, 325.0, 359.7, header=False),
        cell("1.346", 3, 2, 3, 395.0, 420.4, header=False),
        cell("Consolidado", 4, 1, 3, 395.0, 428.0),  # segundo bloco, escrito perto da 2ª coluna
    ])
    assert parser.fix_header_spans(data) == 0
    assert [c.text for c in data.grid[4]] == ["", "Consolidado", "Consolidado"]


def test_text_split_between_two_columns_is_joined_on_the_column_it_sits_on():
    """ "(não auditado)" sobre a coluna de 2021 saiu como "(não" em 2022 e "auditado)" em 2021."""
    data = TableData(num_rows=5, num_cols=3, table_cells=[
        cell("31/12/2022", 0, 1, 2, 238.8, 282.9),
        cell("31/12/2021", 0, 2, 3, 325.7, 369.7),
        cell("(não", 1, 1, 2, 317.5, 336.3),
        cell("auditado)", 1, 2, 3, 338.4, 377.8),
        # Formulário com opções na mesma linha: não é um texto partido, fica como está.
        cell("( ) direção (", 4, 1, 2, 250.0, 300.0, header=False),
        cell("X ) direção e gerências", 4, 2, 3, 302.0, 369.7, header=False),
        cell("Caixa (nota 4)", 2, 0, 1, 50.0, 120.0, header=False),  # parênteses fechados: não mexe
        cell("96.453", 2, 1, 2, 250.0, 282.9, header=False),
        cell("1.346", 2, 2, 3, 345.0, 369.7, header=False),
        cell("(1.200", 3, 1, 2, 250.0, 282.9, header=False),  # longe demais do vizinho para ser a mesma frase
        cell("15)", 3, 2, 3, 355.0, 369.7, header=False),
    ])
    assert parser.merge_split_cells(data) == 1
    assert [[c.text for c in row] for row in data.grid] == [
        ["", "31/12/2022", "31/12/2021"],
        ["", "", "(não auditado)"],
        ["Caixa (nota 4)", "96.453", "1.346"],
        ["", "(1.200", "15)"],
        ["", "( ) direção (", "X ) direção e gerências"],
    ]
    assert parser.merge_split_cells(data) == 0


def test_parse_converts_in_page_batches(monkeypatch, tmp_path, settings):
    """PDF grande é convertido em lotes, para a memória não crescer com o número de páginas."""
    ranges = []

    class FakeConverter:
        def convert(self, path, page_range, progress_callback):
            ranges.append(page_range)
            progress_callback(SimpleNamespace(kind="page_completed", completed_pages=1, total_pages=10))
            return SimpleNamespace(document=page_range)

    def fake_items(document, out_dir, figure_count):
        start, end = document
        items = [ParsedItem(type="text", text=f"página {n}", page=n) for n in range(start, end + 1)]
        return items, figure_count + 1

    native = [ParsedPage(page=n, native_text="texto nativo da página " * 3) for n in range(1, 26)]
    monkeypatch.setattr(parser, "check_system_fonts", lambda: None)
    monkeypatch.setattr(parser, "_native_pages", lambda path: native)
    monkeypatch.setattr(parser, "_convert_items", fake_items)
    monkeypatch.setattr(parser.PdfParser, "_converter", lambda self, ocr: FakeConverter())
    settings.parse_page_batch = 10

    progress = []
    parsed = parser.PdfParser(settings).parse(
        tmp_path / "x.pdf", "doc", "x.pdf", tmp_path, on_progress=lambda done, total: progress.append((done, total))
    )
    assert ranges == [(1, 10), (11, 20), (21, 25)]
    assert [item.page for item in parsed.items] == list(range(1, 26))
    assert progress == [(1, 25), (10, 25), (11, 25), (20, 25), (21, 25), (25, 25)]
    assert parsed.page_count == 25
