"""API do orquestrador: recebe o pedido do usuário, roda a execução e mostra o que aconteceu.

O login é o do indexador: o mesmo token vale aqui (botão Authorize do Swagger).

Todo pedido pertence a uma conversa. Sem `conversa_id`, cada pedido abre uma conversa nova; com
ele, o pedido continua a conversa: o orquestrador lembra os turnos anteriores, e cada
especialista lembra o que fez nela.
"""
import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Annotated, Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from langgraph.checkpoint.base import BaseCheckpointSaver
from opentelemetry import trace
from pydantic import BaseModel, Field

from app import telemetria
from app.config import Settings, get_settings
from app.ferramentas.avaliacao_automatica import avaliar as avaliar_relatorio
from app.ferramentas.avaliacao_automatica import zerar_metricas as zerar_metricas_avaliacao
from app.nucleo.agentes import ORCHESTRATOR, Registry
from app.nucleo.banco import is_postgres, preparar, psycopg_dsn
from app.nucleo.executar import Engine, default_engine, instrumentar_agentes
from app.nucleo.tokens import Identity
from app.orquestrador.especialistas_a2a import SpecialistClient
from app.orquestrador.execucoes import RUNNING, Run, open_store
from app.orquestrador.grafo import Deps, execute, zerar_metricas
from app.orquestrador.intencao import Roteador, embedder_openai
from app.orquestrador.usuario import Users

_bearer = HTTPBearer(description="O access_token devolvido por POST /auth/login do indexador.")
_CONVERSA = Field(default=None, description="Continua esta conversa (o orquestrador e os especialistas lembram os "
                  "turnos anteriores). null abre uma conversa nova.")


class Pedido(BaseModel):
    pedido: str = Field(min_length=5, max_length=2000, description="O que você quer, em texto livre.",
                        examples=["Extraia os indicadores da Light e revise as fontes."])
    conversa_id: str | None = _CONVERSA


class Pergunta(BaseModel):
    pergunta: str = Field(min_length=5, max_length=1000, examples=["Qual empresa teve o maior lucro líquido em 2022?"])
    aguardar: bool = Field(default=True, description="Esperar a resposta nesta chamada. false devolve só o "
                           "execucao_id, para consultar depois.")
    conversa_id: str | None = _CONVERSA


class Relatorio(BaseModel):
    aguardar: bool = Field(default=False, description="Esperar o relatório nesta chamada (pode levar vários "
                           "minutos). O padrão devolve o execucao_id.")
    conversa_id: str | None = _CONVERSA


class Mensagem(BaseModel):
    texto: str = Field(min_length=2, max_length=2000, description="O que você quer, em texto livre.",
                       examples=["Qual empresa teve a maior margem EBITDA?", "E a menor?"])
    aguardar: bool = Field(default=True, description="Esperar a resposta nesta chamada.")


def view(run: Run, steps: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    body = {"execucao_id": run.id, **{key: value for key, value in asdict(run).items() if key not in ("id", "user_id")}}
    if steps is not None:
        body["passos"] = steps
    return body


def turn_view(run: Run) -> dict[str, Any]:
    answers = (run.resultado or {}).get("respostas") or []
    return {"execucao_id": run.id, "pedido": run.pedido, "tipo": run.tipo, "estado": run.estado,
            "mensagem": run.mensagem, "erro": run.erro,
            "respostas": [{"resposta": a["resposta"], "fontes": a.get("fontes", [])} for a in answers],
            "relatorio": (run.resultado or {}).get("relatorio"),
            "opcoes": (run.resultado or {}).get("opcoes"),
            "criada_em": run.criada_em, "terminada_em": run.terminada_em}


def create_app(settings: Settings | None = None, engine: Engine | None = None,
               specialists_transport: httpx.AsyncBaseTransport | None = None,
               indexer_transport: httpx.AsyncBaseTransport | None = None,
               checkpointer: BaseCheckpointSaver | None = None,
               avaliar: Callable[[Settings, Identity, str, str | None], Awaitable[dict | None]] | None = avaliar_relatorio,
               roteador: Roteador | None = None,
               ) -> FastAPI:
    settings = settings or get_settings()
    if is_postgres(settings.database_url):
        preparar(settings.database_url)
    runs, conversations = open_store(settings)
    deps = Deps(settings=settings, registry=Registry(settings.agentes_dir), engine=engine or default_engine(settings),
                runs=runs, checkpointer=checkpointer, avaliar=avaliar, roteador=roteador,
                specialists=lambda identity: SpecialistClient(settings, identity, specialists_transport))
    users = Users(settings, indexer_transport)
    running: set[asyncio.Task] = set()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if deps.checkpointer is not None:  # dado de fora (testes)
            yield
        elif is_postgres(settings.database_url):
            # Com Postgres, o checkpoint fica no banco: qualquer réplica continua a conversa.
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

            async with AsyncPostgresSaver.from_conn_string(psycopg_dsn(settings.database_url)) as saver:
                await saver.setup()
                deps.checkpointer = saver
                yield
        else:
            # O checkpoint das conversas: o estado do grafo por conversa, em SQLite no volume de dados.
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

            settings.data_dir.mkdir(parents=True, exist_ok=True)
            async with AsyncSqliteSaver.from_conn_string(str(settings.data_dir / "conversas.db")) as saver:
                deps.checkpointer = saver
                yield
        for task in running:
            task.cancel()

    app = FastAPI(
        title="Agentes — orquestrador",
        description="Orquestrador e especialistas para análise de demonstrações financeiras. O orquestrador é um "
                    "grafo (LangGraph) com agentes PydanticAI; chama os especialistas por A2A, e todos usam as "
                    "ferramentas pelo servidor MCP.",
        lifespan=lifespan)
    app.state.deps = deps
    if telemetria.configurar("agents-orquestrador"):
        instrumentar_agentes(settings)
        agentes = deps.registry.current().agents
        zerar_metricas(sorted(agentes), sorted({spec.modelo for spec in agentes.values()}))
        zerar_metricas_avaliacao()
    telemetria.instrumentar(app)

    async def caller(credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)]) -> tuple[str, str]:
        return await users.identify(credentials.credentials), credentials.credentials

    Caller = Annotated[tuple[str, str], Depends(caller)]

    def conversation_of(user_id: str, conversa_id: str | None) -> str:
        if conversa_id is None:
            return conversations.create(user_id).id
        if conversations.get(conversa_id, user_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversa não encontrada.")
        if any(run.estado in RUNNING for run in deps.runs.of_conversation(conversa_id)):
            raise HTTPException(status.HTTP_409_CONFLICT, "Esta conversa já tem um pedido em andamento. Espere ele "
                                "terminar para mandar o próximo.")
        return conversa_id

    async def start(user: tuple[str, str], tipo: str, pedido: str, wait: bool,
                    conversa_id: str | None = None) -> dict[str, Any]:
        user_id, token = user
        if settings.problem:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, settings.problem)
        users.require_time(token)
        run = deps.runs.create(user_id, tipo, pedido, conversation_of(user_id, conversa_id))
        task = asyncio.create_task(execute(run, token, deps))
        running.add(task)
        task.add_done_callback(running.discard)
        if wait:
            await asyncio.shield(task)
            return view(run, deps.runs.trace(run.id).read())
        return view(run)

    def own_run(execucao_id: str, user_id: str) -> Run:
        run = deps.runs.get(execucao_id, user_id)
        if run is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Execução não encontrada.")
        return run

    @app.get("/health", tags=["Serviço"], summary="Estado do serviço")
    async def health() -> dict[str, Any]:
        snapshot = deps.registry.current()
        settings.reload()
        return {"status": "ok" if not settings.problem else "configuração incompleta", "problema": settings.problem,
                "agentes": sorted(snapshot.agents), "arquivos_invalidos": snapshot.errors,
                "modelos": {nome: spec.modelo for nome, spec in sorted(snapshot.agents.items())},
                "erro_ajustes": settings.config_error, "em_andamento": len(running),
                "roteador_intencao": ("desligado" if deps.roteador is None
                                      else deps.roteador.erro or "ligado")}

    # --- conversas ----------------------------------------------------------------------------

    @app.post("/api/conversas", tags=["Conversas"], status_code=status.HTTP_201_CREATED,
              summary="Abre uma conversa: os pedidos seguintes nela lembram os anteriores")
    async def new_conversation(user: Caller) -> dict[str, Any]:
        conversation = conversations.create(user[0])
        return {"conversa_id": conversation.id, "criada_em": conversation.criada_em}

    @app.get("/api/conversas", tags=["Conversas"], summary="Conversas do usuário, da mais nova para a mais antiga")
    async def list_conversations(user: Caller) -> list[dict[str, Any]]:
        listed = []
        for conversation in conversations.of(user[0]):
            turns = deps.runs.of_conversation(conversation.id)
            listed.append({"conversa_id": conversation.id, "criada_em": conversation.criada_em, "turnos": len(turns),
                           # o primeiro pedido serve de título na lista
                           "titulo": turns[0].pedido if turns else None})
        return listed

    @app.get("/api/conversas/{conversa_id}", tags=["Conversas"], summary="Os turnos de uma conversa")
    async def get_conversation(conversa_id: str, user: Caller) -> dict[str, Any]:
        conversation = conversations.get(conversa_id, user[0])
        if conversation is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversa não encontrada.")
        return {"conversa_id": conversation.id, "criada_em": conversation.criada_em,
                "turnos": [turn_view(run) for run in deps.runs.of_conversation(conversation.id)]}

    @app.post("/api/conversas/{conversa_id}/mensagens", tags=["Conversas"],
              summary="Um turno da conversa: pergunta ou relatório, lembrando os turnos anteriores; o resto é "
                      "respondido como fora do escopo, com as opções")
    async def send_message(conversa_id: str, body: Mensagem, user: Caller) -> dict[str, Any]:
        # O orquestrador diz o que a mensagem é (pergunta, relatório ou fora do escopo), e o tipo da
        # execução passa a ser esse: a conferência final cobra o que cada tipo exige (a pergunta, uma
        # resposta gravada com fonte; o relatório, o relatório montado). Fora do escopo, nada roda e a
        # resposta é um texto fixo com as opções. Na primeira versão, sem essa
        # cobrança, o orquestrador respondia perguntas sozinho, na mensagem de estado, sem fonte.
        # A requisição chega antes de a conversa ir para o baggage: marcada aqui, ela também conta
        # na visão geral filtrada por conversa.
        trace.get_current_span().set_attribute(telemetria.CONVERSA, conversa_id)
        return await start(user, "auto", body.texto, wait=body.aguardar, conversa_id=conversa_id)

    # --- execuções ----------------------------------------------------------------------------

    @app.post("/api/execucoes", tags=["Execuções"], status_code=status.HTTP_202_ACCEPTED,
              summary="Pedido em texto livre: o orquestrador decide o que fazer")
    async def create_run(body: Pedido, user: Caller) -> dict[str, Any]:
        return await start(user, "livre", body.pedido, wait=False, conversa_id=body.conversa_id)

    @app.get("/api/execucoes", tags=["Execuções"], summary="Execuções do usuário, da mais nova para a mais antiga")
    async def list_runs(user: Caller) -> list[dict[str, Any]]:
        return [{"execucao_id": run.id, "tipo": run.tipo, "estado": run.estado, "pedido": run.pedido,
                 "conversa_id": run.conversa_id, "criada_em": run.criada_em, "terminada_em": run.terminada_em}
                for run in deps.runs.of(user[0])]

    @app.get("/api/execucoes/{execucao_id}", tags=["Execuções"],
             summary="Estado, resultado, passos (quem chamou o quê, e o raciocínio), tokens e custo de uma execução")
    async def get_run(execucao_id: str, user: Caller) -> dict[str, Any]:
        run = own_run(execucao_id, user[0])
        return view(run, deps.runs.trace(run.id).read())

    @app.get("/api/execucoes/{execucao_id}/mensagens", tags=["Execuções"],
             summary="As mensagens completas de cada agente: pedido, raciocínio, tools com argumentos e resultados")
    async def get_messages(execucao_id: str, user: Caller,
                           agente: Annotated[str | None, Query(description="Só este agente.")] = None
                           ) -> list[dict[str, Any]]:
        run = own_run(execucao_id, user[0])
        return deps.runs.messages(run.id, agente)

    @app.post("/api/perguntas", tags=["Atalhos"], summary="Uma pergunta sobre os documentos, respondida com fontes")
    async def ask(body: Pergunta, user: Caller) -> dict[str, Any]:
        return await start(user, "pergunta", body.pergunta, wait=body.aguardar, conversa_id=body.conversa_id)

    @app.post("/api/relatorios", tags=["Atalhos"], summary="O relatório consolidado completo dos documentos")
    async def report(user: Caller, body: Relatorio | None = None) -> dict[str, Any]:
        return await start(user, "relatorio", "Relatório consolidado completo.", wait=bool(body and body.aguardar),
                           conversa_id=body.conversa_id if body else None)

    @app.get("/api/agentes", tags=["Agentes"],
             summary="Agentes carregados dos arquivos e as tools que a política do servidor MCP dá a cada um")
    async def agents(user: Caller) -> dict[str, Any]:
        identity = Identity("consulta-agentes", user[0], user[1])
        snapshot = deps.registry.current()
        try:
            catalog = await deps.specialists(identity).catalog()
        except Exception as error:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                f"Nenhum serviço de especialista respondeu ({type(error).__name__}: {error}).") from error
        orchestrator = None
        if snapshot.orchestrator is not None:
            spec = snapshot.orchestrator
            try:
                async with deps.engine.connect(ORCHESTRATOR, identity) as session:
                    tools: list[str] | None = await session.tool_names()
            except Exception:  # noqa: BLE001
                tools = None
            orchestrator = {"nome": spec.nome, "descricao": spec.descricao, "modelo": spec.modelo,
                            "max_turnos": spec.max_turnos, "tools": tools,
                            "chama": [entry["nome"] for entry in catalog["agentes"]]}
        return {"orquestrador": orchestrator, "especialistas": catalog["agentes"],
                "arquivos_invalidos": {**snapshot.errors, **catalog["arquivos_invalidos"]},
                "fora_do_ar": catalog.get("fora_do_ar", [])}

    return app


def app_factory() -> FastAPI:
    settings = get_settings()
    return create_app(settings, roteador=Roteador(settings.intencoes_path, embedder_openai(settings.openai_api_key)))
