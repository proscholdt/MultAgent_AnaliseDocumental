"""parse-worker: consome indexer.parse, roda o Docling e publica em indexer.index.

É o worker pesado (CPU/GPU). Escala separado do index-worker:
    docker compose up --scale parse-worker=2
"""
import asyncio
import logging
import time
from pathlib import Path

from app import db, repo, storage
from app.config import Settings, get_settings
from app.db import session_scope
from app.messaging import KafkaPublisher, PermanentError, Publisher, run_consumer, wait_for_kafka
from app.pipeline.figure_ocr import FigureOcr, read_figures
from app.pipeline.indexing import load_parsed
from app.pipeline.parser import PdfParser
from app.schemas import IndexRequested, ParsedDocument, ParseRequested
from app.workers.common import make_callbacks, setup_logging

log = logging.getLogger("parse-worker")


async def _set_progress(job_id: str, done: int, total: int) -> None:
    async with session_scope() as session:
        await repo.update_job(session, job_id, progress_current=done, progress_total=total)


def _write_parsed(parsed: ParsedDocument, parsed_file: Path) -> None:
    temporary = parsed_file.with_suffix(".json.tmp")
    temporary.write_text(parsed.model_dump_json(), encoding="utf-8")
    temporary.replace(parsed_file)  # troca atômica: nunca fica um parsed.json pela metade


async def handle_parse(message: ParseRequested, *, parser: PdfParser, ocr: FigureOcr,
                       publisher: Publisher, settings: Settings) -> None:
    async with session_scope() as session:
        document = await repo.get_document(session, message.user_id, message.doc_id)
        if document is None:
            log.info("Documento %s removido antes do parse; mensagem descartada.", message.doc_id)
            return
        document.status = "processing"
        file_name = document.file_name
        await repo.update_job(session, message.job_id, status="parsing", stage="parse",
                              attempts=message.attempt, error=None, failed_stage=None)

    pdf_path = storage.upload_path(message.user_id, message.doc_id)
    if not pdf_path.exists():
        raise PermanentError(f"PDF não encontrado no armazenamento: {pdf_path}")
    out_dir = storage.doc_dir(message.user_id, message.doc_id)
    parsed_file = storage.parsed_path(message.user_id, message.doc_id)

    started = time.perf_counter()
    parsed = load_parsed(parsed_file)
    cached = parsed is not None
    if parsed is None:
        loop = asyncio.get_running_loop()

        def on_progress(done: int, total: int) -> None:
            # Chamado na thread do Docling; agenda a gravação no laço principal.
            asyncio.run_coroutine_threadsafe(_set_progress(message.job_id, done, total), loop)

        # O Docling é síncrono e pesado: roda em thread para o consumidor continuar
        # mandando heartbeat ao Kafka.
        parsed = await asyncio.to_thread(parser.parse, pdf_path, message.doc_id, file_name,
                                         out_dir, on_progress)
    # OCR dos recortes de gráfico (segunda fonte para conferir os números). Também completa
    # um parsed.json em cache feito antes de o OCR existir, sem refazer o parse.
    figures_read = await asyncio.to_thread(read_figures, parsed, out_dir, ocr,
                                           settings.figure_min_area_ratio)
    if not cached or figures_read:
        _write_parsed(parsed, parsed_file)
    elapsed = round(time.perf_counter() - started, 2)

    async with session_scope() as session:
        document = await repo.get_document(session, message.user_id, message.doc_id)
        if document is not None:
            document.page_count = parsed.page_count
        await repo.update_job(
            session, message.job_id, stage="aguardando indexação",
            progress_current=parsed.page_count, progress_total=parsed.page_count,
            timings={"parse_s": elapsed, "parse_cached": cached, "figures_ocr": figures_read},
        )

    await publisher.publish(
        settings.kafka_topic_index, message.user_id,
        IndexRequested(job_id=message.job_id, user_id=message.user_id, doc_id=message.doc_id),
    )
    log.info("Parse de %s concluído em %.1fs (cache=%s)", message.doc_id, elapsed, cached)


async def main() -> None:
    setup_logging()
    settings = get_settings()
    await db.init_db()
    await wait_for_kafka(settings)
    publisher = KafkaPublisher(settings)
    await publisher.start()

    parser = PdfParser(settings)
    ocr = FigureOcr()
    log.info("Carregando modelos do Docling e do OCR...")
    await asyncio.to_thread(parser.warmup)
    await asyncio.to_thread(ocr.warmup)
    log.info("Modelos carregados.")

    on_retry, on_dead = make_callbacks("parse", publisher, settings)

    async def handler(message: ParseRequested) -> None:
        await handle_parse(message, parser=parser, ocr=ocr, publisher=publisher, settings=settings)

    try:
        await run_consumer(
            topic=settings.kafka_topic_parse, group_id=settings.kafka_group_parse, model=ParseRequested,
            handler=handler, publisher=publisher, on_retry=on_retry, on_dead=on_dead,
            max_poll_interval_ms=settings.parse_max_poll_interval_ms, settings=settings,
        )
    finally:
        await publisher.stop()
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
