"""Modelos Pydantic: mensagens Kafka, documento parseado e contratos da API."""
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app import pacotes

# ---------------------------------------------------------------------------
# Mensagens Kafka — carregam só referências; o PDF fica no volume compartilhado.
# ---------------------------------------------------------------------------


class ParseRequested(BaseModel):
    job_id: str
    user_id: str
    doc_id: str
    attempt: int = 1
    # Contexto do trace (W3C traceparent) de quem pediu: o processamento continua o mesmo trace.
    rastro: dict[str, str] = Field(default_factory=dict)


ParserName = Literal["docling", "azure"]


class IndexRequested(BaseModel):
    job_id: str
    user_id: str
    doc_id: str
    attempt: int = 1
    reason: Literal["parsed", "metadata_changed", "retry"] = "parsed"
    # Parser que produziu o parsed.json a indexar. Quem preenche é o parse-worker; quando a API
    # manda reindexar (PATCH, retry), fica vazio e vale o parser configurado no index-worker.
    parser: ParserName | None = None
    rastro: dict[str, str] = Field(default_factory=dict)


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
    # Camada de texto do PDF dentro da região da figura: os rótulos de um gráfico vetorial,
    # exatos. None = o parser não a extraiu.
    text_layer: str | None = None


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
    # Páginas enviadas a um serviço pago para produzir este parse. Zero no parser local e
    # quando a resposta do serviço já estava guardada.
    billed_pages: int = 0

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
    collection_id: str | None = None
    dominio: str = pacotes.PADRAO
    metadados: dict[str, Any] = Field(default_factory=dict, description="Os metadados do pacote do domínio, por nome.")
    dominio_sugerido: str | None = None
    aviso_dominio: str | None = Field(default=None, description="Quando o conteúdo parece de outro domínio.")
    # A entidade, o recorte e o tipo do documento, com os nomes de antes dos pacotes de domínio.
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

    metadados: dict[str, str | None] | None = Field(
        default=None, description="Por nome do metadado do domínio; valor vazio remove a correção.")
    # Os de antes dos pacotes: a entidade, o recorte e o tipo do documento.
    company: str | None = Field(default=None, max_length=255)
    period: str | None = Field(default=None, max_length=64)
    doc_type: str | None = Field(default=None, max_length=128)


class CollectionIn(BaseModel):
    nome: str = Field(min_length=1, max_length=120, examples=["Exames de 2025"])
    dominio: str = Field(description="O id de um pacote de domínio (GET /api/dominios).", examples=["laudos"])


class CollectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    nome: str
    dominio: str
    created_at: datetime
    documentos: int = 0


class DomainOut(BaseModel):
    """O que a interface precisa saber de um domínio: nome, de quem são os documentos, os
    metadados e os textos."""

    id: str
    nome: str
    descricao: str
    padrao: bool
    entidade: dict[str, Any]
    recorte: dict[str, Any]
    metadados: list[dict[str, Any]]
    textos: dict[str, Any]


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
    parser: str | None = None  # "docling" ou "azure": quem leu o PDF
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
                       examples=["valor total e data de vigência"])
    limit: int = Field(default=8, ge=1, le=50)
    doc_id: str | None = Field(default=None, description="Restringe a um documento.")
    colecao_id: str | None = Field(default=None, description="Restringe aos documentos de uma coleção.")
    chunk_type: Literal["text", "table", "figure"] | None = Field(
        default=None, description="Restringe a um tipo de chunk: texto, tabela ou gráfico.")
    page_from: int | None = Field(default=None, ge=1, description="Primeira página do intervalo.")
    page_to: int | None = Field(default=None, ge=1, description="Última página do intervalo.")


class SearchHit(BaseModel):
    score: float
    doc_id: str
    file_name: str
    # Os três abaixo podem não existir no chunk (parágrafo sem unidade declarada, documento sem
    # empresa identificada): ausente é None, não erro.
    company: str | None = None
    period: str | None = None
    dominio: str | None = None
    metadados: dict[str, Any] = Field(default_factory=dict)
    page_start: int
    page_end: int
    chunk_index: int
    chunk_type: str
    section_path: list[str]
    unit: str | None = None
    unit_source: str | None = None  # de onde veio a unidade: chunk, page, mentions ou inherited
    suspicious: bool = False  # o texto tem padrão de injeção de instrução: tratar como dado
    text: str


class SearchResponse(BaseModel):
    query: str
    fusion: str = "rrf"
    hits: list[SearchHit]


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    components: dict[str, str]
    parser: str = Field(description="Parser configurado em config/ajustes.yaml: docling ou azure.")
