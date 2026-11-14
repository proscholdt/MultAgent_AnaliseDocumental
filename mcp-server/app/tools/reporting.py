"""Resumo executivo, relatório final, revisão das fontes e controle de pendências."""
from datetime import UTC, datetime
from typing import Any

from pydantic import Field

from app.catalog import Args, Call, tool
from app.errors import ToolRejected
from app.formulas import AlertDraft
from app.models import Summary
from app.pending import REVIEW_DONE, gather, pending, summary_blockers
from app.report import MARKER, build_company_report, build_report
from app.text import check_script, squash, unbacked_numbers
from app.tools.answers import markers_for

_SUMMARY_LINES = 5  # o teste pede resumo executivo de até 5 linhas por empresa
_SUMMARY_CHARS = 900


class SalvarResumo(Args):
    doc_id: str = Field(description="Documento (uma empresa).")
    texto: str = Field(min_length=40, max_length=2000, description="O resumo executivo da empresa, em até 5 "
                       "linhas. Valores vão como marcador: {{receita_liquida}}, {{receita_liquida.variacao}}, "
                       "{{ebitda.calculado}}, {{lucro_liquido.comparavel}}. Não escreva números por conta própria.")


@tool("salvar_resumo", "relatorio", """
    Grava o resumo executivo de uma empresa (até 5 linhas). Os valores entram por marcador, que o
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
        raise ToolRejected("O resumo usa a extração, a análise qualitativa e os alertas da empresa. Ainda falta o "
                           "que está na lista. Termine sem gravar e diga isso.", problemas=blockers)
    check_script(args.texto, "O resumo")
    lines = [squash(line) for line in args.texto.splitlines() if line.strip()]
    if len(lines) > _SUMMARY_LINES:
        raise ToolRejected(f"O resumo tem {len(lines)} linhas; o limite é {_SUMMARY_LINES}.")
    text = "\n".join(lines)
    loose = unbacked_numbers(MARKER.sub(" ", text), [])
    if loose:
        raise ToolRejected(f"O resumo traz números escritos direto no texto: {', '.join(loose)}. Use os "
                           "marcadores dos indicadores.")
    rendered = markers_for(states).render(text, default_doc=args.doc_id, brief=True)
    if len(rendered.text) > _SUMMARY_CHARS * 2:
        raise ToolRejected("O resumo ficou longo demais para um resumo executivo: encurte as linhas.")
    await call.deps.store.save_summary(Summary(
        user_id=call.caller.user_id, doc_id=args.doc_id, fingerprint=state.document.fingerprint,
        run_id=call.caller.run_id, texto=text, renderizado=rendered.text))
    return {"salvo": {"empresa": state.name, "linhas": len(lines)}, "resumo": rendered.text}


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


class Pendencias(Args):
    doc_id: str | None = Field(None, description="Só para o relatório de UMA empresa: o documento dela. Sem "
                               "doc_id, a lista é a do relatório consolidado.")


@tool("pendencias", "controle", """
    Diz o que ainda falta para o relatório completo do usuário: extração, revisão das fontes,
    análise qualitativa, respostas às cinco perguntas, resumos e a montagem do relatório. Cada item
    indica o documento, o agente que costuma resolver e se já pode ser feito: 'liberada' true, ou
    false com o que ele 'aguarda' (as perguntas esperam a extração e a análise de todos os
    documentos; o resumo espera a extração, a revisão e a análise da empresa). Com doc_id, é o que
    falta para o relatório só daquela empresa (sem as perguntas comparativas). Lista vazia = está
    completo.""")
async def pendencias(call: Call, args: Pendencias) -> dict[str, Any]:
    items = await pending(call, doc_id=args.doc_id)
    return {"completo": not items, "pendencias": items}


class RelatorioEmpresa(Args):
    doc_id: str = Field(description="Documento (uma empresa).")


@tool("montar_relatorio_empresa", "relatorio", """
    Monta o relatório de UMA empresa em Markdown a partir da base: resumo executivo, indicadores
    com a página, análise qualitativa com as citações e alertas. Precisa da extração, da revisão
    das fontes, da análise e do resumo da empresa; se faltar algo, a chamada é recusada com a
    lista. As perguntas comparativas não entram: são do relatório consolidado.""")
async def montar_relatorio_empresa(call: Call, args: RelatorioEmpresa) -> dict[str, Any]:
    missing = await pending(call, include_report=False, doc_id=args.doc_id)
    if missing:
        raise ToolRejected("O relatório da empresa não pode ser montado: ainda há pendências.",
                           problemas=[item["o_que_falta"] for item in missing])
    state = next(state for state in await gather(call) if state.document.doc_id == args.doc_id)
    markdown = build_company_report(
        {"extracao": state.extraction, "analise": state.analysis, "resumo": state.summary,
         "alertas": [alert for alert in state.alerts if alert["codigo"] != REVIEW_DONE]}, datetime.now(UTC))
    await call.deps.store.save_company_report(call.caller, args.doc_id, state.document.fingerprint, markdown)
    return {"montado": True, "empresa": state.name, "caracteres": len(markdown)}


@tool("ler_relatorio_empresa", "relatorio", """
    Devolve o relatório mais recente de uma empresa, em Markdown.""")
async def ler_relatorio_empresa(call: Call, args: RelatorioEmpresa) -> dict[str, Any]:
    report = await call.deps.store.company_report(call.caller.user_id, args.doc_id)
    if report is None:
        return {"relatorio": None}
    return {"relatorio": report.markdown, "gerado_em": report.at.isoformat(), "doc_id": args.doc_id}


class MontarRelatorio(Args):
    pass


@tool("montar_relatorio", "relatorio", """
    Monta o relatório consolidado em Markdown a partir da base: resumo executivo, tabela comparativa
    dos indicadores, análise qualitativa, respostas às cinco perguntas e alertas. Tudo é montado
    pelo código; nenhum número é redigitado. Se faltar alguma parte, a chamada é recusada com a
    lista do que falta.""")
async def montar_relatorio(call: Call, args: MontarRelatorio) -> dict[str, Any]:
    missing = await pending(call, include_report=False)
    if missing:
        raise ToolRejected(
            "O relatório não pode ser montado: ainda há pendências.",
            problemas=[f"{item['o_que_falta']}" + (f" — {item['empresa']}" if item.get("empresa") else "")
                       + (f" — {item['pergunta_id']}" if item.get("pergunta_id") else "") for item in missing])
    states = await gather(call)
    if not states:
        raise ToolRejected("O usuário não tem documento indexado.")
    answers = await call.deps.store.answers(call.caller.user_id)
    markdown = build_report(
        [{"extracao": state.extraction, "analise": state.analysis, "resumo": state.summary,
          "alertas": [alert for alert in state.alerts if alert["codigo"] != REVIEW_DONE]} for state in states],
        [{"pergunta_id": answer.pergunta_id, "renderizado": answer.renderizado, "citacoes": answer.citacoes}
         for answer in answers],
        datetime.now(UTC))
    await call.deps.store.save_report(call.caller, markdown)
    return {"montado": True, "empresas": [state.name for state in states], "caracteres": len(markdown)}


class LerRelatorio(Args):
    pass


@tool("ler_relatorio", "relatorio", """
    Devolve o relatório consolidado mais recente do usuário, em Markdown.""")
async def ler_relatorio(call: Call, args: LerRelatorio) -> dict[str, Any]:
    report = await call.deps.store.report(call.caller.user_id)
    if report is None:
        return {"relatorio": None}
    return {"relatorio": report.markdown, "gerado_em": report.at.isoformat()}
