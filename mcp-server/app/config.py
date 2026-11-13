"""Configuração do servidor MCP, lida do ambiente e do .env."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Identidade: o token do agente é um JWT HS256 assinado com este segredo pelo sistema dos
    # agentes. O LLM nunca vê o token; ele só chega aqui no cabeçalho Authorization.
    mcp_agent_secret: str = ""
    mcp_audience: str = "mcp-server"
    admin_key: str = ""  # rotas /governanca e /auditoria

    indexer_url: str = "http://localhost:8000"
    indexer_timeout_seconds: float = 60.0
    user_cache_seconds: int = 300

    policy_path: Path = Path("policy.yaml")
    database_url: str = "sqlite+aiosqlite:///./data/mcp.db"

    # Tamanho do que volta para o modelo: o texto de cada trecho da busca é cortado, e quem
    # precisa do chunk inteiro pede a página.
    search_text_chars: int = 1200
    search_limit_max: int = 10

    @property
    def problem(self) -> str | None:
        """O que falta para o servidor poder subir, ou None."""
        if len(self.mcp_agent_secret) < 32:
            return "MCP_AGENT_SECRET ausente ou curto: precisa de pelo menos 32 caracteres."
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()
