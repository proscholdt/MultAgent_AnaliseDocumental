"""Roda UM agente PydanticAI: o prompt do arquivo, o modelo e a conexão MCP dele, com os freios.

É a mesma função para o orquestrador e para os especialistas. O especialista devolve texto; o
orquestrador devolve uma decisão estruturada (`output_type`), que o grafo executa.

O que sai de uma execução de agente:

- os passos (cada tool chamada, se deu certo ou foi recusada, o tempo; e o raciocínio do
  modelo, quando ele devolve o resumo), que vão para o trace;
- os tokens, que vão para o custo e são descontados do orçamento;
- as mensagens completas, que ficam guardadas e servem de memória na chamada seguinte.
"""
import asyncio
import dataclasses
import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelResponse,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models import Model
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import UsageLimits

from app import mcp
from app.config import Settings
from app.registry import AgentFile
from app.tokens import Identity

_ARGS_IN_TRACE = 300
_THOUGHT_IN_TRACE = 1500


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


def empty_usage() -> dict[str, int]:
    return {"chamadas": 0, "entrada": 0, "entrada_cache": 0, "saida": 0, "total": 0}


def add_usage(total: dict[str, int], part: dict[str, int]) -> dict[str, int]:
    for key in total:
        total[key] += int(part.get(key, 0))
    return total


def usage_of(usage: Any) -> dict[str, int]:
    """Os tokens de uma execução de agente, nos nomes usados pelo custo e pelo trace."""
    entrada, saida = int(usage.input_tokens or 0), int(usage.output_tokens or 0)
    return {"chamadas": int(usage.requests or 0), "entrada": entrada,
            "entrada_cache": int(usage.cache_read_tokens or 0), "saida": saida, "total": entrada + saida}


def _short(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def steps_of(messages: list[ModelMessage]) -> list[dict[str, Any]]:
    """Os passos de uma execução, lidos das mensagens: cada tool (com o resultado: deu certo ou
    o servidor recusou) e cada trecho de raciocínio que o modelo devolveu."""
    calls: dict[str, tuple[ToolCallPart, Any]] = {}
    steps: list[dict[str, Any]] = []
    for message in messages:
        for part in message.parts:
            if isinstance(part, ThinkingPart) and part.content.strip():
                steps.append({"raciocinio": _short(part.content.strip(), _THOUGHT_IN_TRACE)})
            elif isinstance(part, ToolCallPart):
                # A saída estruturada (a decisão do orquestrador) chega como uma "tool" do PydanticAI:
                # não é chamada ao servidor MCP e não entra nos passos.
                if getattr(part, "tool_kind", None) == "output" or part.tool_name == "final_result":
                    continue
                calls[part.tool_call_id] = (part, message.timestamp if isinstance(message, ModelResponse) else None)
            elif isinstance(part, ToolReturnPart) and part.tool_call_id in calls:
                call, asked = calls.pop(part.tool_call_id)
                arguments = call.args if isinstance(call.args, str) else json.dumps(call.args, ensure_ascii=False)
                ms = round((part.timestamp - asked).total_seconds() * 1000) if asked and part.timestamp else None
                steps.append({"tool": call.tool_name, "ok": part.outcome == "success", "ms": ms,
                              "argumentos": _short(arguments or "", _ARGS_IN_TRACE)})
    return steps


def dump(messages: list[ModelMessage]) -> list[dict[str, Any]]:
    return ModelMessagesTypeAdapter.dump_python(messages, mode="json")


def load(data: list[dict[str, Any]] | None) -> list[ModelMessage]:
    return ModelMessagesTypeAdapter.validate_python(data or [])


@dataclass
class AgentRun:
    agente: str
    # concluido | limite_de_turnos | orcamento_esgotado | tempo_esgotado | sem_permissao | erro
    estado: str
    modelo: str = ""
    texto: str = ""  # a mensagem final do agente
    saida: Any = None  # a saída estruturada, quando o agente tem `output_type`
    erro: str | None = None
    segundos: float = 0.0
    uso: dict[str, int] = field(default_factory=empty_usage)
    passos: list[dict[str, Any]] = field(default_factory=list)
    mensagens: list[dict[str, Any]] = field(default_factory=list)  # só as desta execução, em JSON
    historico: list[dict[str, Any]] = field(default_factory=list)  # as recebidas + as desta execução

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def writes(self) -> dict[str, int]:
        """Quantas chamadas deram certo, por tool. É fato contado pelo código, não relato do modelo."""
        counts: dict[str, int] = {}
        for step in self.passos:
            if step.get("ok"):
                counts[step["tool"]] = counts.get(step["tool"], 0) + 1
        return counts


@dataclass
class Engine:
    """O que rodar um agente precisa do mundo de fora. Os testes trocam os dois por dublês."""

    settings: Settings
    connect: Callable[[str, Identity], mcp.McpSession]
    model: Callable[[AgentFile], Model]


def instrumentar_agentes() -> None:
    """Liga os spans do PydanticAI (execução do agente, cada chamada ao modelo com os tokens, cada
    tool). O conteúdo das mensagens (texto dos documentos) só vai junto com OTEL_INCLUIR_CONTEUDO=true."""
    import os

    from pydantic_ai.models.instrumented import InstrumentationSettings

    incluir = os.getenv("OTEL_INCLUIR_CONTEUDO", "false").lower() == "true"
    Agent.instrument_all(InstrumentationSettings(include_content=incluir, include_binary_content=False))


GROQ = "groq:"


def default_engine(settings: Settings) -> Engine:
    from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
    from pydantic_ai.providers.openai import OpenAIProvider

    provider = OpenAIProvider(api_key=settings.openai_api_key or "sem-chave")
    groq = OpenAIProvider(base_url=settings.groq_base_url, api_key=settings.groq_api_key or "sem-chave")

    def model(spec: AgentFile) -> Model:
        """O modelo do arquivo do agente, com o esforço dele (ou o padrão do ajustes.yaml)."""
        name = spec.modelo
        effort = settings.esforco_raciocinio if spec.raciocinio is None else spec.raciocinio
        if name.startswith(GROQ):
            # Chat Completions: o esforço de raciocínio vai como reasoning_effort (os gpt-oss aceitam).
            return OpenAIChatModel(name.removeprefix(GROQ), provider=groq,
                                   settings={"openai_reasoning_effort": effort} if effort else None)
        model_settings = None
        if effort:  # sem esforço, o modelo não raciocina e não há resumo
            model_settings = {"thinking": effort}
            if settings.resumo_raciocinio:
                model_settings["openai_reasoning_summary"] = settings.resumo_raciocinio
        return OpenAIResponsesModel(name, provider=provider, settings=model_settings)

    return Engine(settings, lambda agent, identity: mcp.connect(settings, agent, identity), model)


async def _with_properties(_ctx: Any, tools: list[ToolDefinition]) -> list[ToolDefinition]:
    """Tool sem argumento (listar_documentos, pendencias...). A Groq recusa o pedido inteiro se uma
    delas vai em modo strict ("'required' present but 'properties' is missing", mesmo com
    "properties": {}); a OpenAI aceita. Sem argumento, o strict não tem o que garantir."""
    return [tool if tool.parameters_json_schema.get("properties") else
            dataclasses.replace(tool, strict=False,
                                parameters_json_schema={**tool.parameters_json_schema, "properties": {}})
            for tool in tools]


def _limit_state(error: UsageLimitExceeded, spec: AgentFile) -> tuple[str, str]:
    if "request_limit" in str(error):
        return "limite_de_turnos", f"parou no limite de {spec.max_turnos} turnos do agente"
    return "orcamento_esgotado", f"teto de tokens da execução atingido ({error})"[:300]


async def run_agent(spec: AgentFile, entrada: str, *, identity: Identity, engine: Engine, budget: Budget,
                    output_type: Any = str, history: list[dict[str, Any]] | None = None,
                    allow_without_tools: bool = False) -> AgentRun:
    started = time.monotonic()
    run = AgentRun(agente=spec.nome, estado="erro")
    agent_run = None
    try:
        if not budget.tokens_left:
            raise BudgetExceeded("teto de tokens da execução atingido")
        model = engine.model(spec)
        run.modelo = spec.modelo  # para o custo
        async with asyncio.timeout(budget.seconds_left), engine.connect(spec.nome, identity) as session:
            # Agente sem tool nenhuma é agente sem entrada na política do servidor MCP: não roda, e
            # o erro diz o que fazer.
            if not allow_without_tools and not await session.tool_names():
                run.estado = "sem_permissao"
                run.erro = (f"o agente '{spec.nome}' não tem nenhuma tool liberada: falta a entrada dele na "
                            "política do servidor MCP (policy.yaml)")
                return run
            agent = Agent(model, name=spec.nome, instructions=spec.prompt, output_type=output_type,
                          toolsets=[session.toolset.prepared(_with_properties)])
            limits = UsageLimits(request_limit=spec.max_turnos, total_tokens_limit=budget.tokens_left)
            async with agent.iter(entrada, message_history=load(history), usage_limits=limits) as agent_run:
                async for _node in agent_run:
                    pass
            result = agent_run.result
            run.estado = "concluido"
            if output_type is str:
                run.texto = str(result.output or "")
            else:
                run.saida = result.output
    except UsageLimitExceeded as error:
        run.estado, run.erro = _limit_state(error, spec)
    except BudgetExceeded as error:
        run.estado, run.erro = "orcamento_esgotado", str(error)
    except TimeoutError:
        run.estado, run.erro = "tempo_esgotado", "o tempo máximo da execução acabou"
    except Exception as error:  # noqa: BLE001 - falha de modelo, de rede ou do servidor: vira estado, não exceção
        run.estado, run.erro = "erro", f"{type(error).__name__}: {error}"[:500]
    finally:
        run.segundos = round(time.monotonic() - started, 1)
        if agent_run is not None:  # mesmo se o freio parou no meio: o que foi feito fica registrado
            new = agent_run.new_messages()
            run.passos, run.mensagens = steps_of(new), dump(new)
            run.historico = dump(agent_run.all_messages())
            run.uso = usage_of(agent_run.usage)
            budget.spend(run.uso["total"])
    return run
