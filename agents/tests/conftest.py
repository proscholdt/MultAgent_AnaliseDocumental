"""Dublês para testar sem rede: um modelo com roteiro, um servidor MCP em memória e o
indexador de mentira. O A2A roda de verdade, em memória (cliente e servidor do SDK, por ASGI)."""
import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import jwt
import mcp.types as mcp_types
import pytest
from agents import ModelResponse, Usage
from agents.mcp import MCPServer
from agents.models.interface import Model
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText
from openai.types.responses.response_usage import InputTokensDetails, OutputTokensDetails

from app.config import Settings
from app.especialistas import create_app as create_specialists
from app.main import create_app
from app.runner import Engine
from app.tokens import Identity

SECRET_MCP = "segredo-do-mcp-para-os-testes-com-40-chars"
SECRET_A2A = "segredo-do-a2a-para-os-testes-com-40-chars"
ANA, BRUNO = "user-ana", "user-bruno"
DOC = "doc-brasal"
MODEL_NAME = "modelo-de-teste"


# --- modelo com roteiro ---------------------------------------------------------------------


def call(name: str, **arguments: Any) -> ResponseFunctionToolCall:
    stamp = f"{name}-{time.monotonic_ns()}"
    return ResponseFunctionToolCall(id=f"fc-{stamp}", call_id=f"call-{stamp}", name=name, type="function_call",
                                    arguments=json.dumps(arguments, ensure_ascii=False))


def say(text: str) -> ResponseOutputMessage:
    return ResponseOutputMessage(id=f"msg-{time.monotonic_ns()}", role="assistant", status="completed", type="message",
                                 content=[ResponseOutputText(text=text, type="output_text", annotations=[])])


class ScriptedModel(Model):
    """Um modelo que segue um roteiro por agente. O agente é reconhecido pelo prompt ("agente
    de teste <nome>"). Cada item do roteiro é a saída de uma chamada ao modelo: uma lista de
    chamadas de tool e/ou uma mensagem final. Roteiro esgotado = repete o último item."""

    def __init__(self) -> None:
        self.scripts: dict[str, list[list[Any]]] = {}
        self.inputs: dict[str, list[Any]] = {}  # o que cada agente recebeu, chamada a chamada
        self.tokens = (100, 10)  # entrada, saída de cada chamada

    async def get_response(self, system_instructions, input, model_settings, tools, output_schema, handoffs, tracing,
                           **_kwargs) -> ModelResponse:
        agent = re.search(r"agente de teste (\w+)", system_instructions or "").group(1)
        self.inputs.setdefault(agent, []).append(input)
        script = self.scripts[agent]
        output = script.pop(0) if len(script) > 1 else script[0]
        # Cada chamada de tool precisa de um id próprio, mesmo quando o roteiro repete o item.
        output = [call(item.name, **json.loads(item.arguments)) if isinstance(item, ResponseFunctionToolCall) else item
                  for item in output]
        entrada, saida = self.tokens
        usage = Usage(requests=1, input_tokens=entrada, output_tokens=saida, total_tokens=entrada + saida,
                      input_tokens_details=InputTokensDetails.model_construct(cached_tokens=entrada // 2),
                      output_tokens_details=OutputTokensDetails.model_construct(reasoning_tokens=0))
        return ModelResponse(output=list(output), usage=usage, response_id=None)

    def stream_response(self, *args, **kwargs):
        raise NotImplementedError

    def first_input(self, agent: str, turn: int = 0) -> str:
        """O texto do pedido que o agente recebeu na sua n-ésima execução."""
        starts = [entry for entry in self.inputs[agent] if isinstance(entry, str) or len(entry) == 1]
        entry = starts[turn]
        return entry if isinstance(entry, str) else entry[0]["content"]


# --- servidor MCP em memória ----------------------------------------------------------------


class Refused(Exception):
    pass


class World:
    """O servidor MCP de mentira: a política (agente -> tools), o que cada tool responde e o
    registro de quem chamou o quê."""

    def __init__(self) -> None:
        self.policy: dict[str, list[str]] = {
            "orquestrador": ["listar_documentos", "pendencias"],
            "extrator": ["buscar_trechos", "salvar_indicador", "fechar_extracao"],
            "redator": ["salvar_resumo", "montar_relatorio"],
            "sistema": ["pendencias", "ler_relatorio", "ler_respostas"],
        }
        self.pending: list[dict[str, Any]] = []
        self.answers: list[dict[str, Any]] = []
        self.report: str | None = None
        self.calls: list[tuple[str, str, dict[str, Any], Identity]] = []
        self.handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "listar_documentos": lambda _: {"documentos": [{"doc_id": DOC, "empresa": "Brasal"}]},
            "pendencias": lambda _: {"completo": not self.pending, "pendencias": list(self.pending)},
            "ler_relatorio": lambda _: {"relatorio": self.report, "gerado_em": "2026-11-13T12:00:00+00:00"},
            "ler_respostas": lambda _: {"respostas": list(self.answers)},
        }

    def on(self, tool: str, handler: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        self.handlers[tool] = handler

    def called(self, agent: str) -> list[str]:
        return [tool for who, tool, _, _ in self.calls if who == agent]


class FakeMcp(MCPServer):
    def __init__(self, world: World, agent: str, identity: Identity) -> None:
        super().__init__()
        self.world, self.agent, self.identity = world, agent, identity

    @property
    def name(self) -> str:
        return f"mcp:{self.agent}"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc) -> None:
        return None

    async def connect(self) -> None:
        return None

    async def cleanup(self) -> None:
        return None

    async def list_tools(self, run_context=None, agent=None) -> list[mcp_types.Tool]:
        return [mcp_types.Tool(name=name, description=f"Tool {name}.",
                               input_schema={"type": "object", "properties": {}, "additionalProperties": True})
                for name in self.world.policy.get(self.agent, [])]

    async def call_tool(self, tool_name: str, arguments: dict[str, Any] | None, meta=None) -> mcp_types.CallToolResult:
        arguments = arguments or {}
        self.world.calls.append((self.agent, tool_name, arguments, self.identity))
        try:
            if tool_name not in self.world.policy.get(self.agent, []):
                raise Refused(f"A tool '{tool_name}' não está liberada para o agente '{self.agent}'.")
            body, failed = self.world.handlers.get(tool_name, lambda _: {"ok": True})(arguments), False
        except Refused as error:
            body, failed = {"erro": str(error)}, True
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text=json.dumps(body, ensure_ascii=False))], is_error=failed)

    async def list_prompts(self):
        raise NotImplementedError

    async def get_prompt(self, name, arguments=None):
        raise NotImplementedError


# --- arquivos de agente ---------------------------------------------------------------------


def write_agent(folder: Path, nome: str, **header: Any) -> Path:
    fields = {"nome": nome, "descricao": f"Agente de teste que faz o papel de {nome} nos testes.",
              "modelo": "padrao", "max_turnos": 6, **header}
    head = "\n".join(f"{key}: {value}" for key, value in fields.items())
    path = folder / f"{nome}.md"
    path.write_text(f"---\n{head}\n---\nVocê é o agente de teste {nome}. Siga o roteiro do teste, passo a passo.\n",
                    encoding="utf-8")
    return path


# --- fixtures -------------------------------------------------------------------------------


@pytest.fixture
def agents_dir(tmp_path) -> Path:
    folder = tmp_path / "agentes"
    folder.mkdir()
    for nome in ("orquestrador", "extrator", "redator"):
        write_agent(folder, nome)
    return folder


@pytest.fixture
def settings(tmp_path, agents_dir) -> Settings:
    return Settings(_env_file=None, openai_api_key="sk-teste", mcp_agent_secret=SECRET_MCP, a2a_secret=SECRET_A2A,
                    agentes_dir=agents_dir, data_dir=tmp_path / "data", modelos={"padrao": MODEL_NAME},
                    precos={MODEL_NAME: {"entrada": 2.0, "entrada_cache": 1.0, "saida": 10.0}},
                    especialistas_url="http://especialistas.teste", indexer_url="http://indexador.teste")


@pytest.fixture
def world() -> World:
    return World()


@pytest.fixture
def model() -> ScriptedModel:
    scripted = ScriptedModel()
    scripted.scripts = {"orquestrador": [[say("Feito.")]], "extrator": [[say("Extração fechada.")]],
                        "redator": [[say("Relatório montado.")]]}
    return scripted


@pytest.fixture
def engine(settings, world, model) -> Engine:
    def resolve(alias: str) -> Model:
        if alias not in settings.modelos:
            raise KeyError(f"o apelido de modelo '{alias}' não está em MODELOS")
        return model

    return Engine(settings, lambda agent, identity: FakeMcp(world, agent, identity), resolve)


@pytest.fixture
def identity() -> Identity:
    return Identity(run_id="exec-teste", user_id=ANA, user_token="tok-ana")


@pytest.fixture
def specialists_app(settings, engine):
    return create_specialists(settings, engine)


def user_token(user_id: str, minutes: float = 60) -> str:
    """Um JWT com a forma do do indexador. Quem o "confere" é o indexador de mentira."""
    return jwt.encode({"sub": user_id, "exp": int(time.time() + minutes * 60)}, "segredo-do-indexador-nos-testes-0123456789")


def fake_indexer(request: httpx.Request) -> httpx.Response:
    token = request.headers.get("authorization", "")[7:]
    try:
        claims = jwt.decode(token, "segredo-do-indexador-nos-testes-0123456789", algorithms=["HS256"])
    except jwt.PyJWTError:
        return httpx.Response(401, json={"detail": "Token inválido"})
    return httpx.Response(200, json={"user_id": claims["sub"], "username": claims["sub"]})


@pytest.fixture
def api(settings, engine, specialists_app):
    """A API do orquestrador ligada aos especialistas em memória."""
    return create_app(settings, engine, specialists_transport=httpx.ASGITransport(app=specialists_app),
                      indexer_transport=httpx.MockTransport(fake_indexer))


@pytest.fixture
async def http(api):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://agentes.teste") as client:
        yield client


def auth(user_id: str = ANA, minutes: float = 60) -> dict[str, str]:
    return {"Authorization": f"Bearer {user_token(user_id, minutes)}"}
