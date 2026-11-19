"""O contrato entre os agentes e o servidor MCP, conferido dos dois lados do repositório.

O servidor grava o contrato das tools em mcp-server/tests/contratos/tools.json (e o teste dele
falha se mudar sem querer). Aqui, o outro lado: toda tool que o código dos agentes chama e toda
tool que um prompt cita entre crases existe nesse contrato. Uma tool renomeada no servidor quebra
este teste antes de quebrar uma execução.
"""
import json
import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
CONTRATO = RAIZ / "mcp-server" / "tests" / "contratos" / "tools.json"
AGENTS = RAIZ / "agents"

pytestmark = pytest.mark.skipif(not CONTRATO.exists(), reason="fora do repositório completo")


def _tools() -> set[str]:
    return set(json.loads(CONTRATO.read_text(encoding="utf-8")))


def test_tools_chamadas_pelo_codigo_existem_no_servidor():
    chamadas = set()
    for arquivo in [*AGENTS.glob("app/**/*.py"), *AGENTS.glob("scripts/*.py")]:
        chamadas |= set(re.findall(r'\.call\("([a-z_]+)"', arquivo.read_text(encoding="utf-8")))
    assert chamadas, "nenhuma chamada achada: o padrão do teste ficou velho"
    assert not chamadas - _tools(), f"tools que não existem no servidor: {sorted(chamadas - _tools())}"


def test_tools_citadas_nos_prompts_existem_no_servidor():
    tools = _tools()
    # Um nome entre crases com cara de tool (verbo_objeto das tools do servidor).
    verbos = {nome.split("_", 1)[0] for nome in tools}
    faltam = {}
    for arquivo in AGENTS.glob("agentes/*/*.md"):
        citadas = {nome for nome in re.findall(r"`([a-z]+_[a-z_]+)`", arquivo.read_text(encoding="utf-8"))
                   if nome.split("_", 1)[0] in verbos}
        if citadas - tools:
            faltam[arquivo.name] = sorted(citadas - tools)
    assert not faltam, f"prompts citam tools que não existem: {faltam}"


def test_cada_agente_tem_entrada_na_politica_do_servidor():
    politica = (RAIZ / "mcp-server" / "config" / "policy.yaml").read_text(encoding="utf-8")
    agentes = {pasta.name for pasta in AGENTS.glob("agentes/*") if (pasta / f"{pasta.name}.md").exists()}
    sem = sorted(nome for nome in agentes if not re.search(rf"^  {nome}:", politica, re.MULTILINE))
    assert not sem, f"agentes sem permissão no policy.yaml: {sem}"
