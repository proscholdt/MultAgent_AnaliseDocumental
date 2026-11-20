"""Rodar um agente PydanticAI: tools pelo MCP com a identidade dele, passos, raciocínio, tokens,
memória e os freios."""
import time

import jwt
from pydantic import BaseModel
from pydantic_ai.messages import ToolReturnPart

from app.nucleo.agentes import AgentFile, Registry
from app.nucleo.custos import Ledger, cost_usd
from app.nucleo.executar import Budget, run_agent
from app.nucleo.tokens import mcp_headers
from tests.conftest import ANA, DOC, MODEL_NAME, SECRET_MCP, Refused, call, say, think, write_agent


def budget(tokens: int = 1_000_000, seconds: float = 60) -> Budget:
    return Budget(tokens=tokens, seconds=seconds)


def spec_of(settings, nome: str = "extrator") -> AgentFile:
    return Registry(settings.agentes_dir).current().agents[nome]


async def test_agent_uses_its_own_mcp_connection_and_every_step_is_recorded(settings, engine, world, model, identity):
    model.scripts["extrator"] = [
        [call("buscar_trechos", consulta="receita líquida", doc_id=DOC)],
        [call("salvar_campo", doc_id=DOC, campo="receita_liquida"), call("fechar_extracao", doc_id=DOC)],
        [say("Extração fechada.")],
    ]
    run = await run_agent(spec_of(settings), f"Extraia os indicadores.\n\nDocumento: doc_id = {DOC}", identity=identity,
                          engine=engine, budget=budget())

    assert (run.estado, run.texto, run.modelo) == ("concluido", "Extração fechada.", MODEL_NAME)
    # As chamadas chegaram ao servidor como o extrator, na execução e para o usuário certos.
    assert world.called("extrator") == ["buscar_trechos", "salvar_campo", "fechar_extracao"]
    assert {(who, ident.run_id, ident.user_id) for who, _, _, ident in world.calls} == {("extrator", "exec-teste", ANA)}
    assert [(step["tool"], step["ok"]) for step in run.passos] == [
        ("buscar_trechos", True), ("salvar_campo", True), ("fechar_extracao", True)]
    assert '"consulta": "receita líquida"' in run.passos[0]["argumentos"] and run.passos[0]["ms"] is not None
    assert run.writes == {"buscar_trechos": 1, "salvar_campo": 1, "fechar_extracao": 1}
    # Três chamadas ao modelo, somadas.
    assert run.uso == {"chamadas": 3, "entrada": 300, "entrada_cache": 150, "saida": 30, "total": 330}


async def test_a_refusal_from_the_server_goes_back_to_the_model_and_is_marked_in_the_trace(settings, engine, world, model,
                                                                                         identity):
    attempts = []

    def gate(arguments):
        attempts.append(arguments)
        if arguments["rotulo_linha"] == "Lucro do ano":
            raise Refused("Linha 'Lucro do ano' não existe na tabela.")
        return {"salvo": True}

    world.on("salvar_campo", gate)
    model.scripts["extrator"] = [[call("salvar_campo", rotulo_linha="Lucro do ano")],
                                 [call("salvar_campo", rotulo_linha="Lucro líquido do exercício")],
                                 [say("Pronto.")]]
    run = await run_agent(spec_of(settings), "Extraia.", identity=identity, engine=engine, budget=budget())

    assert run.estado == "concluido" and len(attempts) == 2
    assert [step["ok"] for step in run.passos] == [False, True]
    assert run.writes == {"salvar_campo": 1}  # a recusada não conta como gravação
    # O modelo recebeu o motivo da recusa como resultado da tool, para poder corrigir.
    returns = [part for message in model.inputs["extrator"][1] for part in message.parts
               if isinstance(part, ToolReturnPart)]
    assert returns[-1].outcome == "failed" and "não existe na tabela" in str(returns[-1].content)


async def test_brakes_turn_limit_token_budget_and_time(settings, engine, world, model, identity):
    spec = spec_of(settings)  # max_turnos: 6
    model.scripts["extrator"] = [[call("buscar_trechos", consulta="x")]]  # nunca termina

    looping = await run_agent(spec, "Extraia.", identity=identity, engine=engine, budget=budget())
    assert looping.estado == "limite_de_turnos" and "6 turnos" in looping.erro
    assert looping.uso["chamadas"] == 6 and len(looping.passos) >= 5  # o que foi feito antes do limite fica registrado

    limited = budget(tokens=250)  # cada chamada ao modelo gasta 110
    broke = await run_agent(spec, "Extraia.", identity=identity, engine=engine, budget=limited)
    assert broke.estado == "orcamento_esgotado" and "250" in broke.erro and broke.uso["chamadas"] == 3
    assert limited.tokens_left == 0

    expired = Budget(tokens=10**6, seconds=30, started=time.monotonic() - 31)
    late = await run_agent(spec, "Extraia.", identity=identity, engine=engine, budget=expired)
    assert late.estado == "tempo_esgotado" and not late.passos


async def test_reasoning_goes_to_the_trace_and_the_messages_are_kept(settings, engine, model, identity):
    """O resumo do raciocínio que o modelo devolve entra nos passos, antes da tool que ele motivou;
    as mensagens completas saem em JSON, para guardar."""
    model.scripts["extrator"] = [[think("A receita está na DRE, chunk 11."), call("buscar_trechos", consulta="DRE")],
                                 [say("Pronto.")]]
    run = await run_agent(spec_of(settings), "Extraia.", identity=identity, engine=engine, budget=budget())
    assert run.passos[0] == {"raciocinio": "A receita está na DRE, chunk 11."}
    assert run.passos[1]["tool"] == "buscar_trechos"
    kinds = [part["part_kind"] for message in run.mensagens for part in message["parts"]]
    assert kinds == ["user-prompt", "thinking", "tool-call", "tool-return", "text"]


async def test_history_is_sent_back_and_the_new_messages_come_apart(settings, engine, model, identity):
    model.scripts["extrator"] = [[call("buscar_trechos", consulta="DRE")], [say("Li a DRE.")]]
    first = await run_agent(spec_of(settings), "Primeira tarefa.", identity=identity, engine=engine, budget=budget())
    model.scripts["extrator"] = [[say("Já tinha lido a DRE.")]]
    second = await run_agent(spec_of(settings), "Segunda tarefa.", identity=identity, engine=engine, budget=budget(),
                             history=first.historico)
    # O modelo recebeu a primeira chamada inteira antes do pedido novo.
    received = model.inputs["extrator"][-1]
    assert [p.content for m in received for p in m.parts if p.part_kind == "user-prompt"] == [
        "Primeira tarefa.", "Segunda tarefa."]
    # `mensagens` é só o desta chamada; `historico`, tudo.
    assert len(second.mensagens) == 2 and len(second.historico) == len(first.historico) + 2


async def test_structured_output_for_the_orchestrator(settings, engine, model, identity):
    class Decisao(BaseModel):
        terminado: bool

    from tests.conftest import Decide

    model.scripts["orquestrador"] = [[Decide(terminado=True)]]
    run = await run_agent(spec_of(settings, "orquestrador"), "Decida.", identity=identity, engine=engine,
                          budget=budget(), output_type=Decisao)
    assert run.estado == "concluido" and run.saida == Decisao(terminado=True) and run.texto == ""


async def test_agent_without_policy_entry_does_not_run(settings, engine, world, model, identity):
    world.policy.pop("extrator")
    run = await run_agent(spec_of(settings), "Extraia.", identity=identity, engine=engine, budget=budget())
    assert run.estado == "sem_permissao" and "policy.yaml" in run.erro
    assert "extrator" not in model.inputs  # o modelo nem foi chamado


async def test_the_model_comes_from_the_agent_file(settings, engine, agents_dir, identity):
    """Cada agente diz o seu modelo; o motor recebe o arquivo do agente e usa o que está nele."""
    write_agent(agents_dir, "extrator", modelo="gigante")
    run = await run_agent(spec_of(settings), "Extraia.", identity=identity, engine=engine, budget=budget())
    assert run.estado == "erro" and "gigante" in run.erro


def test_mcp_token_says_which_agent_is_calling_and_the_user_token_travels_beside_it(settings, identity):
    headers = mcp_headers(settings, "analista", identity)
    claims = jwt.decode(headers["Authorization"][7:], SECRET_MCP, algorithms=["HS256"], audience="mcp-server")
    assert (claims["agent"], claims["run_id"], claims["usr"]) == ("analista", "exec-teste", ANA)
    assert claims["exp"] > time.time() + settings.execucao_max_segundos  # vale a execução inteira
    assert headers["X-User-Token"] == "tok-ana"


def test_cost_comes_from_the_price_table_and_is_null_without_a_price():
    prices = {MODEL_NAME: {"entrada": 2.0, "entrada_cache": 1.0, "saida": 10.0}}
    usage = {"chamadas": 3, "entrada": 300_000, "entrada_cache": 100_000, "saida": 20_000, "total": 320_000}
    # 200 mil de entrada cheia + 100 mil em cache + 20 mil de saída.
    assert cost_usd(MODEL_NAME, usage, prices) == round(0.2 * 2.0 + 0.1 * 1.0 + 0.02 * 10.0, 4)
    assert cost_usd("modelo-sem-preco", usage, prices) is None

    ledger = Ledger(prices)
    ledger.add("extrator", MODEL_NAME, usage, DOC)
    ledger.add("extrator", MODEL_NAME, usage, "doc-light")
    ledger.add("orquestrador", MODEL_NAME, usage)
    summary = ledger.summary()
    assert summary["total"] == {"uso": {key: value * 3 for key, value in usage.items()}, "custo_usd": 2.1}
    assert summary["por_agente"]["extrator"]["custo_usd"] == 1.4 and summary["por_agente"]["extrator"]["uso"]["chamadas"] == 6
    assert set(summary["por_documento"]) == {DOC, "doc-light"} and summary["por_documento"][DOC]["custo_usd"] == 0.7
    ledger.add("analista", "modelo-sem-preco", usage, DOC)
    unpriced = ledger.summary()
    assert unpriced["total"]["custo_usd"] is None and unpriced["modelos_sem_preco"] == ["modelo-sem-preco"]
    assert unpriced["por_agente"]["extrator"]["custo_usd"] == 1.4 and unpriced["por_documento"][DOC]["custo_usd"] is None
