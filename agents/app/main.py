"""API do orquestrador: recebe o pedido do usuário, roda a execução e mostra o que aconteceu.

O login é o do indexador: o mesmo token vale aqui (botão Authorize do Swagger).
"""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Annotated, Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from app import mcp
from app.a2a import SpecialistClient
from app.config import Settings, get_settings
from app.execucoes import Run, Runs
from app.orquestrador import Deps, execute
from app.registry import ORCHESTRATOR, Registry
from app.runner import Engine, default_engine
from app.security import Users
from app.tokens import Identity

_bearer = HTTPBearer(description="O access_token devolvido por POST /auth/login do indexador.")


class Pedido(BaseModel):
    pedido: str = Field(min_length=5, max_length=2000, description="O que você quer, em texto livre.",
                        examples=["Extraia os indicadores da Light e revise as fontes."])


class Pergunta(BaseModel):
    pergunta: str = Field(min_length=5, max_length=1000, examples=["Qual empresa teve o maior lucro líquido em 2022?"])
    aguardar: bool = Field(default=True, description="Esperar a resposta nesta chamada. false devolve só o "
                           "execucao_id, para consultar depois.")


class Relatorio(BaseModel):
    aguardar: bool = Field(default=False, description="Esperar o relatório nesta chamada (pode levar vários "
                           "minutos). O padrão devolve o execucao_id.")


def view(run: Run, steps: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    body = {"execucao_id": run.id, **{key: value for key, value in asdict(run).items() if key not in ("id", "user_id")}}
    if steps is not None:
        body["passos"] = steps
    return body


def create_app(settings: Settings | None = None, engine: Engine | None = None,
               specialists_transport: httpx.AsyncBaseTransport | None = None,
               indexer_transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    settings = settings or get_settings()
    deps = Deps(settings=settings, registry=Registry(settings.agentes_dir), engine=engine or default_engine(settings),
                runs=Runs(settings.data_dir),
                specialists=lambda identity: SpecialistClient(settings, identity, specialists_transport))
    users = Users(settings, indexer_transport)
    running: set[asyncio.Task] = set()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        for task in running:
            task.cancel()

    app = FastAPI(
        title="Agentes — orquestrador",
        description="Orquestrador e especialistas para análise de demonstrações financeiras. O orquestrador "
                    "chama os especialistas por A2A; todos usam as ferramentas pelo servidor MCP.",
        lifespan=lifespan)
    app.state.deps = deps

    async def caller(credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)]) -> tuple[str, str]:
        return await users.identify(credentials.credentials), credentials.credentials

    Caller = Annotated[tuple[str, str], Depends(caller)]

    async def start(user: tuple[str, str], tipo: str, pedido: str, wait: bool) -> dict[str, Any]:
        user_id, token = user
        if settings.problem:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, settings.problem)
        users.require_time(token)
        run = deps.runs.create(user_id, tipo, pedido)
        task = asyncio.create_task(execute(run, token, deps))
        running.add(task)
        task.add_done_callback(running.discard)
        if wait:
            await asyncio.shield(task)
            return view(run, deps.runs.trace(run.id).read())
        return view(run)

    @app.get("/health", tags=["Serviço"], summary="Estado do serviço")
    async def health() -> dict[str, Any]:
        snapshot = deps.registry.current()
        return {"status": "ok" if not settings.problem else "configuração incompleta", "problema": settings.problem,
                "agentes": sorted(snapshot.agents), "arquivos_invalidos": snapshot.errors,
                "modelos": settings.modelos, "em_andamento": len(running)}

    @app.post("/api/execucoes", tags=["Execuções"], status_code=status.HTTP_202_ACCEPTED,
              summary="Pedido em texto livre: o orquestrador decide o que fazer")
    async def create_run(body: Pedido, user: Caller) -> dict[str, Any]:
        return await start(user, "livre", body.pedido, wait=False)

    @app.get("/api/execucoes", tags=["Execuções"], summary="Execuções do usuário, da mais nova para a mais antiga")
    async def list_runs(user: Caller) -> list[dict[str, Any]]:
        return [{"execucao_id": run.id, "tipo": run.tipo, "estado": run.estado, "pedido": run.pedido,
                 "criada_em": run.criada_em, "terminada_em": run.terminada_em} for run in deps.runs.of(user[0])]

    @app.get("/api/execucoes/{execucao_id}", tags=["Execuções"],
             summary="Estado, resultado, passos (quem chamou o quê), tokens e custo de uma execução")
    async def get_run(execucao_id: str, user: Caller) -> dict[str, Any]:
        run = deps.runs.get(execucao_id, user[0])
        if run is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Execução não encontrada.")
        return view(run, deps.runs.trace(run.id).read())

    @app.post("/api/perguntas", tags=["Atalhos"], summary="Uma pergunta sobre os documentos, respondida com fontes")
    async def ask(body: Pergunta, user: Caller) -> dict[str, Any]:
        return await start(user, "pergunta", body.pergunta, wait=body.aguardar)

    @app.post("/api/relatorios", tags=["Atalhos"], summary="O relatório consolidado completo dos documentos")
    async def report(user: Caller, body: Relatorio | None = None) -> dict[str, Any]:
        return await start(user, "relatorio", "Relatório consolidado completo.", wait=bool(body and body.aguardar))

    @app.get("/api/agentes", tags=["Agentes"],
             summary="Agentes carregados dos arquivos e as tools que a política do servidor MCP dá a cada um")
    async def agents(user: Caller) -> dict[str, Any]:
        identity = Identity("consulta-agentes", user[0], user[1])
        snapshot = deps.registry.current()
        try:
            catalog = await deps.specialists(identity).catalog()
        except Exception as error:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                f"Serviço dos especialistas fora do ar ({type(error).__name__}: {error}).") from error
        orchestrator = None
        if snapshot.orchestrator is not None:
            spec = snapshot.orchestrator
            try:
                async with deps.engine.connect(ORCHESTRATOR, identity) as server:
                    tools: list[str] | None = await mcp.tool_names(server)
            except Exception:  # noqa: BLE001
                tools = None
            orchestrator = {"nome": spec.nome, "descricao": spec.descricao, "modelo": spec.modelo,
                            "max_turnos": spec.max_turnos, "tools": tools,
                            "chama": [entry["nome"] for entry in catalog["agentes"]]}
        return {"orquestrador": orchestrator, "especialistas": catalog["agentes"],
                "arquivos_invalidos": {**snapshot.errors, **catalog["arquivos_invalidos"]}, "modelos": settings.modelos}

    return app


def app_factory() -> FastAPI:
    from agents import set_default_openai_key

    settings = get_settings()
    if settings.openai_api_key:
        set_default_openai_key(settings.openai_api_key, use_for_tracing=settings.openai_tracing)
    return create_app(settings)
