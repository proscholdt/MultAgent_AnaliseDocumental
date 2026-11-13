"""O que já existe na base para um usuário e o que ainda falta para o relatório.

A lista de pendências é uma função só, usada em três lugares: pelo orquestrador durante a
execução (para saber o que delegar), pelo sistema dos agentes no fim (para conferir se nada
ficou para trás) e pelo `montar_relatorio` (que não monta relatório incompleto).

A ordem do trabalho também está aqui, e não na boa vontade do orquestrador. Cada pendência diz
se está `liberada` ou o que ela `aguarda`: as cinco perguntas comparam as empresas, então só
podem ser respondidas com a extração e a análise de todos os documentos; o resumo de uma
empresa usa a extração, a análise e os alertas dela. As tools que gravam recusam o que chega
fora de ordem (na primeira execução real, o orquestrador mandou responder as perguntas enquanto
duas extrações ainda rodavam, e as respostas saíram dizendo "sem extração publicada").

O que foi escrito antes de a base mudar volta para a lista: resposta ou resumo mais antigo que
a extração ou a análise de que depende está desatualizado.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.catalog import Call
from app.documents import IndexedDocument
from app.fields import CATEGORIES, QUESTIONS
from app.tools.analysis import alert_view, analysis_view

REVIEW_DONE = "revisao_das_fontes_concluida"


def _latest(*moments: datetime | None) -> datetime | None:
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

    @property
    def name(self) -> str:
        return self.document.company or self.document.file_name

    @property
    def reviewed(self) -> bool:
        return any(alert["codigo"] == REVIEW_DONE for alert in self.alerts)

    @property
    def missing_categories(self) -> list[str]:
        return [categoria for categoria in CATEGORIES if not self.analysis.get(categoria)]

    @property
    def newest(self) -> datetime | None:
        """O que foi gravado por último sobre este documento."""
        return _latest(self.extraction_at, self.analysis_at, self.alerts_at, self.summary_at)

    @property
    def summary_is_stale(self) -> bool:
        base = _latest(self.extraction_at, self.analysis_at, self.alerts_at)
        return self.summary_at is not None and base is not None and self.summary_at < base


async def gather(call: Call) -> list[DocState]:
    """O estado de cada documento indexado do usuário, em ordem de nome."""
    store, user_id = call.deps.store, call.caller.user_id
    listed = await call.deps.indexer.documents(call.caller.user_token)
    states = []
    for meta in listed:
        if meta.get("status") != "indexed":
            continue
        document = await call.deps.documents.get(call.caller, meta["doc_id"])
        published = await store.extraction(user_id, document.doc_id)
        current = published is not None and published.fingerprint == document.fingerprint
        items = await store.analysis(user_id, document.doc_id, document.fingerprint)
        alerts = await store.alerts(user_id, document.doc_id, document.fingerprint)
        summary = await store.summary(user_id, document.doc_id, document.fingerprint)
        states.append(DocState(
            document=document, extraction=published.payload if current else None, analysis=analysis_view(items),
            alerts=[alert_view(alert) for alert in alerts], summary=summary.renderizado if summary else None,
            extraction_at=published.at if current else None, analysis_at=_latest(*(item.at for item in items)),
            alerts_at=_latest(*(alert.at for alert in alerts)), summary_at=summary.at if summary else None))
    return sorted(states, key=lambda state: state.name.casefold())


def question_blockers(states: list[DocState]) -> list[str]:
    """O que falta para as cinco perguntas do relatório poderem ser respondidas."""
    blockers = []
    for state in states:
        if state.extraction is None:
            blockers.append(f"extração dos indicadores de {state.name}")
        if state.missing_categories:
            blockers.append(f"análise qualitativa de {state.name} ({', '.join(state.missing_categories)})")
    return blockers


def summary_blockers(state: DocState) -> list[str]:
    """O que falta para o resumo executivo de uma empresa poder ser escrito."""
    blockers = []
    if state.extraction is None:
        blockers.append(f"extração dos indicadores de {state.name}")
    elif not state.reviewed:
        blockers.append(f"revisão das fontes de {state.name}")
    if state.missing_categories:
        blockers.append(f"análise qualitativa de {state.name} ({', '.join(state.missing_categories)})")
    return blockers


async def pending(call: Call, *, include_report: bool = True) -> list[dict[str, Any]]:
    """O que falta, em ordem de execução. Cada item diz qual agente costuma resolver e se já
    pode ser feito."""
    states = await gather(call)
    items: list[dict[str, Any]] = []

    def add(what: str, agent: str, state: DocState | None = None, waits: list[str] | None = None,
            **extra: Any) -> None:
        entry: dict[str, Any] = {"o_que_falta": what, "agente": agent, "liberada": not waits, **extra}
        if waits:
            entry["aguarda"] = "; ".join(waits)
        if state is not None:
            entry.update(doc_id=state.document.doc_id, empresa=state.name)
        items.append(entry)

    for state in states:
        if state.extraction is None:
            add("extração dos indicadores (fechar_extracao)", "extrator", state)
    for state in states:
        if not state.reviewed:
            waits = [f"extração dos indicadores de {state.name}"] if state.extraction is None else None
            add("revisão das fontes em busca de inconsistências (concluir_revisao)", "validador", state, waits)
    for state in states:
        if state.missing_categories:
            add("análise qualitativa", "analista", state, categorias=state.missing_categories)

    answers = {answer.pergunta_id: answer for answer in await call.deps.store.answers(call.caller.user_id)}
    blockers = question_blockers(states)
    base_changed = _latest(*(moment for state in states for moment in (state.extraction_at, state.analysis_at)))
    for question_id, question in QUESTIONS.items():
        answer = answers.get(question_id)
        if answer is None:
            add("resposta à pergunta", "pesquisador", waits=blockers, pergunta_id=question_id, pergunta=question)
        elif blockers or (base_changed is not None and answer.at < base_changed):
            add("refazer a resposta: a extração ou a análise mudou depois dela", "pesquisador", waits=blockers,
                pergunta_id=question_id, pergunta=question)

    for state in states:
        if state.summary is None:
            add("resumo executivo (salvar_resumo)", "redator", state, summary_blockers(state))
        elif state.summary_is_stale:
            add("refazer o resumo executivo: a base da empresa mudou depois dele (salvar_resumo)", "redator", state,
                summary_blockers(state))

    if include_report and not items:
        report = await call.deps.store.report(call.caller.user_id)
        # Só as cinco perguntas entram no relatório: uma pergunta livre do usuário respondida depois
        # não o deixa desatualizado (antes, cada pergunta de conversa fazia remontar o relatório).
        newest = _latest(*(state.newest for state in states),
                         *(answer.at for question_id, answer in answers.items() if question_id in QUESTIONS))
        if report is None:
            add("montagem do relatório (montar_relatorio)", "redator")
        elif newest is not None and report.at < newest:
            add("remontar o relatório: há dados mais novos que ele (montar_relatorio)", "redator")
    return items
