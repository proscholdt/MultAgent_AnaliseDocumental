"""Configuração dos dois serviços deste sistema (orquestrador e especialistas), lida do .env."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Modelo. O arquivo do agente traz um apelido ("padrao", "forte"); o modelo real fica aqui,
    # para trocar de modelo sem tocar nos agentes.
    openai_api_key: str = ""
    modelos: dict[str, str] = {"padrao": "gpt-5.4-nano", "forte": "gpt-5.4-nano"}
    # Modelo com o prefixo "groq:" (ex.: "groq:openai/gpt-oss-120b") vai à Groq, pela API compatível
    # com a OpenAI (Chat Completions). O preço em PRECOS usa o nome com o prefixo.
    groq_api_key: str = ""
    groq_base_url: str = "https://api.groq.com/openai/v1"
    # US$ por milhão de tokens, por modelo: {"modelo": {"entrada": x, "entrada_cache": y, "saida": z}}.
    # Modelo sem preço aqui aparece com os tokens e sem custo.
    precos: dict[str, dict[str, float]] = {}
    # Raciocínio do modelo: o esforço ("minimal", "low", "medium", "high"; "" desliga) e o resumo
    # que a OpenAI devolve ("detailed", "auto"). Sem o esforço, o modelo responde sem raciocinar e
    # não há o que registrar. O resumo entra no trace e nas mensagens guardadas de cada agente.
    esforco_raciocinio: str = "low"
    resumo_raciocinio: str = "detailed"

    # Servidor MCP: a única porta dos agentes para tools e RAG. O segredo é o mesmo do servidor.
    mcp_url: str = "http://localhost:8100/mcp"
    mcp_agent_secret: str = ""
    mcp_audience: str = "mcp-server"
    mcp_timeout_seconds: float = 120

    # A2A: o orquestrador assina um token para cada chamada a um especialista.
    a2a_secret: str = ""
    # Onde os especialistas atendem. O orquestrador chama este endereço, e ele vai no Agent Card.
    especialistas_url: str = "http://localhost:8201"
    especialista_timeout_seconds: float = 900

    # Indexador: é quem diz quem é o usuário do token.
    indexer_url: str = "http://localhost:8000"
    # Uma execução não começa se o token do usuário vence antes disto (o indexador recusaria as
    # buscas no meio do caminho).
    min_minutos_token: int = 15

    agentes_dir: Path = Path("agentes")
    data_dir: Path = Path("data")

    # Freios de uma execução inteira (orquestrador + especialistas).
    execucao_max_segundos: int = 2400
    execucao_max_tokens: int = 6_000_000
    # Quantas vezes o orquestrador é chamado de novo quando a conferência final acha pendência.
    rodadas_pendencias: int = 2
    # Quantas decisões o orquestrador toma numa rodada (cada uma é uma onda de delegações).
    max_ondas: int = 20

    # Memória de um especialista na conversa do usuário: o teto, em tokens estimados, do histórico
    # que ele recebe de volta. Acima disso, as chamadas mais antigas saem; o resultado das tools
    # antigas já entra encurtado.
    memoria_max_tokens: int = 40_000

    @property
    def problem(self) -> str | None:
        """O que falta para o serviço funcionar; aparece em /health."""
        missing = [name for name, value in (("OPENAI_API_KEY", self.openai_api_key),
                                            ("MCP_AGENT_SECRET", self.mcp_agent_secret),
                                            ("A2A_SECRET", self.a2a_secret)) if not value]
        if missing:
            return "Faltam no .env: " + ", ".join(missing)
        short = [name for name, value in (("MCP_AGENT_SECRET", self.mcp_agent_secret), ("A2A_SECRET", self.a2a_secret))
                 if len(value) < 32]
        return ("Segredo curto demais (mínimo 32 caracteres): " + ", ".join(short)) if short else None


@lru_cache
def get_settings() -> Settings:
    return Settings()
