"""Serviço dos especialistas: cada arquivo de agente vira um agente remoto A2A.

Para cada `agentes/<nome>.md` (menos o orquestrador) o serviço publica:

    GET  /agentes/<nome>/.well-known/agent-card.json   o Agent Card, gerado do arquivo
    POST /agentes/<nome>/                              o endpoint A2A (JSON-RPC)

O card é público. O endpoint só atende o orquestrador: exige o token assinado por ele e
emitido para aquele especialista. É o hub-and-spoke: os raios não se falam e não atendem mais
ninguém.

Os endpoints são montados na hora do pedido, a partir da pasta de agentes. Um arquivo novo
passa a atender sem reiniciar o serviço.
"""
import asyncio
from typing import Any

from a2a import helpers
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import DefaultServerCallContextBuilder, create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    HTTPAuthSecurityScheme,
    SecurityRequirement,
    SecurityScheme,
    StringList,
)
from a2a.utils import AGENT_CARD_WELL_KNOWN_PATH
from fastapi import FastAPI, Request
from google.protobuf.json_format import MessageToDict
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app import mcp
from app.config import Settings, get_settings
from app.registry import AgentFile, Registry
from app.runner import AgentRun, Budget, Engine, default_engine, run_agent
from app.tokens import CATALOG, AuthError, Identity, verify_a2a_token

PROTOCOL_VERSION = "1.0"
_CLAIMS = "a2a.claims"
# Tools que só leem: não entram na contagem do que o agente gravou.
_READS = ("ler_", "buscar_", "listar_", "comparar_", "pendencias")


def agent_card(spec: AgentFile, settings: Settings) -> AgentCard:
    """O card sai do arquivo do agente: quem cria um agente não escreve card."""
    base = settings.especialistas_url.rstrip("/")
    return AgentCard(
        name=spec.nome, description=spec.descricao, version=spec.digest,
        supported_interfaces=[AgentInterface(url=f"{base}/agentes/{spec.nome}/", protocol_binding="JSONRPC",
                                             protocol_version=PROTOCOL_VERSION)],
        capabilities=AgentCapabilities(streaming=False),
        default_input_modes=["text/plain", "application/json"], default_output_modes=["application/json"],
        skills=[AgentSkill(id=spec.nome, name=spec.nome, description=spec.descricao,
                           tags=["demonstracoes-financeiras"])],
        security_schemes={"orquestrador": SecurityScheme(http_auth_security_scheme=HTTPAuthSecurityScheme(
            scheme="bearer", bearer_format="JWT",
            description="Token assinado pelo orquestrador e emitido para este especialista."))},
        security_requirements=[SecurityRequirement(schemes={"orquestrador": StringList()})],
    )


def read_request(context: RequestContext) -> dict[str, Any]:
    """O pedido do orquestrador: a tarefa em texto e, numa parte de dados, o documento e o que
    resta do orçamento da execução."""
    data: dict[str, Any] = {}
    for part in context.message.parts:
        if part.HasField("data"):
            value = MessageToDict(part.data)
            if isinstance(value, dict):
                data.update(value)
    return {"tarefa": context.get_user_input().strip(), "doc_id": data.get("doc_id") or None,
            "orcamento": data.get("orcamento") or {}}


class _ClaimsContext(DefaultServerCallContextBuilder):
    """Leva para o executor quem chamou (as claims já conferidas pelo portão)."""

    def build(self, request: Request):
        context = super().build(request)
        context.state["claims"] = request.scope.get(_CLAIMS)
        return context


class SpecialistExecutor(AgentExecutor):
    def __init__(self, nome: str, service: "Specialists") -> None:
        self.nome, self.service = nome, service

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        if context.current_task is None:
            await event_queue.enqueue_event(helpers.new_task_from_user_message(context.message))
        await updater.start_work()
        try:
            state = context.call_context.state
            claims, token = state["claims"], state["headers"].get("x-user-token")
            identity = Identity(run_id=claims["run_id"], user_id=claims["usr"], user_token=token)
            report = await self.service.work(self.nome, read_request(context), identity)
            await updater.add_artifact([helpers.new_data_part(report)], name="relato")
            await updater.complete()
        except Exception as error:  # noqa: BLE001 - a tarefa falha com o motivo, em vez de um erro de transporte
            await updater.failed(helpers.new_text_message(f"{type(error).__name__}: {error}"[:500]))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()


class Specialists:
    """Aplicação ASGI montada em /agentes: escolhe o especialista pelo primeiro trecho do caminho."""

    def __init__(self, settings: Settings, registry: Registry, engine: Engine) -> None:
        self.settings, self.registry, self.engine = settings, registry, engine
        self._apps: dict[str, tuple[str, ASGIApp]] = {}

    # --- o trabalho -------------------------------------------------------------------------

    async def work(self, nome: str, request: dict[str, Any], identity: Identity) -> dict[str, Any]:
        spec = self.registry.current().agents[nome]  # a versão do arquivo na hora da chamada
        if not request["tarefa"]:
            raise ValueError("pedido sem tarefa")
        budget = Budget(
            tokens=min(self.settings.execucao_max_tokens, int(request["orcamento"].get("tokens") or 10**12)),
            seconds=min(self.settings.execucao_max_segundos, float(request["orcamento"].get("segundos") or 10**9)))
        entrada = request["tarefa"] + (f"\n\nDocumento: doc_id = {request['doc_id']}" if request["doc_id"] else "")
        run = await run_agent(spec, entrada, identity=identity, engine=self.engine, budget=budget)
        return await self.report(spec, run, request["doc_id"], identity)

    async def report(self, spec: AgentFile, run: AgentRun, doc_id: str | None, identity: Identity) -> dict[str, Any]:
        """O que volta ao orquestrador. É montado pelo código: o que o agente gravou (contado nas
        chamadas que deram certo) e o que ainda falta para ele (lido da base). O texto do agente
        só volta se o arquivo dele pedir `resposta: texto`."""
        report: dict[str, Any] = {
            "agente": spec.nome, "estado": run.estado, "doc_id": doc_id,
            "gravacoes": {tool: count for tool, count in run.writes.items() if not tool.startswith(_READS)},
        }
        if run.erro:
            report["erro"] = run.erro
        try:
            pending = await self.pending(identity)
            report["pendencias"] = [item for item in pending if item.get("agente") == spec.nome
                                    and (doc_id is None or item.get("doc_id") in (None, doc_id))]
        except Exception as error:  # noqa: BLE001 - o trabalho foi feito; só o relato fica sem esta parte
            report["pendencias_indisponiveis"] = f"{type(error).__name__}: {error}"[:200]
        if spec.resposta == "texto":
            report["texto"] = run.texto
        # Para o trace e o custo da execução; o orquestrador (o código) tira antes de mostrar ao modelo.
        report["execucao"] = {"modelo": run.modelo, "segundos": run.segundos, "uso": run.uso, "passos": run.passos}
        return report

    async def pending(self, identity: Identity) -> list[dict[str, Any]]:
        async with self.engine.connect(mcp.SYSTEM, identity) as server:
            return (await mcp.call(server, "pendencias"))["pendencias"]

    async def catalog(self, identity: Identity) -> dict[str, Any]:
        """Os especialistas no ar e as tools que a política do servidor MCP dá a cada um."""
        snapshot = self.registry.current()

        async def tools_of(spec: AgentFile) -> list[str] | None:
            try:
                async with self.engine.connect(spec.nome, identity) as server:
                    return await mcp.tool_names(server)
            except Exception:  # noqa: BLE001 - servidor MCP fora do ar: o catálogo diz que não soube
                return None

        tools = await asyncio.gather(*(tools_of(spec) for spec in snapshot.specialists))
        base = self.settings.especialistas_url.rstrip("/")
        return {
            "digest": snapshot.digest,
            "agentes": [{"nome": spec.nome, "descricao": spec.descricao, "modelo": spec.modelo,
                         "max_turnos": spec.max_turnos, "resposta": spec.resposta, "tools": listed,
                         "card": f"{base}/agentes/{spec.nome}{AGENT_CARD_WELL_KNOWN_PATH}"}
                        for spec, listed in zip(snapshot.specialists, tools, strict=True)],
            "arquivos_invalidos": snapshot.errors,
        }

    # --- o transporte -----------------------------------------------------------------------

    def identify(self, headers: dict[str, str], target: str) -> tuple[dict, Identity]:
        claims = verify_a2a_token(self.settings, headers.get("authorization"), target)
        if not headers.get("x-user-token"):
            raise AuthError("Falta o token do usuário (X-User-Token).")
        return claims, Identity(claims["run_id"], claims["usr"], headers["x-user-token"])

    def _app(self, spec: AgentFile) -> ASGIApp:
        cached = self._apps.get(spec.nome)
        if cached is None or cached[0] != spec.digest:
            card = agent_card(spec, self.settings)
            handler = DefaultRequestHandler(agent_executor=SpecialistExecutor(spec.nome, self),
                                            task_store=InMemoryTaskStore(), agent_card=card)
            app = Starlette(routes=[*create_agent_card_routes(card),
                                    *create_jsonrpc_routes(handler, "/", context_builder=_ClaimsContext())])
            cached = self._apps[spec.nome] = (spec.digest, app)
        return cached[1]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        root = scope.get("root_path", "")
        path = scope["path"][len(root):] if root and scope["path"].startswith(root) else scope["path"]
        nome, _, rest = path.lstrip("/").partition("/")
        spec = next((agent for agent in self.registry.current().specialists if agent.nome == nome), None)
        if spec is None:
            await JSONResponse({"erro": f"Não há especialista '{nome}'."}, status_code=404)(scope, receive, send)
            return
        child = dict(scope, root_path=f"{root}/{nome}")
        if "/" + rest != AGENT_CARD_WELL_KNOWN_PATH:  # o card é público; o resto, só para o orquestrador
            headers = {key.decode("latin-1"): value.decode("latin-1") for key, value in scope["headers"]}
            try:
                child[_CLAIMS], _ = self.identify(headers, nome)
            except AuthError as error:
                await JSONResponse({"erro": str(error)}, status_code=401)(scope, receive, send)
                return
        await self._app(spec)(child, receive, send)


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    settings = settings or get_settings()
    service = Specialists(settings, Registry(settings.agentes_dir), engine or default_engine(settings))
    app = FastAPI(
        title="Especialistas (A2A)",
        description="Agentes especialistas servidos pelo protocolo A2A. Só o orquestrador os chama.")
    app.state.service = service

    @app.get("/health", summary="Estado do serviço")
    async def health() -> dict[str, Any]:
        snapshot = service.registry.current()
        return {"status": "ok" if not settings.problem else "configuração incompleta", "problema": settings.problem,
                "especialistas": [spec.nome for spec in snapshot.specialists], "arquivos_invalidos": snapshot.errors}

    @app.get("/agentes", summary="Especialistas no ar e as tools de cada um (só para o orquestrador)")
    async def catalog(request: Request) -> JSONResponse:
        try:
            _, identity = service.identify(dict(request.headers), CATALOG)
        except AuthError as error:
            return JSONResponse({"erro": str(error)}, status_code=401)
        return JSONResponse(await service.catalog(identity))

    app.mount("/agentes", service)
    return app


def app_factory() -> FastAPI:
    from agents import set_default_openai_key

    settings = get_settings()
    if settings.openai_api_key:
        set_default_openai_key(settings.openai_api_key, use_for_tracing=settings.openai_tracing)
    return create_app(settings)
