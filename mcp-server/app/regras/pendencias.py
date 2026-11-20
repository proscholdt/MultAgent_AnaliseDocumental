"""O que já existe na base para um usuário e o que ainda falta para o relatório.

A lista de pendências é uma regra só, usada em três lugares: pelo orquestrador durante a
execução (para saber o que delegar), pelo sistema dos agentes no fim (para conferir se nada
ficou para trás) e pelo `montar_relatorio` (que não monta relatório incompleto).

A ordem do trabalho também está aqui, e não na boa vontade do orquestrador. Cada pendência diz
se está `liberada` ou o que ela `aguarda`: as perguntas do relatório comparam os documentos, então só
podem ser respondidas com a extração e a análise de todos os documentos; o resumo de uma
empresa usa a extração, a análise e os alertas dela. As tools que gravam recusam o que chega
fora de ordem (na primeira execução real, o orquestrador mandou responder as perguntas enquanto
duas extrações ainda rodavam, e as respostas saíram dizendo "sem extração publicada").

O que foi escrito antes de a base mudar volta para a lista: resposta ou resumo mais antigo que
a extração ou a análise de que depende está desatualizado.

Este módulo é puro: recebe o estado já lido. Quem lê a base e o indexador é
`app.casos_de_uso.pendencias`.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.dominio.documento import IndexedDocument
from app.dominio.erros import ToolRejected
from app.regras.campos import CATEGORIES, QUESTIONS

REVIEW_DONE = "revisao_das_fontes_concluida"
# Candidato a inconsistência que o validador julgou não ser erro, com o motivo dele.
DISCARDED = "candidato_descartado"
# Registros da revisão que não são alertas para o analista: ficam fora do relatório.
INTERNAL_ALERTS = (REVIEW_DONE, DISCARDED)


def latest(*moments: datetime | None) -> datetime | None:
    return max((moment for moment in moments if moment is not None), default=None)


@dataclass
class DocState:
    document: IndexedDocument
    extraction: dict[str, Any] | None  # JSON publicado, se é do índice atual
    analysis: dict[str, list[dict[str, Any]]]
    alerts: list[dict[str, Any]]
    summary: str | None
    extraction_at: datetime | None = None
    analysis_at: datetime | None = None  # o item de análise mais novo
    alerts_at: datetime | None = None  # o alerta mais novo (a conclusão da revisão é um deles)
    summary_at: datetime | None = None
    # Com dois documentos da mesma empresa (2022 e 2023), o nome leva o período: "Alfa S.A. (2023)".
    label: str | None = None

    @property
    def name(self) -> str:
        return self.label or self.document.company or self.document.file_name

    @property
    def reviewed(self) -> bool:
        return any(alert["codigo"] == REVIEW_DONE for alert in self.alerts)

    @property
    def missing_categories(self) -> list[str]:
        return [categoria for categoria in CATEGORIES if not self.analysis.get(categoria)]

    @property
    def newest(self) -> datetime | None:
        """O que foi gravado por último sobre este documento."""
        return latest(self.extraction_at, self.analysis_at, self.alerts_at, self.summary_at)

    @property
    def summary_is_stale(self) -> bool:
        base = latest(self.extraction_at, self.analysis_at, self.alerts_at)
        return self.summary_at is not None and base is not None and self.summary_at < base


def question_blockers(states: list[DocState]) -> list[str]:
    """O que falta para as perguntas do relatório poderem ser respondidas."""
    blockers = []
    for state in states:
        if state.extraction is None:
            blockers.append(f"extração dos campos de {state.name}")
        if state.missing_categories:
            blockers.append(f"análise qualitativa de {state.name} ({', '.join(state.missing_categories)})")
    return blockers


def summary_blockers(state: DocState) -> list[str]:
    """O que falta para o resumo executivo de um documento poder ser escrito."""
    blockers = []
    if state.extraction is None:
        blockers.append(f"extração dos campos de {state.name}")
    elif not state.reviewed:
        blockers.append(f"revisão das fontes de {state.name}")
    if state.missing_categories:
        blockers.append(f"análise qualitativa de {state.name} ({', '.join(state.missing_categories)})")
    return blockers


def _item(what: str, agent: str, state: DocState | None = None, waits: list[str] | None = None,
          **extra: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {"o_que_falta": what, "agente": agent, "liberada": not waits, **extra}
    if waits:
        entry["aguarda"] = "; ".join(waits)
    if state is not None:
        entry.update(doc_id=state.document.doc_id, entidade=state.name)
    return entry


def select_states(states: list[DocState], doc_id: str | None) -> list[DocState]:
    """Os estados que contam: todos, ou só o do documento pedido (que tem de existir)."""
    if doc_id is None:
        return states
    return [state for state in states if state.document.doc_id == doc_id] or _unknown(doc_id, states)


def pending_work(states: list[DocState], answers: dict[str, Any], *, doc_id: str | None = None) -> list[dict[str, Any]]:
    """O que falta antes do relatório, em ordem de execução. Cada item diz qual agente costuma
    resolver e se já pode ser feito. Com `doc_id`, é o que falta para o relatório daquela empresa:
    a extração, a revisão, a análise e o resumo dela, sem as perguntas comparativas.

    `answers` é pergunta_id → resposta gravada (com o atributo `at`)."""
    items: list[dict[str, Any]] = []
    for state in states:
        if state.extraction is None:
            items.append(_item("extração dos campos (fechar_extracao)", "extrator", state))
    for state in states:
        if not state.reviewed:
            waits = [f"extração dos campos de {state.name}"] if state.extraction is None else None
            items.append(_item("revisão das fontes em busca de inconsistências (concluir_revisao)", "validador",
                               state, waits))
    for state in states:
        if state.missing_categories:
            items.append(_item("análise qualitativa", "analista", state, categorias=state.missing_categories))

    blockers = question_blockers(states)
    base_changed = latest(*(moment for state in states for moment in (state.extraction_at, state.analysis_at)))
    for question_id, question in QUESTIONS.items() if doc_id is None else ():
        answer = answers.get(question_id)
        if answer is None:
            items.append(_item("resposta à pergunta", "pesquisador", waits=blockers, pergunta_id=question_id,
                               pergunta=question))
        elif blockers or (base_changed is not None and answer.at < base_changed):
            items.append(_item("refazer a resposta: a extração ou a análise mudou depois dela", "pesquisador",
                               waits=blockers, pergunta_id=question_id, pergunta=question))

    for state in states:
        if state.summary is None:
            items.append(_item("resumo executivo (salvar_resumo)", "redator", state, summary_blockers(state)))
        elif state.summary_is_stale:
            items.append(_item("refazer o resumo executivo: a base do documento mudou depois dele (salvar_resumo)",
                               "redator", state, summary_blockers(state)))
    return items


def company_report_pending(state: DocState, report: Any | None) -> list[dict[str, Any]]:
    """Com o resto pronto: falta montar (ou remontar) o relatório de um documento?"""
    if report is None or report.fingerprint != state.document.fingerprint:
        return [_item("montagem do relatório do documento (montar_relatorio_documento)", "redator", state)]
    if state.newest is not None and report.at < state.newest:
        return [_item("remontar o relatório do documento: há dados mais novos que ele (montar_relatorio_documento)",
                      "redator", state)]
    return []


def report_pending(states: list[DocState], answers: dict[str, Any], report: Any | None) -> list[dict[str, Any]]:
    """Com o resto pronto: falta montar (ou remontar) o relatório consolidado?"""
    # Só as perguntas do pacote entram no relatório: uma pergunta livre do usuário respondida depois
    # não o deixa desatualizado (antes, cada pergunta de conversa fazia remontar o relatório).
    newest = latest(*(state.newest for state in states),
                     *(answer.at for question_id, answer in answers.items() if question_id in QUESTIONS))
    if report is None:
        return [_item("montagem do relatório (montar_relatorio)", "redator")]
    if newest is not None and report.at < newest:
        return [_item("remontar o relatório: há dados mais novos que ele (montar_relatorio)", "redator")]
    return []


def _unknown(doc_id: str, states: list[DocState]) -> list[DocState]:
    raise ToolRejected(f"Não existe o documento '{doc_id}' indexado para este usuário.",
                       opcoes=[f"{state.document.doc_id} ({state.name})" for state in states])


def _company_key(state: DocState) -> str:
    return " ".join((state.document.company or state.document.file_name).casefold().split())


def label_periods(states: list[DocState]) -> list[DocState]:
    """A empresa é uma, o documento é de um período: quando a mesma empresa tem mais de um
    documento, cada um aparece como "Empresa (período)", e o relatório não as toma por duas
    empresas diferentes. Volta ordenado por empresa e, dentro dela, por período."""
    counts: dict[str, int] = {}
    for state in states:
        counts[_company_key(state)] = counts.get(_company_key(state), 0) + 1
    for state in states:
        if counts[_company_key(state)] > 1:
            base = state.document.company or state.document.file_name
            state.label = f"{base} ({state.document.period or state.document.file_name})"
    return sorted(states, key=lambda state: (_company_key(state), state.document.period or ""))
