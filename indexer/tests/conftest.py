"""Fixtures: tudo roda sem rede nem contêiner (SQLite e Qdrant em memória, Kafka e embedders falsos)."""
import io
from types import SimpleNamespace

import httpx
import pytest
from pydantic import BaseModel
from pypdf import PdfWriter
from qdrant_client import AsyncQdrantClient

from app import db
from app.config import PIPELINE_VERSION, get_settings
from app.main import create_app
from app.pipeline.embedder import FakeDenseEmbedder, FakeSparseEmbedder
from app.pipeline.vector_store import VectorStore
from app.schemas import ParsedDocument, ParsedItem, ParsedPage


class FakePublisher:
    """Guarda o que seria publicado no Kafka."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str, BaseModel]] = []
        self.fail = False

    async def publish(self, topic: str, key: str, message: BaseModel) -> None:
        if self.fail:
            raise ConnectionError("broker fora do ar")
        self.messages.append((topic, key, message))

    async def ping(self) -> bool:
        return not self.fail

    def on(self, topic: str) -> list[BaseModel]:
        return [message for t, _, message in self.messages if t == topic]


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("EMBEDDING_DIM", "64")
    monkeypatch.setenv("JWT_SECRET", "segredo-de-teste-com-mais-de-32-bytes!")
    monkeypatch.setenv("RETRY_BACKOFF_SECONDS", "0")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    # Os testes não dependem do .env nem do config/ajustes.yaml de quem os roda: parser padrão e
    # nenhuma credencial real.
    monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "ajustes-de-teste.yaml"))
    monkeypatch.setenv("PARSER", "docling")
    monkeypatch.setenv("AZURE_DI_ENDPOINT", "")
    monkeypatch.setenv("AZURE_DI_KEY", "")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture
async def store(settings):
    vector_store = VectorStore(AsyncQdrantClient(location=":memory:"), settings)
    await vector_store.ensure_collection()
    yield vector_store
    await vector_store.close()


@pytest.fixture
async def database(settings):
    db.configure(settings.database_url)
    await db.init_db()
    yield
    await db.dispose()


@pytest.fixture
async def ctx(settings, database, store):
    """API montada com dependências falsas e um cliente HTTP em memória."""
    app = create_app(with_lifespan=False)
    publisher = FakePublisher()
    app.state.publisher = publisher
    app.state.store = store
    app.state.embedders = (FakeDenseEmbedder(settings.embedding_dim), FakeSparseEmbedder())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield SimpleNamespace(app=app, client=client, publisher=publisher, store=store, settings=settings)


async def register_and_login(client: httpx.AsyncClient, username: str, password: str = "senha-forte-123"):
    """Cria o usuário e devolve (user_id, cabeçalhos com o token)."""
    created = await client.post("/auth/register", json={"username": username, "password": password})
    assert created.status_code == 201, created.text
    login = await client.post("/auth/login", data={"username": username, "password": password})
    assert login.status_code == 200, login.text
    body = login.json()
    return body["user_id"], {"Authorization": f"Bearer {body['access_token']}"}


@pytest.fixture
def pdf_bytes() -> bytes:
    """PDF mínimo e válido, de uma página."""
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


@pytest.fixture
def parsed_doc() -> ParsedDocument:
    """Documento parseado de exemplo, no formato que o parse-worker grava."""
    dre = (
        "|  | 2022 | 2021 |\n"
        "|---|---|---|\n"
        "| Receita operacional líquida | 1.325.059 | 1.061.410 |\n"
        "| Lucro bruto | 485.156 | 388.450 |"
    )
    items = [
        ParsedItem(type="heading", text="Demonstrações dos resultados", page=1, level=1),
        ParsedItem(type="text", text="Exercícios findos em 31 de dezembro de 2022 e 2021", page=1),
        ParsedItem(type="text", text="(Valores expressos em milhares de reais)", page=1),
        ParsedItem(type="table", text=dre, page=1),
        ParsedItem(type="heading", text="26. Gestão de riscos", page=2, level=1),
        ParsedItem(type="text", text="A Companhia está exposta a riscos de mercado, de crédito e de "
                                     "taxa de juros decorrentes de suas atividades.", page=2),
        ParsedItem(type="heading", text="Endividamento", page=3, level=1),
        ParsedItem(type="text", text="A dívida líquida totalizou R$9.032 milhões e o indicador de "
                                     "covenants Dívida Líquida/EBITDA encerrou em 3,32x.", page=3),
    ]
    pages = [
        ParsedPage(page=1, native_text="Empresa Exemplo S.A.\nDemonstrações financeiras\n"
                                       "Em 31 de dezembro de 2022\n(Valores expressos em milhares de reais)"),
        ParsedPage(page=2, native_text="26. Gestão de riscos"),
        ParsedPage(page=3, native_text="Endividamento R$9.032 milhões 3,32x"),
    ]
    return ParsedDocument(doc_id="abcdef0123456789", file_name="exemplo.pdf", page_count=3,
                          parser="teste", pipeline_version=PIPELINE_VERSION, items=items, pages=pages)
