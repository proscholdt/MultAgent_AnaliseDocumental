"""Arquivos de agente: o que é aceito, o que é recusado e a recarga sem reiniciar."""
import os
import time

import pytest

from app.cli import main as cli
from app.registry import AgentFileError, Registry, parse_agent
from tests.conftest import write_agent

GOOD = """---
nome: extrator
descricao: Extrai os indicadores financeiros de um documento.
modelo: forte
max_turnos: 40
---
Você extrai indicadores de demonstrações financeiras, com a origem de cada valor.
"""


def test_agent_file_is_a_header_plus_the_prompt():
    agent = parse_agent(GOOD, "extrator.md")
    assert (agent.nome, agent.modelo, agent.max_turnos, agent.resposta) == ("extrator", "forte", 40, "base")
    assert agent.descricao == "Extrai os indicadores financeiros de um documento."
    assert agent.prompt.startswith("Você extrai indicadores") and "---" not in agent.prompt
    # O que muda o arquivo muda a impressão dele (é o que as execuções guardam).
    assert parse_agent(GOOD.replace("40", "41"), "extrator.md").digest != agent.digest


@pytest.mark.parametrize("change, reason", [
    (lambda text: text.replace("---\n", "", 1), "falta o cabeçalho"),
    (lambda text: text.replace("nome: extrator", "nome: Extrator Um"), "letras minúsculas"),
    (lambda text: text.replace("nome: extrator", "nome: sistema"), "nome reservado"),
    (lambda text: text.replace("nome: extrator", "nome: outro"), "tem de se chamar outro.md"),
    (lambda text: text.replace("descricao: Extrai os indicadores financeiros de um documento.", "descricao: Extrai"),
     "'descricao' tem de dizer"),
    (lambda text: text.replace("max_turnos: 40", "max_turnos: 0"), "inteiro de 1 a 200"),
    (lambda text: text.replace("max_turnos: 40", "max_turnos: 40\ntools: [apagar_tudo]"), "chave desconhecida"),
    (lambda text: text.replace("max_turnos: 40", "max_turnos: 40\nresposta: livre"), "'base' ou 'texto'"),
    (lambda text: text.split("---\n")[0] + "---\n" + text.split("---\n")[1] + "---\nCurto.\n", "vazio ou curto"),
    (lambda text: text.replace("modelo: forte", "modelo: [forte"), "YAML inválido"),
])
def test_invalid_agent_file_says_what_is_wrong(change, reason):
    with pytest.raises(AgentFileError, match=reason):
        parse_agent(change(GOOD), "extrator.md")


def test_the_file_cannot_grant_itself_tools():
    """Permissão é do servidor MCP. Uma chave 'tools' no arquivo é erro, não é ignorada em silêncio."""
    with pytest.raises(AgentFileError, match="chave desconhecida no cabeçalho: tools"):
        parse_agent(GOOD.replace("modelo: forte", "tools: [salvar_indicador]"), "extrator.md")


def bump(path) -> None:
    stamp = time.time_ns() + 5_000_000_000
    os.utime(path, ns=(stamp, stamp))


def test_folder_is_reread_when_it_changes_and_a_bad_file_does_not_take_the_others_down(agents_dir):
    registry = Registry(agents_dir)
    first = registry.current()
    assert sorted(first.agents) == ["extrator", "orquestrador", "redator"] and not first.errors
    assert [agent.nome for agent in first.specialists] == ["extrator", "redator"]  # o orquestrador não é especialista
    assert registry.current() is first  # nada mudou: não relê

    write_agent(agents_dir, "tradutor")
    (agents_dir / "quebrado.md").write_text("sem cabeçalho", encoding="utf-8")
    second = registry.current()
    assert "tradutor" in second.agents and second.digest != first.digest
    assert second.errors == {"quebrado.md": "falta o cabeçalho entre linhas '---' no começo do arquivo"}

    path = write_agent(agents_dir, "extrator", max_turnos=9)
    bump(path)
    assert registry.current().agents["extrator"].max_turnos == 9
    path.unlink()
    assert "extrator" not in registry.current().agents


def test_cli_creates_the_file_and_prints_the_policy_entry(agents_dir, monkeypatch, capsys):
    monkeypatch.setenv("AGENTES_DIR", str(agents_dir))
    from app.config import get_settings
    get_settings.cache_clear()
    try:
        assert cli(["novo-agente", "consultor", "--descricao", "Compara os riscos citados pelas empresas.",
                    "--tools", "ler_analise,buscar_trechos"]) == 0
        printed = capsys.readouterr().out
        assert "  consultor:\n    tools: [ler_analise, buscar_trechos]" in printed and "policy.yaml" in printed
        created = Registry(agents_dir).current().agents["consultor"]
        assert created.descricao == "Compara os riscos citados pelas empresas." and created.resposta == "base"
        # Não sobrescreve, e não cria com nome que o servidor MCP recusaria.
        assert cli(["novo-agente", "consultor", "--descricao", "Outra descrição com tamanho suficiente."]) == 2
        assert cli(["novo-agente", "Consultor-2", "--descricao", "Outra descrição com tamanho suficiente."]) == 2
        assert cli(["novo-agente", "sistema", "--descricao", "Outra descrição com tamanho suficiente."]) == 2
        assert cli(["validar"]) == 0
        (agents_dir / "ruim.md").write_text("nada", encoding="utf-8")
        assert cli(["validar"]) == 1 and "ERRO  ruim.md" in capsys.readouterr().out
    finally:
        get_settings.cache_clear()
