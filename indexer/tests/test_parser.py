"""Parser do parse-worker. Só roda onde o Docling está instalado (grupo "parse")."""
from types import SimpleNamespace

import pytest

pytest.importorskip("docling")

from docling_core.types.doc import DocItemLabel, ListItem, TextItem

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
