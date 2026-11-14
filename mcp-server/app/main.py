"""Aplicação HTTP: o endpoint MCP em /mcp e três rotas de apoio (/health, /governanca e
/auditoria), estas com Swagger em /docs."""
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel

from app import telemetria
from app.audit import Audit
from app.auth import UserResolver, check_request
from app.catalog import Deps, load_tools
from app.config import Settings, get_settings
from app.db import Database
from app.documents import Documents
from app.errors import AuthError
from app.indexer_client import IndexerClient
from app.policy import PolicyStore
from app.server import Governance, build_server, zerar_metricas
from app.store import Store

MCP_PATH = "/mcp"


class AgentTokenGate:
    """Barra, antes de qualquer mensagem MCP, a requisição sem token de agente válido ou sem
    token de usuário. A resposta é um 401 de HTTP, como o protocolo pede."""

    def __init__(self, app: Any, settings: Settings) -> None:
        self._app, self._settings = app, settings

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope["path"].rstrip("/") == MCP_PATH:
            headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope["headers"]}
            try:
                check_request(headers, self._settings)
            except AuthError as exc:
                body = json.dumps({"erro": exc.message}, ensure_ascii=False).encode("utf-8")
                await send({"type": "http.response.start", "status": exc.status, "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"www-authenticate", b"Bearer"), (b"content-length", str(len(body)).encode())]})
                await send({"type": "http.response.body", "body": body})
                return
        await self._app(scope, receive, send)


class HealthOut(BaseModel):
    status: str
    indexer: str
    politica: str
    erro_politica: str | None


class GovernanceOut(BaseModel):
    politica: str
    erro_da_ultima_recarga: str | None
    tools: dict[str, str]  # nome → grupo
    agentes: dict[str, dict[str, Any]]


class AuditOut(BaseModel):
    quando: datetime
    execucao: str
    agente: str
    usuario: str
    tipo: str
    tool: str | None
    resultado: str
    detalhe: str
    argumentos: dict[str, Any] | None
    duracao_ms: int
    politica: str


def create_app(settings: Settings | None = None, *,
               indexer_transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    settings = settings or get_settings()
    if settings.problem:
        raise RuntimeError(settings.problem)
    tools = load_tools()
    database = Database(settings.database_url)
    indexer = IndexerClient(settings, transport=indexer_transport)
    deps = Deps(settings=settings, indexer=indexer, users=UserResolver(indexer, settings.user_cache_seconds),
                policies=PolicyStore(settings.policy_path, set(tools)), audit=Audit(database),
                database=database, documents=Documents(indexer), store=Store(database))
    mcp_server = build_server(Governance(deps, tools))
    # Sem sessão e com resposta em JSON: cada mensagem é uma requisição completa, com os seus
    # cabeçalhos. A proteção de DNS rebinding do SDK (lista de Hosts aceitos) fica desligada
    # porque este servidor é chamado por serviços, não por navegador, e nenhuma mensagem
    # passa sem um token assinado.
    mcp_app = mcp_server.streamable_http_app(
        streamable_http_path=MCP_PATH, stateless_http=True, json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await database.init()
        async with mcp_server.session_manager.run():
            yield
        await indexer.close()
        await database.close()

    app = FastAPI(
        title="Servidor MCP com governança",
        description="Única porta dos agentes para as ferramentas e para o RAG. O endpoint MCP fica em "
                    f"`{MCP_PATH}` (Streamable HTTP). As rotas abaixo são de apoio.",
        version="0.1.0", lifespan=lifespan)
    app.state.deps = deps
    if telemetria.configurar("mcp-server"):
        zerar_metricas(deps.policies.current())
    telemetria.instrumentar(app)

    def admin(x_admin_key: Annotated[str | None, Header()] = None) -> None:
        if not settings.admin_key or x_admin_key != settings.admin_key:
            raise HTTPException(status_code=401, detail="Chave de administração ausente ou incorreta.")

    @app.get("/health", response_model=HealthOut, tags=["Apoio"], summary="Estado do servidor")
    async def health() -> HealthOut:
        reachable = await indexer.ping()
        policy = deps.policies.current()
        return HealthOut(status="ok" if reachable else "degraded", indexer="ok" if reachable else "inacessível",
                         politica=policy.digest, erro_politica=deps.policies.last_error)

    @app.get("/governanca", response_model=GovernanceOut, tags=["Apoio"], dependencies=[Depends(admin)],
             summary="Política em vigor: o que cada agente vê e chama")
    async def governance() -> GovernanceOut:
        policy = deps.policies.current()
        return GovernanceOut(
            politica=policy.digest, erro_da_ultima_recarga=deps.policies.last_error,
            tools={name: spec.group for name, spec in tools.items()},
            agentes={name: {"tools": sorted(entry.tools), "chamadas_por_execucao": entry.calls_per_run}
                     for name, entry in policy.agents.items()})

    @app.get("/auditoria", response_model=list[AuditOut], tags=["Apoio"], dependencies=[Depends(admin)],
             summary="Trilha das listagens e chamadas de tools, da mais recente para a mais antiga")
    async def audit(run_id: Annotated[str | None, Query(description="Uma execução.")] = None,
                    agente: Annotated[str | None, Query(description="Um agente.")] = None,
                    limite: Annotated[int, Query(ge=1, le=1000)] = 200) -> list[AuditOut]:
        rows = await deps.audit.rows(run_id=run_id, agent=agente, limit=limite)
        return [AuditOut(quando=row.at, execucao=row.run_id, agente=row.agent, usuario=row.user_id,
                         tipo=row.kind, tool=row.tool, resultado=row.outcome, detalhe=row.detail,
                         argumentos=row.arguments, duracao_ms=row.duration_ms, politica=row.policy_digest)
                for row in rows]

    app.mount("/", AgentTokenGate(mcp_app, settings))
    return app


def app_factory() -> FastAPI:
    """Ponto de entrada do uvicorn: `uvicorn app.main:app_factory --factory`."""
    return create_app()
