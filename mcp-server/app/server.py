"""O servidor MCP e a governança das duas mensagens que importam: tools/list e tools/call.

A política é aplicada aqui, no servidor, a cada mensagem. Não há sessão nem cache da lista:
o que um agente vê e pode chamar é decidido de novo toda vez, a partir dos cabeçalhos da
requisição. Filtrar só no cliente não protegeria nada: um agente com o prompt adulterado por
um texto do documento poderia pedir uma tool pelo nome.
"""
import json
import logging
import time
from collections.abc import Mapping
from typing import Any

import mcp.types as t
from mcp import MCPError
from mcp.server import Server
from opentelemetry import trace
from pydantic import ValidationError

from app import telemetria
from app.auth import Caller, identify
from app.catalog import Call, Deps, ToolSpec
from app.errors import AuthError, IndexerAuthError, IndexerError, IndexerNotFound, ToolRejected

log = logging.getLogger(__name__)

_UNAUTHORIZED = -32001  # código de erro JSON-RPC usado para identidade recusada


def _result(payload: Any, *, error: bool = False) -> t.CallToolResult:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, default=str)
    return t.CallToolResult(content=[t.TextContent(type="text", text=text)], is_error=error)


class Governance:
    """Lista e chama tools em nome de um chamador, aplicando política, limites e auditoria.

    Fica separada do transporte (recebe só os cabeçalhos) para poder ser testada sem HTTP."""

    def __init__(self, deps: Deps, tools: dict[str, ToolSpec]) -> None:
        self._deps, self._tools = deps, tools

    async def _caller(self, headers: Mapping[str, str]) -> Caller:
        return await identify(headers, self._deps.settings, self._deps.users)

    async def list_tools(self, headers: Mapping[str, str]) -> list[t.Tool]:
        caller = await self._caller(headers)
        policy = self._deps.policies.current()
        allowed = policy.tools_of(caller.agent)
        tools = [spec.definition() for name, spec in self._tools.items() if name in allowed]
        await self._deps.audit.record(caller, kind="lista", outcome="ok", policy_digest=policy.digest,
                                      detail=f"{len(tools)} tools")
        return tools

    async def call_tool(self, headers: Mapping[str, str], name: str,
                        arguments: dict[str, Any] | None) -> t.CallToolResult:
        # Um span por chamada de tool, filho da requisição HTTP (que já traz o trace do agente).
        self._deps.settings.reload()  # o config/ajustes.yaml de agora vale para esta chamada
        with telemetria.tracer().start_as_current_span(f"tool {name}", attributes={"mcp.tool": name}):
            return await self._call_tool(headers, name, arguments)

    async def _call_tool(self, headers: Mapping[str, str], name: str,
                         arguments: dict[str, Any] | None) -> t.CallToolResult:
        started = time.perf_counter()
        try:
            caller = await self._caller(headers)
        except IndexerAuthError:
            raise  # token de usuário recusado: é problema de identidade, tratado por quem chamou
        except IndexerError as exc:  # indexador fora do ar: não dá nem para saber quem é o usuário
            return _result({"erro": str(exc)}, error=True)
        policy = self._deps.policies.current()

        async def finish(outcome: str, payload: Any, detail: str = "") -> t.CallToolResult:
            seconds = time.perf_counter() - started
            await self._deps.audit.record(
                caller, kind="chamada", tool=name, outcome=outcome, policy_digest=policy.digest, detail=detail,
                arguments=arguments, duration_ms=round(seconds * 1000))
            _observe(caller, name, outcome, seconds, detail)
            return _result(payload, error=outcome != "ok")

        spec = self._tools.get(name)
        # A tool que não existe e a que não é do agente têm a mesma resposta: quem não pode
        # chamar também não fica sabendo o que existe.
        if spec is None or not policy.allows(caller.agent, name):
            message = f"A tool '{name}' não está liberada para o agente '{caller.agent}'."
            return await finish("recusada", {"erro": message}, "fora da política")
        limit = policy.calls_per_run(caller.agent)
        if limit is not None and await self._deps.audit.calls_in_run(caller.run_id, caller.agent) >= limit:
            message = (f"O agente '{caller.agent}' atingiu o limite de {limit} chamadas nesta execução. "
                       "Encerre com o que já foi feito.")
            return await finish("recusada", {"erro": message}, "limite de chamadas")
        try:
            args = spec.args.model_validate(arguments or {})
        except ValidationError as exc:
            problems = [f"{'.'.join(str(part) for part in error['loc']) or 'argumentos'}: {error['msg']}"
                        for error in exc.errors()]
            return await finish("invalida", {"erro": "Argumentos inválidos.", "problemas": problems},
                                "; ".join(problems))
        try:
            payload = await spec.handler(Call(caller=caller, deps=self._deps), args)
        except ToolRejected as exc:
            return await finish("rejeitada", exc.payload(), exc.message)
        except IndexerNotFound as exc:
            return await finish("rejeitada", {"erro": str(exc)}, str(exc))
        except IndexerAuthError as exc:
            return await finish("erro", {"erro": f"{exc} A execução não pode continuar: peça um novo login."},
                                str(exc))
        except IndexerError as exc:
            return await finish("erro", {"erro": str(exc)}, str(exc))
        except Exception as exc:  # o agente recebe um erro, não a pilha
            log.exception("Falha na tool %s", name)
            return await finish("erro", {"erro": "Falha interna na tool."}, f"{type(exc).__name__}: {exc}")
        return await finish("ok", payload)


_TOOL_CALLS = telemetria.medidor().create_counter(
    "mcp.tool.chamadas", description="Chamadas de tool, por agente, tool e resultado")
_TOOL_SECONDS = telemetria.medidor().create_histogram(
    "mcp.tool.duracao", unit="s", description="Duração das chamadas de tool")


RESULTADOS = ("ok", "rejeitada", "recusada", "invalida", "erro")


def zerar_metricas(policy: Any) -> None:
    """Cria as séries dos contadores em zero, na subida. Um contador do OpenTelemetry só passa a
    existir no primeiro incremento, e o Prometheus nunca vê o zero de antes: increase() perde o
    primeiro evento. Com as séries conhecidas já em zero, a conta fecha."""
    for agente, entrada in policy.agents.items():
        for tool in entrada.tools:
            for resultado in RESULTADOS:
                _TOOL_CALLS.add(0, {"agente": agente, "tool": tool, "resultado": resultado})


def _observe(caller: Caller, name: str, outcome: str, seconds: float, detail: str) -> None:
    """Métricas, atributos do span e uma linha de log por chamada (o log vai ao Loki)."""
    labels = {"agente": caller.agent, "tool": name, "resultado": outcome}
    _TOOL_CALLS.add(1, labels)
    _TOOL_SECONDS.record(seconds, {"tool": name, "resultado": outcome})
    span = trace.get_current_span()
    span.set_attributes({"mcp.agente": caller.agent, "mcp.resultado": outcome, "mcp.execucao": caller.run_id})
    if outcome != "ok":
        span.set_status(trace.Status(trace.StatusCode.ERROR, detail[:200]))
    log.info("tool=%s agente=%s execucao=%s resultado=%s ms=%d detalhe=%r", name, caller.agent, caller.run_id,
             outcome, round(seconds * 1000), detail[:200])


def build_server(governance: Governance) -> Server:
    """Servidor MCP de baixo nível: só os dois handlers, cada um lendo os cabeçalhos da
    requisição para saber quem chama."""

    def headers_of(ctx: Any) -> Mapping[str, str]:
        if ctx.request is None:  # transporte sem HTTP (stdio): aqui não há como identificar ninguém
            raise MCPError(_UNAUTHORIZED, "Este servidor só atende por HTTP, com identificação.")
        return ctx.request.headers

    async def on_list_tools(ctx: Any, params: Any) -> t.ListToolsResult:
        try:
            return t.ListToolsResult(tools=await governance.list_tools(headers_of(ctx)))
        except (AuthError, IndexerError) as exc:  # identidade recusada, ou indexador fora do ar
            raise MCPError(_UNAUTHORIZED, str(exc)) from exc

    async def on_call_tool(ctx: Any, params: t.CallToolRequestParams) -> t.CallToolResult:
        try:
            return await governance.call_tool(headers_of(ctx), params.name, params.arguments)
        except (AuthError, IndexerAuthError) as exc:
            return _result({"erro": str(exc)}, error=True)

    return Server("mcp-server", on_list_tools=on_list_tools, on_call_tool=on_call_tool)
