"""Política de retry e DLQ do consumidor Kafka."""
from app.infra.kafka import PermanentError, handle_record
from app.schemas import IndexRequested
from tests.conftest import FakePublisher

TOPIC = "indexer.index"


def raw(attempt: int = 1) -> bytes:
    return IndexRequested(job_id="j1", user_id="u1", doc_id="d1", attempt=attempt).model_dump_json().encode()


class Recorder:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.handled, self.retried, self.dead = [], [], []

    async def handler(self, message) -> None:
        self.handled.append(message)
        if self.error:
            raise self.error

    async def on_retry(self, message, exc) -> None:
        self.retried.append((message.attempt, str(exc)))

    async def on_dead(self, message, exc) -> None:
        self.dead.append((message.attempt, str(exc)))


async def process(recorder: Recorder, publisher: FakePublisher, settings, payload: bytes) -> None:
    await handle_record(raw=payload, topic=TOPIC, model=IndexRequested, handler=recorder.handler,
                        publisher=publisher, on_retry=recorder.on_retry, on_dead=recorder.on_dead,
                        settings=settings)


async def test_success_publishes_nothing(settings):
    recorder, publisher = Recorder(), FakePublisher()
    await process(recorder, publisher, settings, raw())
    assert len(recorder.handled) == 1 and publisher.messages == []


async def test_transient_failure_is_requeued_with_next_attempt(settings):
    recorder, publisher = Recorder(TimeoutError("OpenAI lenta")), FakePublisher()
    await process(recorder, publisher, settings, raw(attempt=1))
    assert recorder.retried == [(1, "OpenAI lenta")] and recorder.dead == []
    topic, key, message = publisher.messages[0]
    assert (topic, key, message.attempt) == (TOPIC, "u1", 2)


async def test_last_attempt_goes_to_dlq_and_marks_dead(settings):
    recorder, publisher = Recorder(TimeoutError("OpenAI lenta")), FakePublisher()
    await process(recorder, publisher, settings, raw(attempt=settings.max_attempts))
    assert recorder.retried == [] and recorder.dead == [(settings.max_attempts, "OpenAI lenta")]
    (topic, key, dead_letter), = publisher.messages
    assert topic == settings.kafka_topic_dlq and key == "u1"
    assert dead_letter.source_topic == TOPIC and "OpenAI lenta" in dead_letter.error
    assert '"job_id":"j1"' in dead_letter.message


async def test_permanent_error_skips_retries(settings):
    recorder, publisher = Recorder(PermanentError("PDF sumiu")), FakePublisher()
    await process(recorder, publisher, settings, raw(attempt=1))
    assert recorder.retried == [] and recorder.dead == [(1, "PDF sumiu")]
    assert [topic for topic, _, _ in publisher.messages] == [settings.kafka_topic_dlq]


async def test_invalid_message_goes_to_dlq_without_calling_handler(settings):
    recorder, publisher = Recorder(), FakePublisher()
    await process(recorder, publisher, settings, b"isto nao e json")
    assert recorder.handled == [] and recorder.dead == []
    assert [topic for topic, _, _ in publisher.messages] == [settings.kafka_topic_dlq]
