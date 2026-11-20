"""A conferência final, feita pelo código com as tools de leitura do "sistema": o pedido está completo?"""
from datetime import UTC, datetime
from typing import Any

from app.dominio.relatorio import (
    out_of_scope,
)
from app.nucleo import mcp
from app.nucleo.agentes import ORCHESTRATOR
from app.nucleo.tokens import Identity
from app.orquestrador.estado import Deps
from app.orquestrador.execucoes import Run


def _after(stamp: str | None, start: str) -> bool:
    if not stamp:
        return False
    moment = datetime.fromisoformat(stamp)
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)) >= datetime.fromisoformat(start)

async def check(run: Run, identity: Identity, deps: Deps, finished: bool) -> tuple[bool, list[dict[str, Any]], dict]:
    """A conferência final, feita pelo código com as tools de leitura do "sistema". Devolve se
    o pedido está completo, o que falta e o resultado para o usuário."""
    if run.tipo == "fora_do_escopo":
        return True, [], {"fora_do_escopo": True, "opcoes": out_of_scope(run.dominio)[1]}
    if run.tipo == "relatorio_empresa":
        return await check_company_report(run, identity, deps)
    async with deps.engine.connect(mcp.SYSTEM, identity) as session:
        pending = (await session.call("pendencias"))["pendencias"]
        if run.tipo == "relatorio":
            report = await session.call("ler_relatorio") if not pending else {}
            return not pending, pending, {"relatorio": report.get("relatorio"), "gerado_em": report.get("gerado_em")}
        answers = (await session.call("ler_respostas"))["respostas"]
    new = [answer for answer in answers if _after(answer.get("gravada_em"), run.iniciada_em or run.criada_em)]
    if run.tipo == "pergunta":
        missing = [] if new else [{"o_que_falta": "resposta à pergunta do usuário, gravada com salvar_resposta "
                                   "(pergunta_id 'livre')", "agente": "pesquisador", "pergunta": run.pedido}]
        return bool(new), missing, {"respostas": new}
    # Pedido livre: não há o que conferir além de o orquestrador ter chegado ao fim. As respostas
    # gravadas neste pedido e as pendências do relatório vão junto, como informação.
    return finished, pending, {"respostas": new} if new else {}

async def check_company_report(run: Run, identity: Identity, deps: Deps) -> tuple[bool, list[dict[str, Any]], dict]:
    """O relatório de uma empresa: as pendências DELA vazias e o relatório dela montado."""
    if not run.doc_id:
        return False, [{"o_que_falta": "dizer de qual empresa é o relatório: o doc_id em `empresa`, de "
                                       "listar_documentos", "agente": ORCHESTRATOR}], {}
    async with deps.engine.connect(mcp.SYSTEM, identity) as session:
        try:
            pending = (await session.call("pendencias", {"doc_id": run.doc_id}))["pendencias"]
        except mcp.McpError as error:  # doc_id que não existe: o orquestrador escolhe de novo
            run.doc_id = None
            return False, [{"o_que_falta": f"escolher a empresa de novo: {error}", "agente": ORCHESTRATOR}], {}
        report = await session.call("ler_relatorio_empresa", {"doc_id": run.doc_id}) if not pending else {}
    return not pending, pending, {"relatorio": report.get("relatorio"), "gerado_em": report.get("gerado_em"),
                                  "doc_id": run.doc_id}
