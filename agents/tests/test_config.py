"""O config/ajustes.yaml: aplicado quando muda, sem reiniciar; inválido não derruba nada."""
import os

from app.config import Settings


def write(path, text: str, bump: int) -> None:
    path.write_text(text, encoding="utf-8")
    stamp = 1_700_000_000 + bump  # o mtime muda a cada escrita, mesmo no mesmo segundo
    os.utime(path, (stamp, stamp))


def test_adjustments_are_applied_when_the_file_changes_and_a_bad_file_keeps_the_last_good_one(tmp_path):
    path = tmp_path / "ajustes.yaml"
    settings = Settings(_env_file=None, config_path=path)
    settings.reload()  # sem arquivo: ficam os padrões
    assert (settings.esforco_raciocinio, settings.max_ondas) == ("low", 20)

    write(path, "raciocinio_padrao: nenhum\nexecucao: {max_ondas: 7}\n"
                "precos:\n  gpt-5.4-nano: {entrada: 0.2, entrada_cache: 0.02, saida: 1.25}\n", 1)
    settings.reload()
    assert (settings.esforco_raciocinio, settings.max_ondas) == ("", 7)
    assert settings.precos == {"gpt-5.4-nano": {"entrada": 0.2, "entrada_cache": 0.02, "saida": 1.25}}
    assert settings.config_error is None

    write(path, "execucao: {max_onda: 3}\n", 2)  # chave com erro de digitação
    settings.reload()
    assert settings.max_ondas == 7 and "max_onda" in settings.config_error

    write(path, "execucao: {max_ondas: 3}\n", 3)
    settings.reload()
    assert settings.max_ondas == 3 and settings.config_error is None


def test_the_shipped_adjustments_file_is_valid():
    settings = Settings(_env_file=None)  # config/ajustes.yaml, na pasta do projeto
    settings.reload()
    assert settings.config_error is None and "gpt-5.4-nano" in settings.precos
