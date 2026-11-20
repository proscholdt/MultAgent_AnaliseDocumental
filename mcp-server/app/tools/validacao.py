"""Tools do validador: alertas de inconsistência da fonte e a conclusão da revisão."""
from typing import Any

from pydantic import Field

from app.dominio.documento import IndexedDocument
from app.governanca.catalogo import Args, Call, tool
from app.governanca.erros import ToolRejected
from app.regras.formulas import AlertDraft
from app.regras.inconsistencias import Candidate, find
from app.regras.pendencias import DISCARDED, REVIEW_DONE
from app.regras.texto import (
    check_quote,
    check_script,
    squash,
    unbacked_numbers,
)
from app.regras.visoes import alert_view
from app.tools.analise import _QUOTE_MIN, _chunk, _numbers_backed
from app.tools.extracao import build_extraction


class RegistrarAlerta(Args):
    doc_id: str = Field(description="Documento.")
    descricao: str = Field(min_length=20, max_length=500, description="O que está inconsistente na fonte, em "
                           "uma ou duas frases.")
    chunk_index: int = Field(ge=0, description="Chunk onde a inconsistência está.")
    citacao: str = Field(description="O trecho inconsistente, copiado literalmente do chunk (pelo menos 40 "
                         "caracteres).")


@tool("registrar_alerta", "validacao", """
    Registra uma inconsistência que está no próprio documento (uma frase cujos números não fecham,
    um ano trocado), com a citação literal. Não corrija o documento: sinalize. Os números da
    descrição têm de estar na citação.""")
async def registrar_alerta(call: Call, args: RegistrarAlerta) -> dict[str, Any]:
    document = await call.deps.documents.get(call.caller, args.doc_id)
    chunk = _chunk(document, args.chunk_index)
    quote = check_quote(args.citacao, chunk, min_chars=_QUOTE_MIN)
    description = squash(args.descricao)
    check_script(description, "A descrição")
    _numbers_backed(description, quote, "A descrição")
    draft = AlertDraft("inconsistencia_na_fonte", "atencao", mensagem=description, detalhes={
        "pagina": chunk["page_start"], "chunk_index": args.chunk_index,
        "secao": " > ".join(chunk.get("section_path") or []), "citacao": quote})
    added = await call.deps.store.add_alerts(call.caller, args.doc_id, document.fingerprint, "fonte", [draft])
    return {"registrado": bool(added), "pagina": chunk["page_start"],
            "aviso": None if added else "Este alerta já estava registrado."}


class CandidatosInconsistencia(Args):
    doc_id: str = Field(description="Documento.")


@tool("candidatos_inconsistencia", "validacao", """
    Lista os trechos do documento que uma regra marcou como possível inconsistência: conta que
    não fecha numa frase (valor, variação e comparável), variação no texto que não bate com os
    dois períodos da extração, um ano comparado com ele mesmo, projeção para um ano que já passou,
    quadro com data diferente da do texto que o apresenta ou com duas colunas de mesmo título.
    São candidatos, não conclusões: leia cada um e registre só o que for erro do documento.""")
async def candidatos_inconsistencia(call: Call, args: CandidatosInconsistencia) -> dict[str, Any]:
    document = await call.deps.documents.get(call.caller, args.doc_id)
    found = await _candidates(call, document)
    return {"doc_id": args.doc_id, "empresa": document.company, "candidatos": [c.view() for c in found],
            "como_usar": "Julgue CADA candidato: confira o trecho (ler_chunk ou ler_pagina) e, quando houver, a "
                         "`prova` (os mesmos números em outro quadro, com o ano de lá). Se for erro do documento: "
                         "registrar_alerta com o chunk_index e a citação copiada do trecho; a descrição só leva "
                         "números que estão na citação. Se não for (arredondamento, outro período citado de "
                         "propósito, coluna que o quadro explica): descartar_candidato, dizendo por quê. "
                         "concluir_revisao só aceita com todos os candidatos julgados."}


async def _candidates(call: Call, document: IndexedDocument) -> list[Candidate]:
    """Os candidatos do documento, com a extração publicada para a regra que compara com ela."""
    extraction, _ = await build_extraction(call, document)
    return find(document.chunks, document.period, extraction)


def _judged(candidate: Candidate, alerts: list[Any]) -> bool:
    """O candidato virou alerta (no mesmo chunk ou na mesma página) ou foi descartado com motivo."""
    for alert in alerts:
        details = alert.detalhes or {}
        if alert.codigo == "inconsistencia_na_fonte" and (details.get("chunk_index") == candidate.chunk_index
                                                         or details.get("pagina") == candidate.pagina):
            return True
        if alert.codigo == DISCARDED and details.get("chunk_index") == candidate.chunk_index \
                and details.get("regra") == candidate.regra:
            return True
    return False


class DescartarCandidato(Args):
    doc_id: str = Field(description="Documento.")
    chunk_index: int = Field(ge=0, description="O chunk_index do candidato, como veio em candidatos_inconsistencia.")
    regra: str = Field(description="A regra do candidato, como veio em candidatos_inconsistencia.")
    motivo: str = Field(min_length=20, max_length=400, description="Por que não é erro do documento, em uma ou duas "
                        "frases (ex.: 'arredondamento entre o texto e o quadro', 'a nota explica que a coluna é de "
                        "outra controlada'). Números só os que estão no trecho.")


@tool("descartar_candidato", "validacao", """
    Diz que um candidato de candidatos_inconsistencia NÃO é erro do documento, e por quê. O motivo
    fica guardado e na auditoria (não vai para o relatório). Use só depois de ler o trecho e a
    prova; se o trecho estiver errado, registre com registrar_alerta.""")
async def descartar_candidato(call: Call, args: DescartarCandidato) -> dict[str, Any]:
    document = await call.deps.documents.get(call.caller, args.doc_id)
    found = await _candidates(call, document)
    candidate = next((c for c in found if c.chunk_index == args.chunk_index and c.regra == args.regra), None)
    if candidate is None:
        raise ToolRejected(f"Não há candidato da regra '{args.regra}' no chunk {args.chunk_index}.",
                           opcoes=[f"chunk {c.chunk_index}, regra {c.regra}" for c in found])
    if candidate.prova_forte:
        raise ToolRejected("Este candidato não pode ser descartado: pelo menos dois valores do quadro aparecem em "
                           "outro quadro na coluna de outro ano, e isso contradiz o ano que o texto dá ao quadro. "
                           "Registre com registrar_alerta, citando o trecho; a prova vai na descrição.",
                           prova=candidate.prova)
    reason = squash(args.motivo)
    check_script(reason, "O motivo")
    _numbers_backed(reason, _chunk(document, args.chunk_index)["text"], "O motivo")
    added = await call.deps.store.add_alerts(call.caller, args.doc_id, document.fingerprint, "fonte", [AlertDraft(
        DISCARDED, "informacao", mensagem=reason,
        detalhes={"chunk_index": candidate.chunk_index, "pagina": candidate.pagina, "regra": candidate.regra,
                  "motivo_da_regra": candidate.motivo})])
    return {"descartado": True, "pagina": candidate.pagina, "aviso": None if added else "Já estava descartado."}


class LerAlertas(Args):
    doc_id: str = Field(description="Documento.")


@tool("ler_alertas", "validacao", """
    Lê os alertas de um documento: os das validações automáticas (margem, variação, unidades) e os
    de inconsistência na fonte registrados com citação.""")
async def ler_alertas(call: Call, args: LerAlertas) -> dict[str, Any]:
    document = await call.deps.documents.get(call.caller, args.doc_id)
    alerts = await call.deps.store.alerts(call.caller.user_id, args.doc_id, document.fingerprint)
    return {"doc_id": args.doc_id, "empresa": document.company,
            "alertas": [alert_view(alert) for alert in alerts if alert.codigo != DISCARDED]}


class ConcluirRevisao(Args):
    doc_id: str = Field(description="Documento revisado.")
    observacao: str = Field(min_length=15, max_length=400, description="O que foi revisado e o resultado, em uma "
                            "frase (ex.: 'Relatório da administração e notas de dívida lidos; duas "
                            "inconsistências registradas').")


@tool("concluir_revisao", "validacao", """
    Marca que as fontes de um documento foram revisadas em busca de inconsistências. Chame depois
    de ler os alertas automáticos e de julgar cada candidato (registrar_alerta ou
    descartar_candidato). Com candidato sem julgamento, é recusada e diz quais faltam. Sem ela, a
    revisão continua aparecendo como pendência.""")
async def concluir_revisao(call: Call, args: ConcluirRevisao) -> dict[str, Any]:
    document = await call.deps.documents.get(call.caller, args.doc_id)
    published = await call.deps.store.extraction(call.caller.user_id, args.doc_id)
    if published is None or published.fingerprint != document.fingerprint:
        raise ToolRejected("A revisão vem depois da extração: este documento não tem extração publicada.")
    note = squash(args.observacao)
    check_script(note, "A observação")
    loose = unbacked_numbers(note, [])
    if loose:
        raise ToolRejected(f"A observação não leva valores ({', '.join(loose)}): eles ficam nos alertas, com citação.")
    alerts = await call.deps.store.alerts(call.caller.user_id, args.doc_id, document.fingerprint)
    open_ = [c for c in await _candidates(call, document) if not _judged(c, alerts)]
    if open_:
        raise ToolRejected(
            f"Falta julgar {len(open_)} candidato(s) de candidatos_inconsistencia: registre com registrar_alerta "
            "ou descarte com descartar_candidato, dizendo por quê.",
            problemas=[f"chunk {c.chunk_index} (p. {c.pagina}), regra {c.regra}: {c.motivo}" for c in open_])
    found = sum(alert.tipo == "fonte" and alert.codigo == "inconsistencia_na_fonte" for alert in alerts)
    await call.deps.store.add_alerts(call.caller, args.doc_id, document.fingerprint, "fonte", [AlertDraft(
        REVIEW_DONE, "informacao", mensagem=f"Fontes revisadas pelo agente {call.caller.agent}. {note}",
        detalhes={"inconsistencias_registradas": found})])
    return {"concluida": True, "inconsistencias_registradas": found}
