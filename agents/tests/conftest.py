"""Dublês para testar sem rede: um modelo com roteiro, um servidor MCP em memória e o
indexador de mentira. O A2A roda de verdade, em memória (cliente e servidor do SDK, por ASGI), e o
grafo usa checkpoint em memória."""
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self

import httpx
import jwt
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic_ai import FunctionToolset, Tool
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

from app.config import Settings
from app.especialistas.servico import create_app as create_specialists
from app.nucleo.executar import Engine
from app.nucleo.mcp import McpError
from app.nucleo.tokens import Identity
from app.orquestrador.api import create_app

SECRET_MCP = "segredo-do-mcp-para-os-testes-com-40-chars"
SECRET_A2A = "segredo-do-a2a-para-os-testes-com-40-chars"
ANA, BRUNO = "user-ana", "user-bruno"
DOC = "doc-brasal"
MODEL_NAME = "modelo-de-teste"


# --- modelo com roteiro ---------------------------------------------------------------------


def call(name: str, **arguments: Any) -> ToolCallPart:
    return ToolCallPart(name, arguments)


def say(text: str) -> TextPart:
    return TextPart(text)


def think(text: str) -> ThinkingPart:
    return ThinkingPart(content=text)


@dataclass
class Decide:
    """A decisão estruturada do orquestrador (a saída dele no grafo)."""

    delegacoes: list[dict] = field(default_factory=list)
    terminado: bool = False
    mensagem: str = "Feito."
    intencao: str = "pergunta"
    empresa: str | None = None


def delegation(agente: str, tarefa: str, doc_id: str | None = None) -> dict:
    return {"agente": agente, "tarefa": tarefa, "doc_id": doc_id}


def decide(*delegacoes: dict, terminado: bool = False, mensagem: str = "Feito.",
           intencao: str = "pergunta", empresa: str | None = None) -> Decide:
    return Decide(list(delegacoes), terminado, mensagem, intencao, empresa)


class ScriptedModel:
    """Um modelo que segue um roteiro por agente. O agente é reconhecido pelo prompt ("agente
    de teste <nome>"). Cada item do roteiro é a saída de uma chamada ao modelo: uma lista de
    partes (chamadas de tool, raciocínio, texto ou a decisão do orquestrador). Roteiro esgotado =
    repete o último item."""

    def __init__(self) -> None:
        self.scripts: dict[str, list[list[Any]]] = {}
        self.inputs: dict[str, list[list[ModelMessage]]] = {}  # o que cada agente recebeu, chamada a chamada
        self.prompts: dict[str, list[str]] = {}  # o pedido com que cada execução do agente começou
        self.tokens = (100, 10)  # entrada, saída de cada chamada
        self.model = FunctionModel(self._respond, model_name=MODEL_NAME)

    def _respond(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        agent = re.search(r"agente de teste (\w+)", info.instructions or "").group(1)
        self.inputs.setdefault(agent, []).append(list(messages))
        prompt = [part.content for part in messages[-1].parts if isinstance(part, UserPromptPart)]
        if prompt:
            self.prompts.setdefault(agent, []).append(prompt[-1])
        script = self.scripts[agent]
        output = script.pop(0) if len(script) > 1 else script[0]
        parts = []
        for item in output:
            if isinstance(item, Decide):
                parts.append(ToolCallPart(info.output_tools[0].name, {
                    "delegacoes": item.delegacoes, "terminado": item.terminado, "mensagem": item.mensagem,
                    "intencao": item.intencao, "empresa": item.empresa}))
            elif isinstance(item, ToolCallPart):  # cada chamada precisa de um id próprio, mesmo repetida
                parts.append(ToolCallPart(item.tool_name, item.args))
            else:
                parts.append(item)
        entrada, saida = self.tokens
        return ModelResponse(parts=parts, usage=RequestUsage(input_tokens=entrada, output_tokens=saida,
                                                             cache_read_tokens=entrada // 2))

    def first_input(self, agent: str, turn: int = 0) -> str:
        """O pedido com que a n-ésima execução do agente começou."""
        return self.prompts[agent][turn]


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

    def invoke(self, agent: str, identity: Identity, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Como o servidor: recusa o que a política não dá; o resto vai ao handler (que pode recusar)."""
        self.calls.append((agent, tool, arguments, identity))
        if tool not in self.policy.get(agent, []):
            raise Refused(f"A tool '{tool}' não está liberada para o agente '{agent}'.")
        return self.handlers.get(tool, lambda _: {"ok": True})(arguments)


class FakeMcp:
    """A conexão MCP de um agente, em memória: o toolset que o agente PydanticAI usa e as chamadas
    diretas do código. A recusa chega ao modelo como no servidor real (`ToolFailed`)."""

    def __init__(self, world: World, agent: str, identity: Identity) -> None:
        self.world, self.agent, self.identity = world, agent, identity
        self.toolset = FunctionToolset([
            Tool.from_schema(self._tool(name), name=name, description=f"Tool {name}.",
                             json_schema={"type": "object", "properties": {}, "additionalProperties": True})
            for name in world.policy.get(agent, [])])

    def _tool(self, name: str):
        def run(**arguments: Any) -> dict[str, Any]:
            try:
                return self.world.invoke(self.agent, self.identity, name, arguments)
            except Refused as error:
                raise ToolFailed(json.dumps({"erro": str(error)}, ensure_ascii=False)) from error
        return run

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def tool_names(self) -> list[str]:
        return list(self.world.policy.get(self.agent, []))

    async def call(self, tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            return self.world.invoke(self.agent, self.identity, tool, arguments or {})
        except Refused as error:
            raise McpError(f"{tool}: {error}") from error


# --- arquivos de agente ---------------------------------------------------------------------


def write_agent(folder: Path, nome: str, **header: Any) -> Path:
    fields = {"nome": nome, "descricao": f"Agente de teste que faz o papel de {nome} nos testes.",
              "modelo": MODEL_NAME, "max_turnos": 6, **header}
    head = "\n".join(f"{key}: {value}" for key, value in fields.items())
    path = folder / nome / f"{nome}.md"
    path.parent.mkdir(exist_ok=True)
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
                    agentes_dir=agents_dir, data_dir=tmp_path / "data", config_path=tmp_path / "ajustes.yaml",
                    precos={MODEL_NAME: {"entrada": 2.0, "entrada_cache": 1.0, "saida": 10.0}},
                    especialistas_url="http://especialistas.teste", indexer_url="http://indexador.teste")


@pytest.fixture
def world() -> World:
    return World()


@pytest.fixture
def model() -> ScriptedModel:
    scripted = ScriptedModel()
    scripted.scripts = {"orquestrador": [[decide(terminado=True)]], "extrator": [[say("Extração fechada.")]],
                        "redator": [[say("Relatório montado.")]]}
    return scripted


@pytest.fixture
def engine(settings, world, model) -> Engine:
    def resolve(spec) -> FunctionModel:
        assert spec.modelo == MODEL_NAME, spec.modelo
        return model.model

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
def checkpointer() -> InMemorySaver:
    return InMemorySaver()


@pytest.fixture
def api(settings, engine, specialists_app, checkpointer):
    """A API do orquestrador ligada aos especialistas em memória."""
    return create_app(settings, engine, specialists_transport=httpx.ASGITransport(app=specialists_app),
                      indexer_transport=httpx.MockTransport(fake_indexer), checkpointer=checkpointer, avaliar=None)


@pytest.fixture
async def http(api):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://agentes.teste") as client:
        yield client


def auth(user_id: str = ANA, minutes: float = 60) -> dict[str, str]:
    return {"Authorization": f"Bearer {user_token(user_id, minutes)}"}
