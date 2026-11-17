"""Tools do analista: a análise qualitativa (motivo do resultado, riscos, guidance, eventos, tom).

A afirmação é do modelo; o que a sustenta é a citação, e a citação é conferida: tem de existir
literalmente no chunk. Número que aparece na afirmação tem de estar na citação. No relatório a
citação sai ao lado da afirmação, com a página, para o analista conferir com os próprios olhos.
"""
from typing import Any, Literal

from pydantic import Field

from app.dados.modelos import AnalysisItem
from app.dados.visoes import analysis_view
from app.governanca.catalogo import Args, Call, tool
from app.governanca.erros import ToolRejected
from app.regras.campos import CATEGORIES, Categoria
from app.regras.evidencias import occurrences
from app.regras.texto import (
    GOING_CONCERN,
    check_quote,
    check_script,
    denies_existence,
    source_kind,
    squash,
    unbacked_numbers,
)

_QUOTE_MIN = 40


def _chunk(document: Any, index: int) -> dict[str, Any]:
    chunk = document.chunk(index)
    if chunk is None:
        raise ToolRejected(f"O documento tem chunks de 0 a {len(document.chunks) - 1}; não existe o {index}.")
    return chunk


def _numbers_backed(text: str, quote: str, what: str) -> None:
    missing = unbacked_numbers(text, [quote])
    if missing:
        raise ToolRejected(f"{what} traz números que a citação não escreve: {', '.join(missing)}. Use só os "
                           "números que estão na citação, como estão escritos.")


class SalvarItemAnalise(Args):
    doc_id: str = Field(description="Documento.")
    categoria: Categoria = Field(description="motivo_resultado: o que explicou o resultado do período. risco: "
                                 "risco ou ponto de atenção citado. guidance: perspectiva ou projeção. "
                                 "evento_relevante: aquisição, reestruturação, contingência, mudança contábil. "
                                 "tom: o tom geral da administração.")
    afirmacao: str = Field(min_length=15, max_length=500, description="O achado, em uma ou duas frases suas.")
    chunk_index: int = Field(ge=0, description="Chunk de onde vem a citação.")
    citacao: str = Field(description="Frase do documento que sustenta a afirmação, copiada literalmente do chunk "
                         "(pelo menos 40 caracteres).")
    tom: Literal["positivo", "neutro", "negativo"] | None = Field(
        default=None, description="Só na categoria 'tom': a classificação. A afirmação é a justificativa. Nas "
        "outras categorias, null.")


@tool("salvar_item_analise", "analise", """
    Grava um item da análise qualitativa de um documento, com a citação literal que o sustenta. A
    citação tem de existir no chunk, e os números da afirmação têm de estar na citação. Uma
    categoria pode ter vários itens; na categoria 'tom' informe também a classificação.""")
async def salvar_item_analise(call: Call, args: SalvarItemAnalise) -> dict[str, Any]:
    if (args.categoria == "tom") != (args.tom is not None):
        raise ToolRejected("O campo 'tom' é obrigatório na categoria 'tom' e fica null nas outras.")
    document = await call.deps.documents.get(call.caller, args.doc_id)
    chunk = _chunk(document, args.chunk_index)
    quote = check_quote(args.citacao, chunk, min_chars=_QUOTE_MIN)
    statement = squash(args.afirmacao)
    check_script(statement, "A afirmação")
    if args.categoria != "tom" and denies_existence(statement):
        raise ToolRejected("Isto é uma ausência, não um achado: um item afirma algo que o documento diz. Se o "
                           "documento não traz esta categoria, use registrar_ausencia_analise, que confere e guarda "
                           "a evidência da busca.")
    _numbers_backed(statement, quote, "A afirmação")
    if args.categoria == "guidance" and GOING_CONCERN.search(quote):
        raise ToolRejected("Continuidade operacional não é guidance: é a conclusão da administração sobre um risco. "
                           "Grave este trecho na categoria 'risco'. Guidance é o que a administração diz que vai "
                           "fazer ou espera: planos, estratégia, investimentos, metas.")
    sections = chunk.get("section_path") or []
    row = AnalysisItem(
        user_id=call.caller.user_id, doc_id=args.doc_id, fingerprint=document.fingerprint,
        run_id=call.caller.run_id, categoria=args.categoria, status="encontrado", afirmacao=statement,
        citacao=quote, chunk_index=args.chunk_index, pagina=chunk["page_start"], secao=" > ".join(sections),
        fonte=source_kind(sections), tom=args.tom)
    # O tom é um só por documento; as outras categorias acumulam itens.
    await call.deps.store.save_analysis(row, replace_category=args.categoria == "tom")
    return {"salvo": {"categoria": args.categoria, "pagina": row.pagina, "fonte": row.fonte, "tom": args.tom},
            "aviso": "Trecho marcado como suspeito pelo indexador." if chunk.get("suspicious") else None}


class RegistrarAusenciaAnalise(Args):
    doc_id: str = Field(description="Documento.")
    categoria: Categoria = Field(description="A categoria que o documento não traz.")
    motivo: str = Field(min_length=20, max_length=600, description="Por que não há essa informação e onde foi "
                        "procurada (ex.: o documento não tem relatório da administração).")
    paginas_descartadas: list[int] = Field(default_factory=list, description="Páginas em que os termos da "
                                           "categoria aparecem e que não servem. Pode começar vazia.")


@tool("registrar_ausencia_analise", "analise", """
    Registra que o documento não traz uma categoria da análise qualitativa (por exemplo, não há
    guidance). O servidor procura os termos da categoria; se aparecerem em páginas que você não
    descartou, a chamada é recusada com as páginas, para você ler antes de afirmar a ausência.""")
async def registrar_ausencia_analise(call: Call, args: RegistrarAusenciaAnalise) -> dict[str, Any]:
    document = await call.deps.documents.get(call.caller, args.doc_id)
    label, terms = CATEGORIES[args.categoria]
    check_script(args.motivo, "O motivo")
    found = occurrences(document, terms) if terms else {}
    pending = sorted(set(found) - set(args.paginas_descartadas))
    if pending:
        raise ToolRejected(
            f"Os termos de '{label}' aparecem em páginas que você não descartou: "
            f"{', '.join(str(page) for page in pending)}. Leia-as: se há o que registrar, use salvar_item_analise.",
            problemas=[f"p. {page}: …{found[page]}…" for page in pending[:10]])
    evidence = {"termos_procurados": list(terms), "paginas_com_o_termo": sorted(found),
                "paginas_descartadas": sorted(set(args.paginas_descartadas) & set(found))}
    await call.deps.store.save_analysis(AnalysisItem(
        user_id=call.caller.user_id, doc_id=args.doc_id, fingerprint=document.fingerprint,
        run_id=call.caller.run_id, categoria=args.categoria, status="nao_encontrado",
        motivo=squash(args.motivo), evidencia=evidence), replace_category=True)
    return {"salvo": {"categoria": args.categoria, "status": "nao_encontrado"}, "evidencia": evidence}


class LerAnalise(Args):
    doc_id: str = Field(description="Documento.")


@tool("ler_analise", "analise", """
    Lê a análise qualitativa guardada de um documento, por categoria, com a citação e a página de
    cada item.""")
async def ler_analise(call: Call, args: LerAnalise) -> dict[str, Any]:
    document = await call.deps.documents.get(call.caller, args.doc_id)
    items = await call.deps.store.analysis(call.caller.user_id, args.doc_id, document.fingerprint)
    return {"doc_id": args.doc_id, "empresa": document.company, "analise": analysis_view(items)}
