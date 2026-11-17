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
import re
import time
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from pydantic import BaseModel, Field

from app import telemetria
from app.config import Settings
from app.nucleo import mcp
from app.nucleo.agentes import ORCHESTRATOR, AgentFile, Registry, agent_file
from app.nucleo.custos import Ledger, cost_usd
from app.nucleo.executar import AgentRun, Budget, Engine, atualizar_instrumentacao, run_agent
from app.nucleo.tokens import Identity
from app.orquestrador.especialistas_a2a import SpecialistClient, delegate, for_the_model
from app.orquestrador.execucoes import Run, Runs, Trace, now
from app.orquestrador.intencao import Roteador

_medidor = telemetria.medidor()
_TOKENS = _medidor.create_counter("agentes.tokens", description="Tokens por agente, modelo e tipo")
_CUSTO = _medidor.create_counter("agentes.custo_usd", description="Custo em US$ por agente e modelo")
_DELEGACOES = _medidor.create_counter("agentes.delegacoes", description="Delegações por especialista e estado")
_ESPECIALISTA_S = _medidor.create_histogram("agentes.especialista.duracao", unit="s",
                                            description="Duração de cada chamada a um especialista")
_EXECUCOES = _medidor.create_counter("agentes.execucoes", description="Execuções por tipo e estado")
_EXECUCAO_S = _medidor.create_histogram("agentes.execucao.duracao", unit="s", description="Duração das execuções")
_INTENCOES = _medidor.create_counter("agentes.intencoes", description="Intenção das mensagens do chat, por quem decidiu")

def zerar_metricas(agentes: list[str], modelos: list[str]) -> None:
    """Cria as séries dos contadores em zero, na subida. Um contador do OpenTelemetry só passa a
    existir no primeiro incremento, e o Prometheus nunca vê o zero de antes: increase() perde o
    primeiro evento. Com as séries conhecidas já em zero, a conta fecha."""
    for tipo in ("pergunta", "relatorio", "relatorio_empresa", "livre", "fora_do_escopo"):
        for estado in ("completa", "incompleta", "falhou"):
            _EXECUCOES.add(0, {"tipo": tipo, "estado": estado})
    for agente in agentes:
        for modelo in modelos:
            _CUSTO.add(0, {"agente": agente, "modelo": modelo})
            for tipo in ("entrada", "cache", "saida"):
                _TOKENS.add(0, {"agente": agente, "modelo": modelo, "tipo": tipo})
        if agente != ORCHESTRATOR:
            for estado in ("concluido", "limite_de_turnos", "orcamento_esgotado", "tempo_esgotado", "sem_permissao",
                           "erro"):
                _DELEGACOES.add(0, {"agente": agente, "estado": estado})


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
    intencao: Literal["pergunta", "relatorio", "relatorio_empresa", "fora_do_escopo"] = Field(
        description="O que o pedido atual do usuário é: 'pergunta' (quer saber algo do conteúdo dos documentos), "
                    "'relatorio' (quer o relatório consolidado, de todas as empresas), 'relatorio_empresa' (quer o "
                    "relatório ou a análise completa de UMA empresa) ou 'fora_do_escopo' (qualquer outra coisa).")
    empresa: str | None = Field(
        description="Só em 'relatorio_empresa': o doc_id da empresa pedida, como vem em listar_documentos. "
                    "null nas outras intenções.")


# A intenção que o orquestrador dá a uma mensagem de conversa vira o tipo da execução, e o tipo
# diz o que a conferência final cobra: pergunta exige resposta gravada com fonte; relatório exige
# o relatório montado. O resto está fora do escopo: o código não executa nada e responde com um
# texto fixo e as opções. Nenhum texto livre do orquestrador chega ao usuário como resposta.
INTENT_TYPE = {"pergunta": "pergunta", "relatorio": "relatorio", "relatorio_empresa": "relatorio_empresa",
               "fora_do_escopo": "fora_do_escopo"}
REPORT_TYPES = ("relatorio", "relatorio_empresa")

# Numa pergunta, o que é só do relatório: o texto que vem depois de tudo pronto. Validador e
# analista podem servir a uma pergunta ("a Light tem inconsistências?", "quais riscos..."), e
# ficam a critério do orquestrador.
_REPORT_ONLY = {"redator": "os resumos e a montagem do relatório"}
ANSWERED = "Pergunta respondida, com as fontes."
# A pergunta do chat igual a uma das cinco (a p1 é "qual a maior margem EBITDA?") ia gravada como
# p1, e a p1 só é aceita com a extração e a análise de todos os documentos: a recusa puxava a
# análise inteira para responder a um número.
# Uma das cinco perguntas do relatório é definida pelo id, não pelo texto que o orquestrador
# escreve. Numa execução real ele delegou "pergunta_id: p2. Responda: qual empresa apresentou a
# MAIOR margem EBITDA...": o pesquisador respondeu à margem, pôs o enunciado oficial da p2 no campo
# `pergunta`, e o servidor gravou (ele confere o campo, não o assunto da resposta).
_REPORT_QUESTION = re.compile(r"\bp([1-5])\b", re.IGNORECASE)
FOR_THE_CHAT = ("\n\nÉ uma pergunta do usuário, não do relatório: grave com pergunta_id 'livre', mesmo que o texto "
                "seja igual ao de uma das cinco perguntas do relatório.")

OUT_OF_SCOPE = ("Isso está fora do escopo deste sistema. Ele responde perguntas sobre as demonstrações financeiras "
                "enviadas, sempre com a página de origem, e gera relatórios: o consolidado ou o de uma empresa. "
                "Escolha uma das opções ou escreva a sua pergunta.")
OPTIONS = [
    {"titulo": "Perguntar sobre um indicador", "exemplo": "Qual empresa teve a maior margem EBITDA?"},
    {"titulo": "Comparar as empresas", "exemplo": "Compare o lucro líquido das empresas"},
    {"titulo": "Entender o resultado", "exemplo": "Quais riscos as empresas destacam?"},
    {"titulo": "Gerar o relatório de uma empresa", "exemplo": "Gere o relatório da Brasal"},
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
    # A nota ao fim de um relatório (app/ferramentas/avaliacao_automatica.py). Os testes trocam.
    avaliar: Callable[[Settings, Identity, str, str | None], Awaitable[dict | None]] | None = None
    # A intenção das mensagens do chat por roteamento semântico (intencao.py). Sem ele, o LLM decide.
    roteador: Roteador | None = None


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
    elif run.tipo == "relatorio_empresa":
        text = (f"Produza o relatório de UMA empresa, como o usuário pediu: «{run.pedido}». Empresa: "
                f"{run.doc_id or 'diga em `empresa` o doc_id dela'}. Use `pendencias` com esse doc_id.")
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


_READY_IN_REASON = 6


def _held_reason(mine: list[dict[str, Any]], ready: list[dict[str, Any]]) -> str:
    """Por que a delegação não saiu e o que delegar no lugar dela."""
    waits = list(dict.fromkeys(item["aguarda"] for item in mine if item.get("aguarda")))
    text = "não delegado: chegou antes da hora. Antes disso falta: " + "; ".join(waits) + "."
    if ready:
        options = [f"{item['agente']}: {item['o_que_falta']}" + (f" ({item['empresa']})" if item.get("empresa") else "")
                   for item in ready[:_READY_IN_REASON]]
        text += " O que já pode ser delegado: " + "; ".join(options) + "."
    return text


def turn_answer(run: Run, state: Estado) -> str:
    """O que este turno respondeu, para os turnos seguintes da conversa lembrarem."""
    answers = (state.get("resultado") or {}).get("respostas") or []
    if answers:
        return " ".join(answer["resposta"] for answer in answers)
    if run.tipo == "relatorio" and state.get("completo"):
        return "Relatório consolidado montado."
    if run.tipo == "relatorio_empresa" and state.get("completo"):
        return f"Relatório da empresa {run.doc_id} montado."
    return state.get("mensagem") or ""


class Execution:
    """Os objetos de uma execução, fora do estado do grafo: o token do usuário não vai para o disco."""

    def __init__(self, run: Run, user_token: str, deps: Deps) -> None:
        self.run, self.deps, self.settings = run, deps, deps.settings
        self.settings.reload()  # o config/ajustes.yaml de agora vale para esta execução inteira
        atualizar_instrumentacao(self.settings)
        self.identity = Identity(run.id, run.user_id, user_token)
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
        self.ledger.add(agente, modelo, uso, doc_id)
        for tipo, chave in (("entrada", "entrada"), ("cache", "entrada_cache"), ("saida", "saida")):
            if uso.get(chave):
                _TOKENS.add(int(uso[chave]), {"agente": agente, "modelo": modelo or "?", "tipo": tipo})
        custo = cost_usd(modelo, uso, self.ledger.prices) if modelo else None
        if custo:
            _CUSTO.add(custo, {"agente": agente, "modelo": modelo})
        # O mesmo no span do nó (grafo.orquestrador, grafo.delegar): o Grafana soma por conversa.
        entrada, saida = int(uso.get("entrada") or 0), int(uso.get("saida") or 0)
        telemetria.trace.get_current_span().set_attributes({
            "agente": agente, "agente.modelo": modelo or "?", "agente.custo_usd": float(custo or 0.0),
            "agente.tokens": entrada + saida, "agente.tokens_entrada": entrada,
            "agente.tokens_cache": int(uso.get("entrada_cache") or 0), "agente.tokens_saida": saida})
        for step in passos:
            if "raciocinio" in step:
                self.trace.add("raciocinio", agente=agente, doc_id=doc_id, texto=step["raciocinio"])
            else:
                self.trace.add("tool", agente=agente, doc_id=doc_id, **step)
        if mensagens:
            self.deps.runs.save_messages(self.run.id, agente, doc_id, mensagens)

    # --- nós --------------------------------------------------------------------------------

    async def rotear(self, turns: list[dict]) -> None:
        """Antes do orquestrador: o roteador decide a intenção da mensagem, se tiver certeza.
        Sem certeza (ou sem roteador), o tipo continua "auto" e o orquestrador decide."""
        roteador = self.deps.roteador
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
                    "relatos": {"substituir": []}, "mensagem": OUT_OF_SCOPE,
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
                return {**update, "mensagem": OUT_OF_SCOPE, "decisao": {"delegacoes": [], "terminado": True}}
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

    def in_scope(self, delegations: list[dict]) -> tuple[list[dict], list[dict]]:
        """Numa pergunta, o que é só do relatório não sai: volta ao orquestrador como relato."""
        go, held = [], []
        for delegation in delegations:
            what = _REPORT_ONLY.get(delegation["agente"])
            if what is None:
                go.append(delegation)
                continue
            held.append({"agente": delegation["agente"], "estado": "nao_delegado", "doc_id": delegation.get("doc_id"),
                         "erro": f"não delegado: o pedido é uma pergunta, e {what} são do relatório. A pergunta "
                                 "termina quando o pesquisador grava a resposta."})
        return go, held

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
            go, held = self.in_scope(delegations)
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
        ready = [item for item in pending if item.get("liberada", True)]
        go, held = [], []
        for delegation in delegations:
            mine = [item for item in pending if item.get("agente") == delegation["agente"]
                    and (delegation.get("doc_id") is None or item.get("doc_id") in (None, delegation.get("doc_id")))]
            if mine and not any(item.get("liberada", True) for item in mine):
                held.append({"agente": delegation["agente"], "estado": "nao_delegado", "doc_id": delegation.get("doc_id"),
                             "erro": _held_reason(mine, ready)})
            else:
                go.append(delegation)
        return go, held

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
        """A tarefa do pesquisador que cita uma das cinco perguntas passa a ser o enunciado oficial
        dela. Com mais de uma citada, os enunciados vão junto da tarefa."""
        ids = list(dict.fromkeys(f"p{number}" for number in _REPORT_QUESTION.findall(item["tarefa"])))
        questions = await self.report_questions() if ids else {}
        known = [(question_id, questions[question_id]) for question_id in ids if question_id in questions]
        if not known:
            return item
        if len(known) == 1:
            question_id, text = known[0]
            task = (f"Responda à pergunta {question_id} do relatório, com pergunta_id '{question_id}'. Enunciado "
                    f"oficial: «{text}». Responda exatamente a este enunciado.")
        else:
            task = (item["tarefa"] + "\n\nEnunciados oficiais (responda a eles, não a outra formulação): "
                    + "; ".join(f"{question_id}: «{text}»" for question_id, text in known))
        return {**item, "tarefa": task, "tarefa_do_orquestrador": item["tarefa"]}

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
