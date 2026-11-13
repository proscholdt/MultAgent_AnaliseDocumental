"""O lado do orquestrador no A2A: descobrir os especialistas e chamar cada um.

Cada especialista entra no orquestrador como UMA tool, cujo corpo é uma chamada A2A. O
orquestrador decide quem chamar e com que tarefa; o protocolo é só o cabo.
"""
import json
import uuid
from collections.abc import Callable
from typing import Any

import httpx
from a2a import helpers
from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.types import Message, Role, SendMessageRequest, TaskState
from agents import FunctionTool
from google.protobuf.json_format import MessageToDict

from app.config import Settings
from app.runner import Budget
from app.tokens import CATALOG, Identity, a2a_headers

_PARAMS = {
    "type": "object", "additionalProperties": False, "required": ["tarefa", "doc_id"],
    "properties": {
        "tarefa": {"type": "string", "description": "O que o especialista deve fazer, em uma ou duas frases."},
        "doc_id": {"type": ["string", "null"], "description": "Documento em que ele deve trabalhar. null quando a "
                   "tarefa não é sobre um documento só."},
    },
}


class A2AError(Exception):
    pass


def _plain(value: Any) -> Any:
    """Os dados viajam como JSON genérico, em que todo número é decimal: 1234.0 volta a ser 1234."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


class SpecialistClient:
    def __init__(self, settings: Settings, identity: Identity,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings, self.identity, self.transport = settings, identity, transport
        self.base = settings.especialistas_url.rstrip("/")

    def _http(self, target: str) -> httpx.AsyncClient:
        # Um token por destinatário: o emitido para o extrator não serve no analista.
        return httpx.AsyncClient(headers=a2a_headers(self.settings, target, self.identity), transport=self.transport,
                                 timeout=self.settings.especialista_timeout_seconds)

    async def catalog(self) -> dict[str, Any]:
        async with self._http(CATALOG) as http:
            response = await http.get(f"{self.base}/agentes")
        if response.status_code != 200:
            raise A2AError(f"o serviço dos especialistas respondeu {response.status_code}: {response.text[:200]}")
        return response.json()

    async def call(self, nome: str, tarefa: str, doc_id: str | None, budget: Budget) -> dict[str, Any]:
        """Manda a tarefa e espera o relato. A chamada bloqueia até o especialista terminar."""
        async with self._http(nome) as http:
            card = await A2ACardResolver(http, f"{self.base}/agentes/{nome}").get_agent_card()
            client = await create_client(card, client_config=ClientConfig(streaming=False, httpx_client=http))
            data = {"doc_id": doc_id, "orcamento": {"tokens": budget.tokens_left, "segundos": budget.seconds_left}}
            message = Message(message_id=uuid.uuid4().hex, role=Role.ROLE_USER,
                              parts=[helpers.new_text_part(tarefa), helpers.new_data_part(data)])
            task = None
            async for event in client.send_message(SendMessageRequest(message=message)):
                if event.HasField("task"):
                    task = event.task
        if task is None:
            raise A2AError(f"o especialista '{nome}' não devolveu uma tarefa")
        if task.status.state != TaskState.TASK_STATE_COMPLETED:
            reason = helpers.get_message_text(task.status.message) if task.status.HasField("message") else ""
            raise A2AError(f"a tarefa do especialista '{nome}' terminou como "
                           f"{TaskState.Name(task.status.state)}: {reason}"[:400])
        for artifact in task.artifacts:
            for part in artifact.parts:
                if part.HasField("data"):
                    return _plain(MessageToDict(part.data))
        raise A2AError(f"o especialista '{nome}' terminou sem relato")


def specialist_tool(entry: dict[str, Any], client: SpecialistClient, budget: Budget,
                    on_report: Callable[[dict[str, Any], str | None], None]) -> FunctionTool:
    """Uma tool do orquestrador = um especialista remoto. Nome e descrição vêm do catálogo
    (isto é, do arquivo do agente)."""
    nome = entry["nome"]

    async def invoke(_context: Any, arguments: str) -> str:
        args = json.loads(arguments or "{}")
        doc_id = args.get("doc_id") or None
        try:
            report = await client.call(nome, str(args.get("tarefa") or ""), doc_id, budget)
        except Exception as error:  # noqa: BLE001 - o orquestrador recebe a falha e decide o que fazer
            report = {"agente": nome, "estado": "erro", "doc_id": doc_id, "erro": f"{type(error).__name__}: {error}"[:400]}
        on_report(report, doc_id)
        # O modelo vê o relato; os passos e os tokens do especialista ficam no trace.
        return json.dumps({key: value for key, value in report.items() if key != "execucao"}, ensure_ascii=False)

    return FunctionTool(name=nome, description=entry["descricao"], params_json_schema=_PARAMS,
                        on_invoke_tool=invoke, strict_json_schema=True)
