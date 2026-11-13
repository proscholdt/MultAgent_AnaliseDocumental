"""Perguntas e respostas (item 4 do teste).

A resposta é texto do modelo, com duas amarras:

- valores vêm de marcadores ({{doc_id.campo}}), que o código troca pelo valor guardado, com a
  unidade e a página;
- trechos citados vêm como [[doc_id:chunk_index]], que o código confere e troca por "(arquivo,
  p. N)". Número escrito direto no texto só passa se estiver num dos trechos citados.

Comparações entre empresas (maior margem, acima de 3x) não são feitas pelo modelo: há uma tool
que ordena e filtra pelos valores guardados.
"""
import hashlib
import re
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field

from app.catalog import Args, Call, tool
from app.errors import ToolRejected
from app.fields import FIELDS, QUESTIONS, CampoPrincipal
from app.models import Answer
from app.numbers import in_reais
from app.pending import DocState, gather
from app.report import MARKER, Markers, render_amount
from app.text import squash, unbacked_numbers

CITATION = re.compile(r"\[\[\s*([0-9a-f]{8,32})\s*:\s*(\d+)\s*\]\]")


def markers_for(states: list[DocState]) -> Markers:
    """Os marcadores disponíveis: extrações publicadas e categorias de análise dadas como ausentes."""
    absences = {
        state.document.doc_id: {categoria: items[0] for categoria, items in state.analysis.items()
                                if items and items[0].get("status") == "nao_encontrado"}
        for state in states}
    return Markers({state.document.doc_id: state.extraction for state in states if state.extraction}, absences)


class CompararIndicadores(Args):
    campo: CampoPrincipal = Field(description="Indicador a comparar entre as empresas.")
    criterio: Literal["maior", "menor", "acima_de", "abaixo_de"] = Field(
        description="'maior' e 'menor' ordenam as empresas; 'acima_de' e 'abaixo_de' filtram pelo limite.")
    limite: float | None = Field(default=None, description="Só para 'acima_de' e 'abaixo_de'. Percentual e "
                                 "múltiplo na própria escala (3 = 3x; 20 = 20%); dinheiro, em reais.")


@tool("comparar_indicadores", "perguntas", """
    Compara um indicador entre os documentos do usuário, pelos valores guardados: ordena (maior,
    menor) ou filtra por um limite (acima_de, abaixo_de). Diz, para cada empresa, se o valor é
    informado por ela ou calculado, e devolve o marcador para usar na resposta. Use esta tool em
    vez de comparar números por conta própria.""")
async def comparar_indicadores(call: Call, args: CompararIndicadores) -> dict[str, Any]:
    threshold = args.criterio in ("acima_de", "abaixo_de")
    if threshold and args.limite is None:
        raise ToolRejected("Com 'acima_de' ou 'abaixo_de', informe o limite.")
    rows, without = [], []
    for state in await gather(call):
        doc_id = state.document.doc_id
        if state.extraction is None:
            without.append({"empresa": state.name, "motivo": "sem extração publicada"})
            continue
        block = state.extraction["indicadores"][args.campo]
        informed, computed = block["informado"], block["calculado"]
        if informed and informed["status"] == "encontrado":
            source, base, marker = informed, "informado pela empresa", f"{{{{{doc_id}.{args.campo}}}}}"
        elif computed:
            source, base, marker = computed, "calculado pelo servidor", f"{{{{{doc_id}.{args.campo}.calculado}}}}"
        else:
            without.append({"empresa": state.name, "motivo": "não informado e sem base para calcular",
                            "marcador": f"{{{{{doc_id}.{args.campo}}}}}"})
            continue
        number = Decimal(str(source["valor_numerico"]))
        amount = in_reais(number, source["unidade"]) if FIELDS[args.campo].kind == "valor" else number
        if amount is None:
            without.append({"empresa": state.name, "motivo": "valor sem unidade monetária declarada"})
            continue
        rows.append({"empresa": state.name, "doc_id": doc_id, "base": base,
                     "valor": render_amount(source["valor"], source["unidade"]), "marcador": marker,
                     "_amount": amount})
    rows.sort(key=lambda row: row["_amount"], reverse=args.criterio in ("maior", "acima_de"))
    if threshold:
        limit = Decimal(str(args.limite))
        rows = [row for row in rows if (row["_amount"] > limit if args.criterio == "acima_de" else row["_amount"] < limit)]
    for row in rows:
        del row["_amount"]
    return {"indicador": FIELDS[args.campo].label, "criterio": args.criterio, "limite": args.limite,
            "resultado": rows, "sem_valor": without,
            "observacao": "Valor calculado não é informado pela empresa: diga isso na resposta."}


class SalvarResposta(Args):
    pergunta_id: Literal["p1", "p2", "p3", "p4", "p5", "livre"] = Field(
        description="p1 a p5: as cinco perguntas do relatório. 'livre': outra pergunta do usuário.")
    pergunta: str = Field(min_length=5, max_length=500, description="A pergunta respondida.")
    resposta: str = Field(min_length=20, max_length=4000, description="A resposta. Valores vão como marcador "
                          "{{doc_id.campo}} (ou .calculado, .variacao, .comparavel); trechos citados vão como "
                          "[[doc_id:chunk_index]]; categoria da análise registrada como ausente vai como "
                          "{{doc_id.analise.guidance}}. Não escreva números fora dos marcadores, a não ser que "
                          "estejam num trecho citado.")


@tool("salvar_resposta", "perguntas", """
    Grava a resposta a uma pergunta, com as fontes. O servidor troca os marcadores {{doc_id.campo}}
    pelos valores guardados (com unidade e página) e as citações [[doc_id:chunk_index]] por
    "(arquivo, p. N)". Toda resposta precisa de pelo menos uma fonte, e número fora de marcador só
    passa se estiver escrito num trecho citado.""")
async def salvar_resposta(call: Call, args: SalvarResposta) -> dict[str, Any]:
    states = await gather(call)
    by_id = {state.document.doc_id: state for state in states}
    raw = args.resposta.strip()

    # 1. Cada citação tem de apontar para um chunk que existe para este usuário.
    citations: list[dict[str, Any]] = []
    cited_texts: list[str] = []
    for match in CITATION.finditer(raw):
        doc_id, index = match.group(1), int(match.group(2))
        state = by_id.get(doc_id)
        chunk = state.document.chunk(index) if state else None
        if chunk is None:
            raise ToolRejected(f"Citação [[{doc_id}:{index}]]: não existe esse chunk nos documentos do usuário.")
        cited_texts.append(chunk["text"])
        citations.append({"doc_id": doc_id, "arquivo": state.document.file_name, "empresa": state.name,
                          "pagina": chunk["page_start"], "pagina_final": chunk["page_end"], "chunk_index": index,
                          "secao": " > ".join(chunk.get("section_path") or [])})

    # 2. Tirando marcadores e citações, o que sobra é o que o modelo escreveu por conta própria:
    #    um número ali só passa se estiver escrito num dos trechos citados, ou na própria
    #    pergunta ("acima de 3x").
    question = QUESTIONS.get(args.pergunta_id) or squash(args.pergunta)
    loose = unbacked_numbers(CITATION.sub(" ", MARKER.sub(" ", raw)), [*cited_texts, question])
    if loose:
        raise ToolRejected(f"A resposta traz números que não vêm de marcador nem estão nos trechos citados: "
                           f"{', '.join(loose)}. Use o marcador do indicador ou cite o trecho que traz o número.")

    # 3. O código põe os valores e as fontes no lugar.
    rendered = markers_for(states).render(raw)
    in_order = iter(citations)
    text = CITATION.sub(lambda _: "({arquivo}, p. {pagina})".format(**next(in_order)), rendered.text)
    sources = citations + [{"doc_id": s["doc_id"], "arquivo": s["arquivo"], "pagina": s["pagina"],
                            "campo": s["campo"]} for s in rendered.sources]
    if not sources and not rendered.markers:
        raise ToolRejected("A resposta não cita nenhuma fonte. Use marcadores de indicador ou cite trechos com "
                           "[[doc_id:chunk_index]]: toda resposta tem de dizer documento e página.")
    question_id = args.pergunta_id
    if question_id == "livre":
        question_id = "livre:" + hashlib.sha256(question.casefold().encode("utf-8")).hexdigest()[:10]
    await call.deps.store.save_answer(Answer(
        user_id=call.caller.user_id, run_id=call.caller.run_id, pergunta_id=question_id, pergunta=question,
        texto=raw, renderizado=text, citacoes=sources))
    return {"salva": {"pergunta_id": question_id, "pergunta": question}, "resposta": text,
            "fontes": [f"{s['arquivo']}, p. {s['pagina']}" for s in sources]}


class LerRespostas(Args):
    pass


@tool("ler_respostas", "perguntas", """
    Lê as respostas guardadas do usuário, já com os valores e as fontes no lugar dos marcadores.""")
async def ler_respostas(call: Call, args: LerRespostas) -> dict[str, Any]:
    answers = await call.deps.store.answers(call.caller.user_id)
    return {"respostas": [{"pergunta_id": answer.pergunta_id, "pergunta": answer.pergunta,
                           "resposta": answer.renderizado,
                           "fontes": [f"{c['arquivo']}, p. {c['pagina']}" for c in answer.citacoes]}
                          for answer in answers]}
