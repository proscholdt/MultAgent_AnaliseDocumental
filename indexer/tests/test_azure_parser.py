"""Parser da Azure, sem chamar o serviço: a resposta é montada no formato documentado da API
(Analyze Result, versão 2024-11-30) e um cliente falso a devolve."""
import io
from types import SimpleNamespace

import pytest
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError
from pypdf import PdfReader, PdfWriter

from app.pipeline import azure_parser
from app.pipeline.azure_parser import (
    AzureParser,
    convert_layout,
    figure_segments,
    pdf_for_upload,
    table_markdown,
)
from app.pipeline.chunker import chunk_document
from app.pipeline.figures import (
    FigureResult,
    FigureTranscription,
    FigureValue,
    is_chart_candidate,
    verify_transcription,
)
from app.pipeline.parsing import ParserError, build_parser, unconfirmed_table_numbers
from app.schemas import OcrWord, ParsedDocument, ParsedItem, ParsedPage

PAGE = (8.5, 11.0)  # polegadas, como o serviço informa para PDF
PX = 144.0  # pixels por polegada do recorte (escala 2)


def polygon(x0: float, y0: float, x1: float, y1: float) -> list[float]:
    return [x0, y0, x1, y0, x1, y1, x0, y1]


class Layout:
    """Monta uma resposta do modelo de layout: o texto corrido e os elementos que apontam para ele."""

    def __init__(self, pages: int = 2) -> None:
        self.content = ""
        self.paragraphs, self.tables, self.figures = [], [], []
        self.words: dict[int, list[dict]] = {n: [] for n in range(1, pages + 1)}
        self.lines: dict[int, list[dict]] = {n: [] for n in range(1, pages + 1)}

    def element(self, text: str, page: int, box: tuple, **extra) -> dict:
        offset = len(self.content)
        self.content += text + "\n"
        return {"content": text, "spans": [{"offset": offset, "length": len(text)}],
                "boundingRegions": [{"pageNumber": page, "polygon": polygon(*box)}], **extra}

    def paragraph(self, text: str, page: int, box: tuple, role: str | None = None) -> dict:
        item = self.element(text, page, box, **({"role": role} if role else {}))
        self.paragraphs.append(item)
        return item

    def block(self, lines: list[str], page: int, box: tuple, role: str | None = None) -> dict:
        """Parágrafo de várias linhas: o serviço devolve o texto junto e as linhas à parte."""
        item = self.paragraph(" ".join(lines), page, box, role)
        offset = item["spans"][0]["offset"]
        for line in lines:
            self.lines[page].append({"content": line, "spans": [{"offset": offset, "length": len(line)}]})
            offset += len(line) + 1
        return item

    def word(self, text: str, page: int, box: tuple) -> None:
        self.words[page].append({"content": text, "polygon": polygon(*box), "confidence": 0.99})

    def table(self, page: int, box: tuple, rows: list[list[tuple]], **extra) -> dict:
        """Cada célula é (texto, tipo, colunas que cobre). As células também são parágrafos."""
        start, cells = len(self.content), []
        for r, row in enumerate(rows):
            column = 0
            for text, kind, span in row:
                cell = self.paragraph(text, page, (box[0] + column, box[1] + r * 0.2,
                                                   box[0] + column + span, box[1] + r * 0.2 + 0.15))
                cells.append({"rowIndex": r, "columnIndex": column, "content": text, "kind": kind,
                              "columnSpan": span, "spans": cell["spans"],
                              "boundingRegions": cell["boundingRegions"]})
                column += span
        table = {"rowCount": len(rows), "columnCount": max(sum(c[2] for c in row) for row in rows),
                 "cells": cells, "spans": [{"offset": start, "length": len(self.content) - start}],
                 "boundingRegions": [{"pageNumber": page, "polygon": polygon(*box)}], **extra}
        self.tables.append(table)
        return table

    def figure(self, page: int, box: tuple, spans: list[dict] | None = None, **extra) -> dict:
        figure = {"id": f"{page}.{len(self.figures) + 1}", "spans": spans or [],
                  "boundingRegions": [{"pageNumber": page, "polygon": polygon(*box)}], **extra}
        self.figures.append(figure)
        return figure

    def result(self) -> dict:
        pages = [{"pageNumber": n, "width": PAGE[0], "height": PAGE[1], "unit": "inch",
                  "words": self.words[n], "lines": self.lines[n]} for n in self.words]
        return {"apiVersion": "2024-11-30", "modelId": "prebuilt-layout", "content": self.content,
                "pages": pages, "paragraphs": self.paragraphs, "tables": self.tables, "figures": self.figures}


def fake_crop(calls: list):
    def crop(page: int, box: tuple, size: tuple):
        calls.append((page, box, size))
        return f"figures/p{page:04d}_{len(calls):03d}.png", PX, PX, (int(box[0] * PX), int(box[1] * PX))
    return crop


@pytest.fixture
def layout() -> Layout:
    doc = Layout()
    doc.paragraph("Empresa Exemplo S.A.", 1, (1, 0.3, 4, 0.5), role="pageHeader")
    doc.paragraph("Demonstrações dos resultados", 1, (1, 1.0, 5, 1.2), role="sectionHeading")
    doc.paragraph("(Valores expressos em milhares de reais)", 1, (1, 1.3, 5, 1.5))
    table = doc.table(1, (1, 1.8, 4, 3.0), rows=[
        [("", "stubHead", 1), ("Consolidado", "columnHeader", 2)],
        [("", "stubHead", 1), ("2022", "columnHeader", 1), ("2021 (a)", "columnHeader", 1)],
        [("Receita operacional líquida", "rowHeader", 1), ("1.325.059", "content", 1), ("1.061.410", "content", 1)],
        [("Outras receitas | despesas:", "content", 3)],
        [("Possui auditoria? :selected: sim", "content", 1), ("485.156", "content", 1), ("388.450", "content", 1)],
    ])
    # A nota de rodapé fica abaixo da grade e vem tanto na tabela quanto na lista de parágrafos.
    note = doc.paragraph("(a) Não auditado pelos auditores independentes", 1, (1, 3.2, 5, 3.4))
    table["footnotes"] = [{key: note[key] for key in ("content", "spans", "boundingRegions")}]
    across = doc.paragraph("A Administração entende que as estratégias incluem a renovação da concessão.",
                           1, (1, 9.5, 7.5, 10.5))
    across["boundingRegions"].append({"pageNumber": 2, "polygon": polygon(1, 1.0, 7.5, 1.4)})
    doc.paragraph("3", 1, (4, 10.7, 4.2, 10.9), role="pageNumber")

    doc.paragraph("26. Gestão de riscos", 2, (1, 1.8, 5, 2.0), role="title")
    # Gráfico vetorial: os rótulos são texto do PDF e o serviço os devolve como parágrafos.
    start = len(doc.content)
    for text, box in (("26,51%", (2.0, 3.0, 2.5, 3.15)), ("21,53%", (4.0, 3.0, 4.5, 3.15)),
                      ("dez/22", (2.0, 5.0, 2.5, 5.15)), ("Patamar Regulatório", (3.9, 5.0, 5.2, 5.15))):
        doc.paragraph(text, 2, box)
    for text, box in (("26,51%", (2.0, 3.0, 2.5, 3.15)), ("21,53%", (4.0, 3.0, 4.5, 3.15)),
                      ("dez/22", (2.0, 5.0, 2.5, 5.15)), ("Patamar", (3.9, 5.0, 4.4, 5.15)),
                      ("Regulatório", (4.45, 5.0, 5.2, 5.15)), ("fora", (7.0, 5.0, 7.4, 5.15))):
        doc.word(text, 2, box)
    labels = [{"offset": start, "length": len(doc.content) - start}]
    caption = doc.paragraph("Gráfico 1 - Evolução das perdas totais", 2, (1, 6.1, 6, 6.3))
    doc.figure(2, (1.0, 2.5, 6.0, 6.0), spans=labels, caption=caption)
    doc.paragraph("As perdas totais recuaram no exercício.", 2, (1, 6.6, 7.5, 6.9))
    # Gráfico colado como imagem: o serviço leu só o título, que é texto do PDF; os números
    # estão dentro da imagem.
    doc.word("Carga", 2, (1.2, 7.3, 1.7, 7.45))
    doc.word("Fio", 2, (1.75, 7.3, 2.0, 7.45))
    doc.figure(2, (1.0, 7.2, 6.0, 9.0))
    doc.paragraph("O patamar regulatório é de 21,53%.", 2, (1, 9.2, 7.5, 9.5))
    doc.figure(2, (7.0, 0.3, 7.8, 0.7))  # logotipo
    doc.paragraph("4", 2, (4, 10.7, 4.2, 10.9), role="pageFooter")
    return doc


def convert(layout: Layout, calls: list | None = None) -> list[ParsedItem]:
    return convert_layout(layout.result(), crop=fake_crop([] if calls is None else calls),
                          min_area_ratio=0.04, small_min_area_ratio=0.02)


# --- texto e ordem de leitura -------------------------------------------------


def test_items_follow_reading_order_with_roles_and_pages(layout):
    items = convert(layout)
    assert [(i.type, i.page, i.text[:24]) for i in items] == [
        ("heading", 1, "Demonstrações dos result"),
        ("text", 1, "(Valores expressos em mi"),
        ("table", 1, "|  | Consolidado - 2022 "),
        ("text", 1, "(a) Não auditado pelos a"),
        ("text", 1, "A Administração entende "),
        ("figure", 2, ""),  # logotipo no alto da página: sem texto, posicionado pela altura
        ("heading", 2, "26. Gestão de riscos"),
        ("figure", 2, "Gráfico 1 - Evolução das"),
        ("text", 2, "As perdas totais recuara"),
        ("figure", 2, ""),  # gráfico em imagem, sem texto: fica entre os dois parágrafos
        ("text", 2, "O patamar regulatório é "),
    ]
    # Cabeçalho, rodapé e número de página não entram; células e rótulos de gráfico não viram texto.
    texts = " ".join(i.text for i in items if i.type == "text")
    assert "Empresa Exemplo" not in texts and "1.325.059" not in texts and "dez/22" not in texts


def test_paragraph_across_a_page_break_keeps_both_pages(layout):
    items = convert(layout)
    crossing = next(i for i in items if i.text.startswith("A Administração"))
    assert (crossing.page, crossing.page_end) == (1, 2)
    assert all(i.page_end is None for i in items if i is not crossing)


# --- títulos, cabeçalhos e rodapés ----------------------------------------------


def test_statement_title_block_is_split_and_a_title_tagged_as_page_header_is_kept(settings):
    """O serviço devolve o bloco de título de uma demonstração como um parágrafo só e, na
    Millet, rotulou "Demonstrações dos fluxos de caixa" como cabeçalho de página: a tabela
    ficava na seção da demonstração anterior."""
    doc = Layout()
    doc.paragraph("Empresa Exemplo S.A.", 1, (1, 0.3, 4, 0.5), role="pageHeader")
    doc.block(["Empresa Exemplo S.A.", "Demonstrações do resultado",
               "Exercícios findos em 31 de dezembro de 2022 e 2021", "(em milhares de reais)"],
              1, (1, 1.0, 6, 1.8), role="title")
    doc.table(1, (1, 2.0, 3, 2.6), rows=[[("", "stubHead", 1), ("2022", "columnHeader", 1)],
                                          [("Receita operacional líquida", "content", 1), ("462.127", "content", 1)]])
    doc.block(["Empresa Exemplo S.A.", "Demonstrações dos fluxos de caixa"], 2, (1, 0.3, 6, 0.7), role="pageHeader")
    doc.paragraph("Exercícios findos em 31 de dezembro de 2022 e 2021", 2, (1, 1.0, 6, 1.2))
    doc.table(2, (1, 2.0, 3, 2.6), rows=[[("(em milhares de reais)", "columnHeader", 1), ("2022", "columnHeader", 1)],
                                          [("Caixa gerado nas operações", "content", 1), ("14.743", "content", 1)]])
    # Linha de período rotulada como título: é o subtítulo da tabela, não uma seção.
    doc.paragraph("Exercício findo em 31 de dezembro de 2021 -- não auditado", 2, (1, 3.0, 6, 3.2), role="sectionHeading")
    # Título quebrado pela largura da página: a segunda linha continua a primeira. Fica inteiro.
    doc.block(["Demonstração do resultado da controlada em conjunto Amazônia Energia em 31 de dezembro",
               "de 2022"], 2, (1, 4.0, 7, 4.4), role="sectionHeading")
    # Parágrafo comum que começa pelo nome de uma demonstração: é frase, não bloco de título.
    doc.block(["Balanço patrimonial e demonstração do resultado são apresentados de forma", "Condensada, a seguir."],
              2, (1, 5.0, 7, 5.4))
    items = convert(doc)
    assert [(i.type, i.page, i.text) for i in items if i.type != "table"] == [
        ("heading", 1, "Demonstrações do resultado"),  # a linha da empresa é cabeçalho corrido: sai
        ("text", 1, "Exercícios findos em 31 de dezembro de 2022 e 2021 (em milhares de reais)"),
        ("heading", 2, "Demonstrações dos fluxos de caixa"),
        ("text", 2, "Exercícios findos em 31 de dezembro de 2022 e 2021"),
        ("text", 2, "Exercício findo em 31 de dezembro de 2021 -- não auditado"),
        ("heading", 2, "Demonstração do resultado da controlada em conjunto Amazônia Energia em 31 de dezembro de 2022"),
        ("text", 2, "Balanço patrimonial e demonstração do resultado são apresentados de forma Condensada, a seguir."),
    ]
    parsed = ParsedDocument(doc_id="d", file_name="f.pdf", page_count=2, parser="azure", pipeline_version="x",
                            items=items, pages=[ParsedPage(page=1), ParsedPage(page=2)])
    first, second = [c for c in chunk_document(parsed, {}, settings) if c.chunk_type == "table"]
    assert first.section_path == ["Demonstrações do resultado"] and second.section_path == ["Demonstrações dos fluxos de caixa"]
    assert first.text.startswith("Exercícios findos em 31 de dezembro de 2022 e 2021 (em milhares de reais)")
    assert first.unit == "R$ mil"


def test_page_header_that_does_not_repeat_is_a_title_unless_the_page_already_has_one():
    doc = Layout()
    # Página 1: timbre do auditor e um nome solto, rotulados como cabeçalho. A página abre com
    # um título de verdade, então nada disso vira título.
    doc.block(["Auditores Associados", "Rua das Flores, 18 - 15º andar", "20090-010 - Rio de Janeiro - RJ"],
              1, (5, 0.3, 8, 0.9), role="pageHeader")
    doc.paragraph("Membro de uma rede global", 1, (5, 1.0, 8, 1.1), role="pageHeader")
    doc.paragraph("Relatório do auditor independente", 1, (1, 1.4, 6, 1.6), role="title")
    doc.paragraph("Examinamos as demonstrações financeiras da Companhia.", 1, (1, 1.8, 7, 2.2))
    # Página 2: o título do anexo veio como cabeçalho de página, e a página não tem outro.
    doc.paragraph("Anexo I", 2, (3, 0.5, 5, 0.7), role="pageHeader")
    doc.block(['Relatório anual do comitê de auditoria da Empresa Exemplo ("Companhia" ou', '"Empresa")'],
              2, (1, 0.8, 7, 1.2), role="pageHeader")
    doc.paragraph("Ao longo do exercício, o comitê realizou reuniões ordinárias.", 2, (1, 1.6, 7, 2.0))
    # Timbre numa página que começa por texto: tem CEP, não tem cara de título.
    doc.paragraph("Av. Central, 100 - 20040-020", 2, (5, 0.2, 8, 0.4), role="pageHeader")
    assert [(i.type, i.text) for i in convert(doc)] == [
        ("heading", "Relatório do auditor independente"),
        ("text", "Examinamos as demonstrações financeiras da Companhia."),
        ("heading", "Anexo I"),
        ("heading", 'Relatório anual do comitê de auditoria da Empresa Exemplo ("Companhia" ou "Empresa")'),
        ("text", "Ao longo do exercício, o comitê realizou reuniões ordinárias."),
    ]


def test_running_header_left_untagged_on_one_page_is_still_dropped():
    """Light: o mesmo cabeçalho veio rotulado em 79 páginas e sem rótulo em 4."""
    header = "Empresa Exemplo S.A. CNPJ 00.000.000/0001-00"
    doc = Layout(pages=3)
    for page in (1, 2):
        doc.paragraph(header, page, (1, 0.3, 5, 0.5), role="pageHeader")
        doc.paragraph(f"Texto da página {page}.", page, (1, 1.0, 7, 1.3))
    doc.paragraph(header, 3, (1, 0.3, 5, 0.5))  # sem rótulo, no mesmo lugar: é o cabeçalho
    doc.paragraph(header, 3, (1, 5.0, 5, 5.2))  # mesmo texto no meio da página: é conteúdo
    doc.paragraph("Texto da página 3.", 3, (1, 6.0, 7, 6.3))
    assert [i.text for i in convert(doc)] == [
        "Texto da página 1.", "Texto da página 2.", header, "Texto da página 3."]


# --- tabelas ------------------------------------------------------------------


def test_table_becomes_markdown_with_header_levels_joined(layout):
    assert table_markdown(layout.tables[0]).splitlines() == [
        "|  | Consolidado - 2022 | Consolidado - 2021 (a) |",
        "|---|---|---|",
        "| Receita operacional líquida | 1.325.059 | 1.061.410 |",
        # Célula de dados que cobre as três colunas fica só na primeira; a barra é escapada.
        "| Outras receitas \\| despesas: |  |  |",
        # A marca de caixa de seleção que o serviço inventa (":selected:") não entra.
        "| Possui auditoria? sim | 485.156 | 388.450 |",
    ]
    assert table_markdown({"rowCount": 0, "columnCount": 0, "cells": []}) == ""


def test_header_note_row_goes_to_the_column_it_is_written_over():
    """Brasal: "(não auditado)" vale só para 2021, e o serviço devolve as duas marcas numa
    célula de dados que cobre todas as colunas, na linha do primeiro rótulo."""
    doc = Layout()
    table = doc.table(1, (1, 2.0, 6, 3.4), rows=[
        [("", "stubHead", 1), ("Controladora", "columnHeader", 2), ("Consolidado", "columnHeader", 2)],
        [("", "stubHead", 1), ("2022", "columnHeader", 1), ("2021", "columnHeader", 1),
         ("2022", "columnHeader", 1), ("2021", "columnHeader", 1)],
        [("Ativo", "content", 1), ("(não auditado) (não auditado)", "content", 4)],
        [("Caixa", "content", 1), ("355", "content", 1), ("871", "content", 1),
         ("10.656", "content", 1), ("8.195", "content", 1)],
        [("Lucro operacional antes das receitas e despesas", "content", 1), ("173.736", "content", 1),
         ("275.244", "content", 1), ("192.840", "content", 1), ("238.224", "content", 1)],
        [("financeiras", "content", 1), ("", "content", 1), ("", "content", 1), ("", "content", 1), ("", "content", 1)],
        [("(-) Provisão", "content", 1), ("(1.551)", "content", 1), ("", "content", 1), ("", "content", 1), ("", "content", 1)],
    ])
    # As colunas têm 1 polegada: 2021 da controladora vai de 3 a 4, e a do consolidado de 5 a 6.
    for text, box in (("(não", (3.1, 2.42, 3.4, 2.55)), ("auditado)", (3.45, 2.42, 3.9, 2.55)),
                      ("(não", (5.1, 2.42, 5.4, 2.55)), ("auditado)", (5.45, 2.42, 5.9, 2.55))):
        doc.word(text, 1, box)
    words = [(word["content"], word["polygon"]) for word in doc.words[1]]
    assert table_markdown(table, words).splitlines() == [
        ("|  | Controladora - 2022 | Controladora - 2021 - (não auditado) | Consolidado - 2022 "
         "| Consolidado - 2021 - (não auditado) |"),
        "|---|---|---|---|---|",
        "| Ativo |  |  |  |  |",  # a linha fica, sem a marca
        "| Caixa | 355 | 871 | 10.656 | 8.195 |",
        # Rótulo quebrado em duas linhas: a continuação volta para o rótulo.
        "| Lucro operacional antes das receitas e despesas financeiras | 173.736 | 275.244 | 192.840 | 238.224 |",
        "| (-) Provisão | (1.551) |  |  |  |",
    ]
    # Sem as posições das palavras não dá para saber a coluna: a linha fica como veio.
    assert table_markdown(table).splitlines()[2] == "| Ativo | (não auditado) (não auditado) |  |  |  |"

    # Valor negativo entre parênteses na primeira linha de dados não é anotação de cabeçalho.
    negative = Layout().table(1, (1, 2.0, 3, 3.0), rows=[
        [("Saldos", "columnHeader", 1), ("2022", "columnHeader", 1)],
        [("", "content", 1), ("(1.551)", "content", 1)],
    ])
    assert table_markdown(negative).splitlines()[2] == "|  | (1.551) |"

    # Marca sozinha numa célula de uma coluna só: é dessa coluna, e a linha vazia some.
    single = Layout().table(1, (1, 2.0, 4, 3.0), rows=[
        [("Saldos", "columnHeader", 1), ("31/12/2022", "columnHeader", 1), ("31/12/2021", "columnHeader", 1)],
        [("", "content", 1), ("", "content", 1), ("(não auditado)", "content", 1)],
        [("Terrenos", "content", 1), ("13.278", "content", 1), ("13.292", "content", 1)],
    ])
    assert table_markdown(single).splitlines() == [
        "| Saldos | 31/12/2022 | 31/12/2021 - (não auditado) |", "|---|---|---|", "| Terrenos | 13.278 | 13.292 |"]


def test_table_without_header_cells_uses_the_first_row():
    table = {"rowCount": 2, "columnCount": 2, "cells": [
        {"rowIndex": 0, "columnIndex": 0, "content": "Caixa", "kind": "content"},
        {"rowIndex": 0, "columnIndex": 1, "content": "96.453", "kind": "content"},
        {"rowIndex": 1, "columnIndex": 0, "content": "Bancos", "kind": "content"},
        {"rowIndex": 1, "columnIndex": 1, "content": "1.346"},
    ]}
    assert table_markdown(table) == "| Caixa | 96.453 |\n|---|---|\n| Bancos | 1.346 |"


# --- figuras ------------------------------------------------------------------


def test_only_figures_above_the_minimum_area_are_cropped(layout):
    calls: list = []
    logo, vector, image = [i for i in convert(layout, calls) if i.type == "figure"]
    assert [(page, box) for page, box, _ in calls] == [(2, (1.0, 2.5, 6.0, 6.0)), (2, (1.0, 7.2, 6.0, 9.0))]
    assert vector.image_path == "figures/p0002_001.png" and image.image_path == "figures/p0002_002.png"
    assert vector.area_ratio == round(5.0 * 3.5 / (8.5 * 11.0), 4)
    assert logo.image_path is None and logo.ocr_words is None and logo.area_ratio < 0.04


def test_figure_text_comes_from_the_service_words_in_crop_pixels(layout):
    _, vector, image = [i for i in convert(layout) if i.type == "figure"]
    # Origem do recorte: (1.0, 2.5) polegadas = (144, 360) pixels.
    assert [(w.text, *(round(v, 1) for v in (w.x0, w.y0, w.x1, w.y1))) for w in vector.ocr_words] == [
        ("26,51%", 144.0, 72.0, 216.0, 93.6),
        ("21,53%", 432.0, 72.0, 504.0, 93.6),
        ("dez/22", 144.0, 360.0, 216.0, 381.6),
        # Palavras vizinhas viram um trecho só; a palavra fora da região não entra.
        ("Patamar Regulatório", 417.6, 360.0, 604.8, 381.6),
    ]
    # O serviço leu só o título, nenhum número: a figura fica para o OCR local, se houver.
    assert image.ocr_words is None


def test_words_far_apart_on_the_same_line_are_separate_segments():
    words = [(text, polygon(*box)) for text, box in (
        ("dez/20", (1.0, 5.0, 1.5, 5.15)), ("mar/21", (2.2, 5.0, 2.7, 5.15)),
        ("Perdas", (1.0, 5.5, 1.5, 5.65)), ("Totais/Carga", (1.55, 5.5, 2.4, 5.65)), ("Fio", (2.45, 5.5, 2.7, 5.65)))]
    segments = figure_segments(words, (0.5, 4.0, 6.0, 6.0))
    assert [s.text for s in segments] == ["dez/20", "mar/21", "Perdas Totais/Carga Fio"]
    # As caixas saem na unidade da página; a conversão para pixels do recorte vem depois.
    assert (segments[2].x0, segments[2].y0, segments[2].x1, segments[2].y1) == (1.0, 5.5, 2.7, 5.65)
    assert figure_segments(words, (6.5, 4.0, 8.0, 6.0)) is None


def vertical(x0: float, y0: float, x1: float, y1: float) -> list[float]:
    """Polígono de uma palavra escrita de baixo para cima, como o serviço devolve: começa no
    canto de baixo e a primeira aresta sobe."""
    return [x0, y1, x0, y0, x1, y0, x1, y1]


def test_vertical_word_is_not_joined_to_the_horizontal_label_beside_it():
    """Gráfico de barras da Light: o valor escrito na vertical dentro da barra ("29.082") e o
    percentual ao lado ("85%") estão na mesma altura e quase encostados."""
    words = [("29.082", vertical(1.35, 5.87, 1.44, 6.13)), ("85%", polygon(1.48, 5.93, 1.65, 6.01)),
             ("28.977", vertical(1.94, 5.89, 2.03, 6.14)), ("85%", polygon(2.06, 5.97, 2.23, 6.05)),
             # Rótulo vertical de duas palavras: lido de baixo para cima, na mesma coluna.
             ("Receita", vertical(0.5, 5.6, 0.6, 6.1)), ("líquida", vertical(0.5, 5.0, 0.6, 5.55)),
             # Barra empilhada: dois valores na vertical, um logo acima do outro. São dois números.
             ("3.573", vertical(3.0, 6.0, 3.09, 6.2)), ("3.190", vertical(3.0, 5.75, 3.09, 5.95)),
             # Na horizontal vale o mesmo, mas "12 meses" continua sendo um rótulo só.
             ("94,2%", polygon(1.0, 6.5, 1.3, 6.6)), ("94,0%", polygon(1.33, 6.5, 1.63, 6.6)),
             ("12", polygon(2.5, 6.5, 2.62, 6.6)), ("meses", polygon(2.65, 6.5, 3.0, 6.6))]
    segments = figure_segments(words, (0.3, 4.8, 4.0, 6.8))
    assert sorted(s.text for s in segments) == [
        "12 meses", "28.977", "29.082", "3.190", "3.573", "85%", "85%", "94,0%", "94,2%", "Receita líquida"]


def test_small_figure_is_a_chart_only_when_the_service_read_numbers_in_it(settings):
    """A Azure delimita só a área do gráfico: três gráficos da Light ficam abaixo dos 4% da
    página que separam gráfico de logotipo."""
    doc = Layout()
    doc.paragraph("Arrecadação", 1, (1, 1.0, 5, 1.2), role="sectionHeading")
    for text, box in (("6,0%", (1.2, 2.2, 1.6, 2.35)), ("Dez/22", (1.2, 3.2, 1.7, 3.35))):
        doc.word(text, 1, box)
    doc.figure(1, (1.0, 2.0, 3.2, 3.5))  # 3,5% da página, com um número lido dentro
    doc.word("Light", 1, (5.1, 2.2, 5.9, 2.5))
    doc.figure(1, (5.0, 2.0, 7.2, 3.5))  # mesmo tamanho, só com um nome: ícone ou logotipo
    doc.word("96,0%", 1, (1.1, 5.05, 1.5, 5.2))
    doc.figure(1, (1.0, 5.0, 2.0, 5.5))  # com número, mas menor que 2%: pequena demais
    calls: list = []
    chart, icon, tiny = [i for i in convert(doc, calls) if i.type == "figure"]
    assert [box for _, box, _ in calls] == [(1.0, 2.0, 3.2, 3.5)]
    assert chart.image_path and [w.text for w in chart.ocr_words] == ["6,0%", "Dez/22"]
    assert icon.image_path is None and tiny.image_path is None
    # O index-worker usa a mesma regra para decidir o que vai ao modelo de visão.
    assert is_chart_candidate(chart, settings)
    chart.ocr_words = [OcrWord(text="Dez", x0=0, y0=0, x1=1, y1=1)]
    assert not is_chart_candidate(chart, settings)  # pequena e sem número lido
    chart.area_ratio = 0.10
    assert is_chart_candidate(chart, settings)  # grande: é candidata de qualquer jeito


def test_chart_verification_and_chunking_work_on_the_converted_document(layout, settings):
    """O que sai do parser da Azure passa pela mesma conferência de gráficos e pelo mesmo chunker."""
    items = convert(layout)
    vector = next(i for i in items if i.type == "figure" and i.image_path)
    transcription = FigureTranscription(tipo="grafico", titulo=None, legivel=True, notas=[], valores=[
        FigureValue(valor="26,51%", rotulos=["dez/22"]),
        FigureValue(valor="21,53%", rotulos=["Patamar Regulatório"]),
        FigureValue(valor="19,30%", rotulos=["dez/21"]),  # o modelo inventou: não está na figura
    ])
    result = verify_transcription(transcription, "texto da página sem os números", vector.ocr_words)
    assert result.text.split("\n")[1:3] == ["dez/22: 26,51%", "Patamar Regulatório: 21,53%"]
    assert "19,30" not in result.text and result.verified_by_ocr == 2

    parsed = ParsedDocument(
        doc_id="d", file_name="f.pdf", page_count=2, parser="azure", pipeline_version="x", items=items,
        pages=[ParsedPage(page=1, native_text="(Valores expressos em milhares de reais) 2022 2021 1.325.059 "
                                              "1.061.410 485.156 388.450"), ParsedPage(page=2, native_text="26. Gestão")])
    chunks = chunk_document(parsed, {vector.image_path: FigureResult(text=result.text, verified_ratio=1.0)}, settings)
    table = next(c for c in chunks if c.chunk_type == "table")
    assert table.section_path == ["Demonstrações dos resultados"] and table.unit == "R$ mil"
    assert table.text.startswith("(Valores expressos em milhares de reais)")  # título junto da tabela
    assert table.text.endswith("(a) Não auditado pelos auditores independentes")  # nota de rodapé também
    figure = next(c for c in chunks if c.chunk_type == "figure")
    assert figure.section_path == ["26. Gestão de riscos"] and "Legenda: Gráfico 1" in figure.text
    assert unconfirmed_table_numbers(parsed) == 0
    parsed.pages[0].native_text = "(Valores expressos em milhares de reais) 2022 2021 1.325.059 1.061.410 485.156"
    assert unconfirmed_table_numbers(parsed) == 1  # 388.450 não está na camada de texto


# --- chamada ao serviço ---------------------------------------------------------


def two_page_pdf(encrypt: bool = False) -> bytes:
    writer = PdfWriter()
    for _ in range(2):
        writer.add_blank_page(width=612, height=792)  # 8,5 x 11 polegadas
    if encrypt:
        writer.encrypt(user_password="", owner_password="dono", algorithm="AES-256")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


class FakeClient:
    def __init__(self, result: dict | None = None, error: Exception | None = None, done: bool = True) -> None:
        self.result, self.error, self.done, self.calls = result, error, done, []

    def begin_analyze_document(self, model_id, body, **kwargs):
        self.calls.append((model_id, body.read(), kwargs))
        if self.error:
            raise self.error
        return SimpleNamespace(wait=lambda timeout=None: None, done=lambda: self.done,
                               result=lambda: SimpleNamespace(as_dict=lambda: self.result))


def azure_settings(settings, monkeypatch):
    monkeypatch.setattr(settings, "parser", "azure")
    monkeypatch.setattr(settings, "azure_di_endpoint", "https://exemplo.cognitiveservices.azure.com/")
    monkeypatch.setattr(settings, "azure_di_key", "chave-de-teste")
    return settings


def test_parse_sends_the_pdf_and_builds_the_parsed_document(layout, settings, monkeypatch, tmp_path):
    settings = azure_settings(settings, monkeypatch)
    pdf = tmp_path / "upload.pdf"
    pdf.write_bytes(two_page_pdf())
    client = FakeClient(layout.result())
    progress = []

    parsed = AzureParser(settings, client).parse(pdf, "abc", "exemplo.pdf", tmp_path / "out",
                                                 on_progress=lambda done, total: progress.append((done, total)))

    model_id, body, kwargs = client.calls[0]
    assert model_id == "prebuilt-layout" and body == pdf.read_bytes()
    assert kwargs["content_type"] == "application/octet-stream"
    assert parsed.parser == "azure-document-intelligence prebuilt-layout 2024-11-30"
    assert (parsed.page_count, len(parsed.pages), progress) == (2, 2, [(0, 2), (2, 2)])
    assert parsed.ocr_pages == [1, 2]  # PDF em branco: nenhuma página tem camada de texto
    # Os recortes são renderizados aqui, com o pypdfium2, na escala 2: 5 x 3,5 polegadas.
    from PIL import Image

    vector = next(i for i in parsed.items if i.type == "figure" and i.image_path)
    with Image.open(tmp_path / "out" / vector.image_path) as image:
        assert image.size == (720, 504)
    assert [w.text for w in vector.ocr_words] == ["26,51%", "21,53%", "dez/22", "Patamar Regulatório"]
    assert sorted(p.name for p in (tmp_path / "out" / "figures").iterdir()) == ["p0002_001.png", "p0002_002.png"]
    assert parsed.billed_pages == 2  # as duas páginas foram enviadas ao serviço
    # A camada de texto do PDF na região de cada figura recortada também é lida aqui. Este PDF
    # está em branco, então ela vem vazia; o logotipo, que não é recortado, fica sem ela.
    assert [i.text_layer for i in parsed.items if i.type == "figure"] == [None, "", ""]


def test_text_layer_of_the_figure_region_is_read_from_the_pdf(tmp_path):
    """Só o texto do PDF que está dentro da região entra. É ele que completa os valores que o
    modelo de visão pula num gráfico vetorial."""
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)  # 8,5 x 11 polegadas, em pontos
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
    content = DecodedStreamObject()
    # "26,51%" a 1,3 polegada do alto da página; "9.902" bem mais abaixo, a 6,8.
    content.set_data(b"BT /F1 12 Tf 100 700 Td (26,51%) Tj ET BT /F1 12 Tf 100 300 Td (9.902) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(content)
    pdf = tmp_path / "grafico.pdf"
    with pdf.open("wb") as handle:
        writer.write(handle)

    cropper = azure_parser._FigureCropper(pdf, tmp_path / "out", 2.0)
    try:
        assert cropper.text_layer(1, (1.0, 1.0, 4.0, 1.6), PAGE).strip() == "26,51%"
        assert cropper.text_layer(1, (1.0, 6.5, 4.0, 7.0), PAGE).strip() == "9.902"
        assert cropper.text_layer(1, (5.0, 1.0, 8.0, 1.6), PAGE).strip() == ""  # região sem texto
    finally:
        cropper.close()


def test_service_response_is_kept_so_converting_again_costs_nothing(layout, settings, monkeypatch, tmp_path):
    settings = azure_settings(settings, monkeypatch)
    pdf = tmp_path / "upload.pdf"
    pdf.write_bytes(two_page_pdf())
    client = FakeClient(layout.result())
    first = AzureParser(settings, client).parse(pdf, "abc", "exemplo.pdf", tmp_path / "out")
    assert (tmp_path / "out" / azure_parser.RESPONSE_FILE).exists()

    # Mesmo documento, mesma pasta (o parsed.json foi invalidado, por exemplo): a conversão é
    # refeita a partir da resposta guardada, sem chamar nem pagar o serviço.
    again = AzureParser(settings, client).parse(pdf, "abc", "exemplo.pdf", tmp_path / "out")
    assert len(client.calls) == 1 and again.billed_pages == 0
    assert again.items == first.items and again.parser == first.parser

    # Resposta de outro modelo, ou arquivo corrompido, não serve: o serviço é chamado de novo.
    monkeypatch.setattr(settings, "azure_di_model", "prebuilt-read")
    other = FakeClient({**layout.result(), "modelId": "prebuilt-read"})
    assert AzureParser(settings, other).parse(pdf, "abc", "exemplo.pdf", tmp_path / "out").billed_pages == 2
    (tmp_path / "out" / azure_parser.RESPONSE_FILE).write_bytes(b"pela metade")
    assert AzureParser(settings, other).parse(pdf, "abc", "exemplo.pdf", tmp_path / "out").billed_pages == 2
    assert len(other.calls) == 2


def test_free_tier_that_reads_only_two_pages_is_an_error_not_a_short_index(layout, settings, monkeypatch, tmp_path):
    settings = azure_settings(settings, monkeypatch)
    pdf = tmp_path / "upload.pdf"
    pdf.write_bytes(two_page_pdf())
    truncated = layout.result()
    truncated["pages"] = truncated["pages"][:1]
    with pytest.raises(ParserError, match="1 de 2 páginas.*F0"):
        AzureParser(settings, FakeClient(truncated)).parse(pdf, "abc", "x.pdf", tmp_path / "out")
    assert not (tmp_path / "out" / azure_parser.RESPONSE_FILE).exists()  # resposta incompleta não é guardada


def test_service_errors_are_permanent_only_when_retrying_cannot_help(settings, monkeypatch, tmp_path):
    settings = azure_settings(settings, monkeypatch)
    pdf = tmp_path / "upload.pdf"
    pdf.write_bytes(two_page_pdf())

    def parse_with(error: Exception, **client):
        return AzureParser(settings, FakeClient(error=error, **client)).parse(pdf, "abc", "x.pdf", tmp_path / "out")

    def http_error(status: int) -> HttpResponseError:
        error = HttpResponseError(message=f"status {status}")
        error.status_code = status
        return error

    with pytest.raises(ParserError, match="credencial"):
        parse_with(ClientAuthenticationError(message="Access denied due to invalid subscription key"))
    with pytest.raises(ParserError, match="404"):
        parse_with(http_error(404))  # endpoint ou modelo errado
    with pytest.raises(HttpResponseError):
        parse_with(http_error(429))  # limite de requisições: o worker tenta de novo
    with pytest.raises(TimeoutError):
        AzureParser(settings, FakeClient({}, done=False)).parse(pdf, "abc", "x.pdf", tmp_path / "out")


def test_permission_restricted_pdf_is_sent_without_encryption(tmp_path):
    """PDF só com restrição de permissões (caso da Brasal): o serviço pede a proteção removida."""
    pdf = tmp_path / "restrito.pdf"
    pdf.write_bytes(two_page_pdf(encrypt=True))
    assert PdfReader(pdf).is_encrypted
    sent = PdfReader(io.BytesIO(pdf_for_upload(pdf)))
    assert not sent.is_encrypted and len(sent.pages) == 2
    plain = tmp_path / "comum.pdf"
    plain.write_bytes(two_page_pdf())
    assert pdf_for_upload(plain) == plain.read_bytes()  # PDF comum vai como está


def test_parser_choice_comes_from_settings(settings, monkeypatch):
    pytest.importorskip("docling")
    assert build_parser(settings).name == "docling"  # o padrão
    monkeypatch.setattr(settings, "parser", "azure")
    with pytest.raises(ParserError, match="AZURE_DI_ENDPOINT"):
        build_parser(settings)
    parser = build_parser(azure_settings(settings, monkeypatch))
    assert parser.name == "azure" and parser.page_price_usd == pytest.approx(0.01)


def test_figure_without_service_text_falls_back_to_local_ocr_when_installed(settings, monkeypatch, tmp_path):
    settings = azure_settings(settings, monkeypatch)

    def document() -> ParsedDocument:
        return ParsedDocument(doc_id="d", file_name="f.pdf", page_count=1, parser="azure", pipeline_version="x", items=[
            ParsedItem(type="figure", page=1, image_path="figures/imagem.png", area_ratio=0.2),
            ParsedItem(type="figure", page=1, image_path="figures/vetor.png", area_ratio=0.2,
                       ocr_words=[OcrWord(text="26,51%", x0=0, y0=0, x1=10, y1=5)]),
            ParsedItem(type="figure", page=1, area_ratio=0.01),  # logotipo: sem recorte
        ])

    def no_rapidocr(self):
        raise ImportError("No module named 'rapidocr'")

    # Imagem leve, sem RapidOCR: a figura fica sem texto de conferência.
    monkeypatch.setattr(azure_parser, "check_system_fonts", lambda: None)
    monkeypatch.setattr("app.pipeline.figure_ocr.FigureOcr.warmup", no_rapidocr)
    parsed = document()
    assert AzureParser(settings).read_figures(parsed, tmp_path) == 0
    assert parsed.items[0].ocr_words == [] and parsed.items[2].ocr_words is None

    # Imagem com o OCR local: ele lê só a figura que o serviço deixou sem texto.
    read = [OcrWord(text="14,68%", x0=1, y0=1, x1=9, y1=4)]
    monkeypatch.setattr("app.pipeline.figure_ocr.FigureOcr.warmup", lambda self: None)
    monkeypatch.setattr("app.pipeline.figure_ocr.FigureOcr.read", lambda self, path: read)
    parsed = document()
    assert AzureParser(settings).read_figures(parsed, tmp_path) == 1
    assert parsed.items[0].ocr_words == read and parsed.items[1].ocr_words[0].text == "26,51%"
