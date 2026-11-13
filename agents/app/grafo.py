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
import dataclasses
import json
import operator
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from pydantic import BaseModel, Field

from app import mcp
from app.a2a import SpecialistClient, delegate, for_the_model
from app.config import Settings
from app.costs import Ledger
from app.execucoes import Run, Runs, Trace, now
from app.registry import ORCHESTRATOR, AgentFile, Registry
from app.runner import AgentRun, Budget, Engine, run_agent
from app.tokens import Identity

_STOPPED = ("limite_de_turnos", "orcamento_esgotado", "tempo_esgotado")
_TURNS_IN_PROMPT = 6
_TURN_CHARS = 900


class Delegacao(BaseModel):
    agente: str = Field(description="Nome do especialista, como está na lista de especialistas.")
    tarefa: str = Field(description="O que ele deve fazer, em uma ou duas frases.")
    doc_id: str | None = Field(description="Documento em que ele deve trabalhar; null se a tarefa não é sobre um "
                               "documento só.")


class Decisao(BaseModel):
    delegacoes: list[Delegacao] = Field(description="As delegações desta onda, executadas em paralelo. Vazia quando "
                                        "não há mais o que delegar.")
    terminado: bool = Field(description="true quando o pedido está atendido (ou não há mais o que fazer).")
    mensagem: str = Field(description="Para o usuário, em duas ou três frases: o que foi feito e o que ficou pendente. "
                          "Sem valores dos documentos.")
    intencao: Literal["pergunta", "relatorio", "fora_do_escopo"] = Field(
        description="O que o pedido atual do usuário é: 'pergunta' (quer saber algo do conteúdo dos documentos), "
                    "'relatorio' (quer o relatório consolidado) ou 'fora_do_escopo' (qualquer outra coisa).")


# A intenção que o orquestrador dá a uma mensagem de conversa vira o tipo da execução, e o tipo
# diz o que a conferência final cobra: pergunta exige resposta gravada com fonte; relatório exige
# o relatório montado. O resto está fora do escopo: o código não executa nada e responde com um
# texto fixo e as opções. Nenhum texto livre do orquestrador chega ao usuário como resposta.
INTENT_TYPE = {"pergunta": "pergunta", "relatorio": "relatorio", "fora_do_escopo": "fora_do_escopo"}

OUT_OF_SCOPE = ("Isso está fora do escopo deste sistema. Ele responde perguntas sobre as demonstrações financeiras "
                "enviadas, sempre com a página de origem, e gera o relatório consolidado. Escolha uma das opções ou "
                "escreva a sua pergunta.")
OPTIONS = [
    {"titulo": "Perguntar sobre um indicador", "exemplo": "Qual empresa teve a maior margem EBITDA?"},
    {"titulo": "Comparar as empresas", "exemplo": "Compare o lucro líquido das empresas"},
    {"titulo": "Entender o resultado", "exemplo": "Quais riscos as empresas destacam?"},
    {"titulo": "Gerar o relatório consolidado", "exemplo": "Gere o relatório consolidado completo"},
]


@dataclass
class Deps:
    settings: Settings
    registry: Registry
    engine: Engine
    runs: Runs
    specialists: Callable[[Identity], SpecialistClient]
    checkpointer: BaseCheckpointSaver | None = None


class RunFailed(Exception):
    pass


def _relatos(old: list[dict], new: Any) -> list[dict]:
    """Os relatos da onda atual. O orquestrador zera a lista ao ler (`{"substituir": [...]}`); cada
    delegação acrescenta o seu."""
    if isinstance(new, dict) and "substituir" in new:
        return list(new["substituir"])
    return (old or []) + list(new or [])


class Estado(TypedDict, total=False):
    # Da conversa: fica no checkpoint de um turno para o outro.
    turnos: Annotated[list[dict], operator.add]
    # Da execução atual: o pedido de cada turno zera estes campos.
    execucao_id: str
    entrada: str | None  # o próximo pedido ao orquestrador; None = os relatos da onda
    historico: list[dict]  # as mensagens do orquestrador nesta rodada
    relatos: Annotated[list[dict], _relatos]
    decisao: dict | None
    ondas: int
    rodada: int
    parada: str | None  # um freio parou o orquestrador
    falha: str | None
    completo: bool
    pendencias: list[dict]
    resultado: dict
    mensagem: str


def latin_only(text: str) -> str:
    """A mensagem final do orquestrador sem as palavras em outro alfabeto. O modelo pequeno solta
    uma de vez em quando ("Não մնou pendência"); no servidor MCP isso é recusado na gravação, mas a
    mensagem de estado não passa por lá."""
    def foreign(word: str) -> bool:
        return any(ch.isalpha() and ch not in "ªºµ" and "LATIN" not in unicodedata.name(ch, "") for ch in word)

    return " ".join(word for word in text.split(" ") if not foreign(word))


def conversation_so_far(turns: list[dict]) -> str:
    """Os turnos anteriores da conversa, para o orquestrador: o pedido e o que foi respondido."""
    if not turns:
        return ""
    lines = ["## A conversa até aqui (turnos anteriores, do mais antigo ao mais novo)"]
    for turn in turns[-_TURNS_IN_PROMPT:]:
        answer = turn.get("resposta") or ""
        if len(answer) > _TURN_CHARS:
            answer = answer[:_TURN_CHARS] + "…"
        lines += [f"- Pedido: {turn['pedido']}", f"  Resposta: {answer or '(sem resposta)'} [{turn.get('estado')}]"]
    return "\n".join(lines)


def first_input(run: Run, turns: list[dict] | None = None) -> str:
    if run.tipo == "relatorio":
        text = ("Produza o relatório consolidado completo dos documentos do usuário: extração, revisão das "
                "fontes, análise qualitativa, as cinco perguntas, os resumos e a montagem do relatório.")
    elif run.tipo == "pergunta":
        text = f"Responda à pergunta do usuário, com as fontes. Pergunta: «{run.pedido}»"
    elif run.tipo == "auto":
        text = f"Mensagem do usuário (diga em `intencao` o que ela pede): «{run.pedido}»"
    else:
        text = run.pedido
    history = conversation_so_far(turns or [])
    return f"{history}\n\n## Pedido atual\n{text}" if history else text


def retry_input(run: Run, pending: list[dict[str, Any]], reason: str | None, turns: list[dict] | None = None) -> str:
    lines = [first_input(run, turns), "", "Uma rodada anterior deste pedido terminou sem concluir."]
    if reason:
        lines.append(f"Motivo: {reason}.")
    if pending:
        lines += ["O que ainda falta, segundo a base:", json.dumps(pending, ensure_ascii=False, indent=1)]
    lines.append("O que já foi feito está gravado e não precisa ser refeito. Resolva só o que falta.")
    return "\n".join(lines)


def reports_input(reports: list[dict]) -> str:
    return ("Relatos da onda que terminou (montados pelo sistema, não pelos especialistas):\n"
            + json.dumps(reports, ensure_ascii=False, indent=1)
            + "\n\nDecida a próxima onda. Chame `pendencias` se o pedido envolve o relatório.")


def with_team(spec: AgentFile, catalog: dict[str, Any]) -> AgentFile:
    """O prompt do orquestrador mais a lista dos especialistas no ar, com a descrição de cada um
    (vem do arquivo do agente, pelo catálogo A2A)."""
    team = "\n".join(f"- `{entry['nome']}`: {entry['descricao']}" for entry in catalog["agentes"])
    return dataclasses.replace(spec, prompt=f"{spec.prompt}\n\n## Especialistas disponíveis\n\n{team}")


def _after(stamp: str | None, start: str) -> bool:
    if not stamp:
        return False
    moment = datetime.fromisoformat(stamp)
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)) >= datetime.fromisoformat(start)


async def check(run: Run, identity: Identity, deps: Deps, finished: bool) -> tuple[bool, list[dict[str, Any]], dict]:
    """A conferência final, feita pelo código com as tools de leitura do "sistema". Devolve se
    o pedido está completo, o que falta e o resultado para o usuário."""
    if run.tipo == "fora_do_escopo":
        return True, [], {"fora_do_escopo": True, "opcoes": OPTIONS}
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


def turn_answer(run: Run, state: Estado) -> str:
    """O que este turno respondeu, para os turnos seguintes da conversa lembrarem."""
    answers = (state.get("resultado") or {}).get("respostas") or []
    if answers:
        return " ".join(answer["resposta"] for answer in answers)
    if run.tipo == "relatorio" and state.get("completo"):
        return "Relatório consolidado montado."
    return state.get("mensagem") or ""


class Execution:
    """Os objetos de uma execução, fora do estado do grafo: o token do usuário não vai para o disco."""

    def __init__(self, run: Run, user_token: str, deps: Deps) -> None:
        self.run, self.deps, self.settings = run, deps, deps.settings
        self.identity = Identity(run.id, run.user_id, user_token)
        self.budget = Budget(self.settings.execucao_max_tokens, self.settings.execucao_max_segundos)
        self.ledger = Ledger(self.settings.precos)
        self.trace: Trace = deps.runs.trace(run.id)
        self.client = deps.specialists(self.identity)
        self.spec: AgentFile | None = None
        self.team: set[str] = set()

    # --- registro ---------------------------------------------------------------------------

    def record(self, agente: str, doc_id: str | None, modelo: str, uso: dict, passos: list[dict],
               mensagens: list[dict]) -> None:
        self.ledger.add(agente, modelo, uso, doc_id)
        for step in passos:
            if "raciocinio" in step:
                self.trace.add("raciocinio", agente=agente, doc_id=doc_id, texto=step["raciocinio"])
            else:
                self.trace.add("tool", agente=agente, doc_id=doc_id, **step)
        if mensagens:
            self.deps.runs.save_messages(self.run.id, agente, doc_id, mensagens)

    # --- nós --------------------------------------------------------------------------------

    async def orquestrador(self, state: Estado) -> dict:
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
            self.trace.add("intencao", intencao=decision.intencao, tipo_execucao=self.run.tipo)
            self.deps.runs.save(self.run)
            if self.run.tipo == "fora_do_escopo":
                # Nada é delegado, e a mensagem do orquestrador não vai ao usuário: o texto é fixo.
                return {**update, "mensagem": OUT_OF_SCOPE, "decisao": {"delegacoes": [], "terminado": True}}
        # Delegação a quem não existe vira relato de erro, que o orquestrador recebe na onda seguinte.
        unknown = [{"agente": item.agente, "estado": "erro", "doc_id": item.doc_id,
                    "erro": f"não há especialista '{item.agente}'"} for item in decision.delegacoes
                   if item.agente not in self.team]
        valid = [item.model_dump() for item in decision.delegacoes if item.agente in self.team]
        return {**update, "relatos": {"substituir": unknown}, "mensagem": latin_only(decision.mensagem),
                "decisao": {"delegacoes": valid, "terminado": decision.terminado}}

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

    async def delegar(self, item: dict) -> dict:
        # Quem está trabalhando agora: o especialista só entra de novo no trace quando termina.
        self.trace.add("delegando", agente=item["agente"], doc_id=item.get("doc_id"), tarefa=item["tarefa"])
        report = await delegate(self.client, item["agente"], item["tarefa"], item.get("doc_id"), self.budget,
                                self.run.conversa_id or None)
        detail = report.get("execucao") or {}
        usage = detail.get("uso") or {}
        self.budget.spend(int(usage.get("total", 0)))  # o gasto do especialista conta no teto da execução
        self.record(report["agente"], item.get("doc_id"), detail.get("modelo", ""), usage, detail.get("passos") or [],
                    detail.get("mensagens") or [])
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

    def graph(self):
        graph = StateGraph(Estado)
        graph.add_node("orquestrador", self.orquestrador)
        graph.add_node("delegar", self.delegar)
        graph.add_node("conferencia", self.conferencia)
        graph.add_node("fim", self.fim)
        graph.add_edge(START, "orquestrador")
        graph.add_conditional_edges("orquestrador", self.after_orchestrator,
                                    ["delegar", "orquestrador", "conferencia", "fim"])
        graph.add_edge("delegar", "orquestrador")
        graph.add_conditional_edges("conferencia", self.after_check, ["orquestrador", "fim"])
        graph.add_edge("fim", END)
        return graph.compile(checkpointer=self.deps.checkpointer)


async def execute(run: Run, user_token: str, deps: Deps) -> Run:
    execution = Execution(run, user_token, deps)
    trace, settings = execution.trace, deps.settings
    run.estado, run.iniciada_em = "em_andamento", now()
    try:
        deps.runs.save(run)
        trace.add("inicio", execucao=run.id, pedido_tipo=run.tipo, pedido=run.pedido, conversa=run.conversa_id)
        snapshot = deps.registry.current()
        run.agentes_digest = snapshot.digest
        if snapshot.orchestrator is None:
            problem = snapshot.errors.get(f"{ORCHESTRATOR}.md", "o arquivo não existe")
            raise RunFailed(f"Não há orquestrador: agentes/{ORCHESTRATOR}.md ({problem}).")
        catalog = await execution.client.catalog()
        if any(entry["tools"] is None for entry in catalog["agentes"]):
            raise RunFailed("O serviço dos especialistas não conseguiu falar com o servidor MCP.")
        locked = [entry["nome"] for entry in catalog["agentes"] if not entry["tools"]]
        if locked:
            raise RunFailed("Agente sem entrada na política do servidor MCP (policy.yaml): " + ", ".join(locked)
                            + ". Acrescente a entrada ou remova o arquivo do agente.")
        trace.add("especialistas", agentes=[entry["nome"] for entry in catalog["agentes"]], digest=catalog["digest"])
        execution.spec = with_team(snapshot.orchestrator, catalog)
        execution.team = {entry["nome"] for entry in catalog["agentes"]}

        config = {"configurable": {"thread_id": run.conversa_id or run.id},
                  "recursion_limit": 4 * settings.max_ondas * (settings.rodadas_pendencias + 1) + 10}
        graph = execution.graph()
        # Os turnos anteriores da conversa, recuperados do checkpoint.
        turns = list((await graph.aget_state(config)).values.get("turnos") or []) if deps.checkpointer else []
        if turns:
            trace.add("conversa", turnos_anteriores=len(turns))
        start: Estado = {"execucao_id": run.id, "entrada": first_input(run, turns), "historico": [],
                         "relatos": {"substituir": []}, "decisao": None, "ondas": 0, "rodada": 1, "parada": None,
                         "falha": None, "completo": False, "pendencias": [], "resultado": {}, "mensagem": ""}
        final = await graph.ainvoke(start, config)

        run.rodadas, run.mensagem = final.get("rodada", 1), final.get("mensagem", "")
        run.pendencias, run.resultado = final.get("pendencias") or [], final.get("resultado") or {}
        if final.get("falha"):
            raise RunFailed(final["falha"])
        run.estado = "completa" if final.get("completo") else "incompleta"
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
