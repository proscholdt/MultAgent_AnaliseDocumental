"""O que já existe na base para um usuário e o que ainda falta para o relatório.

A lista de pendências é uma função só, usada em três lugares: pelo orquestrador durante a
execução (para saber o que delegar), pelo sistema dos agentes no fim (para conferir se nada
ficou para trás) e pelo `montar_relatorio` (que não monta relatório incompleto).
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.catalog import Call
from app.documents import IndexedDocument
from app.fields import CATEGORIES, QUESTIONS
from app.tools.analysis import alert_view, analysis_view

REVIEW_DONE = "revisao_das_fontes_concluida"


@dataclass
class DocState:
    document: IndexedDocument
    extraction: dict[str, Any] | None  # JSON publicado, se é do índice atual
    analysis: dict[str, list[dict[str, Any]]]
    alerts: list[dict[str, Any]]
    summary: str | None
    newest: datetime | None  # o que foi gravado por último sobre este documento

    @property
    def name(self) -> str:
        return self.document.company or self.document.file_name

    @property
    def reviewed(self) -> bool:
        return any(alert["codigo"] == REVIEW_DONE for alert in self.alerts)


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
        times = [row.at for row in (*items, *alerts)] + ([published.at] if current else []) \
            + ([summary.at] if summary else [])
        states.append(DocState(
            document=document, extraction=published.payload if current else None, analysis=analysis_view(items),
            alerts=[alert_view(alert) for alert in alerts], summary=summary.renderizado if summary else None,
            newest=max(times) if times else None))
    return sorted(states, key=lambda state: state.name.casefold())


async def pending(call: Call, *, include_report: bool = True) -> list[dict[str, Any]]:
    """O que falta, em ordem de execução. Cada item diz qual agente costuma resolver."""
    states = await gather(call)
    items: list[dict[str, Any]] = []

    def add(what: str, agent: str, state: DocState | None = None, **extra: Any) -> None:
        entry = {"o_que_falta": what, "agente": agent, **extra}
        if state is not None:
            entry.update(doc_id=state.document.doc_id, empresa=state.name)
        items.append(entry)

    for state in states:
        if state.extraction is None:
            add("extração dos indicadores (fechar_extracao)", "extrator", state)
    for state in states:
        if state.extraction is not None and not state.reviewed:
            add("revisão das fontes em busca de inconsistências (concluir_revisao)", "validador", state)
    for state in states:
        missing = [categoria for categoria in CATEGORIES if not state.analysis.get(categoria)]
        if missing:
            add("análise qualitativa", "analista", state, categorias=missing)
    answered = {answer.pergunta_id for answer in await call.deps.store.answers(call.caller.user_id)}
    for question_id, question in QUESTIONS.items():
        if question_id not in answered:
            add("resposta à pergunta", "pesquisador", pergunta_id=question_id, pergunta=question)
    for state in states:
        if state.extraction is not None and state.summary is None:
            add("resumo executivo (salvar_resumo)", "redator", state)
    if include_report and not items:
        report = await call.deps.store.report(call.caller.user_id)
        answers = await call.deps.store.answers(call.caller.user_id)
        newest = max([state.newest for state in states if state.newest] + [answer.at for answer in answers],
                     default=None)
        if report is None:
            add("montagem do relatório (montar_relatorio)", "redator")
        elif newest is not None and report.at < newest:
            add("remontar o relatório: há dados mais novos que ele (montar_relatorio)", "redator")
    return items
