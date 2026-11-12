"""Configuração central. Tudo vem de variáveis de ambiente / .env — nenhum segredo no código."""
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# Muda sempre que parse/chunking mudam de forma incompatível; invalida o cache de parse.
PIPELINE_VERSION = "4"

INSECURE_JWT_SECRET = "troque-este-segredo"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Autenticação ---
    jwt_secret: str = INSECURE_JWT_SECRET
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = 60

    # --- PostgreSQL ---
    database_url: str = "postgresql+asyncpg://indexer:indexer@localhost:5432/indexer"

    # --- Kafka ---
    kafka_bootstrap_servers: str = "localhost:29092"
    kafka_topic_parse: str = "indexer.parse"
    kafka_topic_index: str = "indexer.index"
    kafka_topic_events: str = "indexer.events"
    kafka_topic_dlq: str = "indexer.dlq"
    kafka_partitions: int = 3
    kafka_group_parse: str = "indexer-parse"
    kafka_group_index: str = "indexer-index"
    max_attempts: int = 3
    retry_backoff_seconds: float = 5.0
    # O parse de um PDF grande leva minutos; sem isto o broker expulsa o consumidor do grupo.
    parse_max_poll_interval_ms: int = 30 * 60 * 1000
    index_max_poll_interval_ms: int = 15 * 60 * 1000

    # --- Qdrant ---
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "financial_docs"

    # --- Embeddings / LLM ---
    # "fake" roda o pipeline inteiro sem chamar a OpenAI (vetor denso sem significado,
    # BM25 real, sem transcrição de gráficos nem metadados por LLM). Só para testes locais.
    embedding_provider: Literal["openai", "fake"] = "openai"
    openai_api_key: str = ""
    openai_embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536
    embedding_batch_size: int = 64
    openai_llm_model: str = "gpt-4o-mini"
    # Preços em US$ por 1 milhão de tokens, usados só para estimar custo por documento.
    embedding_price_per_1m: float = 0.02
    llm_input_price_per_1m: float = 0.15
    llm_output_price_per_1m: float = 0.60
    bm25_language: str = "portuguese"

    # --- Armazenamento ---
    storage_dir: Path = Path("storage")
    max_upload_mb: int = 50
    max_pages: int = 500

    # --- Parser (Docling) ---
    table_mode: Literal["accurate", "fast"] = "accurate"
    # Páginas por chamada ao Docling. Ele guarda as páginas renderizadas até o fim da conversão;
    # num PDF de 124 páginas convertido de uma vez o worker passou de 5,7 GB. Em lotes, o pico
    # de memória depende do lote, não do tamanho do documento.
    parse_page_batch: int = 10
    parse_threads: int = 0  # 0 = todos os núcleos
    figure_min_area_ratio: float = 0.04  # figuras menores que 4% da página são logotipos

    # --- Chunking ---
    chunk_target_tokens: int = 450
    chunk_max_tokens: int = 800
    table_max_tokens: int = 1500

    @property
    def llm_enabled(self) -> bool:
        return self.embedding_provider == "openai" and bool(self.openai_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
