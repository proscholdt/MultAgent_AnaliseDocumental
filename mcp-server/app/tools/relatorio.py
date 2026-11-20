"""Tools do redator e do controle: resumo executivo, pendências e a montagem dos relatórios."""
from datetime import UTC, datetime
from typing import Any

from pydantic import Field

from app.casos_de_uso.pendencias import gather, pending
from app.dados.modelos import Summary
from app.governanca.catalogo import Args, Call, tool
from app.governanca.erros import ToolRejected
from app.regras.marcadores import MARKER
from app.regras.pendencias import INTERNAL_ALERTS, summary_blockers
from app.regras.relatorios import build_company_report, build_report
from app.regras.texto import check_script, squash, unbacked_numbers
from app.tools.respostas import markers_for

_SUMMARY_LINES = 5  # resumo executivo de até 5 linhas por documento
_SUMMARY_CHARS = 900


class SalvarResumo(Args):
    doc_id: str = Field(description="Documento (um(a) $entidade).")
    texto: str = Field(min_length=40, max_length=2000, description="O resumo executivo do documento, em até 5 "
                       "linhas. Valores vão como marcador: {{campo}}, {{campo.variacao}}, {{campo.calculado}}, "
                       "{{campo.comparavel}}. Não escreva números por conta própria.")


@tool("salvar_resumo", "relatorio", """
    Grava o resumo executivo de um documento (até 5 linhas). Os valores entram por marcador, que o
    servidor troca pelo número guardado com a unidade e a página. Número escrito direto no texto é
    recusado, a não ser ano e contagem pequena.""")
async def salvar_resumo(call: Call, args: SalvarResumo) -> dict[str, Any]:
    states = await gather(call)
    state = next((s for s in states if s.document.doc_id == args.doc_id), None)
    if state is None:
        raise ToolRejected(f"Não existe o documento '{args.doc_id}' para este usuário.",
                           opcoes=[f"{s.document.doc_id} ({s.name})" for s in states])
    blockers = summary_blockers(state)
    if blockers:
        raise ToolRejected("O resumo usa a extração, a análise qualitativa e os alertas do documento. Ainda falta o "
                           "que está na lista. Termine sem gravar e diga isso.", problemas=blockers)
    check_script(args.texto, "O resumo")
    lines = [squash(line) for line in args.texto.splitlines() if line.strip()]
    if len(lines) > _SUMMARY_LINES:
        raise ToolRejected(f"O resumo tem {len(lines)} linhas; o limite é {_SUMMARY_LINES}.")
    text = "\n".join(lines)
    loose = unbacked_numbers(MARKER.sub(" ", text), [])
    if loose:
        raise ToolRejected(f"O resumo traz números escritos direto no texto: {', '.join(loose)}. Use os "
                           "marcadores dos campos.")
    rendered = markers_for(states).render(text, default_doc=args.doc_id, brief=True)
    if len(rendered.text) > _SUMMARY_CHARS * 2:
        raise ToolRejected("O resumo ficou longo demais para um resumo executivo: encurte as linhas.")
    await call.deps.store.save_summary(Summary(
        user_id=call.caller.user_id, doc_id=args.doc_id, fingerprint=state.document.fingerprint,
        run_id=call.caller.run_id, texto=text, renderizado=rendered.text))
    return {"salvo": {"entidade": state.name, "linhas": len(lines)}, "resumo": rendered.text}


class Pendencias(Args):
    doc_id: str | None = Field(None, description="Só para o relatório de UM documento: o doc_id dele. Sem "
                               "doc_id, a lista é a do relatório consolidado.")


@tool("pendencias", "controle", """
    Diz o que ainda falta para o relatório completo do usuário: extração, revisão das fontes,
    análise qualitativa, respostas às $n_perguntas perguntas, resumos e a montagem do relatório. Cada item
    indica o documento, o agente que costuma resolver e se já pode ser feito: 'liberada' true, ou
    false com o que ele 'aguarda' (as perguntas esperam a extração e a análise de todos os
    documentos; o resumo espera a extração, a revisão e a análise do documento). Com doc_id, é o que
    falta para o relatório só daquele documento (sem as perguntas comparativas). Lista vazia = está
    completo.""")
async def pendencias(call: Call, args: Pendencias) -> dict[str, Any]:
    items = await pending(call, doc_id=args.doc_id)
    return {"completo": not items, "pendencias": items}


class RelatorioDocumento(Args):
    doc_id: str = Field(description="Documento (um(a) $entidade).")


@tool("montar_relatorio_documento", "relatorio", """
    Monta o relatório de UM documento em Markdown a partir da base: resumo executivo, campos
    com a página, análise qualitativa com as citações e alertas. Precisa da extração, da revisão
    das fontes, da análise e do resumo do documento; se faltar algo, a chamada é recusada com a
    lista. As perguntas comparativas não entram: são do relatório consolidado.""")
async def montar_relatorio_documento(call: Call, args: RelatorioDocumento) -> dict[str, Any]:
    missing = await pending(call, include_report=False, doc_id=args.doc_id)
    if missing:
        raise ToolRejected("O relatório do documento não pode ser montado: ainda há pendências.",
                           problemas=[item["o_que_falta"] for item in missing])
    state = next(state for state in await gather(call) if state.document.doc_id == args.doc_id)
    markdown = build_company_report(
        {"extracao": state.extraction, "analise": state.analysis, "resumo": state.summary,
         "alertas": [alert for alert in state.alerts if alert["codigo"] not in INTERNAL_ALERTS]}, datetime.now(UTC))
    await call.deps.store.save_company_report(call.caller, args.doc_id, state.document.fingerprint, markdown)
    return {"montado": True, "entidade": state.name, "caracteres": len(markdown)}


@tool("ler_relatorio_documento", "relatorio", """
    Devolve o relatório mais recente de um documento, em Markdown.""")
async def ler_relatorio_documento(call: Call, args: RelatorioDocumento) -> dict[str, Any]:
    report = await call.deps.store.company_report(call.caller.user_id, args.doc_id)
    if report is None:
        return {"relatorio": None}
    return {"relatorio": report.markdown, "gerado_em": report.at.isoformat(), "doc_id": args.doc_id}


class MontarRelatorio(Args):
    pass


@tool("montar_relatorio", "relatorio", """
    Monta o relatório consolidado em Markdown a partir da base: resumo executivo, tabela comparativa
    dos campos, análise qualitativa, respostas às $n_perguntas perguntas e alertas. Tudo é montado
    pelo código; nenhum número é redigitado. Se faltar alguma parte, a chamada é recusada com a
    lista do que falta.""")
async def montar_relatorio(call: Call, args: MontarRelatorio) -> dict[str, Any]:
    missing = await pending(call, include_report=False)
    if missing:
        raise ToolRejected(
            "O relatório não pode ser montado: ainda há pendências.",
            problemas=[f"{item['o_que_falta']}" + (f" — {item['entidade']}" if item.get("entidade") else "")
                       + (f" — {item['pergunta_id']}" if item.get("pergunta_id") else "") for item in missing])
    states = await gather(call)
    if not states:
        raise ToolRejected("O usuário não tem documento indexado.")
    answers = await call.deps.store.answers(call.caller.user_id, call.caller.colecao_id)
    markdown = build_report(
        [{"extracao": state.extraction, "analise": state.analysis, "resumo": state.summary,
          "alertas": [alert for alert in state.alerts if alert["codigo"] not in INTERNAL_ALERTS]} for state in states],
        [{"pergunta_id": answer.pergunta_id, "renderizado": answer.renderizado, "citacoes": answer.citacoes}
         for answer in answers],
        datetime.now(UTC))
    await call.deps.store.save_report(call.caller, markdown)
    return {"montado": True, "entidades": [state.name for state in states], "caracteres": len(markdown)}


class LerRelatorio(Args):
    pass


@tool("ler_relatorio", "relatorio", """
    Devolve o relatório consolidado mais recente do usuário, em Markdown.""")
async def ler_relatorio(call: Call, args: LerRelatorio) -> dict[str, Any]:
    report = await call.deps.store.report(call.caller.user_id, call.caller.colecao_id)
    if report is None:
        return {"relatorio": None}
    return {"relatorio": report.markdown, "gerado_em": report.at.isoformat()}
