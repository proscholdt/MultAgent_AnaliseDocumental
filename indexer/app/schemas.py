"""Modelos Pydantic: mensagens Kafka, documento parseado e contratos da API."""
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Mensagens Kafka — carregam só referências; o PDF fica no volume compartilhado.
# ---------------------------------------------------------------------------


class ParseRequested(BaseModel):
    job_id: str
    user_id: str
    doc_id: str
    attempt: int = 1


class IndexRequested(BaseModel):
    job_id: str
    user_id: str
    doc_id: str
    attempt: int = 1
    reason: Literal["parsed", "metadata_changed", "retry"] = "parsed"


class DocumentEvent(BaseModel):
    """Publicado em indexer.events; o sistema do agente pode assinar para reagir."""

    type: Literal["document.indexed", "document.failed"]
    job_id: str
    user_id: str
    doc_id: str
    chunk_count: int | None = None
    error: str | None = None
    occurred_at: datetime


# ---------------------------------------------------------------------------
# Documento parseado — esquema próprio gravado pelo parse-worker em parsed.json.
# O index-worker lê este arquivo e por isso não depende de Docling nem de torch.
# ---------------------------------------------------------------------------

ItemType = Literal["heading", "text", "table", "figure"]


class OcrWord(BaseModel):
    """Trecho de texto lido por OCR num recorte de figura, com a caixa em pixels do recorte."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float


class ParsedItem(BaseModel):
    type: ItemType
    text: str = ""  # parágrafo, título, tabela em Markdown ou legenda da figura
    page: int  # página do PDF, 1-based
    page_end: int | None = None  # última página, quando o parágrafo continua na página seguinte
    level: int | None = None  # nível do título, quando o parser informa
    image_path: str | None = None  # recorte da figura, relativo à pasta do documento
    area_ratio: float | None = None  # área da figura / área da página
    # Textos lidos por OCR no recorte da figura, com a posição de cada um.
    # None = ainda não lido; [] = lido, nada encontrado.
    ocr_words: list[OcrWord] | None = None


class ParsedPage(BaseModel):
    page: int
    native_text: str = ""  # camada de texto do PDF, usada para conferir números de figuras


class ParsedDocument(BaseModel):
    doc_id: str
    file_name: str
    page_count: int
    parser: str
    pipeline_version: str
    items: list[ParsedItem] = Field(default_factory=list)
    pages: list[ParsedPage] = Field(default_factory=list)
    ocr_pages: list[int] = Field(default_factory=list)  # páginas sem texto nativo que passaram por OCR

    def native_text(self, page: int) -> str:
        for p in self.pages:
            if p.page == page:
                return p.native_text
        return ""


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.@-]+$",
                          examples=["analista1"])
    password: str = Field(min_length=8, max_length=128, examples=["senha-forte-123"])


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: str = Field(validation_alias="id")
    username: str
    created_at: datetime


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Validade do token em segundos.")
    user_id: str


class UploadAccepted(BaseModel):
    doc_id: str = Field(description="16 primeiros hex do SHA-256 do arquivo.")
    job_id: str
    status: str = "queued"


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    doc_id: str
    user_id: str
    file_name: str
    size_bytes: int
    page_count: int | None
    company: str | None
    period: str | None
    doc_type: str | None
    default_unit: str | None
    metadata_source: dict[str, Any]
    status: str
    chunk_count: int
    created_at: datetime
    updated_at: datetime
    indexed_at: datetime | None


class DocumentPatch(BaseModel):
    """Correção manual de metadados. Dispara reindexação a partir do parse em cache."""

    company: str | None = Field(default=None, max_length=255)
    period: str | None = Field(default=None, max_length=64)
    doc_type: str | None = Field(default=None, max_length=128)


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    job_id: str = Field(validation_alias="id")
    doc_id: str
    kind: str
    status: str
    stage: str | None
    failed_stage: str | None
    progress_current: int
    progress_total: int
    attempts: int
    error: str | None
    timings: dict[str, Any]
    usage: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None


class ChunkOut(BaseModel):
    chunk_index: int
    chunk_type: str
    extraction_method: str
    page_start: int
    page_end: int
    section_path: list[str]
    unit: str | None
    unit_source: str | None = None
    token_count: int
    verified_ratio: float | None = None
    verified_by_ocr: int | None = None
    verified_labels_ratio: float | None = None
    suspicious: bool = False
    context_header: str
    text: str


class ChunkPage(BaseModel):
    doc_id: str
    total: int
    offset: int
    limit: int
    items: list[ChunkOut]


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=1000,
                       examples=["alavancagem dívida líquida EBITDA covenants"])
    limit: int = Field(default=8, ge=1, le=50)
    doc_id: str | None = Field(default=None, description="Restringe a um documento.")


class SearchHit(BaseModel):
    score: float
    doc_id: str
    file_name: str
    company: str | None
    period: str | None
    page_start: int
    page_end: int
    chunk_index: int
    chunk_type: str
    section_path: list[str]
    unit: str | None
    text: str


class SearchResponse(BaseModel):
    query: str
    fusion: str = "rrf"
    hits: list[SearchHit]


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    components: dict[str, str]
