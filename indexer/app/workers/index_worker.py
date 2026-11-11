"""index-worker: consome indexer.index e faz figuras, metadados, chunks, embeddings e upsert.

É o worker leve (quase só chamadas de rede) e não importa Docling nem torch.
"""
import asyncio
import logging
from datetime import UTC, datetime

from app import db, repo, storage
from app.config import Settings, get_settings
from app.db import session_scope
from app.messaging import KafkaPublisher, Publisher, run_consumer, wait_for_kafka
from app.models import utcnow
from app.pipeline.embedder import (
    DenseEmbedder,
    SparseEmbedder,
    build_dense_embedder,
    build_sparse_embedder,
)
from app.pipeline.indexing import index_document, load_parsed
from app.pipeline.llm import LLMClient
from app.pipeline.vector_store import VectorStore
from app.schemas import DocumentEvent, IndexRequested, ParseRequested
from app.workers.common import make_callbacks, setup_logging

log = logging.getLogger("index-worker")


async def handle_index(message: IndexRequested, *, store: VectorStore, dense: DenseEmbedder,
                       sparse: SparseEmbedder, publisher: Publisher, settings: Settings) -> None:
    async with session_scope() as session:
        document = await repo.get_document(session, message.user_id, message.doc_id)
        if document is None:
            log.info("Documento %s removido antes da indexação; mensagem descartada.", message.doc_id)
            return
        document.status = "processing"
        file_name, overrides = document.file_name, dict(document.overrides or {})
        await repo.update_job(session, message.job_id, status="indexing", stage="index",
                              attempts=message.attempt, error=None, failed_stage=None)

    parsed = load_parsed(storage.parsed_path(message.user_id, message.doc_id))
    if parsed is None:
        # Sem parse em cache (ou de versão antiga do pipeline): devolve para a fila de parse.
        log.info("parsed.json ausente para %s; reenfileirando o parse.", message.doc_id)
        await publisher.publish(
            settings.kafka_topic_parse, message.user_id,
            ParseRequested(job_id=message.job_id, user_id=message.user_id, doc_id=message.doc_id),
        )
        return

    async def on_stage(name: str) -> None:
        async with session_scope() as session:
            await repo.update_job(session, message.job_id, stage=name)

    # Um cliente por documento: os contadores de tokens dele viram o custo do job.
    llm = LLMClient(settings) if settings.llm_enabled else None
    result = await index_document(
        user_id=message.user_id, doc_id=message.doc_id, file_name=file_name, overrides=overrides,
        parsed=parsed, doc_dir=storage.doc_dir(message.user_id, message.doc_id),
        figures_cache=storage.figures_cache_path(message.user_id, message.doc_id),
        metadata_cache=storage.metadata_cache_path(message.user_id, message.doc_id),
        store=store, dense=dense, sparse=sparse, llm=llm, settings=settings, on_stage=on_stage,
    )

    async with session_scope() as session:
        document = await repo.get_document(session, message.user_id, message.doc_id)
        if document is None:
            # Removido durante a indexação: desfaz o que acabou de ser gravado.
            await store.delete_document(message.user_id, message.doc_id)
            return
        meta = result.metadata
        document.company, document.period, document.doc_type = meta.company, meta.period, meta.doc_type
        document.default_unit = meta.default_unit
        document.metadata_source = meta.source
        document.chunk_count = result.chunk_count
        document.status = "indexed"
        document.indexed_at = utcnow()
        await repo.update_job(session, message.job_id, status="done", stage=None, finished_at=utcnow(),
                              timings=result.timings, usage=result.usage)

    await publisher.publish(
        settings.kafka_topic_events, message.user_id,
        DocumentEvent(type="document.indexed", job_id=message.job_id, user_id=message.user_id,
                      doc_id=message.doc_id, chunk_count=result.chunk_count, occurred_at=datetime.now(UTC)),
    )
    log.info("Documento %s indexado: %d chunks, custo estimado US$ %.4f",
             message.doc_id, result.chunk_count, result.usage["cost_usd"])


async def main() -> None:
    setup_logging()
    settings = get_settings()
    await db.init_db()
    await wait_for_kafka(settings)
    publisher = KafkaPublisher(settings)
    await publisher.start()

    store = VectorStore.from_settings(settings)
    await store.ensure_collection()
    dense = build_dense_embedder(settings)
    sparse = await asyncio.to_thread(build_sparse_embedder, settings)
    if not settings.llm_enabled:
        log.warning("LLM desligado (EMBEDDING_PROVIDER=fake ou sem OPENAI_API_KEY): "
                    "gráficos não serão transcritos e os metadados virão só de regex.")

    on_retry, on_dead = make_callbacks("index", publisher, settings)

    async def handler(message: IndexRequested) -> None:
        await handle_index(message, store=store, dense=dense, sparse=sparse,
                           publisher=publisher, settings=settings)

    try:
        await run_consumer(
            topic=settings.kafka_topic_index, group_id=settings.kafka_group_index, model=IndexRequested,
            handler=handler, publisher=publisher, on_retry=on_retry, on_dead=on_dead,
            max_poll_interval_ms=settings.index_max_poll_interval_ms, settings=settings,
        )
    finally:
        await publisher.stop()
        await store.close()
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
