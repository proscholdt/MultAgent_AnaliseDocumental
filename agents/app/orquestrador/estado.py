"""O que o grafo carrega: a decisão estruturada do orquestrador, o estado (checkpoint) e as dependências."""
import operator
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from pydantic import BaseModel, Field

from app.config import Settings
from app.nucleo.agentes import Registry
from app.nucleo.executar import Engine
from app.nucleo.tokens import Identity
from app.orquestrador.especialistas_a2a import SpecialistClient
from app.orquestrador.execucoes import Runs
from app.orquestrador.intencao import Roteador


class Delegacao(BaseModel):
    agente: str = Field(description="Nome do especialista, como está na lista de especialistas.")
    tarefa: str = Field(description="O que ele deve fazer, em uma ou duas frases.")
    doc_id: str | None = Field(description="Documento em que ele deve trabalhar; null se a tarefa não é sobre um "
                               "documento só.")

class Decisao(BaseModel):
    delegacoes: list[Delegacao] = Field(description="As delegações desta onda, executadas em paralelo. Vazia quando "
                                        "não há mais o que delegar.")
    terminado: bool = Field(description="true quando o pedido está atendido (ou não há mais o que fazer).")
    mensagem: str = Field(description="Para o usuário, em duas ou três frases: o que foi feito e o que ficou pendente. "
                          "Sem valores dos documentos.")
    intencao: Literal["pergunta", "relatorio", "relatorio_documento", "fora_do_escopo"] = Field(
        description="O que o pedido atual do usuário é: 'pergunta' (quer saber algo do conteúdo dos documentos), "
                    "'relatorio' (quer o relatório consolidado, de todos os documentos), 'relatorio_documento' (quer o "
                    "relatório ou a análise completa de UM documento) ou 'fora_do_escopo' (qualquer outra coisa).")
    documento: str | None = Field(
        description="Só em 'relatorio_documento': o doc_id do documento pedido, como vem em listar_documentos. "
                    "null nas outras intenções.")

@dataclass
class Deps:
    settings: Settings
    registry: Registry
    engine: Engine
    runs: Runs
    specialists: Callable[[Identity], SpecialistClient]
    checkpointer: BaseCheckpointSaver | None = None
    # A nota ao fim de um relatório (app/ferramentas/avaliacao_automatica.py). Os testes trocam.
    avaliar: Callable[[Settings, Identity, str, str | None], Awaitable[dict | None]] | None = None
    # A intenção das mensagens do chat por roteamento semântico (intencao.py). Sem ele, o LLM decide.
    roteador: Roteador | None = None

class RunFailed(Exception):
    pass

def _relatos(old: list[dict], new: Any) -> list[dict]:
    """Os relatos da onda atual. O orquestrador zera a lista ao ler (`{"substituir": [...]}`); cada
    delegação acrescenta o seu."""
    if isinstance(new, dict) and "substituir" in new:
        return list(new["substituir"])
    return (old or []) + list(new or [])

class Estado(TypedDict, total=False):
    # Da conversa: fica no checkpoint de um turno para o outro.
    turnos: Annotated[list[dict], operator.add]
    # Da execução atual: o pedido de cada turno zera estes campos.
    execucao_id: str
    entrada: str | None  # o próximo pedido ao orquestrador; None = os relatos da onda
    historico: list[dict]  # as mensagens do orquestrador nesta rodada
    relatos: Annotated[list[dict], _relatos]
    decisao: dict | None
    ondas: int
    rodada: int
    parada: str | None  # um freio parou o orquestrador
    falha: str | None
    completo: bool
    pendencias: list[dict]
    resultado: dict
    mensagem: str
