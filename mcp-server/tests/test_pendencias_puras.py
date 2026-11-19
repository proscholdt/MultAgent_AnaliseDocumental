"""As regras de pendência sem banco nem indexador: só estados em memória."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.dominio.documento import IndexedDocument
from app.dominio.erros import ToolRejected
from app.regras.campos import CATEGORIES, QUESTIONS
from app.regras.pendencias import (
    REVIEW_DONE,
    DocState,
    company_report_pending,
    pending_work,
    report_pending,
    select_states,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _doc(doc_id: str, company: str) -> IndexedDocument:
    return IndexedDocument(doc_id=doc_id, file_name=f"{company}.pdf", company=company, period="2022",
                           doc_type=None, page_count=1, metadata_source={}, fingerprint="f1", chunks=())


def _pronto(doc_id: str, company: str) -> DocState:
    return DocState(document=_doc(doc_id, company), extraction={"ok": True},
                    analysis={categoria: [{"afirmacao": "x"}] for categoria in CATEGORIES},
                    alerts=[{"codigo": REVIEW_DONE}], summary="resumo", extraction_at=T0, analysis_at=T0,
                    alerts_at=T0, summary_at=T0 + timedelta(minutes=1))


def _respostas(at: datetime) -> dict:
    return {qid: SimpleNamespace(pergunta_id=qid, at=at) for qid in QUESTIONS}


def test_documento_vazio_pede_a_extracao_primeiro_e_bloqueia_as_perguntas():
    vazio = DocState(document=_doc("a", "Alfa"), extraction=None, analysis={}, alerts=[], summary=None)
    itens = pending_work([vazio], {})
    assert itens[0]["agente"] == "extrator" and itens[0]["liberada"]
    revisao = next(item for item in itens if item["agente"] == "validador")
    assert not revisao["liberada"] and "extração" in revisao["aguarda"]
    perguntas = [item for item in itens if item["agente"] == "pesquisador"]
    assert len(perguntas) == len(QUESTIONS) and not any(item["liberada"] for item in perguntas)


def test_tudo_pronto_so_falta_montar_o_relatorio():
    estados, respostas = [_pronto("a", "Alfa")], _respostas(T0 + timedelta(minutes=2))
    assert pending_work(estados, respostas) == []
    assert [item["o_que_falta"] for item in report_pending(estados, respostas, None)] == [
        "montagem do relatório (montar_relatorio)"]
    montado = SimpleNamespace(at=T0 + timedelta(hours=1))
    assert report_pending(estados, respostas, montado) == []


def test_resposta_mais_antiga_que_a_extracao_volta_para_a_lista():
    itens = pending_work([_pronto("a", "Alfa")], _respostas(T0 - timedelta(minutes=1)))
    assert {item["pergunta_id"] for item in itens} == set(QUESTIONS)
    assert all(item["o_que_falta"].startswith("refazer") for item in itens)


def test_relatorio_de_empresa_de_outro_indice_e_remontado():
    estado = _pronto("a", "Alfa")
    antigo = SimpleNamespace(fingerprint="f0", at=T0 + timedelta(hours=1))
    assert company_report_pending(estado, antigo)[0]["agente"] == "redator"
    atual = SimpleNamespace(fingerprint="f1", at=T0 + timedelta(hours=1))
    assert company_report_pending(estado, atual) == []


def test_documento_desconhecido_e_recusado_com_as_opcoes():
    with pytest.raises(ToolRejected) as erro:
        select_states([_pronto("a", "Alfa")], "zzz")
    assert erro.value.opcoes == ["a (Alfa)"]
