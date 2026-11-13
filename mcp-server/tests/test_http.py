"""O servidor de ponta a ponta, pelo protocolo: um cliente MCP de verdade fala com a aplicação
por HTTP (em memória, sem abrir porta)."""
from contextlib import asynccontextmanager

import httpx
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from app.main import create_app
from tests.conftest import ADMIN_KEY, DOC_ANA, agent_token, headers, payload

URL = "http://mcp.teste/mcp"


@asynccontextmanager
async def running(settings, indexer):
    """A aplicação com o ciclo de vida aberto. É um gerenciador de contexto usado dentro do
    teste, e não uma fixture: o servidor MCP abre um grupo de tarefas, que tem de ser fechado
    na mesma tarefa em que foi aberto."""
    application = create_app(settings, indexer_transport=indexer.transport())
    async with application.router.lifespan_context(application):
        yield application


def mcp_client(app, request_headers: dict[str, str]) -> Client:
    http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), headers=request_headers)
    return Client(streamable_http_client(URL, http_client=http))


async def test_mcp_client_lists_and_calls_only_what_its_agent_may(settings, indexer):
    async with running(settings, indexer) as app:
        async with mcp_client(app, headers("leitor")) as client:
            assert [tool.name for tool in (await client.list_tools()).tools] == [
                "listar_documentos", "buscar_trechos", "ler_chunk", "ler_pagina"]
            page = await client.call_tool("ler_pagina", {"doc_id": DOC_ANA, "pagina": 8})
            assert not page.is_error and payload(page)["chunks"][0]["chunk_index"] == 1
        async with mcp_client(app, headers("contido")) as client:
            assert [tool.name for tool in (await client.list_tools()).tools] == ["listar_documentos"]
            refused = await client.call_tool("ler_pagina", {"doc_id": DOC_ANA, "pagina": 8})
            assert refused.is_error and "não está liberada" in payload(refused)["erro"]


async def test_request_without_a_valid_agent_token_never_reaches_mcp(settings, indexer):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    expired = f"Bearer {agent_token('leitor', expires_in=-5)}"
    async with (running(settings, indexer) as app,
                httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://mcp.teste") as http):
        for request_headers, reason in [
            ({}, "Falta o token do agente"),
            ({"authorization": "Bearer nao-e-um-jwt", "x-user-token": "tok-ana"}, "inválido"),
            ({"authorization": expired, "x-user-token": "tok-ana"}, "expirado"),
            ({"authorization": f"Bearer {agent_token('leitor')}"}, "Falta o token do usuário"),
        ]:
            response = await http.post("/mcp", json=body, headers=request_headers)
            assert response.status_code == 401 and reason in response.json()["erro"]
            assert response.headers["www-authenticate"] == "Bearer"


async def test_support_routes_show_policy_and_audit_trail_to_the_administrator(settings, indexer):
    async with running(settings, indexer) as app:
        async with mcp_client(app, headers("contido", run_id="exec-9")) as client:
            await client.list_tools()
            await client.call_tool("ler_pagina", {"doc_id": DOC_ANA, "pagina": 8})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://mcp.teste") as http:
            health = (await http.get("/health")).json()
            assert health["status"] == "ok" and health["erro_politica"] is None
            assert (await http.get("/governanca")).status_code == 401
            assert (await http.get("/auditoria", headers={"X-Admin-Key": "errada"})).status_code == 401

            admin = {"X-Admin-Key": ADMIN_KEY}
            governance = (await http.get("/governanca", headers=admin)).json()
            assert governance["agentes"]["contido"] == {"tools": ["listar_documentos"], "chamadas_por_execucao": 2}
            assert governance["tools"]["buscar_trechos"] == "rag" and governance["politica"] == health["politica"]

            trail = (await http.get("/auditoria", params={"run_id": "exec-9"}, headers=admin)).json()
            # A mais recente vem primeiro. O cliente MCP também lista as tools por conta própria
            # (para conferir a chamada), então pode haver mais de uma linha de listagem.
            assert (trail[0]["tipo"], trail[0]["tool"], trail[0]["resultado"]) == ("chamada", "ler_pagina", "recusada")
            assert trail[0]["agente"] == "contido" and trail[0]["argumentos"] == {"doc_id": DOC_ANA, "pagina": 8}
            assert {(row["tipo"], row["resultado"]) for row in trail[1:]} == {("lista", "ok")}


def test_server_refuses_to_start_without_a_signing_secret(settings):
    weak = settings.model_copy(update={"mcp_agent_secret": "curto"})
    with pytest.raises(RuntimeError, match="MCP_AGENT_SECRET"):
        create_app(weak)
