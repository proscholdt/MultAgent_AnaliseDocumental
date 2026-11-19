"""Configuração do servidor MCP, em três lugares:

- `config/policy.yaml`: o que cada agente vê e chama. Relida quando muda.
- `config/ajustes.yaml`: o que se ajusta (tamanho do que a busca devolve). Relido a cada chamada.
- `.env`: só segredos (MCP_AGENT_SECRET, ADMIN_KEY).
"""
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import PrivateAttr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Identidade: o token do agente é um JWT HS256 assinado com este segredo pelo sistema dos
    # agentes. O LLM nunca vê o token; ele só chega aqui no cabeçalho Authorization.
    mcp_agent_secret: str = ""
    mcp_audience: str = "mcp-server"
    # Chaves públicas dos serviços dos agentes (`<servico>.pem`, Ed25519). Com a pasta preenchida,
    # só valem tokens EdDSA, e cada serviço só assina pelo próprio agente: um especialista
    # comprometido não se passa por outro. Vazia ou ausente: vale o segredo HS256 (modo antigo).
    chaves_publicas_dir: Path = Path("/run/chaves/publicas")
    admin_key: str = ""  # rotas /governanca e /auditoria

    indexer_url: str = "http://localhost:8000"
    indexer_timeout_seconds: float = 60.0
    user_cache_seconds: int = 300

    policy_path: Path = Path("config/policy.yaml")
    config_path: Path = Path("config/ajustes.yaml")
    database_url: str = "sqlite+aiosqlite:///./data/mcp.db"

    # Tamanho do que volta para o modelo: o texto de cada trecho da busca é cortado, e quem
    # precisa do chunk inteiro pede a página.
    search_text_chars: int = 1200
    search_limit_max: int = 10

    _config_lido: int | None = PrivateAttr(default=None)
    _config_erro: str | None = PrivateAttr(default=None)

    @property
    def config_error(self) -> str | None:
        """Por que o ajustes.yaml atual não foi aplicado (vale a versão anterior), ou None."""
        return self._config_erro

    def reload(self) -> None:
        """Aplica o config/ajustes.yaml, se ele mudou. Arquivo inválido mantém o que estava."""
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
    def problem(self) -> str | None:
        """O que falta para o servidor poder subir, ou None."""
        if any(self.chaves_publicas_dir.glob("*.pem")):
            return None
        if len(self.mcp_agent_secret) < 32:
            return "MCP_AGENT_SECRET ausente ou curto: precisa de pelo menos 32 caracteres."
        return None


# O que o config/ajustes.yaml pode mudar sem reiniciar.
AJUSTAVEIS = frozenset({"search_text_chars", "search_limit_max"})


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.reload()
    return settings
