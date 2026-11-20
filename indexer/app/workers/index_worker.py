"""index-worker: consome indexer.index e faz figuras, metadados, chunks, embeddings e upsert.

É o worker leve (quase só chamadas de rede) e não importa Docling nem torch.
"""
import asyncio
import logging
from datetime import UTC, datetime

from app import telemetria
from app.config import Settings, get_settings
from app.indexacao.embeddings import (
    DenseEmbedder,
    SparseEmbedder,
    build_dense_embedder,
    build_sparse_embedder,
)
from app.indexacao.indexar import index_document, load_parsed
from app.indexacao.llm import LLMClient
from app.indexacao.qdrant import VectorStore
from app.infra import arquivos as storage
from app.infra import banco as db
from app.infra import repo
from app.infra.banco import session_scope
from app.infra.kafka import KafkaPublisher, Publisher, run_consumer, wait_for_kafka, zerar_metricas
from app.infra.modelos import utcnow
from app.leitura.parsing import variante_das_figuras, variante_do_parse
from app.schemas import DocumentEvent, IndexRequested, ParseRequested
from app.workers.common import make_callbacks, setup_logging

log = logging.getLogger("index-worker")
_CHUNKS = telemetria.medidor().create_counter("indexador.chunks", description="Chunks gravados no Qdrant")
_CUSTO = telemetria.medidor().create_counter("indexador.custo_usd", description="Custo estimado de LLM e embeddings")


async def handle_index(message: IndexRequested, *, store: VectorStore, dense: DenseEmbedder,
                       sparse: SparseEmbedder, publisher: Publisher, settings: Settings) -> None:
    async with session_scope() as session:
        document = await repo.get_document(session, message.user_id, message.doc_id)
        if document is None:
            log.info("Documento %s removido antes da indexação; mensagem descartada.", message.doc_id)
            return
        document.status = "processing"
        file_name, overrides = document.file_name, dict(document.overrides or {})
        dominio, collection_id = document.dominio, document.collection_id
        await repo.update_job(session, message.job_id, status="indexing", stage="index",
                              attempts=message.attempt, error=None, failed_stage=None)

    # O parse-worker diz qual parser produziu o parsed.json. Quando a API manda reindexar
    # (PATCH, retry), vale o parser configurado aqui.
    parser = message.parser or settings.parser
    parsed = load_parsed(storage.parsed_path(message.user_id, message.doc_id, parser,
                                             variante_do_parse(parser, dominio)))
    if parsed is None:
        # Sem parse em cache para este parser (ou de versão antiga do pipeline): devolve para
        # a fila de parse.
        log.info("parsed.json (%s) ausente para %s; reenfileirando o parse.", parser, message.doc_id)
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
        parsed=parsed, parser=parser, doc_dir=storage.parse_dir(message.user_id, message.doc_id, parser),
        figures_cache=storage.figures_cache_path(message.user_id, message.doc_id, parser,
                                                 variante_das_figuras(dominio)),
        metadata_cache=storage.metadata_cache_path(message.user_id, message.doc_id),
        store=store, dense=dense, sparse=sparse, llm=llm, settings=settings, on_stage=on_stage,
        dominio=dominio, collection_id=collection_id,
    )

    async with session_scope() as session:
        document = await repo.get_document(session, message.user_id, message.doc_id)
        if document is None:
            # Removido durante a indexação: desfaz o que acabou de ser gravado.
            await store.delete_document(message.user_id, message.doc_id)
            return
        meta = result.metadata
        document.metadados = dict(meta.valores)
        document.company, document.period, document.doc_type = meta.company, meta.period, meta.doc_type
        document.dominio_sugerido, document.aviso_dominio = result.suggested_domain, result.domain_warning
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
    _CHUNKS.add(result.chunk_count, {"parser": parser or "?"})
    _CUSTO.add(float(result.usage.get("cost_usd") or 0), {"parser": parser or "?"})
    telemetria.trace.get_current_span().set_attributes(  # o mesmo no span da etapa, para o Grafana
        {"indexador.chunks": result.chunk_count, "indexador.custo_usd": float(result.usage.get("cost_usd") or 0),
         "indexador.parser": parser or "?"})
    log.info("Documento %s indexado (%s): %d chunks, custo estimado US$ %.4f",
             message.doc_id, parser, result.chunk_count, result.usage["cost_usd"])


async def build(settings: Settings, publisher: Publisher):
    """O handler da indexação e o que fechar no fim. Usado pelo worker (Kafka) e pela API com a
    fila local."""
    store = VectorStore.from_settings(settings)
    await store.ensure_collection()
    dense = build_dense_embedder(settings)
    sparse = await asyncio.to_thread(build_sparse_embedder, settings)
    if not settings.llm_enabled:
        log.warning("LLM desligado (EMBEDDING_PROVIDER=fake ou sem OPENAI_API_KEY): "
                    "gráficos não serão transcritos e os metadados virão só de regex.")

    async def handler(message: IndexRequested) -> None:
        await handle_index(message, store=store, dense=dense, sparse=sparse,
                           publisher=publisher, settings=settings)

    return handler, store.close


def metricas() -> None:
    zerar_metricas("index")
    for nome in ("docling", "azure"):
        _CHUNKS.add(0, {"parser": nome})
        _CUSTO.add(0, {"parser": nome})


async def main() -> None:
    if telemetria.configurar("indexer-index-worker"):  # antes do log: as linhas ganham o trace_id
        metricas()
    setup_logging()
    settings = get_settings()
    await db.init_db()
    await wait_for_kafka(settings)
    publisher = KafkaPublisher(settings)
    await publisher.start()
    handler, close = await build(settings, publisher)
    on_retry, on_dead = make_callbacks("index", publisher, settings)
    try:
        await run_consumer(
            topic=settings.kafka_topic_index, group_id=settings.kafka_group_index, model=IndexRequested,
            handler=handler, publisher=publisher, on_retry=on_retry, on_dead=on_dead,
            max_poll_interval_ms=settings.index_max_poll_interval_ms, settings=settings,
        )
    finally:
        await publisher.stop()
        await close()
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
