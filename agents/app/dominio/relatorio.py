"""As regras do relatório e das perguntas que o orquestrador aplica, fora do grafo.

O grafo (`app/orquestrador/grafo.py`) só decide, delega e confere. O que é específico deste
domínio (quais intenções existem, o que é só do relatório, como uma das cinco perguntas é
reconhecida, quando uma delegação chega antes da hora) mora aqui, em funções puras.
"""
import re
import unicodedata
from typing import Any

from app import pacotes

# A intenção que o orquestrador dá a uma mensagem de conversa vira o tipo da execução, e o tipo
# diz o que a conferência final cobra: pergunta exige resposta gravada com fonte; relatório exige
# o relatório montado. O resto está fora do escopo: o código não executa nada e responde com um
# texto fixo e as opções. Nenhum texto livre do orquestrador chega ao usuário como resposta.
INTENT_TYPE = {"pergunta": "pergunta", "relatorio": "relatorio", "relatorio_empresa": "relatorio_empresa",
               "fora_do_escopo": "fora_do_escopo"}
REPORT_TYPES = ("relatorio", "relatorio_empresa")

# Numa pergunta, o que é só do relatório: o texto que vem depois de tudo pronto. Validador e
# analista podem servir a uma pergunta ("a empresa tem inconsistências?", "quais riscos..."), e
# ficam a critério do orquestrador.
REPORT_ONLY = {"redator": "os resumos e a montagem do relatório"}
ANSWERED = "Pergunta respondida, com as fontes."

# Uma das cinco perguntas do relatório é definida pelo id, não pelo texto que o orquestrador
# escreve: com o texto livre, o pesquisador chegou a responder a outra pergunta e gravar com o id
# de uma das cinco. Uma pergunta do chat igual a uma das cinco é gravada como 'livre', para não
# puxar a análise inteira (a p1 só é aceita com a extração e a análise de todos os documentos).
# Quais ids existem vem do servidor MCP (`ler_respostas`, que lê o pacote de domínio, dominios/<id>/dominio.yaml):
# aqui só se reconhece a forma "p<número>", e `official_task` descarta o id que não existe.
REPORT_QUESTION = re.compile(r"\bp(\d{1,2})\b", re.IGNORECASE)
FOR_THE_CHAT = ("\n\nÉ uma pergunta do usuário, não do relatório: grave com pergunta_id 'livre', mesmo que o texto "
                "seja igual ao de uma das cinco perguntas do relatório.")

def out_of_scope(dominio: str | None = None) -> tuple[str, list[dict[str, str]]]:
    """A resposta fixa a uma mensagem fora do escopo e as opções, as do pacote do domínio."""
    textos = pacotes.pacote(dominio).textos
    return " ".join(textos.fora_do_escopo.split()), [opcao.model_dump() for opcao in textos.opcoes]


# As do domínio padrão, com os nomes de antes dos pacotes.
OUT_OF_SCOPE, OPTIONS = out_of_scope()

_READY_IN_REASON = 6


def latin_only(text: str) -> str:
    """A mensagem final do orquestrador sem as palavras em outro alfabeto. O modelo pequeno solta
    uma de vez em quando ("Não մնou pendência"); no servidor MCP isso é recusado na gravação, mas a
    mensagem de estado não passa por lá."""
    def foreign(word: str) -> bool:
        return any(ch.isalpha() and ch not in "ªºµ" and "LATIN" not in unicodedata.name(ch, "") for ch in word)

    return " ".join(word for word in text.split(" ") if not foreign(word))


def _not_delegated(delegation: dict[str, Any], reason: str) -> dict[str, Any]:
    return {"agente": delegation["agente"], "estado": "nao_delegado", "doc_id": delegation.get("doc_id"),
            "erro": reason}


def hold_report_only(delegations: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Numa pergunta, o que é só do relatório não sai: volta ao orquestrador como relato."""
    go, held = [], []
    for delegation in delegations:
        what = REPORT_ONLY.get(delegation["agente"])
        if what is None:
            go.append(delegation)
        else:
            held.append(_not_delegated(delegation, f"não delegado: o pedido é uma pergunta, e {what} são do "
                                                   "relatório. A pergunta termina quando o pesquisador grava a "
                                                   "resposta."))
    return go, held


def held_reason(mine: list[dict[str, Any]], ready: list[dict[str, Any]]) -> str:
    """Por que a delegação não saiu e o que delegar no lugar dela."""
    waits = list(dict.fromkeys(item["aguarda"] for item in mine if item.get("aguarda")))
    text = "não delegado: chegou antes da hora. Antes disso falta: " + "; ".join(waits) + "."
    if ready:
        options = [f"{item['agente']}: {item['o_que_falta']}" + (f" ({item['empresa']})" if item.get("empresa") else "")
                   for item in ready[:_READY_IN_REASON]]
        text += " O que já pode ser delegado: " + "; ".join(options) + "."
    return text


def hold_early(delegations: list[dict[str, Any]], pending: list[dict[str, Any]]
               ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Separa as delegações que chegam antes da hora, pela lista de pendências do servidor MCP.
    A delegação cujos itens estão todos aguardando não sai; a que não tem item na lista (refazer
    algo, uma pergunta livre) segue."""
    ready = [item for item in pending if item.get("liberada", True)]
    go, held = [], []
    for delegation in delegations:
        mine = [item for item in pending if item.get("agente") == delegation["agente"]
                and (delegation.get("doc_id") is None or item.get("doc_id") in (None, delegation.get("doc_id")))]
        if mine and not any(item.get("liberada", True) for item in mine):
            held.append(_not_delegated(delegation, held_reason(mine, ready)))
        else:
            go.append(delegation)
    return go, held


def report_question_ids(task: str) -> list[str]:
    """As perguntas do relatório (p1, p2...) citadas numa tarefa, na ordem, sem repetir."""
    return list(dict.fromkeys(f"p{number}" for number in REPORT_QUESTION.findall(task)))


def official_task(item: dict[str, Any], questions: dict[str, str]) -> dict[str, Any]:
    """A tarefa do pesquisador que cita uma das cinco perguntas passa a ser o enunciado oficial
    dela. Com mais de uma citada, os enunciados vão junto da tarefa."""
    known = [(question_id, questions[question_id]) for question_id in report_question_ids(item["tarefa"])
             if question_id in questions]
    if not known:
        return item
    if len(known) == 1:
        question_id, text = known[0]
        task = (f"Responda à pergunta {question_id} do relatório, com pergunta_id '{question_id}'. Enunciado "
                f"oficial: «{text}». Responda exatamente a este enunciado.")
    else:
        task = (item["tarefa"] + "\n\nEnunciados oficiais (responda a eles, não a outra formulação): "
                + "; ".join(f"{question_id}: «{text}»" for question_id, text in known))
    return {**item, "tarefa": task, "tarefa_do_orquestrador": item["tarefa"]}
