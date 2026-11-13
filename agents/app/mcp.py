"""Conexão de um agente com o servidor MCP.

Cada agente abre a SUA conexão, com o SEU token: é assim que o servidor sabe quem está chamando
e aplica a política. Não há conexão compartilhada entre agentes.
"""
import json
from typing import Any

from agents.mcp import MCPServer, MCPServerStreamableHttp

from app.config import Settings
from app.tokens import Identity, mcp_headers

# O próprio sistema, quando lê a base sem modelo no meio (conferir pendências, buscar o
# resultado). Na política do servidor ele só tem tools de leitura.
SYSTEM = "sistema"


class McpError(Exception):
    pass


def connect(settings: Settings, agent: str, identity: Identity) -> MCPServer:
    return MCPServerStreamableHttp(
        name=f"mcp:{agent}",
        params={"url": settings.mcp_url, "headers": mcp_headers(settings, agent, identity),
                "timeout": settings.mcp_timeout_seconds},
        cache_tools_list=True,
        client_session_timeout_seconds=settings.mcp_timeout_seconds,
    )


async def tool_names(server: MCPServer) -> list[str]:
    """As tools que a política dá a este agente (o servidor só lista as dele)."""
    return [tool.name for tool in await server.list_tools()]


async def call(server: MCPServer, tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """Chama uma tool e devolve o JSON da resposta. Recusa do servidor vira McpError."""
    result = await server.call_tool(tool, arguments or {})
    text = next((part.text for part in result.content if getattr(part, "text", None)), "")
    try:
        body = json.loads(text)
    except json.JSONDecodeError:
        body = {"erro": text or "resposta vazia"}
    if getattr(result, "is_error", None) or getattr(result, "isError", False):
        raise McpError(f"{tool}: {body.get('erro', text)}")
    return body
