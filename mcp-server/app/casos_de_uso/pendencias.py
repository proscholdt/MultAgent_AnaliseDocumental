"""Pendências lidas da base: junta o estado de cada documento e aplica as regras de
`app.regras.pendencias`, que são puras.

Usado em três lugares: pelo orquestrador durante a execução (para saber o que delegar), pelo
sistema dos agentes no fim (para conferir se nada ficou para trás) e pelo `montar_relatorio`
(que não monta relatório incompleto).
"""
from typing import Any

from app.governanca.catalogo import Call
from app.portas import Repositorio
from app.regras.pendencias import (
    DocState,
    company_report_pending,
    latest,
    pending_work,
    report_pending,
    select_states,
)
from app.regras.visoes import alert_view, analysis_view


async def gather(call: Call) -> list[DocState]:
    """O estado de cada documento indexado do usuário, em ordem de nome."""
    store: Repositorio = call.deps.store
    user_id = call.caller.user_id
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
            extraction_at=published.at if current else None, analysis_at=latest(*(item.at for item in items)),
            alerts_at=latest(*(alert.at for alert in alerts)), summary_at=summary.at if summary else None))
    return sorted(states, key=lambda state: state.name.casefold())


async def pending(call: Call, *, include_report: bool = True, doc_id: str | None = None) -> list[dict[str, Any]]:
    """O que falta, em ordem de execução (veja `app.regras.pendencias.pending_work`)."""
    store: Repositorio = call.deps.store
    states = select_states(await gather(call), doc_id)
    answers = {answer.pergunta_id: answer for answer in await store.answers(call.caller.user_id)}
    items = pending_work(states, answers, doc_id=doc_id)
    if include_report and not items and doc_id is not None:
        items = company_report_pending(states[0], await store.company_report(call.caller.user_id, doc_id))
    elif include_report and not items:
        items = report_pending(states, answers, await store.report(call.caller.user_id))
    return items
