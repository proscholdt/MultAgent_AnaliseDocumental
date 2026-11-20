"""Uma execução como grafo (LangGraph): o orquestrador decide, os especialistas fazem, o código confere.

    START → orquestrador ──(delegações)──► delegar (uma por delegação, em paralelo) ──► orquestrador
                         └─(terminado, ou nada a delegar)──► conferencia ──(completo)──► fim → END
                                                                         └─(falta algo)──► orquestrador

- **orquestrador**: um agente PydanticAI. Lê pendências e documentos pelo servidor MCP e devolve
  uma decisão estruturada (`Decisao`): as delegações desta onda, se terminou e a mensagem. Ele
  não chama os especialistas: o grafo chama.
- **delegar**: uma chamada A2A a um especialista. As delegações de uma onda rodam em paralelo
  (`Send`), e o grafo só volta ao orquestrador quando todas terminam.
- **conferencia**: o código pergunta ao servidor MCP o que falta. Se faltar, o orquestrador
  roda de novo com a lista (até `rodadas_pendencias` vezes). "Terminei" dito pelo modelo não
  encerra nada.
- **fim**: registra o turno na conversa.

Memória: o grafo tem checkpoint por conversa (`thread_id`). Os turnos anteriores da conversa
ficam no estado e entram no pedido do orquestrador: uma pergunta de seguimento ("e a menor?")
sabe qual foi a anterior. O token do usuário e os objetos da execução não entram no estado
(que é gravado em disco): ficam no contexto do grafo, montado a cada execução.
"""
from collections.abc import Callable
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from app import pacotes, telemetria
from app.dominio.relatorio import (
    ANSWERED,
    FOR_THE_CHAT,
    INTENT_TYPE,
    REPORT_TYPES,
    hold_early,
    hold_report_only,
    latin_only,
    official_task,
    out_of_scope,
    report_question_ids,
)
from app.nucleo import mcp
from app.nucleo.agentes import ORCHESTRATOR, AgentFile
from app.nucleo.custos import Ledger, cost_usd
from app.nucleo.executar import AgentRun, Budget, atualizar_instrumentacao, run_agent
from app.nucleo.tokens import Identity
from app.orquestrador.conferencia import check
from app.orquestrador.entradas import (
    reports_input,
    retry_input,
    turn_answer,
)
from app.orquestrador.especialistas_a2a import delegate, for_the_model
from app.orquestrador.estado import Decisao, Deps, Estado
from app.orquestrador.execucoes import Run, Trace, now
from app.orquestrador.metricas import (
    _DELEGACOES,
    _ESPECIALISTA_S,
    _INTENCOES,
    _STOPPED,
    registrar_uso,
)


class Execution:
    """Os objetos de uma execução, fora do estado do grafo: o token do usuário não vai para o disco."""

    def __init__(self, run: Run, user_token: str, deps: Deps) -> None:
        self.run, self.deps, self.settings = run, deps, deps.settings
        self.settings.reload()  # o config/ajustes.yaml de agora vale para esta execução inteira
        atualizar_instrumentacao(self.settings)
        self.identity = Identity(run.id, run.user_id, user_token,
                                 dominio=run.dominio if run.colecao_id else None, colecao_id=run.colecao_id)
        self.budget = Budget(self.settings.execucao_max_tokens, self.settings.execucao_max_segundos)
        self.ledger = Ledger(self.settings.precos)
        self.trace: Trace = deps.runs.trace(run.id)
        self.client = deps.specialists(self.identity)
        self.spec: AgentFile | None = None
        self.team: set[str] = set()
        self._questions: dict[str, str] | None = None

    # --- registro ---------------------------------------------------------------------------

    def record(self, agente: str, doc_id: str | None, modelo: str, uso: dict, passos: list[dict],
               mensagens: list[dict]) -> None:
        """O gasto de uma chamada de agente: no custo da execução, nas métricas e no trace."""
        self.ledger.add(agente, modelo, uso, doc_id)
        registrar_uso(agente, modelo, uso, cost_usd(modelo, uso, self.ledger.prices) if modelo else None)
        for step in passos:
            kind = "raciocinio" if "raciocinio" in step else "tool"
            fields = {"texto": step["raciocinio"]} if kind == "raciocinio" else step
            self.trace.add(kind, agente=agente, doc_id=doc_id, **fields)
        if mensagens:
            self.deps.runs.save_messages(self.run.id, agente, doc_id, mensagens)

    # --- nós --------------------------------------------------------------------------------

    async def rotear(self, turns: list[dict]) -> None:
        """Antes do orquestrador: o roteador decide a intenção da mensagem, se tiver certeza.
        Sem certeza (ou sem roteador), o tipo continua "auto" e o orquestrador decide."""
        roteador = self.deps.roteador
        if roteador is not None and self.run.colecao_id:
            # As frases de exemplo são do domínio: as do pacote, se ele as traz. Num domínio sem
            # frases próprias, decide o orquestrador.
            path = pacotes.arquivo_do_pacote(self.run.dominio, "intencoes.yaml")
            roteador = roteador.para(path) if path else (roteador if self.run.dominio == pacotes.PADRAO else None)
        if roteador is None or self.run.tipo != "auto":
            return
        try:
            async with self.deps.engine.connect(mcp.SYSTEM, self.identity) as session:
                documentos = (await session.call("listar_documentos"))["documentos"]
        except mcp.McpError:
            documentos = []  # sem a lista, o roteador não acha a empresa: relatório vira o consolidado
        anterior = turns[-1].get("pedido") if turns else None
        try:
            escolha = await roteador.escolher(self.run.pedido, anterior, documentos)
        except Exception as error:  # noqa: BLE001 - roteador fora do ar: o orquestrador decide, como antes
            self.trace.add("roteador", decidiu=False, motivo=f"{type(error).__name__}: {error}"[:300])
            return
        if escolha.tokens:
            self.ledger.add("roteador", escolha.modelo, {"entrada": escolha.tokens, "total": escolha.tokens,
                                                         "chamadas": 1})
        self.trace.add("roteador", decidiu=escolha.intencao is not None, **escolha.view())
        if escolha.intencao is None:
            return
        self.run.tipo, self.run.doc_id = INTENT_TYPE[escolha.intencao], escolha.doc_id
        self.trace.add("intencao", intencao=escolha.intencao, tipo_execucao=self.run.tipo, doc_id=self.run.doc_id,
                       fonte="roteador")
        _INTENCOES.add(1, {"intencao": escolha.intencao, "fonte": "roteador"})
        self.deps.runs.save(self.run)

    async def orquestrador(self, state: Estado) -> dict:
        if self.run.tipo == "fora_do_escopo" and not state.get("historico"):
            # O roteador já decidiu que está fora do escopo: nada a delegar, e o LLM nem é chamado.
            return {"historico": [], "entrada": None, "ondas": state.get("ondas", 0) + 1,
                    "relatos": {"substituir": []}, "mensagem": out_of_scope(self.run.dominio)[0],
                    "decisao": {"delegacoes": [], "terminado": True}}
        if await self.answered(state):
            # A resposta já está na base: o resto da lista do relatório não é deste pedido. Sem
            # isto, numa execução real o orquestrador seguiu por mais sete ondas depois da resposta
            # gravada (as outras quatro perguntas, os resumos, a montagem): 422 s em vez de ~170.
            self.trace.add("respondida", onda=state.get("ondas", 0))
            return {"entrada": None, "relatos": {"substituir": []}, "mensagem": ANSWERED,
                    "decisao": {"delegacoes": [], "terminado": True}}
        prompt = state.get("entrada") or reports_input(state.get("relatos") or [])
        outcome: AgentRun = await run_agent(self.spec, prompt, identity=self.identity, engine=self.deps.engine,
                                            budget=self.budget, output_type=Decisao, history=state.get("historico"))
        self.record(ORCHESTRATOR, None, outcome.modelo, outcome.uso, outcome.passos, outcome.mensagens)
        ondas = state.get("ondas", 0) + 1
        self.trace.add("orquestrador", rodada=state.get("rodada", 1), onda=ondas, estado=outcome.estado,
                       erro=outcome.erro, segundos=outcome.segundos, modelo=outcome.modelo, uso=outcome.uso,
                       decisao=outcome.saida.model_dump() if outcome.saida else None)
        update: dict[str, Any] = {"historico": outcome.historico, "entrada": None, "ondas": ondas,
                                  "relatos": {"substituir": []}, "decisao": None}
        if outcome.estado in ("sem_permissao", "erro"):
            return {**update, "falha": outcome.erro or "o orquestrador falhou"}
        if outcome.estado in _STOPPED:
            return {**update, "parada": outcome.erro}
        decision: Decisao = outcome.saida
        if self.run.tipo == "auto":  # mensagem de conversa: a primeira decisão diz o que ela é
            self.run.tipo = INTENT_TYPE[decision.intencao]
            if self.run.tipo == "relatorio_empresa":
                self.run.doc_id = decision.empresa
            self.trace.add("intencao", intencao=decision.intencao, tipo_execucao=self.run.tipo, doc_id=self.run.doc_id,
                           fonte="llm")
            _INTENCOES.add(1, {"intencao": decision.intencao, "fonte": "llm"})
            self.deps.runs.save(self.run)
            if self.run.tipo == "fora_do_escopo":
                # Nada é delegado, e a mensagem do orquestrador não vai ao usuário: o texto é fixo.
                return {**update, "mensagem": out_of_scope(self.run.dominio)[0], "decisao": {"delegacoes": [], "terminado": True}}
        elif self.run.tipo == "relatorio_empresa" and not self.run.doc_id and decision.empresa:
            self.run.doc_id = decision.empresa  # a conferência pediu a empresa de novo
            self.trace.add("intencao", intencao=decision.intencao, tipo_execucao=self.run.tipo, doc_id=self.run.doc_id)
            self.deps.runs.save(self.run)
        # Delegação a quem não existe vira relato de erro, que o orquestrador recebe na onda seguinte.
        unknown = [{"agente": item.agente, "estado": "erro", "doc_id": item.doc_id,
                    "erro": f"não há especialista '{item.agente}'"} for item in decision.delegacoes
                   if item.agente not in self.team]
        valid = [item.model_dump() for item in decision.delegacoes if item.agente in self.team]
        valid, early = await self.in_order(valid)
        return {**update, "relatos": {"substituir": unknown + early}, "mensagem": latin_only(decision.mensagem),
                "decisao": {"delegacoes": valid, "terminado": decision.terminado}}

    async def answered(self, state: Estado) -> bool:
        """Numa pergunta, depois de uma onda em que o pesquisador concluiu: a resposta deste pedido
        já está gravada? Quem confere é a mesma conferência do fim, pelo servidor MCP."""
        if self.run.tipo != "pergunta" or not any(
                report.get("agente") == "pesquisador" and report.get("estado") == "concluido"
                for report in state.get("relatos") or []):
            return False
        try:
            complete, _, _ = await check(self.run, self.identity, self.deps, finished=False)
        except mcp.McpError:
            return False  # sem a conferência agora, o orquestrador decide, e a do fim confere
        return complete

    async def in_order(self, delegations: list[dict]) -> tuple[list[dict], list[dict]]:
        """Separa as delegações que chegam antes da hora. No relatório, a ordem do trabalho é do
        servidor MCP (`pendencias`: cada item diz se está liberado ou o que aguarda), e as tools de
        gravação recusam o que vem fora de ordem. Mandar o especialista assim mesmo só gasta: ele
        trabalha, a gravação é recusada, e o orquestrador repete (visto numa execução real: as cinco
        perguntas pedidas quatro vezes antes da análise). A delegação cujos itens estão todos
        aguardando não sai: volta ao orquestrador como relato, com o que falta antes e o que já pode
        ser feito. Delegação sem item na lista (refazer algo, uma pergunta livre) segue.
        Numa pergunta, a lista do relatório não manda: só o que é do relatório fica (`in_scope`)."""
        if delegations and self.run.tipo == "pergunta":
            go, held = hold_report_only(delegations)
        elif not delegations or self.run.tipo not in REPORT_TYPES:
            return delegations, []
        else:
            go, held = await self._ready_now(delegations)
        for report in held:
            self.trace.add("barrada", agente=report["agente"], doc_id=report["doc_id"], motivo=report["erro"])
        return go, held

    async def _ready_now(self, delegations: list[dict]) -> tuple[list[dict], list[dict]]:
        arguments = {"doc_id": self.run.doc_id} if self.run.tipo == "relatorio_empresa" and self.run.doc_id else {}
        try:
            async with self.deps.engine.connect(mcp.SYSTEM, self.identity) as session:
                pending = (await session.call("pendencias", arguments))["pendencias"]
        except mcp.McpError:
            return delegations, []  # sem a lista, quem confere é a tool de gravação, como antes
        return hold_early(delegations, pending)

    def after_orchestrator(self, state: Estado) -> list[Send] | str:
        if state.get("falha"):
            return "fim"
        decision = state.get("decisao") or {}
        out_of_waves = state.get("ondas", 0) >= self.settings.max_ondas
        if state.get("parada") or out_of_waves or not self.budget.tokens_left or not self.budget.seconds_left:
            return "conferencia"
        if decision.get("delegacoes"):
            return [Send("delegar", item) for item in decision["delegacoes"]]
        if state.get("relatos") and not decision.get("terminado"):
            return "orquestrador"  # só houve delegação inválida: o orquestrador vê o erro e decide de novo
        return "conferencia"

    async def report_questions(self) -> dict[str, str]:
        """Os enunciados oficiais das cinco perguntas, do servidor MCP (uma vez por execução)."""
        if self._questions is None:
            try:
                async with self.deps.engine.connect(mcp.SYSTEM, self.identity) as session:
                    listed = (await session.call("ler_respostas")).get("perguntas_do_relatorio") or []
                self._questions = {entry["pergunta_id"]: entry["pergunta"] for entry in listed}
            except mcp.McpError:
                return {}  # sem os enunciados, a tarefa segue como o orquestrador escreveu
        return self._questions

    async def official_task(self, item: dict) -> dict:
        """A tarefa do pesquisador com o enunciado oficial da pergunta citada (veja o domínio)."""
        questions = await self.report_questions() if report_question_ids(item["tarefa"]) else {}
        return official_task(item, questions)

    async def delegar(self, item: dict) -> dict:
        if item["agente"] == "pesquisador":
            item = ({**item, "tarefa": item["tarefa"] + FOR_THE_CHAT} if self.run.tipo == "pergunta"
                    else await self.official_task(item))
        # Quem está trabalhando agora: o especialista só entra de novo no trace quando termina.
        self.trace.add("delegando", agente=item["agente"], doc_id=item.get("doc_id"), tarefa=item["tarefa"],
                       **({"tarefa_do_orquestrador": item["tarefa_do_orquestrador"]}
                          if "tarefa_do_orquestrador" in item else {}))
        report = await delegate(self.client, item["agente"], item["tarefa"], item.get("doc_id"), self.budget,
                                self.run.conversa_id or None)
        detail = report.get("execucao") or {}
        usage = detail.get("uso") or {}
        self.budget.spend(int(usage.get("total", 0)))  # o gasto do especialista conta no teto da execução
        self.record(report["agente"], item.get("doc_id"), detail.get("modelo", ""), usage, detail.get("passos") or [],
                    detail.get("mensagens") or [])
        _DELEGACOES.add(1, {"agente": report["agente"], "estado": report["estado"]})
        telemetria.trace.get_current_span().set_attribute("delegacao.estado", report["estado"])
        if detail.get("segundos") is not None:
            _ESPECIALISTA_S.record(float(detail["segundos"]), {"agente": report["agente"]})
        self.trace.add("especialista", agente=report["agente"], doc_id=item.get("doc_id"), tarefa=item["tarefa"],
                       estado=report["estado"], erro=report.get("erro"), gravacoes=report.get("gravacoes"),
                       segundos=detail.get("segundos"), modelo=detail.get("modelo"), uso=usage,
                       memoria=detail.get("memoria"))
        return {"relatos": [for_the_model(report)]}

    async def conferencia(self, state: Estado) -> dict:
        rodada = state.get("rodada", 1)
        finished = bool((state.get("decisao") or {}).get("terminado")) and not state.get("parada")
        complete, pending, result = await check(self.run, self.identity, self.deps, finished)
        self.trace.add("conferencia", rodada=rodada, completo=complete, pendencias=len(pending))
        update: dict[str, Any] = {"completo": complete, "pendencias": pending, "resultado": result}
        stopped = state.get("parada") and "turnos" not in (state.get("parada") or "")
        if complete or stopped or rodada > self.settings.rodadas_pendencias or not self.budget.tokens_left \
                or not self.budget.seconds_left:
            return {**update, "rodada": rodada}
        # Nova rodada: o orquestrador começa uma conversa nova, com o pedido e a lista do que falta.
        return {**update, "rodada": rodada + 1, "historico": [], "ondas": 0, "parada": None,
                "relatos": {"substituir": []},
                "entrada": retry_input(self.run, pending, state.get("parada"), state.get("turnos"))}

    def after_check(self, state: Estado) -> str:
        return "orquestrador" if state.get("entrada") else "fim"

    async def fim(self, state: Estado) -> dict:
        estado = "falhou" if state.get("falha") else "completa" if state.get("completo") else "incompleta"
        return {"turnos": [{"execucao_id": self.run.id, "pedido": self.run.pedido, "tipo": self.run.tipo,
                            "estado": estado, "resposta": turn_answer(self.run, state), "em": now()}]}

    def _no(self, nome: str, funcao: Callable[[Any], Any]) -> Callable[[Any], Any]:
        """Um nó do grafo dentro de um span: no Tempo, a execução aparece onda a onda."""

        async def executar(entrada: Any) -> Any:
            atributos = {"execucao.id": self.run.id}
            if nome == "delegar":
                atributos |= {"agente": entrada["agente"], "doc_id": entrada.get("doc_id") or ""}
            titulo = f"grafo.{nome}" + (f" {entrada['agente']}" if nome == "delegar" else "")
            with telemetria.tracer().start_as_current_span(titulo, attributes=atributos):
                return await funcao(entrada)

        return executar

    def graph(self):
        graph = StateGraph(Estado)
        graph.add_node("orquestrador", self._no("orquestrador", self.orquestrador))
        graph.add_node("delegar", self._no("delegar", self.delegar))
        graph.add_node("conferencia", self._no("conferencia", self.conferencia))
        graph.add_node("fim", self._no("fim", self.fim))
        graph.add_edge(START, "orquestrador")
        graph.add_conditional_edges("orquestrador", self.after_orchestrator,
                                    ["delegar", "orquestrador", "conferencia", "fim"])
        graph.add_edge("delegar", "orquestrador")
        graph.add_conditional_edges("conferencia", self.after_check, ["orquestrador", "fim"])
        graph.add_edge("fim", END)
        return graph.compile(checkpointer=self.deps.checkpointer)
