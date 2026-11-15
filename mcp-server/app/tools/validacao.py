"""Tools do validador: alertas de inconsistência da fonte e a conclusão da revisão."""
from typing import Any

from pydantic import Field

from app.dados.visoes import alert_view
from app.governanca.catalogo import Args, Call, tool
from app.governanca.erros import ToolRejected
from app.regras.formulas import AlertDraft
from app.regras.pendencias import REVIEW_DONE
from app.regras.texto import (
    check_quote,
    check_script,
    squash,
    unbacked_numbers,
)
from app.tools.analise import _QUOTE_MIN, _chunk, _numbers_backed


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


class LerAlertas(Args):
    doc_id: str = Field(description="Documento.")


@tool("ler_alertas", "validacao", """
    Lê os alertas de um documento: os das validações automáticas (margem, variação, unidades) e os
    de inconsistência na fonte registrados com citação.""")
async def ler_alertas(call: Call, args: LerAlertas) -> dict[str, Any]:
    document = await call.deps.documents.get(call.caller, args.doc_id)
    alerts = await call.deps.store.alerts(call.caller.user_id, args.doc_id, document.fingerprint)
    return {"doc_id": args.doc_id, "empresa": document.company, "alertas": [alert_view(alert) for alert in alerts]}


class ConcluirRevisao(Args):
    doc_id: str = Field(description="Documento revisado.")
    observacao: str = Field(min_length=15, max_length=400, description="O que foi revisado e o resultado, em uma "
                            "frase (ex.: 'Relatório da administração e notas de dívida lidos; duas "
                            "inconsistências registradas').")


@tool("concluir_revisao", "validacao", """
    Marca que as fontes de um documento foram revisadas em busca de inconsistências. Chame depois
    de ler os alertas automáticos e de registrar, com registrar_alerta, o que encontrou no texto.
    Sem isso, a revisão continua aparecendo como pendência.""")
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
    found = sum(alert.tipo == "fonte" and alert.codigo != REVIEW_DONE for alert in alerts)
    await call.deps.store.add_alerts(call.caller, args.doc_id, document.fingerprint, "fonte", [AlertDraft(
        REVIEW_DONE, "informacao", mensagem=f"Fontes revisadas pelo agente {call.caller.agent}. {note}",
        detalhes={"inconsistencias_registradas": found})])
    return {"concluida": True, "inconsistencias_registradas": found}
