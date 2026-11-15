"""parse-worker e troca de parser: um cache por parser, e o index-worker lê o do parser certo."""
import pytest

from app.config import PIPELINE_VERSION
from app.indexacao.embeddings import FakeDenseEmbedder, FakeSparseEmbedder
from app.infra import arquivos as storage
from app.infra import banco as db
from app.infra import repo
from app.infra.kafka import PermanentError
from app.infra.modelos import Document, Job, User
from app.leitura.parsing import ParserError
from app.schemas import IndexRequested, ParsedDocument, ParsedItem, ParsedPage, ParseRequested
from app.workers.index_worker import handle_index
from app.workers.parse_worker import handle_parse
from tests.conftest import FakePublisher

USER = "11111111-1111-1111-1111-111111111111"
DOC = "abcdef0123456789"


class FakeParser:
    """Parser que não lê nada: devolve um documento de uma página dizendo quem o "leu"."""

    def __init__(self, name: str, page_price_usd: float = 0.0, error: Exception | None = None) -> None:
        self.name, self.page_price_usd, self.error = name, page_price_usd, error
        self.parse_calls = 0
        self.billed_pages = 1 if page_price_usd else 0  # páginas enviadas ao serviço pago

    def warmup(self) -> None:
        pass

    def parse(self, pdf_path, doc_id, file_name, out_dir, on_progress=None) -> ParsedDocument:
        self.parse_calls += 1
        if self.error:
            raise self.error
        assert out_dir == storage.parse_dir(USER, DOC, self.name) and out_dir.is_dir()
        return ParsedDocument(
            doc_id=doc_id, file_name=file_name, page_count=1, parser=self.name, pipeline_version=PIPELINE_VERSION,
            billed_pages=self.billed_pages,
            items=[ParsedItem(type="heading", text="1. Contexto operacional", page=1),
                   ParsedItem(type="text", text=f"Documento lido pelo parser {self.name}, em uma página.", page=1)],
            pages=[ParsedPage(page=1, native_text="1. Contexto operacional")])

    def read_figures(self, parsed, out_dir) -> int:
        return 0


@pytest.fixture
async def job_id(database, settings, pdf_bytes):
    storage.save_upload(USER, DOC, pdf_bytes)
    async with db.session_scope() as session:
        session.add(User(id=USER, username="ana", password_hash="x"))
        session.add(Document(user_id=USER, doc_id=DOC, file_name="exemplo.pdf", size_bytes=10,
                             page_count=1, overrides={}, metadata_source={}))
        job = Job(user_id=USER, doc_id=DOC, timings={}, usage={})
        session.add(job)
    return job.id


async def run_parse(job_id: str, parser: FakeParser, settings) -> tuple[FakePublisher, Job]:
    publisher = FakePublisher()
    await handle_parse(ParseRequested(job_id=job_id, user_id=USER, doc_id=DOC),
                       parser=parser, publisher=publisher, settings=settings)
    async with db.session_scope() as session:
        job = await repo.get_job(session, USER, job_id)
    return publisher, job


async def test_each_parser_keeps_its_own_cache(job_id, settings):
    docling, azure = FakeParser("docling"), FakeParser("azure", page_price_usd=0.01)

    publisher, job = await run_parse(job_id, docling, settings)
    assert publisher.on(settings.kafka_topic_index) == [
        IndexRequested(job_id=job_id, user_id=USER, doc_id=DOC, parser="docling")]
    assert job.timings["parser"] == "docling" and job.timings["parse_cached"] is False
    assert job.usage["parse_cost_usd"] == 0

    # Trocar para a Azure faz o parse dela, cobra a página e não mexe no que o Docling fez.
    publisher, job = await run_parse(job_id, azure, settings)
    assert publisher.on(settings.kafka_topic_index)[0].parser == "azure"
    assert job.timings["parser"] == "azure" and job.usage["parse_cost_usd"] == pytest.approx(0.01)
    for name in ("docling", "azure"):
        parsed = ParsedDocument.model_validate_json(
            storage.parsed_path(USER, DOC, name).read_text(encoding="utf-8"))
        assert parsed.parser == name

    # Voltar ao Docling (ou repetir a Azure) usa o cache: nada é lido nem cobrado de novo.
    _, job = await run_parse(job_id, docling, settings)
    _, job = await run_parse(job_id, azure, settings)
    assert (docling.parse_calls, azure.parse_calls) == (1, 1)
    assert job.timings["parse_cached"] is True and job.usage["parse_cost_usd"] == 0


async def test_new_parse_bills_only_what_was_sent_and_drops_old_figure_transcriptions(job_id, settings):
    azure = FakeParser("azure", page_price_usd=0.01)
    parse_dir = storage.parse_dir(USER, DOC, "azure")
    (parse_dir / "figures").mkdir(parents=True)
    (parse_dir / "figures" / "p0001_001.png").write_bytes(b"recorte antigo")
    transcriptions = storage.figures_cache_path(USER, DOC, "azure")
    transcriptions.write_text('{"figures/p0001_001.png": {}}', encoding="utf-8")
    response = parse_dir / "response.json.gz"
    response.write_bytes(b"resposta guardada do servico")

    # A conversão foi refeita a partir da resposta guardada: nenhuma página foi enviada.
    azure.billed_pages = 0
    _, job = await run_parse(job_id, azure, settings)
    assert job.timings["parse_cached"] is False and job.usage["parse_cost_usd"] == 0
    # Os recortes vão ser refeitos e podem mudar de nome: o que foi transcrito dos antigos sai.
    assert not transcriptions.exists() and not (parse_dir / "figures").exists()
    assert response.exists()  # a resposta do serviço fica


async def test_parser_configuration_error_is_not_retried(job_id, settings):
    broken = FakeParser("azure", error=ParserError("O Azure recusou a credencial (AZURE_DI_KEY)"))
    with pytest.raises(PermanentError, match="credencial"):
        await run_parse(job_id, broken, settings)


async def test_index_worker_reads_the_cache_of_the_parser_named_in_the_message(job_id, settings, store):
    await run_parse(job_id, FakeParser("azure"), settings)  # só existe o parse da Azure

    async def index(parser: str | None) -> FakePublisher:
        publisher = FakePublisher()
        await handle_index(IndexRequested(job_id=job_id, user_id=USER, doc_id=DOC, parser=parser),
                           store=store, dense=FakeDenseEmbedder(settings.embedding_dim),
                           sparse=FakeSparseEmbedder(), publisher=publisher, settings=settings)
        return publisher

    await index("azure")
    chunks = await store.get_chunks(USER, DOC, 0, 10)
    assert chunks and {c["parser"] for c in chunks} == {"azure"}
    assert "lido pelo parser azure" in chunks[0]["text"]

    # Sem dizer o parser, vale o configurado (docling). Não há parse dele: volta para a fila.
    publisher = await index(None)
    assert publisher.on(settings.kafka_topic_parse) == [ParseRequested(job_id=job_id, user_id=USER, doc_id=DOC)]


def test_parse_dir_only_accepts_a_parser_name(settings):
    assert storage.parse_dir(USER, DOC, "azure") == settings.storage_dir / USER / DOC / "parse" / "azure"
    for bad in ("../fora", "Azure", "", "a/b"):
        with pytest.raises(ValueError):
            storage.parse_dir(USER, DOC, bad)
    storage.parsed_path(USER, DOC, "docling").parent.mkdir(parents=True)
    storage.parsed_path(USER, DOC, "docling").write_text("{}", encoding="utf-8")
    storage.clear_derived_files(USER, DOC)
    assert not (settings.storage_dir / USER / DOC / "parse").exists()
