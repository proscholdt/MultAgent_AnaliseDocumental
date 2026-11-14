"""Configuração central, em três lugares:

- `config/ajustes.yaml`: o que se ajusta (parser, chunks, preços, limites de upload...). Relido a
  cada pedido da API e a cada mensagem dos workers: mudar vale sem build e sem reiniciar.
- `.env`: só chaves e segredos (OPENAI_API_KEY, JWT_SECRET, AZURE_DI_KEY e o endpoint dela).
- o docker-compose: os endereços da infraestrutura (Postgres, Kafka, Qdrant, pasta de arquivos).

Os campos abaixo têm o padrão; o YAML diz só o que muda.
"""
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import PrivateAttr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)

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

    # --- Parser: quem lê o PDF ---
    # "docling": local, sem custo por página, minutos por documento em CPU.
    # "azure": Azure AI Document Intelligence (modelo de layout): o parse roda no serviço
    #          (tempo não medido aqui), é pago por página, e o PDF é enviado ao recurso da Azure.
    # Para trocar, mude `parser` no config/ajustes.yaml: vale no próximo PDF, sem reiniciar. Cada
    # parser guarda o próprio cache por documento, então voltar ao anterior não refaz o parse.
    parser: Literal["docling", "azure"] = "docling"
    figure_min_area_ratio: float = 0.04  # figuras menores que 4% da página são logotipos
    # A exceção: figura pequena (a partir de 2%) em que uma segunda fonte leu números. É um
    # gráfico pequeno. Acontece com o parser da Azure, que delimita só a área do gráfico.
    figure_small_min_area_ratio: float = 0.02

    # --- Parser Azure ---
    azure_di_endpoint: str = ""  # https://<recurso>.cognitiveservices.azure.com/
    azure_di_key: str = ""
    azure_di_model: str = "prebuilt-layout"
    azure_di_timeout_seconds: int = 900
    # Preço de tabela do modelo de layout no nível S0, em US$ por mil páginas. Só para estimar
    # o custo do parse no job; confira o valor da sua região.
    azure_di_price_per_1k_pages: float = 10.0
    # Escala da renderização dos recortes de figura (2 = 144 dpi), igual à do Docling.
    figure_render_scale: float = 2.0

    # --- Parser Docling ---
    table_mode: Literal["accurate", "fast"] = "accurate"
    # Páginas por chamada ao Docling. Ele guarda as páginas renderizadas até o fim da conversão;
    # num PDF de 124 páginas convertido de uma vez o worker passou de 5,7 GB. Em lotes, o pico
    # de memória depende do lote, não do tamanho do documento.
    parse_page_batch: int = 10
    parse_threads: int = 0  # 0 = todos os núcleos

    # --- Chunking ---
    chunk_target_tokens: int = 450
    chunk_max_tokens: int = 800
    table_max_tokens: int = 1500

    # --- Ajustes ---
    config_path: Path = Path("config/ajustes.yaml")
    _config_lido: int | None = PrivateAttr(default=None)
    _config_erro: str | None = PrivateAttr(default=None)

    @property
    def config_error(self) -> str | None:
        """Por que o ajustes.yaml atual não foi aplicado (vale a versão anterior), ou None."""
        return self._config_erro

    def reload(self) -> None:
        """Aplica o config/ajustes.yaml, se ele mudou. Arquivo inválido não derruba nada: continua
        valendo o que estava, e o motivo aparece em /health."""
        try:
            stamp = self.config_path.stat().st_mtime_ns
        except FileNotFoundError:
            return
        if stamp == self._config_lido:
            return
        self._config_lido = stamp
        try:
            data: Any = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
            if not isinstance(data, dict):
                raise ValueError("o arquivo tem de ser uma lista de 'chave: valor'")  # noqa: TRY004
            unknown = sorted(set(data) - AJUSTAVEIS)
            if unknown:
                raise ValueError(f"chave que não se ajusta aqui: {', '.join(unknown)}")
            checked = type(self).model_validate({**self.model_dump(), **data})
        except ValidationError as error:
            self._config_erro = f"{self.config_path}: " + "; ".join(
                f"{'.'.join(map(str, item['loc']))}: {item['msg']}" for item in error.errors())[:400]
            log.warning("ajustes não aplicados: %s", self._config_erro)
            return
        except (yaml.YAMLError, ValueError) as error:
            self._config_erro = f"{self.config_path}: {str(error).splitlines()[0]}"[:400]
            log.warning("ajustes não aplicados: %s", self._config_erro)
            return
        for name in data:
            setattr(self, name, getattr(checked, name))
        self._config_erro = None

    @property
    def llm_enabled(self) -> bool:
        return self.embedding_provider == "openai" and bool(self.openai_api_key)

    @property
    def azure_di_configured(self) -> bool:
        return bool(self.azure_di_endpoint.strip() and self.azure_di_key.strip())

    @property
    def parser_problem(self) -> str | None:
        """O que falta para o parser escolhido funcionar, ou None se está tudo configurado."""
        if self.parser == "azure" and not self.azure_di_configured:
            return ("parser: azure (config/ajustes.yaml) exige AZURE_DI_ENDPOINT e AZURE_DI_KEY no .env "
                    "(endpoint e chave do recurso de Document Intelligence).")
        return None


# O que o config/ajustes.yaml pode mudar. Fica de fora o que não pode mudar com documentos já
# indexados (modelo e dimensão dos embeddings, idioma do BM25, coleção) e a infraestrutura.
AJUSTAVEIS = frozenset({
    "parser", "table_mode", "parse_page_batch", "parse_threads", "figure_min_area_ratio",
    "figure_small_min_area_ratio", "figure_render_scale", "azure_di_model", "azure_di_timeout_seconds",
    "azure_di_price_per_1k_pages", "chunk_target_tokens", "chunk_max_tokens", "table_max_tokens",
    "openai_llm_model", "embedding_batch_size", "embedding_price_per_1m", "llm_input_price_per_1m",
    "llm_output_price_per_1m", "max_upload_mb", "max_pages", "jwt_expires_minutes", "max_attempts",
    "retry_backoff_seconds",
})


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.reload()
    return settings
