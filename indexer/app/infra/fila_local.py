"""A fila em processo: o mesmo contrato do Kafka (`Publisher` e o laço de consumo), com uma
`asyncio.Queue` por tópico, para rodar o indexador sem Kafka (`fila: local`).

Serve para desenvolvimento e demonstração, numa réplica só: a API e as duas etapas rodam no mesmo
processo, e o que está na fila se perde se ele cair. A retentativa, a DLQ e as métricas são as
mesmas do Kafka, porque cada mensagem passa por `handle_record`.
"""
import asyncio
import logging
from collections.abc import Awaitable, Callable

from pydantic import BaseModel

from app.config import Settings
from app.infra.kafka import handle_record

log = logging.getLogger(__name__)


class LocalBus:
    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[tuple[str, bytes]]] = {}

    def queue(self, topic: str) -> asyncio.Queue[tuple[str, bytes]]:
        return self._queues.setdefault(topic, asyncio.Queue())

    def pending(self, topic: str) -> int:
        return self.queue(topic).qsize()


class LocalPublisher:
    """O `Publisher` da fila local. Os eventos e a DLQ ficam na fila deles, como tópicos sem
    consumidor no Kafka (dá para conferir em testes com `bus.pending`)."""

    def __init__(self, bus: LocalBus) -> None:
        self.bus = bus

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def publish(self, topic: str, key: str, message: BaseModel) -> None:
        await self.publish_raw(topic, key, message.model_dump_json().encode())

    async def publish_raw(self, topic: str, key: str, value: bytes) -> None:
        await self.bus.queue(topic).put((key, value))

    async def ping(self) -> bool:
        return True


async def run_local_consumer[M: BaseModel](
    *,
    bus: LocalBus,
    topic: str,
    model: type[M],
    handler: Callable[[M], Awaitable[None]],
    publisher: LocalPublisher,
    on_retry: Callable[[M, Exception], Awaitable[None]],
    on_dead: Callable[[M, Exception], Awaitable[None]],
    settings: Settings,
) -> None:
    """Consome um tópico da fila local, uma mensagem por vez, até ser cancelado."""
    queue = bus.queue(topic)
    log.info("Consumindo %s na fila local", topic)
    while True:
        _, raw = await queue.get()
        try:
            await handle_record(raw=raw, topic=topic, model=model, handler=handler, publisher=publisher,
                                on_retry=on_retry, on_dead=on_dead, settings=settings)
        except Exception:
            log.exception("Falha inesperada na fila local (%s)", topic)
        finally:
            queue.task_done()
