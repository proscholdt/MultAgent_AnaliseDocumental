"""Uma execução: o orquestrador decide, os especialistas fazem, o código confere.

1. Descobre os especialistas no ar (catálogo A2A) e monta uma tool para cada um.
2. Roda o orquestrador com o pedido. Ele decide quem chamar, em que ordem e com que tarefa.
3. No fim, o CÓDIGO pergunta ao servidor MCP o que falta. Se faltar, o orquestrador roda de
   novo com a lista (até `rodadas_pendencias` vezes). Depois disso a execução termina como
   incompleta, com o motivo.

Não há estado de conversa entre as rodadas: tudo o que foi feito está na base do servidor MCP,
e a lista de pendências diz o que resta.
"""
import json
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app import mcp
from app.a2a import SpecialistClient, specialist_tool
from app.config import Settings
from app.costs import Ledger
from app.execucoes import Run, Runs, now
from app.registry import ORCHESTRATOR, Registry
from app.runner import Budget, Engine, run_agent
from app.tokens import Identity

_STOPPED = ("orcamento_esgotado", "tempo_esgotado")


@dataclass
class Deps:
    settings: Settings
    registry: Registry
    engine: Engine
    runs: Runs
    specialists: Callable[[Identity], SpecialistClient]


class RunFailed(Exception):
    pass


def latin_only(text: str) -> str:
    """A mensagem final do orquestrador sem as palavras em outro alfabeto. O modelo pequeno solta
    uma de vez em quando ("Não մնou pendência"); no servidor MCP isso é recusado na gravação, mas a
    mensagem de estado não passa por lá."""
    def foreign(word: str) -> bool:
        return any(ch.isalpha() and ch not in "ªºµ" and "LATIN" not in unicodedata.name(ch, "") for ch in word)

    return " ".join(word for word in text.split(" ") if not foreign(word))


def first_input(run: Run) -> str:
    if run.tipo == "relatorio":
        return ("Produza o relatório consolidado completo dos documentos do usuário: extração, revisão das "
                "fontes, análise qualitativa, as cinco perguntas, os resumos e a montagem do relatório.")
    if run.tipo == "pergunta":
        return f"Responda à pergunta do usuário, com as fontes. Pergunta: «{run.pedido}»"
    return run.pedido


def retry_input(run: Run, pending: list[dict[str, Any]], reason: str | None) -> str:
    lines = [first_input(run), "", "Uma rodada anterior deste pedido terminou sem concluir."]
    if reason:
        lines.append(f"Motivo: {reason}.")
    if pending:
        lines += ["O que ainda falta, segundo a base:", json.dumps(pending, ensure_ascii=False, indent=1)]
    lines.append("O que já foi feito está gravado e não precisa ser refeito. Resolva só o que falta.")
    return "\n".join(lines)


def _after(stamp: str | None, start: str) -> bool:
    if not stamp:
        return False
    moment = datetime.fromisoformat(stamp)
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)) >= datetime.fromisoformat(start)


async def check(run: Run, identity: Identity, deps: Deps, finished: bool) -> tuple[bool, list[dict[str, Any]], dict]:
    """A conferência final, feita pelo código com as tools de leitura do "sistema". Devolve se
    o pedido está completo, o que falta e o resultado para o usuário."""
    async with deps.engine.connect(mcp.SYSTEM, identity) as server:
        pending = (await mcp.call(server, "pendencias"))["pendencias"]
        if run.tipo == "relatorio":
            report = await mcp.call(server, "ler_relatorio") if not pending else {}
            return not pending, pending, {"relatorio": report.get("relatorio"), "gerado_em": report.get("gerado_em")}
        if run.tipo == "pergunta":
            answers = (await mcp.call(server, "ler_respostas"))["respostas"]
            new = [answer for answer in answers if _after(answer.get("gravada_em"), run.iniciada_em or run.criada_em)]
            missing = [] if new else [{"o_que_falta": "resposta à pergunta do usuário, gravada com salvar_resposta "
                                       "(pergunta_id 'livre')", "agente": "pesquisador", "pergunta": run.pedido}]
            return bool(new), missing, {"respostas": new}
    # Pedido livre: não há o que conferir além de o orquestrador ter chegado ao fim. As
    # pendências do relatório vão junto, como informação.
    return finished, pending, {}


async def execute(run: Run, user_token: str, deps: Deps) -> Run:
    settings, trace = deps.settings, deps.runs.trace(run.id)
    identity = Identity(run.id, run.user_id, user_token)
    budget = Budget(settings.execucao_max_tokens, settings.execucao_max_segundos)
    ledger = Ledger(settings.precos)
    run.estado, run.iniciada_em = "em_andamento", now()
    try:
        deps.runs.save(run)
        trace.add("inicio", execucao=run.id, pedido_tipo=run.tipo, pedido=run.pedido)
        snapshot = deps.registry.current()
        run.agentes_digest = snapshot.digest
        spec = snapshot.orchestrator
        if spec is None:
            problem = snapshot.errors.get(f"{ORCHESTRATOR}.md", "o arquivo não existe")
            raise RunFailed(f"Não há orquestrador: agentes/{ORCHESTRATOR}.md ({problem}).")

        client = deps.specialists(identity)
        catalog = await client.catalog()
        unknown = [entry["nome"] for entry in catalog["agentes"] if entry["tools"] is None]
        if unknown:
            raise RunFailed("O serviço dos especialistas não conseguiu falar com o servidor MCP.")
        locked = [entry["nome"] for entry in catalog["agentes"] if not entry["tools"]]
        if locked:
            raise RunFailed("Agente sem entrada na política do servidor MCP (policy.yaml): " + ", ".join(locked)
                            + ". Acrescente a entrada ou remova o arquivo do agente.")
        trace.add("especialistas", agentes=[entry["nome"] for entry in catalog["agentes"]], digest=catalog["digest"])

        def on_report(report: dict[str, Any], doc_id: str | None) -> None:
            detail = report.get("execucao") or {}
            usage = detail.get("uso") or {}
            ledger.add(report["agente"], detail.get("modelo", ""), usage, doc_id)
            budget.used += int(usage.get("total", 0))  # o gasto do especialista conta no teto da execução
            for step in detail.get("passos") or []:
                trace.add("tool", agente=report["agente"], doc_id=doc_id, **step)
            trace.add("especialista", agente=report["agente"], doc_id=doc_id, estado=report["estado"],
                      erro=report.get("erro"), gravacoes=report.get("gravacoes"), segundos=detail.get("segundos"),
                      modelo=detail.get("modelo"), uso=usage)

        tools = [specialist_tool(entry, client, budget, on_report) for entry in catalog["agentes"]]
        entrada, complete, reason = first_input(run), False, None
        for rodada in range(1, settings.rodadas_pendencias + 2):
            run.rodadas = rodada
            outcome = await run_agent(spec, entrada, identity=identity, engine=deps.engine, budget=budget, tools=tools)
            ledger.add(spec.nome, outcome.modelo, outcome.uso)
            for step in outcome.passos:
                if step["tool"] not in {entry["nome"] for entry in catalog["agentes"]}:
                    trace.add("tool", agente=spec.nome, **step)
            trace.add("orquestrador", rodada=rodada, estado=outcome.estado, erro=outcome.erro,
                      segundos=outcome.segundos, modelo=outcome.modelo, uso=outcome.uso)
            if outcome.estado in ("sem_permissao", "erro"):
                raise RunFailed(outcome.erro or "o orquestrador falhou")
            run.mensagem, reason = latin_only(outcome.texto), outcome.erro
            complete, run.pendencias, run.resultado = await check(run, identity, deps, outcome.estado == "concluido")
            trace.add("conferencia", rodada=rodada, completo=complete, pendencias=len(run.pendencias))
            if complete or outcome.estado in _STOPPED or not budget.tokens_left or not budget.seconds_left:
                break
            entrada = retry_input(run, run.pendencias, reason)
            deps.runs.save(run)
        run.estado = "completa" if complete else "incompleta"
        if not complete:
            run.erro = reason or f"terminou com pendências depois de {run.rodadas} rodada(s) do orquestrador"
    except RunFailed as error:
        run.estado, run.erro = "falhou", str(error)
    except Exception as error:  # noqa: BLE001 - a execução roda em segundo plano: a falha tem de ficar no estado
        run.estado, run.erro = "falhou", f"{type(error).__name__}: {error}"[:500]
    finally:
        run.uso, run.terminada_em = ledger.summary(), now()
        trace.add("fim", estado=run.estado, erro=run.erro, uso=run.uso["total"])
        deps.runs.save(run)
    return run
