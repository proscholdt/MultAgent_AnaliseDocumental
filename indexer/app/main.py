"""API do indexador. Suba com: uvicorn app.main:app --port 8000 e abra /docs."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from app import db
from app.api import auth, documents, jobs, search
from app.config import INSECURE_JWT_SECRET, get_settings
from app.messaging import KafkaPublisher, wait_for_kafka
from app.pipeline.vector_store import VectorStore
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
(parse com Docling; depois chunking, embeddings e gravação no Qdrant). Cada usuário só enxerga
os próprios documentos.
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
        await publisher.stop()
        await store.close()
        await db.dispose()


def create_app(with_lifespan: bool = True) -> FastAPI:
    app = FastAPI(
        title="Indexador de Demonstrações Financeiras",
        version="0.1.0",
        description=DESCRIPTION,
        openapi_tags=TAGS,
        lifespan=lifespan if with_lifespan else None,
    )
    app.include_router(auth.router)
    app.include_router(documents.router)
    app.include_router(jobs.router)
    app.include_router(search.router)

    @app.get("/health", response_model=HealthOut, tags=["Saúde"], summary="API, Postgres, Kafka e Qdrant")
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
        healthy = all(value == "ok" for value in components.values())
        return HealthOut(status="ok" if healthy else "degraded", components=components)

    return app


app = create_app()
