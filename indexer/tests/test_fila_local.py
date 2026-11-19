"""A fila em processo (`fila: local`): o mesmo contrato do Kafka, com retry e DLQ."""
import asyncio

from app.infra.fila_local import LocalBus, LocalPublisher, run_local_consumer
from app.schemas import ParseRequested


async def _rodar(settings, handler, *mensagens: ParseRequested):
    bus = LocalBus()
    publisher = LocalPublisher(bus)
    mortos: list[str] = []

    async def on_retry(message, exc):
        return None

    async def on_dead(message, exc):
        mortos.append(message.job_id)

    for mensagem in mensagens:
        await publisher.publish(settings.kafka_topic_parse, mensagem.user_id, mensagem)
    task = asyncio.create_task(run_local_consumer(
        bus=bus, topic=settings.kafka_topic_parse, model=ParseRequested, handler=handler, publisher=publisher,
        on_retry=on_retry, on_dead=on_dead, settings=settings))
    await asyncio.wait_for(bus.queue(settings.kafka_topic_parse).join(), timeout=5)
    task.cancel()
    return bus, mortos


async def test_mensagem_chega_ao_handler(settings):
    vistas = []

    async def handler(message):
        vistas.append(message.doc_id)

    await _rodar(settings, handler, ParseRequested(job_id="j1", user_id="ana", doc_id="d1"),
                 ParseRequested(job_id="j2", user_id="ana", doc_id="d2"))
    assert vistas == ["d1", "d2"]


async def test_falha_tenta_de_novo_e_vai_para_a_dlq_no_fim(settings):
    tentativas = []

    async def handler(message):
        tentativas.append(message.attempt)
        raise RuntimeError("caiu")

    bus, mortos = await _rodar(settings, handler, ParseRequested(job_id="j1", user_id="ana", doc_id="d1"))
    assert tentativas == list(range(1, settings.max_attempts + 1))
    assert mortos == ["j1"] and bus.pending(settings.kafka_topic_dlq) == 1


async def test_falha_temporaria_se_resolve_na_retentativa(settings):
    tentativas = []

    async def handler(message):
        tentativas.append(message.attempt)
        if message.attempt == 1:
            raise RuntimeError("instável")

    bus, mortos = await _rodar(settings, handler, ParseRequested(job_id="j1", user_id="ana", doc_id="d1"))
    assert tentativas == [1, 2] and not mortos and bus.pending(settings.kafka_topic_dlq) == 0
