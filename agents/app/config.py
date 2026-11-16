"""Configuração dos dois serviços deste sistema (orquestrador e especialistas).

Três lugares, cada um com o seu papel:

- `agentes/<nome>.md`: o que é de cada agente (modelo, raciocínio, turnos, prompt). Relido
  quando muda.
- `config/ajustes.yaml`: o que vale para todos (preços, freios, raciocínio padrão, tempos).
  Relido a cada pedido: mudar ali vale no próximo, sem build nem restart.
- `.env`: só o que não pode ir para o Git (chaves e segredos) e o que muda de uma máquina para
  outra. Os campos abaixo têm o padrão; o YAML e o .env só dizem o que muda.
"""
import logging
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, PrivateAttr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Chaves dos provedores (.env). O modelo de cada agente está no arquivo dele; com o prefixo
    # "groq:" (ex.: "groq:openai/gpt-oss-120b") ele vai à Groq, pela API compatível com a OpenAI.
    openai_api_key: str = ""
    groq_api_key: str = ""
    groq_base_url: str = "https://api.groq.com/openai/v1"

    # Do config/ajustes.yaml (os valores abaixo são o padrão, se o arquivo não disser nada).
    config_path: Path = Path("config/ajustes.yaml")
    # US$ por milhão de tokens, por modelo: {"modelo": {"entrada": x, "entrada_cache": y, "saida": z}}.
    # Modelo sem preço aparece com os tokens e sem custo.
    precos: dict[str, dict[str, float]] = {}
    # Raciocínio: o esforço de quem não diz o seu no arquivo do agente ("" desliga) e o resumo que
    # a OpenAI devolve ("detailed", "auto"). O resumo entra no trace e nas mensagens de cada agente.
    esforco_raciocinio: str = "low"
    resumo_raciocinio: str = "detailed"
    # O texto das mensagens (prompts, respostas, resultado das tools) nos traces do Tempo, como no
    # LangSmith. Leva trechos dos documentos para a observabilidade.
    traces_com_conteudo: bool = False
    # Ao fim de cada relatório, a nota contra o gabarito (só para os documentos que têm gabarito).
    avaliar_relatorios: bool = True
    gabarito_path: Path = Path("avaliacao/gabarito.json")

    # Servidor MCP: a única porta dos agentes para tools e RAG. O segredo é o mesmo do servidor.
    mcp_url: str = "http://localhost:8100/mcp"
    mcp_agent_secret: str = ""
    mcp_audience: str = "mcp-server"
    mcp_timeout_seconds: float = 120

    # A2A: o orquestrador assina um token para cada chamada a um especialista.
    a2a_secret: str = ""
    # Onde os especialistas atendem (A2A). Cada especialista pode ter o seu serviço:
    # - no orquestrador, `especialistas_enderecos` diz o endereço de cada um ({"extrator":
    #   "http://extrator:8201", ...}); quem não está ali é procurado em `especialistas_url`, onde
    #   um serviço só atende todos os arquivos da pasta (vazio: não há esse serviço);
    # - no serviço de um especialista, `especialista` diz qual ele atende (vazio: todos) e
    #   `especialistas_url` é o endereço dele, que vai no Agent Card.
    especialistas_url: str = "http://localhost:8201"
    especialistas_enderecos: dict[str, str] = {}
    especialista: str = ""
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

    _config_lido: int | None = PrivateAttr(default=None)  # mtime do ajustes.yaml já aplicado
    _config_erro: str | None = PrivateAttr(default=None)

    @property
    def config_error(self) -> str | None:
        """Por que o ajustes.yaml atual não foi aplicado (vale a versão anterior), ou None."""
        return self._config_erro

    def reload(self) -> None:
        """Aplica o config/ajustes.yaml, se ele mudou desde a última leitura. Arquivo inválido não
        derruba nada: continua valendo o que estava, e o erro aparece em /health."""
        try:
            stamp = self.config_path.stat().st_mtime_ns
        except FileNotFoundError:
            return
        if stamp == self._config_lido:
            return
        self._config_lido = stamp
        try:
            data = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
            values = Ajustes.model_validate(data).campos()
        except ValidationError as error:
            problems = "; ".join(f"{'.'.join(map(str, item['loc']))}: {item['msg']}" for item in error.errors())
            self._config_erro = f"{self.config_path}: {problems}"[:400]
            log.warning("ajustes não aplicados: %s", self._config_erro)
            return
        except (yaml.YAMLError, ValueError) as error:
            self._config_erro = f"{self.config_path}: {str(error).splitlines()[0]}"[:400]
            log.warning("ajustes não aplicados: %s", self._config_erro)
            return
        for name, value in values.items():
            setattr(self, name, value)
        self._config_erro = None

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


Esforco = Literal["nenhum", "minimal", "low", "medium", "high"]


class _Bloco(BaseModel):
    model_config = ConfigDict(extra="forbid")  # chave com erro de digitação é erro, não silêncio


class _Preco(_Bloco):
    entrada: float
    entrada_cache: float
    saida: float


class _Execucao(_Bloco):
    max_segundos: int | None = None
    max_tokens: int | None = None
    rodadas_pendencias: int | None = None
    max_ondas: int | None = None


class _Tempos(_Bloco):
    mcp_segundos: float | None = None
    especialista_segundos: float | None = None


class _Avaliacao(_Bloco):
    ao_fim_do_relatorio: bool | None = None
    gabarito: Path | None = None


class Ajustes(_Bloco):
    """O formato do config/ajustes.yaml."""

    raciocinio_padrao: Esforco | None = None
    resumo_raciocinio: Literal["detailed", "auto", "concise", "nenhum"] | None = None
    precos: dict[str, _Preco] | None = None
    execucao: _Execucao | None = None
    memoria_max_tokens: int | None = None
    traces_com_conteudo: bool | None = None
    avaliacao: _Avaliacao | None = None
    tempo_limite: _Tempos | None = None
    min_minutos_token: int | None = None

    def campos(self) -> dict[str, object]:
        """Os campos do Settings que este arquivo define."""
        out: dict[str, object] = {}
        if self.raciocinio_padrao is not None:
            out["esforco_raciocinio"] = "" if self.raciocinio_padrao == "nenhum" else self.raciocinio_padrao
        if self.resumo_raciocinio is not None:
            out["resumo_raciocinio"] = "" if self.resumo_raciocinio == "nenhum" else self.resumo_raciocinio
        if self.precos is not None:
            out["precos"] = {name: price.model_dump() for name, price in self.precos.items()}
        for block, fields in ((self.execucao, {"max_segundos": "execucao_max_segundos",
                                               "max_tokens": "execucao_max_tokens",
                                               "rodadas_pendencias": "rodadas_pendencias", "max_ondas": "max_ondas"}),
                              (self.tempo_limite, {"mcp_segundos": "mcp_timeout_seconds",
                                                   "especialista_segundos": "especialista_timeout_seconds"})):
            for key, name in fields.items():
                if block is not None and getattr(block, key) is not None:
                    out[name] = getattr(block, key)
        if self.avaliacao is not None:
            if self.avaliacao.ao_fim_do_relatorio is not None:
                out["avaliar_relatorios"] = self.avaliacao.ao_fim_do_relatorio
            if self.avaliacao.gabarito is not None:
                out["gabarito_path"] = self.avaliacao.gabarito
        for name in ("memoria_max_tokens", "min_minutos_token", "traces_com_conteudo"):
            if getattr(self, name) is not None:
                out[name] = getattr(self, name)
        return out


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.reload()
    return settings
