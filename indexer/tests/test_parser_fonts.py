"""Verificação de fontes do parse-worker. Só roda onde o Docling está instalado (grupo "parse")."""
import pytest

pytest.importorskip("docling")

from app.pipeline import parser


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
