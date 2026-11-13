"""Roda UM agente: o prompt do arquivo, o modelo e a conexão MCP dele, com os freios.

É a mesma função para o orquestrador e para os especialistas. A diferença está nas tools: o
especialista só tem as do servidor MCP; o orquestrador tem as dele no MCP e mais uma tool por
especialista, cujo corpo é uma chamada A2A.
"""
import asyncio
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from agents import Agent, RunConfig, RunHooks, Runner, Tool
from agents.exceptions import MaxTurnsExceeded
from agents.mcp import MCPServer
from agents.models.interface import Model

from app import mcp
from app.config import Settings
from app.registry import AgentFile
from app.tokens import Identity

_ARGS_IN_TRACE = 300


class BudgetExceeded(Exception):
    pass


@dataclass
class Budget:
    """Teto de tokens e de tempo de uma execução inteira. O orquestrador passa o que resta a
    cada especialista, e desconta o que ele gastou quando a chamada volta."""

    tokens: int
    seconds: float
    used: int = 0
    started: float = field(default_factory=time.monotonic)

    @property
    def tokens_left(self) -> int:
        return max(0, self.tokens - self.used)

    @property
    def seconds_left(self) -> float:
        return max(0.0, self.seconds - (time.monotonic() - self.started))

    def spend(self, tokens: int) -> None:
        self.used += tokens
        if self.used > self.tokens:
            raise BudgetExceeded(f"teto de {self.tokens} tokens da execução atingido")


def empty_usage() -> dict[str, int]:
    return {"chamadas": 0, "entrada": 0, "entrada_cache": 0, "saida": 0, "total": 0}


def add_usage(total: dict[str, int], part: dict[str, int]) -> dict[str, int]:
    for key in total:
        total[key] += int(part.get(key, 0))
    return total


def _failed(output: object) -> bool:
    """O servidor MCP e as tools dos especialistas devolvem JSON; recusa vem com a chave 'erro'.
    A tool de especialista chega como texto; a do MCP, como {"type": "text", "text": "<json>"}
    (ou uma lista disso)."""
    if isinstance(output, list):
        return any(_failed(item) for item in output)
    if isinstance(output, dict) and isinstance(output.get("text"), str):
        output = output["text"]
    try:
        body = json.loads(output) if isinstance(output, str) else output
    except json.JSONDecodeError:
        return False
    return isinstance(body, dict) and bool(body.get("erro"))


class Recorder(RunHooks):
    """Anota cada chamada de tool e soma os tokens de cada chamada ao modelo. É daqui que saem
    os passos do trace e o custo; e é aqui que o teto de tokens interrompe a execução."""

    def __init__(self, budget: Budget) -> None:
        self.budget = budget
        self.usage = empty_usage()
        self.steps: list[dict[str, Any]] = []
        self._started: dict[str, float] = {}

    async def on_llm_end(self, context, agent, response) -> None:
        usage = response.usage
        cached = getattr(usage.input_tokens_details, "cached_tokens", 0) or 0
        add_usage(self.usage, {"chamadas": 1, "entrada": usage.input_tokens, "entrada_cache": cached,
                               "saida": usage.output_tokens, "total": usage.total_tokens})
        self.budget.spend(usage.total_tokens)

    async def on_tool_start(self, context, agent, tool) -> None:
        self._started[getattr(context, "tool_call_id", None) or tool.name] = time.monotonic()

    async def on_tool_end(self, context, agent, tool, result) -> None:
        started = self._started.pop(getattr(context, "tool_call_id", None) or tool.name, None)
        arguments = getattr(context, "tool_arguments", None) or ""
        self.steps.append({
            "tool": tool.name, "ok": not _failed(result),
            "ms": round((time.monotonic() - started) * 1000) if started else None,
            "argumentos": arguments if len(arguments) <= _ARGS_IN_TRACE else arguments[:_ARGS_IN_TRACE] + "…",
        })


@dataclass
class AgentRun:
    agente: str
    # concluido | limite_de_turnos | orcamento_esgotado | tempo_esgotado | sem_permissao | erro
    estado: str
    modelo: str = ""
    texto: str = ""  # a mensagem final do agente
    erro: str | None = None
    segundos: float = 0.0
    uso: dict[str, int] = field(default_factory=empty_usage)
    passos: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def writes(self) -> dict[str, int]:
        """Quantas chamadas deram certo, por tool. É fato contado pelo código, não relato do modelo."""
        counts: dict[str, int] = {}
        for step in self.passos:
            if step["ok"]:
                counts[step["tool"]] = counts.get(step["tool"], 0) + 1
        return counts


@dataclass
class Engine:
    """O que rodar um agente precisa do mundo de fora. Os testes trocam os dois por dublês."""

    settings: Settings
    connect: Callable[[str, Identity], MCPServer]
    model: Callable[[str], "str | Model"]


def default_engine(settings: Settings) -> Engine:
    def model(alias: str) -> str:
        if alias not in settings.modelos:
            raise KeyError(f"o apelido de modelo '{alias}' não está em MODELOS ({', '.join(sorted(settings.modelos))})")
        return settings.modelos[alias]

    return Engine(settings, lambda agent, identity: mcp.connect(settings, agent, identity), model)


async def run_agent(spec: AgentFile, entrada: str, *, identity: Identity, engine: Engine, budget: Budget,
                    tools: Sequence[Tool] = ()) -> AgentRun:
    started = time.monotonic()
    recorder = Recorder(budget)
    run = AgentRun(agente=spec.nome, estado="erro")
    try:
        model = engine.model(spec.modelo)
        run.modelo = engine.settings.modelos.get(spec.modelo, spec.modelo)  # o nome real, para o custo
        async with asyncio.timeout(budget.seconds_left):
            server = engine.connect(spec.nome, identity)
            async with server:
                # Agente sem tool nenhuma é agente sem entrada na política do servidor MCP: não
                # roda, e o erro diz o que fazer.
                if not tools and not await mcp.tool_names(server):
                    run.estado = "sem_permissao"
                    run.erro = (f"o agente '{spec.nome}' não tem nenhuma tool liberada: falta a entrada dele "
                                "na política do servidor MCP (policy.yaml)")
                    return run
                agent = Agent(name=spec.nome, instructions=spec.prompt, model=model, mcp_servers=[server],
                              tools=list(tools), mcp_config={"convert_schemas_to_strict": True})
                result = await Runner.run(
                    agent, entrada, max_turns=spec.max_turnos, hooks=recorder,
                    run_config=RunConfig(tracing_disabled=not engine.settings.openai_tracing,
                                         workflow_name=f"agente {spec.nome}"))
                run.estado, run.texto = "concluido", str(result.final_output or "")
    except MaxTurnsExceeded:
        run.estado, run.erro = "limite_de_turnos", f"parou no limite de {spec.max_turnos} turnos do agente"
    except BudgetExceeded as error:
        run.estado, run.erro = "orcamento_esgotado", str(error)
    except TimeoutError:
        run.estado, run.erro = "tempo_esgotado", "o tempo máximo da execução acabou"
    except Exception as error:  # noqa: BLE001 - falha de modelo, de rede ou do servidor: vira estado, não exceção
        run.estado, run.erro = "erro", f"{type(error).__name__}: {error}"[:500]
    finally:
        run.segundos = round(time.monotonic() - started, 1)
        run.uso, run.passos = recorder.usage, recorder.steps
    return run
