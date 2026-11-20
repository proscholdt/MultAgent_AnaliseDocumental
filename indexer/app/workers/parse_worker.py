"""parse-worker: consome indexer.parse, lê o PDF com o parser escolhido e publica em indexer.index.

O parser vem de `parser` no config/ajustes.yaml (ver app/leitura/parsing.py): "docling", local e pesado, ou "azure",
que chama o Azure AI Document Intelligence. Escala separado do index-worker:
    docker compose up --scale parse-worker=2
"""
import asyncio
import logging
import shutil
import time
from pathlib import Path

from app import telemetria
from app.config import Settings, get_settings
from app.indexacao.indexar import load_parsed
from app.infra import arquivos as storage
from app.infra import banco as db
from app.infra import repo
from app.infra.banco import session_scope
from app.infra.kafka import (
    KafkaPublisher,
    PermanentError,
    Publisher,
    run_consumer,
    wait_for_kafka,
    zerar_metricas,
)
from app.leitura.parsing import (
    DocumentParser,
    ParserError,
    build_parser,
    unconfirmed_table_numbers,
    variante_do_parse,
)
from app.plugins.carregar import do_dominio
from app.schemas import IndexRequested, ParsedDocument, ParseRequested
from app.workers.common import make_callbacks, setup_logging

log = logging.getLogger("parse-worker")
_PAGINAS = telemetria.medidor().create_counter("indexador.paginas", description="Páginas parseadas")


async def _set_progress(job_id: str, done: int, total: int) -> None:
    async with session_scope() as session:
        await repo.update_job(session, job_id, progress_current=done, progress_total=total)


def _write_parsed(parsed: ParsedDocument, parsed_file: Path) -> None:
    temporary = parsed_file.with_suffix(".json.tmp")
    temporary.write_text(parsed.model_dump_json(), encoding="utf-8")
    temporary.replace(parsed_file)  # troca atômica: nunca fica um parsed.json pela metade


async def handle_parse(message: ParseRequested, *, parser: DocumentParser, publisher: Publisher,
                       settings: Settings) -> None:
    async with session_scope() as session:
        document = await repo.get_document(session, message.user_id, message.doc_id)
        if document is None:
            log.info("Documento %s removido antes do parse; mensagem descartada.", message.doc_id)
            return
        document.status = "processing"
        file_name, dominio = document.file_name, document.dominio
        await repo.update_job(session, message.job_id, status="parsing", stage="parse",
                              attempts=message.attempt, error=None, failed_stage=None)

    pdf_path = storage.upload_path(message.user_id, message.doc_id)
    if not pdf_path.exists():
        raise PermanentError(f"PDF não encontrado no armazenamento: {pdf_path}")
    # Cada parser tem a sua pasta: trocar de parser não apaga o que o outro já fez.
    out_dir = storage.parse_dir(message.user_id, message.doc_id, parser.name)
    out_dir.mkdir(parents=True, exist_ok=True)
    parsed_file = storage.parsed_path(message.user_id, message.doc_id, parser.name,
                                      variante_do_parse(parser.name, dominio))
    plugin = do_dominio(dominio)

    started = time.perf_counter()
    parsed = load_parsed(parsed_file)
    cached = parsed is not None
    try:
        if parsed is None:
            # Parse novo: os recortes são refeitos e podem mudar de nome, então as transcrições
            # guardadas dos recortes antigos deixam de valer.
            shutil.rmtree(out_dir / "figures", ignore_errors=True)
            for cache in out_dir.glob("figures*.json"):  # as de todos os domínios: os recortes mudaram
                cache.unlink(missing_ok=True)
            loop = asyncio.get_running_loop()

            def on_progress(done: int, total: int) -> None:
                # Chamado na thread do parser; agenda a gravação no laço principal.
                asyncio.run_coroutine_threadsafe(_set_progress(message.job_id, done, total), loop)

            # O parse é síncrono (e, no Docling, pesado): roda em thread para o consumidor
            # continuar mandando heartbeat ao Kafka.
            parsed = await asyncio.to_thread(parser.parse, pdf_path, message.doc_id, file_name,
                                             out_dir, on_progress, plugin)
        # Texto de dentro dos gráficos, com posição: segunda fonte para conferir os números.
        # Também completa um parsed.json em cache feito antes de essa leitura existir.
        figures_read = await asyncio.to_thread(parser.read_figures, parsed, out_dir)
    except ParserError as exc:
        raise PermanentError(str(exc)) from exc
    if not cached or figures_read:
        _write_parsed(parsed, parsed_file)
    elapsed = round(time.perf_counter() - started, 2)
    unconfirmed = unconfirmed_table_numbers(parsed)
    if unconfirmed:
        log.warning("%s: %d número(s) de tabela não existem na camada de texto do PDF.",
                    message.doc_id, unconfirmed)
    # Só é cobrado o que foi de fato enviado ao serviço neste job: parse em cache não é, e
    # conversão refeita a partir da resposta guardada também não.
    billed_pages = 0 if cached else parsed.billed_pages

    async with session_scope() as session:
        document = await repo.get_document(session, message.user_id, message.doc_id)
        if document is not None:
            document.page_count = parsed.page_count
        await repo.update_job(
            session, message.job_id, stage="aguardando indexação",
            progress_current=parsed.page_count, progress_total=parsed.page_count,
            timings={"parser": parser.name, "parse_s": elapsed, "parse_cached": cached,
                     "figures_ocr": figures_read, "table_numbers_unconfirmed": unconfirmed},
            usage={"parse_cost_usd": round(billed_pages * parser.page_price_usd, 6)},
        )

    await publisher.publish(
        settings.kafka_topic_index, message.user_id,
        IndexRequested(job_id=message.job_id, user_id=message.user_id, doc_id=message.doc_id,
                       parser=parser.name),
    )
    _PAGINAS.add(parsed.page_count, {"parser": parser.name, "cache": str(cached).lower()})
    telemetria.trace.get_current_span().set_attributes(  # o mesmo no span da etapa, para o Grafana
        {"indexador.paginas": parsed.page_count, "indexador.parser": parser.name, "indexador.cache": bool(cached)})
    log.info("Parse de %s (%s) concluído em %.1fs (cache=%s)", message.doc_id, parser.name, elapsed, cached)


class ParserSlot:
    """O parser do ajustes.yaml de agora. O Docling lê o modo das tabelas e as threads ao carregar;
    se um deles (ou o próprio parser) mudar, o próximo PDF monta e aquece um parser novo."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._key: tuple | None = None
        self._parser: DocumentParser | None = None

    async def current(self) -> DocumentParser:
        key = (self._settings.parser, self._settings.table_mode, self._settings.parse_threads)
        if self._parser is None or key != self._key:
            parser = build_parser(self._settings)  # ParserError: configuração incompleta
            log.info("Parser: %s. Carregando...", parser.name)
            await asyncio.to_thread(parser.warmup)
            log.info("Parser %s pronto.", parser.name)
            self._parser, self._key = parser, key
        return self._parser


async def build(settings: Settings, publisher: Publisher):
    """O handler do parse. Falta configuração do parser: ParserError aqui, antes de abrir a fila."""
    parsers = ParserSlot(settings)
    await parsers.current()

    async def handler(message: ParseRequested) -> None:
        await handle_parse(message, parser=await parsers.current(), publisher=publisher, settings=settings)

    return handler


def metricas() -> None:
    zerar_metricas("parse")
    for nome in ("docling", "azure"):
        for cache in ("true", "false"):
            _PAGINAS.add(0, {"parser": nome, "cache": cache})


async def main() -> None:
    if telemetria.configurar("indexer-parse-worker"):  # antes do log: as linhas ganham o trace_id
        metricas()
    setup_logging()
    settings = get_settings()
    # Antes de abrir qualquer conexão: se faltar configuração do parser, o worker sai aqui com
    # uma linha dizendo o que falta, e não com um job preso na fila.
    publisher = KafkaPublisher(settings)
    try:
        handler = await build(settings, publisher)
    except ParserError as exc:
        log.error("parse-worker não pode subir: %s", exc)
        raise SystemExit(1) from None

    await db.init_db()
    await wait_for_kafka(settings)
    await publisher.start()
    on_retry, on_dead = make_callbacks("parse", publisher, settings)
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
