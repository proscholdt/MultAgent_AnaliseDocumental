"""Conexão de um agente com o servidor MCP.

Cada agente abre a SUA conexão, com o SEU token: é assim que o servidor sabe quem está chamando
e aplica a política. Não há conexão compartilhada entre agentes.

A mesma conexão serve aos dois usos:

- o agente PydanticAI recebe `sessao.toolset` e chama as tools pelo modelo;
- o código chama direto (`sessao.call`), sem modelo, para conferir pendências e ler o resultado.
"""
import json
from typing import Any, Protocol, Self

from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.toolsets import AbstractToolset

from app.config import Settings
from app.tokens import Identity, mcp_headers

# O próprio sistema, quando lê a base sem modelo no meio (conferir pendências, buscar o
# resultado). Na política do servidor ele só tem tools de leitura.
SYSTEM = "sistema"


class McpError(Exception):
    pass


class McpSession(Protocol):
    """O que o resto do código usa de uma conexão MCP. Os testes trocam por um dublê em memória."""

    toolset: AbstractToolset

    async def __aenter__(self) -> Self: ...

    async def __aexit__(self, *exc: object) -> None: ...

    async def tool_names(self) -> list[str]: ...

    async def call(self, tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]: ...


def refusal(error: Exception) -> str:
    """A mensagem de uma recusa do servidor ({"erro": ...}), sem o JSON em volta."""
    text = str(error)
    try:
        body = json.loads(text)
    except json.JSONDecodeError:
        return text
    return body.get("erro", text) if isinstance(body, dict) else text


class Connection:
    def __init__(self, toolset: MCPToolset) -> None:
        self.toolset = toolset

    async def __aenter__(self) -> Self:
        await self.toolset.__aenter__()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.toolset.__aexit__(*exc)

    async def tool_names(self) -> list[str]:
        """As tools que a política dá a este agente (o servidor só lista as dele)."""
        return [tool.name for tool in await self.toolset.list_tools()]

    async def call(self, tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Chama uma tool e devolve o JSON da resposta. Recusa do servidor vira McpError."""
        try:
            body = await self.toolset.direct_call_tool(tool, arguments or {})
        except ToolFailed as error:
            raise McpError(f"{tool}: {refusal(error)}") from error
        return body if isinstance(body, dict) else {"resultado": body}


def connect(settings: Settings, agent: str, identity: Identity) -> Connection:
    # tool_error_behavior="failed": a recusa de um portão volta ao modelo como resultado da tool,
    # para ele corrigir, sem gastar a cota de novas tentativas do PydanticAI. Os agentes recebem
    # dezenas de recusas numa execução; com "retry" (o padrão), a terceira derrubaria a execução.
    return Connection(MCPToolset(settings.mcp_url, headers=mcp_headers(settings, agent, identity),
                                 tool_error_behavior="failed", read_timeout=settings.mcp_timeout_seconds,
                                 id=f"mcp-{agent}"))
