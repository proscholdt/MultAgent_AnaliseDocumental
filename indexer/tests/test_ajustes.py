"""O config/ajustes.yaml: aplicado quando muda, sem reiniciar; inválido não derruba nada."""
import os

from app.config import Settings


def write(path, text: str, bump: int) -> None:
    path.write_text(text, encoding="utf-8")
    stamp = 1_700_000_000 + bump
    os.utime(path, (stamp, stamp))


def test_adjustments_apply_when_the_file_changes_and_a_bad_file_keeps_the_last_good_one(tmp_path):
    path = tmp_path / "ajustes.yaml"
    settings = Settings(_env_file=None, config_path=path)
    settings.reload()
    assert (settings.parser, settings.chunk_target_tokens) == ("docling", 450)

    write(path, "parser: azure\nchunk_target_tokens: 300\n", 1)
    settings.reload()
    assert (settings.parser, settings.chunk_target_tokens, settings.config_error) == ("azure", 300, None)

    write(path, "parser: tesseract\n", 2)  # valor que não existe
    settings.reload()
    assert settings.parser == "azure" and "parser" in settings.config_error

    write(path, "qdrant_collection: outra\n", 3)  # infraestrutura não se ajusta aqui
    settings.reload()
    assert settings.qdrant_collection == "documentos" and "qdrant_collection" in settings.config_error


def test_the_shipped_adjustments_file_is_valid():
    settings = Settings(_env_file=None)
    settings.reload()
    assert settings.config_error is None and settings.parser in ("docling", "azure")
