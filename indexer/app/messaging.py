"""Kafka: tópicos, produtor e o laço de consumo com retry e DLQ.

Semântica de entrega: ao menos uma vez. O offset só é confirmado depois que o
handler terminou (com sucesso, reenfileirado ou enviado à DLQ). Como as etapas
são idempotentes, uma reentrega depois de queda do worker não duplica nada.
"""
import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.errors import CommitFailedError, KafkaError, TopicAlreadyExistsError
from pydantic import BaseModel, ValidationError

from app import telemetria
from app.config import Settings, get_settings

log = logging.getLogger(__name__)

_MENSAGENS = telemetria.medidor().create_counter(
    "indexador.mensagens", description="Mensagens processadas por etapa e resultado")
_ETAPA_S = telemetria.medidor().create_histogram(
    "indexador.etapa.duracao", unit="s", description="Duração de cada etapa (parse, index)")


class PermanentError(Exception):
    """Erro que não adianta tentar de novo (ex.: PDF sumiu do disco). Vai direto para a DLQ."""


class Publisher(Protocol):
    async def publish(self, topic: str, key: str, message: BaseModel) -> None: ...

    async def ping(self) -> bool: ...


class KafkaPublisher:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._producer: AIOKafkaProducer | None = None

    async def start(self) -> None:
        # acks=all + idempotência: a mensagem só é dada como publicada quando está
        # gravada, e uma retentativa do produtor não gera duplicata.
        self._producer = AIOKafkaProducer(
            bootstrap_servers=self._settings.kafka_bootstrap_servers,
            acks="all",
            enable_idempotence=True,
        )
        await self._producer.start()

    async def stop(self) -> None:
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None

    async def publish(self, topic: str, key: str, message: BaseModel) -> None:
        if "rastro" in type(message).model_fields and not message.rastro:
            message = message.model_copy(update={"rastro": telemetria.portador()})
        await self.publish_raw(topic, key, message.model_dump_json().encode())

    async def publish_raw(self, topic: str, key: str, value: bytes) -> None:
        if self._producer is None:
            raise RuntimeError("Produtor Kafka não iniciado.")
        # A chave é o user_id: mantém a ordem dos documentos de um usuário e espalha
        # usuários diferentes pelas partições, para um não travar a fila do outro.
        await self._producer.send_and_wait(topic, value=value, key=key.encode())

    async def ping(self) -> bool:
        if self._producer is None:
            return False
        try:
            await self._producer.client.fetch_all_metadata()
            return True
        except KafkaError:
            return False


def all_topics(settings: Settings) -> list[str]:
    return [settings.kafka_topic_parse, settings.kafka_topic_index,
            settings.kafka_topic_events, settings.kafka_topic_dlq]


async def ensure_topics(settings: Settings | None = None) -> None:
    """Cria os tópicos explicitamente (auto-create fica desligado no broker)."""
    settings = settings or get_settings()
    admin = AIOKafkaAdminClient(bootstrap_servers=settings.kafka_bootstrap_servers)
    await admin.start()
    try:
        existing = set(await admin.list_topics())
        missing = [NewTopic(name=t, num_partitions=settings.kafka_partitions, replication_factor=1)
                   for t in all_topics(settings) if t not in existing]
        if missing:
            try:
                await admin.create_topics(missing)
                log.info("Tópicos criados: %s", [t.name for t in missing])
            except TopicAlreadyExistsError:
                pass  # outro processo criou no mesmo instante
    finally:
        await admin.close()


async def wait_for_kafka(settings: Settings | None = None, attempts: int = 30, delay: float = 2.0) -> None:
    """Espera o broker subir e garante os tópicos. Usado no startup da API e dos workers."""
    last: Exception | None = None
    for i in range(1, attempts + 1):
        try:
            await ensure_topics(settings)
            return
        except Exception as exc:  # noqa: BLE001 - o broker ainda pode estar subindo
            last = exc
            log.warning("Kafka indisponível (tentativa %d/%d): %s", i, attempts, exc)
            await asyncio.sleep(delay)
    raise RuntimeError(f"Kafka não respondeu após {attempts} tentativas: {last}")


async def run_consumer[M: BaseModel](
    *,
    topic: str,
    group_id: str,
    model: type[M],
    handler: Callable[[M], Awaitable[None]],
    publisher: KafkaPublisher,
    on_retry: Callable[[M, Exception], Awaitable[None]],
    on_dead: Callable[[M, Exception], Awaitable[None]],
    max_poll_interval_ms: int,
    settings: Settings | None = None,
) -> None:
    """Consome um tópico, uma mensagem por vez, até ser cancelado."""
    settings = settings or get_settings()
    consumer = AIOKafkaConsumer(
        topic,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=group_id,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        max_poll_records=1,
        max_poll_interval_ms=max_poll_interval_ms,
    )
    await consumer.start()
    log.info("Consumindo %s no grupo %s", topic, group_id)
    try:
        async for record in consumer:
            await handle_record(
                raw=record.value, topic=topic, model=model, handler=handler,
                publisher=publisher, on_retry=on_retry, on_dead=on_dead, settings=settings,
            )
            try:
                await consumer.commit()
            except CommitFailedError:
                # Houve rebalanceamento durante o processamento: a mensagem será
                # reentregue a outro consumidor. As etapas são idempotentes.
                log.warning("Commit recusado após rebalanceamento; a mensagem será reentregue.")
    finally:
        await consumer.stop()


async def handle_record[M: BaseModel](
    *,
    raw: bytes,
    topic: str,
    model: type[M],
    handler: Callable[[M], Awaitable[None]],
    publisher: Publisher,
    on_retry: Callable[[M, Exception], Awaitable[None]],
    on_dead: Callable[[M, Exception], Awaitable[None]],
    settings: Settings,
) -> None:
    """Processa uma mensagem aplicando a política de retry. Separado do laço para ser testável."""
    try:
        message = model.model_validate_json(raw)
    except ValidationError as exc:
        log.error("Mensagem inválida em %s, enviada à DLQ: %s", topic, exc)
        await _to_dlq(publisher, settings, topic, raw, key="invalid", error=str(exc))
        return

    key = getattr(message, "user_id", "unknown")
    attempt = getattr(message, "attempt", 1)
    etapa = topic.rsplit(".", 1)[-1]
    # O trabalho continua o trace de quem publicou a mensagem (o upload, ou a etapa anterior).
    with telemetria.tracer().start_as_current_span(
            f"{etapa} {getattr(message, 'doc_id', '')}", context=telemetria.contexto(getattr(message, "rastro", None)),
            attributes={"messaging.destination": topic, "job.id": getattr(message, "job_id", ""),
                        "doc.id": getattr(message, "doc_id", ""), "tentativa": attempt}) as span:
        inicio = asyncio.get_running_loop().time()
        resultado = "ok"
        try:
            await _handle(message, raw=raw, topic=topic, key=key, attempt=attempt, handler=handler,
                          publisher=publisher, on_retry=on_retry, on_dead=on_dead, settings=settings)
        except _Falhou as falha:
            resultado = falha.resultado
            span.set_status(telemetria.trace.Status(telemetria.trace.StatusCode.ERROR, falha.resultado))
        finally:
            _MENSAGENS.add(1, {"etapa": etapa, "resultado": resultado})
            _ETAPA_S.record(asyncio.get_running_loop().time() - inicio, {"etapa": etapa, "resultado": resultado})


def zerar_metricas(etapa: str) -> None:
    """Cria as séries dos contadores em zero, na subida. Um contador do OpenTelemetry só passa a
    existir no primeiro incremento, e o Prometheus nunca vê o zero de antes: increase() perde o
    primeiro evento. Com as séries conhecidas já em zero, a conta fecha."""
    for resultado in ("ok", "retry", "dlq"):
        _MENSAGENS.add(0, {"etapa": etapa, "resultado": resultado})


class _Falhou(Exception):
    def __init__(self, resultado: str) -> None:
        self.resultado = resultado


async def _handle[M: BaseModel](message: M, *, raw: bytes, topic: str, key: str, attempt: int,
                                handler: Callable[[M], Awaitable[None]], publisher: Publisher,
                                on_retry: Callable[[M, Exception], Awaitable[None]],
                                on_dead: Callable[[M, Exception], Awaitable[None]], settings: Settings) -> None:
    try:
        await handler(message)
    except PermanentError as exc:
        log.error("Erro permanente em %s: %s", topic, exc)
        await _to_dlq(publisher, settings, topic, raw, key=key, error=str(exc))
        await on_dead(message, exc)
        raise _Falhou("dlq") from exc
    except Exception as exc:
        if attempt < settings.max_attempts:
            log.warning("Falha em %s (tentativa %d/%d): %s", topic, attempt, settings.max_attempts, exc)
            await on_retry(message, exc)
            await asyncio.sleep(settings.retry_backoff_seconds * attempt)
            await publisher.publish(topic, key, message.model_copy(update={"attempt": attempt + 1}))
            raise _Falhou("retry") from exc
        else:
            log.exception("Falha definitiva em %s após %d tentativas", topic, attempt)
            await _to_dlq(publisher, settings, topic, raw, key=key, error=repr(exc))
            await on_dead(message, exc)
            raise _Falhou("dlq") from exc


class _DeadLetter(BaseModel):
    source_topic: str
    error: str
    message: str


async def _to_dlq(publisher: Publisher, settings: Settings, topic: str, raw: bytes, *, key: str,
                  error: str) -> None:
    try:
        text = raw.decode("utf-8", errors="replace")
        json.loads(text)
    except ValueError:
        text = repr(raw)
    await publisher.publish(settings.kafka_topic_dlq, key,
                            _DeadLetter(source_topic=topic, error=error[:2000], message=text))
