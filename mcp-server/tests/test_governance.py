"""Governança: quem vê e chama o quê, limites, recarga da política e auditoria."""
import os
import time

import jwt
import pytest

from app.auth import identify, verify_agent_token
from app.catalog import load_tools, strict_schema
from app.errors import AuthError, IndexerAuthError
from app.policy import PolicyError, parse_policy
from tests.conftest import ANA, BRUNO, DOC_ANA, SECRET, agent_token, headers, payload

# --- o que cada agente vê e chama ---------------------------------------------------------


async def test_agent_sees_only_the_tools_the_policy_gives_it(governance):
    assert [tool.name for tool in await governance.list_tools(headers("leitor"))] == [
        "listar_documentos", "buscar_trechos", "ler_chunk", "ler_pagina"]
    assert [tool.name for tool in await governance.list_tools(headers("contido"))] == ["listar_documentos"]
    # Agente que não está na política existe (o token é válido), mas não tem acesso a nada.
    assert await governance.list_tools(headers("desconhecido")) == []


async def test_calling_a_tool_outside_the_policy_is_refused_and_audited(governance, deps):
    refused = await governance.call_tool(headers("contido"), "ler_pagina", {"doc_id": DOC_ANA, "pagina": 8})
    assert refused.is_error and "não está liberada para o agente 'contido'" in payload(refused)["erro"]
    # Tool que não existe tem a mesma resposta: quem não pode chamar não fica sabendo o que existe.
    missing = await governance.call_tool(headers("contido"), "apagar_tudo", {})
    assert payload(missing)["erro"] == payload(refused)["erro"].replace("ler_pagina", "apagar_tudo")

    rows = await deps.audit.rows(run_id="exec-1")
    assert [(row.agent, row.tool, row.outcome, row.detail) for row in reversed(rows)] == [
        ("contido", "ler_pagina", "recusada", "fora da política"),
        ("contido", "apagar_tudo", "recusada", "fora da política")]
    assert rows[0].user_id == ANA and rows[0].policy_digest == deps.policies.current().digest
    assert rows[1].arguments == {"doc_id": DOC_ANA, "pagina": 8}


async def test_invalid_arguments_come_back_naming_the_field(governance, deps):
    result = await governance.call_tool(headers("leitor"), "ler_pagina", {"doc_id": DOC_ANA, "pagina": 0, "x": 1})
    assert result.is_error
    problems = payload(result)["problemas"]
    assert any(problem.startswith("pagina:") for problem in problems)
    assert any(problem.startswith("x:") for problem in problems)  # campo desconhecido é erro
    assert (await deps.audit.rows())[0].outcome == "invalida"


async def test_call_limit_per_run_stops_a_runaway_agent(governance):
    for _ in range(2):
        assert not (await governance.call_tool(headers("contido"), "listar_documentos", {})).is_error
    blocked = await governance.call_tool(headers("contido"), "listar_documentos", {})
    assert blocked.is_error and "limite de 2 chamadas" in payload(blocked)["erro"]
    # O limite é por execução: outra execução do mesmo agente começa do zero.
    other = await governance.call_tool(headers("contido", run_id="exec-2"), "listar_documentos", {})
    assert not other.is_error


# --- política ---------------------------------------------------------------------------------


def bump(path) -> None:
    """Garante que a data do arquivo muda, mesmo em sistemas com relógio de arquivo grosso."""
    stamp = time.time_ns() + 5_000_000_000
    os.utime(path, ns=(stamp, stamp))


async def test_policy_is_reloaded_when_the_file_changes_without_restart(governance, deps, policy_file):
    before = deps.policies.current().digest
    policy_file.write_text("agentes:\n  novo:\n    tools: [listar_documentos, ler_pagina]\n", encoding="utf-8")
    bump(policy_file)
    assert [tool.name for tool in await governance.list_tools(headers("novo"))] == ["listar_documentos", "ler_pagina"]
    assert await governance.list_tools(headers("leitor")) == []  # saiu da política: perdeu tudo
    assert deps.policies.current().digest != before


@pytest.mark.parametrize("broken, reason", [
    ("agentes:\n  leitor:\n    tools: [listar_documentos, apagar_tudo]\n", "tools que não existem: apagar_tudo"),
    ("agentes: [isto não é um mapa", "YAML inválido"),
    ("agentes:\n  Leitor Um:\n    tools: []\n", "Nome de agente inválido"),
    ("agentes:\n  leitor:\n    tools: [listar_documentos]\n    limites: {chamadas_por_execucao: 0}\n", "inteiro positivo"),
])
async def test_invalid_policy_keeps_the_last_good_one(governance, deps, policy_file, broken, reason):
    good = deps.policies.current().digest
    policy_file.write_text(broken, encoding="utf-8")
    bump(policy_file)
    # Não derruba nem abre: continua valendo a anterior, e o motivo fica visível.
    assert len(await governance.list_tools(headers("leitor"))) == 4
    assert deps.policies.current().digest == good and reason in deps.policies.last_error


def test_policy_needs_agents_and_known_tools():
    tools = set(load_tools())
    with pytest.raises(PolicyError, match="seção 'agentes'"):
        parse_policy("outra: coisa", tools)
    with pytest.raises(PolicyError, match="falta a lista 'tools'"):
        parse_policy("agentes:\n  leitor: {}", tools)
    policy = parse_policy("agentes:\n  leitor:\n    tools: [ler_pagina]\n", tools)
    assert policy.allows("leitor", "ler_pagina") and not policy.allows("leitor", "ler_chunk")
    assert policy.calls_per_run("leitor") is None and policy.calls_per_run("ninguem") is None


# --- identidade -------------------------------------------------------------------------------


async def test_agent_token_must_be_signed_current_and_addressed_to_this_server(settings, deps):
    assert verify_agent_token(agent_token("leitor"), settings)["agent"] == "leitor"
    for token, reason in [
        (agent_token("leitor", secret="outro-segredo-com-mais-de-32-caracteres!!"), "inválido"),
        (agent_token("leitor", expires_in=-10), "expirado"),
        (agent_token("leitor", audience="outro-servico"), "inválido"),
        (agent_token("Leitor Um"), "nome de agente inválido"),
        (jwt.encode({"agent": "leitor", "aud": "mcp-server", "exp": int(time.time()) + 60}, SECRET), "inválido"),
    ]:
        with pytest.raises(AuthError, match=reason) as error:
            verify_agent_token(token, settings)
        assert error.value.status == 401


async def test_user_token_is_required_checked_by_the_indexer_and_must_match_the_agent_token(settings, deps, indexer):
    with pytest.raises(AuthError, match="Falta o token do agente"):
        await identify({"x-user-token": "tok-ana"}, settings, deps.users)
    with pytest.raises(AuthError, match="Falta o token do usuário"):
        await identify({"authorization": f"Bearer {agent_token('leitor')}"}, settings, deps.users)
    with pytest.raises(IndexerAuthError):  # token de usuário que o indexador não reconhece
        await identify(headers("leitor", user_token="tok-vencido"), settings, deps.users)
    # Token de agente emitido para a Ana, usado com o JWT do Bruno.
    with pytest.raises(AuthError, match="emitido para outro usuário") as error:
        await identify(headers("leitor", user_token="tok-bruno", user_id=ANA), settings, deps.users)
    assert error.value.status == 403

    caller = await identify(headers("leitor", user_token="tok-bruno", user_id=BRUNO), settings, deps.users)
    assert (caller.agent, caller.user_id, caller.run_id) == ("leitor", BRUNO, "exec-1")
    # Quem é o usuário fica em cache: a segunda identificação não pergunta de novo ao indexador.
    asked = indexer.calls.count("GET /auth/me")
    await identify(headers("leitor", user_token="tok-bruno", user_id=BRUNO), settings, deps.users)
    assert indexer.calls.count("GET /auth/me") == asked


# --- esquema das tools ------------------------------------------------------------------------


def test_every_tool_schema_is_flat_and_strict():
    """O formato que o modo estrito do modelo exige: sem referência, sem objeto aninhado, todo
    campo obrigatório (o opcional aceita null) e nenhum campo extra."""
    forbidden = {"$ref", "$defs", "title", "default", "minLength", "maxLength", "minimum", "maximum", "pattern"}
    for name, spec in load_tools().items():
        schema = strict_schema(spec.args)
        assert schema["type"] == "object" and schema["additionalProperties"] is False, name
        assert schema["required"] == list(schema["properties"]), name
        assert not forbidden & set(str(schema).replace("'", " ").split()), name
        for field, definition in schema["properties"].items():
            assert definition.get("type") != "object", f"{name}.{field}: argumento aninhado"
            assert definition.get("description"), f"{name}.{field}: sem descrição para o modelo"
        assert spec.definition().description == spec.description and "\n" not in spec.description
