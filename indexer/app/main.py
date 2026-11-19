"""API do indexador. Suba com: uvicorn app.main:app --port 8000 e abra /docs."""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from app import telemetria
from app.api import auth, documents, jobs, search
from app.config import INSECURE_JWT_SECRET, get_settings
from app.indexacao.qdrant import VectorStore
from app.infra import banco as db
from app.infra.kafka import KafkaPublisher, wait_for_kafka
from app.schemas import HealthOut

log = logging.getLogger("api")

DESCRIPTION = """
Recebe demonstrações financeiras em PDF e as indexa no Qdrant com **vetor denso (semântico)**
e **vetor esparso (BM25)**, prontos para busca híbrida com RRF.

**Como testar por aqui**

1. `POST /auth/register` — crie um usuário.
2. Botão **Authorize** (canto superior direito) — informe usuário e senha.
3. `POST /api/documents` — envie um PDF; a resposta traz `doc_id` e `job_id`.
4. `GET /api/jobs/{job_id}` — acompanhe até `status = done`.
5. `GET /api/documents/{doc_id}/chunks` e `POST /api/search` — confira o que foi indexado.

O processamento é assíncrono: a API publica o job no Kafka e dois tipos de worker o executam
(parse; depois chunking, embeddings e gravação no Qdrant). O parse é feito pelo parser
configurado em `PARSER`: Docling, na máquina, ou Azure Document Intelligence. `GET /health`
mostra qual está ativo. Cada usuário só enxerga os próprios documentos.
"""

TAGS = [
    {"name": "Autenticação", "description": "Cadastro e login. O `user_id` vem do token, nunca de parâmetro."},
    {"name": "Documentos", "description": "Upload, consulta, correção de metadados e remoção."},
    {"name": "Jobs", "description": "Andamento do processamento e reenvio de jobs com falha."},
    {"name": "Busca (debug)", "description": "Conferência do índice com busca híbrida RRF."},
    {"name": "Saúde", "description": "Estado da API e das dependências."},
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("aiokafka").setLevel(logging.WARNING)
    settings = get_settings()
    if settings.jwt_secret == INSECURE_JWT_SECRET:
        log.warning("JWT_SECRET está com o valor padrão. Defina um segredo próprio no .env.")

    await db.init_db()
    workers: list[asyncio.Task] = []
    closers: list = []
    if settings.fila == "local":
        publisher, workers, closers = await _fila_local(settings)
    else:
        await wait_for_kafka(settings)
        publisher = KafkaPublisher(settings)
        await publisher.start()
    store = VectorStore.from_settings(settings)
    await store.ensure_collection()

    app.state.publisher = publisher
    app.state.store = store
    app.state.embedders = None
    try:
        yield
    finally:
        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        for close in closers:
            await close()
        await publisher.stop()
        await store.close()
        await db.dispose()


async def _fila_local(settings):
    """Sem Kafka: a fila em processo e as duas etapas como tarefas desta API (uma réplica)."""
    from app.infra.fila_local import LocalBus, LocalPublisher, run_local_consumer
    from app.schemas import IndexRequested, ParseRequested
    from app.workers import index_worker, parse_worker
    from app.workers.common import make_callbacks

    log.warning("fila: local. Parse e indexação rodam dentro da API, sem Kafka: só para uma réplica.")
    bus = LocalBus()
    publisher = LocalPublisher(bus)
    parse = await parse_worker.build(settings, publisher)
    index, close_index = await index_worker.build(settings, publisher)
    workers = []
    for etapa, topic, model, handler in (("parse", settings.kafka_topic_parse, ParseRequested, parse),
                                         ("index", settings.kafka_topic_index, IndexRequested, index)):
        on_retry, on_dead = make_callbacks(etapa, publisher, settings)
        workers.append(asyncio.create_task(run_local_consumer(
            bus=bus, topic=topic, model=model, handler=handler, publisher=publisher, on_retry=on_retry,
            on_dead=on_dead, settings=settings)))
    return publisher, workers, [close_index]


def create_app(with_lifespan: bool = True) -> FastAPI:
    app = FastAPI(
        title="Indexador de Demonstrações Financeiras",
        version="0.1.0",
        description=DESCRIPTION,
        openapi_tags=TAGS,
        lifespan=lifespan if with_lifespan else None,
    )
    telemetria.configurar("indexer-api")
    telemetria.instrumentar(app)

    @app.middleware("http")
    async def ajustes(request, call_next):
        get_settings().reload()  # um stat por pedido; o arquivo só é lido quando muda
        return await call_next(request)
    app.include_router(auth.router)
    app.include_router(documents.router)
    app.include_router(jobs.router)
    app.include_router(search.router)

    @app.get("/health", response_model=HealthOut, tags=["Saúde"],
             summary="API, Postgres, Kafka, Qdrant e o parser configurado")
    async def health() -> HealthOut:
        components: dict[str, str] = {"api": "ok"}
        try:
            async with db.session_scope() as session:
                await session.execute(text("SELECT 1"))
            components["postgres"] = "ok"
        except Exception as exc:  # noqa: BLE001
            components["postgres"] = f"erro: {type(exc).__name__}"
        publisher = getattr(app.state, "publisher", None)
        kafka_ok = bool(publisher) and await publisher.ping()
        components["kafka"] = "ok" if kafka_ok else "erro"
        store = getattr(app.state, "store", None)
        components["qdrant"] = "ok" if store and await store.ping() else "erro"
        settings = get_settings()
        if settings.parser_problem:
            components["parser"] = f"erro: {settings.parser_problem}"
        if settings.config_error:
            components["ajustes"] = f"erro: {settings.config_error}"
        healthy = all(value == "ok" for value in components.values())
        return HealthOut(status="ok" if healthy else "degraded", components=components,
                         parser=settings.parser)

    return app


app = create_app()
