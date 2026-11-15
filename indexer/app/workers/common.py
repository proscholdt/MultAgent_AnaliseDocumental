"""Partes comuns aos dois workers: logging, callbacks de retry/falha e publicação de eventos."""
import logging
from datetime import UTC, datetime

from app.config import Settings
from app.infra import repo
from app.infra.banco import session_scope
from app.infra.kafka import Publisher
from app.schemas import DocumentEvent, IndexRequested, ParseRequested

log = logging.getLogger(__name__)

Message = ParseRequested | IndexRequested


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("aiokafka", "httpx", "httpcore", "RapidOCR"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def make_callbacks(stage: str, publisher: Publisher, settings: Settings):
    """Devolve (on_retry, on_dead) para o laço de consumo da etapa `stage`."""

    async def on_retry(message: Message, exc: Exception) -> None:
        async with session_scope() as session:
            await repo.update_job(session, message.job_id, stage=f"{stage}: nova tentativa",
                                  attempts=message.attempt, error=f"{type(exc).__name__}: {exc}"[:4000])

    async def on_dead(message: Message, exc: Exception) -> None:
        error = f"{type(exc).__name__}: {exc}"
        async with session_scope() as session:
            await repo.mark_job_failed(session, message.job_id, stage, error)
        await publisher.publish(
            settings.kafka_topic_events, message.user_id,
            DocumentEvent(type="document.failed", job_id=message.job_id, user_id=message.user_id,
                          doc_id=message.doc_id, error=error[:1000], occurred_at=datetime.now(UTC)),
        )

    return on_retry, on_dead
