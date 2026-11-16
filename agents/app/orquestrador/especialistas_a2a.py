"""O lado do orquestrador no A2A: descobrir os especialistas e chamar cada um.

O orquestrador decide quem chamar e com que tarefa; o nó `delegar` do grafo faz a chamada, uma
por delegação, em paralelo. O protocolo é só o cabo.

Cada especialista pode morar no próprio serviço: o endereço vem de `especialistas_enderecos`
e, para quem não está lá, de `especialistas_url` (um serviço que atende todos). O catálogo é
montado perguntando a cada serviço; um serviço fora do ar tira só os especialistas dele da
execução, e o resto da equipe trabalha.
"""
import asyncio
import hashlib
import uuid
from typing import Any

import httpx
from a2a import helpers
from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.types import Message, Role, SendMessageRequest, TaskState
from google.protobuf.json_format import MessageToDict

from app.config import Settings
from app.nucleo.executar import Budget
from app.nucleo.tokens import CATALOG, Identity, a2a_headers


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
        self.enderecos = {nome: url.rstrip("/") for nome, url in settings.especialistas_enderecos.items()}
        self.base = settings.especialistas_url.rstrip("/")

    def address(self, nome: str) -> str:
        """Onde o especialista atende: o endereço próprio ou, sem ele, o serviço de todos."""
        return self.enderecos.get(nome) or self.base

    def services(self) -> list[str]:
        """Os serviços de especialistas a consultar, sem repetição, na ordem da configuração."""
        return list(dict.fromkeys([*self.enderecos.values(), *([self.base] if self.base else [])]))

    def _http(self, target: str) -> httpx.AsyncClient:
        # Um token por destinatário: o emitido para o extrator não serve no analista.
        return httpx.AsyncClient(headers=a2a_headers(self.settings, target, self.identity), transport=self.transport,
                                 timeout=self.settings.especialista_timeout_seconds)

    async def _catalog_of(self, service: str) -> dict[str, Any]:
        async with self._http(CATALOG) as http:
            response = await http.get(f"{service}/agentes")
        if response.status_code != 200:
            raise A2AError(f"{service} respondeu {response.status_code}: {response.text[:200]}")
        return response.json()

    async def catalog(self) -> dict[str, Any]:
        """Os especialistas no ar, juntando o catálogo de cada serviço. Cada agente vale pelo
        serviço que é o endereço dele (o serviço de todos também o lista, mas não é ele quem
        atende). Serviço fora do ar vai em `fora_do_ar`; se nenhum responder, é erro."""
        services = self.services()
        if not services:
            raise A2AError("nenhum endereço de especialista configurado (ESPECIALISTAS_URL ou ESPECIALISTAS_ENDERECOS)")
        results = await asyncio.gather(*(self._catalog_of(service) for service in services), return_exceptions=True)
        agentes: dict[str, dict[str, Any]] = {}
        invalidos: dict[str, str] = {}
        digests, fora = [], []
        for service, result in zip(services, results, strict=True):
            if isinstance(result, BaseException):
                fora.append(f"{service}: {type(result).__name__}: {result}"[:300])
                continue
            digests.append(result["digest"])
            invalidos.update(result["arquivos_invalidos"])
            for entry in result["agentes"]:
                if self.address(entry["nome"]) == service:
                    agentes.setdefault(entry["nome"], entry)
        if not digests:
            raise A2AError("nenhum serviço de especialista respondeu: " + "; ".join(fora))
        digest = digests[0] if len(digests) == 1 else hashlib.sha256("|".join(digests).encode()).hexdigest()[:16]
        return {"digest": digest, "agentes": list(agentes.values()), "arquivos_invalidos": invalidos, "fora_do_ar": fora}

    async def call(self, nome: str, tarefa: str, doc_id: str | None, budget: Budget,
                   conversa_id: str | None = None) -> dict[str, Any]:
        """Manda a tarefa e espera o relato. A chamada bloqueia até o especialista terminar. A
        conversa vai junto: é a chave da memória do especialista."""
        async with self._http(nome) as http:
            card = await A2ACardResolver(http, f"{self.address(nome)}/agentes/{nome}").get_agent_card()
            client = await create_client(card, client_config=ClientConfig(streaming=False, httpx_client=http))
            data = {"doc_id": doc_id, "conversa_id": conversa_id,
                    "orcamento": {"tokens": budget.tokens_left, "segundos": budget.seconds_left}}
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


async def delegate(client: SpecialistClient, nome: str, tarefa: str, doc_id: str | None, budget: Budget,
                   conversa_id: str | None = None) -> dict[str, Any]:
    """Uma delegação. Falha de transporte ou de tarefa vira um relato de erro: o orquestrador o
    recebe e decide o que fazer, em vez de a execução cair."""
    try:
        return await client.call(nome, tarefa, doc_id, budget, conversa_id)
    except Exception as error:  # noqa: BLE001
        return {"agente": nome, "estado": "erro", "doc_id": doc_id, "erro": f"{type(error).__name__}: {error}"[:400]}


def for_the_model(report: dict[str, Any]) -> dict[str, Any]:
    """O relato que o orquestrador vê: sem os passos, os tokens e as mensagens do especialista,
    que ficam no trace e nos arquivos da execução."""
    return {key: value for key, value in report.items() if key != "execucao"}
