"""Uma execução de ponta a ponta: monta a equipe, roda o grafo, avalia o relatório e grava o resultado."""
import time

from app import telemetria
from app.dominio.relatorio import (
    REPORT_TYPES,
)
from app.nucleo.agentes import ORCHESTRATOR, agent_file
from app.orquestrador.entradas import (
    first_input,
    with_team,
)
from app.orquestrador.estado import Deps, Estado, RunFailed
from app.orquestrador.execucoes import Run, now
from app.orquestrador.grafo import Execution
from app.orquestrador.metricas import (
    _EXECUCAO_S,
    _EXECUCOES,
)


def _resumo_da_nota(nota: dict) -> dict:
    """O passo "avaliacao" do trace: o essencial, sem a tabela inteira."""
    if not nota.get("avaliado"):
        return {"avaliado": False, "motivo": nota.get("motivo"), "sem_gabarito": nota.get("sem_gabarito")}
    resumo = nota["resumo"]
    return {"avaliado": True, "documentos": nota["avaliados"], "sem_gabarito": nota["sem_gabarito"],
            "status_certo": f"{resumo['status_certo']['acertos']}/{resumo['status_certo']['de']}",
            "invencoes": f"{resumo['invencao']['inventados']}/{resumo['invencao']['de']}",
            "perguntas": f"{resumo['perguntas_certas']['acertos']}/{resumo['perguntas_certas']['de']}"}

def _rotulo_da_conversa(run: Run, deps: Deps) -> str:
    """"<primeira mensagem> (conv-…)": a lista de conversas da visão geral no Grafana."""
    turnos = deps.runs.of_conversation(run.conversa_id)
    titulo = " ".join((turnos[0].pedido if turnos else run.pedido).split())
    return f"{titulo[:60]}{'…' if len(titulo) > 60 else ''} ({run.conversa_id})"

async def execute(run: Run, user_token: str, deps: Deps) -> Run:
    atributos = {"execucao.id": run.id}
    if run.conversa_id:
        atributos |= {"conversa.id": run.conversa_id, "conversa.rotulo": _rotulo_da_conversa(run, deps)}
    # Com a conversa no baggage, os spans e os logs de todos os serviços desta execução levam o id dela.
    with telemetria.conversa(run.conversa_id), \
            telemetria.tracer().start_as_current_span("execucao", attributes=atributos) as span:
        inicio = time.monotonic()
        try:
            return await _execute(run, user_token, deps)
        finally:
            total = (run.uso or {}).get("total") or {}
            span.set_attributes({"execucao.tipo": run.tipo, "execucao.estado": run.estado,
                                 "execucao.custo_usd": float(total.get("custo_usd") or 0.0),
                                 "execucao.tokens": int((total.get("uso") or {}).get("total") or 0)})
            span.update_name(f"execucao {run.tipo}")
            _EXECUCOES.add(1, {"tipo": run.tipo, "estado": run.estado})
            _EXECUCAO_S.record(time.monotonic() - inicio, {"tipo": run.tipo})

async def _execute(run: Run, user_token: str, deps: Deps) -> Run:
    execution = Execution(run, user_token, deps)
    trace, settings = execution.trace, deps.settings
    run.estado, run.iniciada_em = "em_andamento", now()
    try:
        deps.runs.save(run)
        trace.add("inicio", execucao=run.id, pedido_tipo=run.tipo, pedido=run.pedido, conversa=run.conversa_id)
        snapshot = deps.registry.current()
        run.agentes_digest = snapshot.digest
        if snapshot.orchestrator is None:
            problem = snapshot.errors.get(agent_file(ORCHESTRATOR), "o arquivo não existe")
            raise RunFailed(f"Não há orquestrador: agentes/{agent_file(ORCHESTRATOR)} ({problem}).")
        catalog = await execution.client.catalog()
        if any(entry["tools"] is None for entry in catalog["agentes"]):
            raise RunFailed("Um serviço de especialista não conseguiu falar com o servidor MCP.")
        locked = [entry["nome"] for entry in catalog["agentes"] if not entry["tools"]]
        if locked:
            raise RunFailed("Agente sem entrada na política do servidor MCP (policy.yaml): " + ", ".join(locked)
                            + ". Acrescente a entrada ou remova o arquivo do agente.")
        trace.add("especialistas", agentes=[entry["nome"] for entry in catalog["agentes"]], digest=catalog["digest"],
                  **({"fora_do_ar": catalog["fora_do_ar"]} if catalog.get("fora_do_ar") else {}))
        execution.spec = with_team(snapshot.orchestrator, catalog)
        execution.team = {entry["nome"] for entry in catalog["agentes"]}

        config = {"configurable": {"thread_id": run.conversa_id or run.id},
                  "recursion_limit": 4 * settings.max_ondas * (settings.rodadas_pendencias + 1) + 10}
        graph = execution.graph()
        # Os turnos anteriores da conversa, recuperados do checkpoint.
        turns = list((await graph.aget_state(config)).values.get("turnos") or []) if deps.checkpointer else []
        if turns:
            trace.add("conversa", turnos_anteriores=len(turns))
        await execution.rotear(turns)
        start: Estado = {"execucao_id": run.id, "entrada": first_input(run, turns), "historico": [],
                         "relatos": {"substituir": []}, "decisao": None, "ondas": 0, "rodada": 1, "parada": None,
                         "falha": None, "completo": False, "pendencias": [], "resultado": {}, "mensagem": ""}
        final = await graph.ainvoke(start, config)

        run.rodadas, run.mensagem = final.get("rodada", 1), final.get("mensagem", "")
        run.pendencias, run.resultado = final.get("pendencias") or [], final.get("resultado") or {}
        if final.get("falha"):
            raise RunFailed(final["falha"])
        run.estado = "completa" if final.get("completo") else "incompleta"
        if run.estado == "completa" and run.tipo in REPORT_TYPES and deps.avaliar:
            run.avaliacao = await deps.avaliar(settings, execution.identity, run.tipo, run.doc_id)
            if run.avaliacao is not None:
                trace.add("avaliacao", **_resumo_da_nota(run.avaliacao))
        if not final.get("completo"):
            run.erro = final.get("parada") or f"terminou com pendências depois de {run.rodadas} rodada(s) do orquestrador"
    except RunFailed as error:
        run.estado, run.erro = "falhou", str(error)
    except Exception as error:  # noqa: BLE001 - a execução roda em segundo plano: a falha tem de ficar no estado
        run.estado, run.erro = "falhou", f"{type(error).__name__}: {error}"[:500]
    finally:
        run.uso, run.terminada_em = execution.ledger.summary(), now()
        trace.add("fim", estado=run.estado, erro=run.erro, uso=run.uso["total"])
        deps.runs.save(run)
    return run
