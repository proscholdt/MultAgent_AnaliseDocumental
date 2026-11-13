"""Uma execução inteira, como grafo: o orquestrador decide, o grafo delega por A2A, o código
confere as pendências, a conversa é lembrada, e a API mostra o que aconteceu."""
import asyncio
import json
from datetime import UTC, datetime

from app.execucoes import Run, Runs
from tests.conftest import (
    ANA,
    BRUNO,
    DOC,
    MODEL_NAME,
    auth,
    call,
    decide,
    delegation,
    say,
    think,
    write_agent,
)

EXTRACTION = {"o_que_falta": "extração dos indicadores (fechar_extracao)", "agente": "extrator", "doc_id": DOC,
              "empresa": "Brasal"}
ASSEMBLY = {"o_que_falta": "montagem do relatório (montar_relatorio)", "agente": "redator"}


def working_team(world, model) -> None:
    """Um extrator e um redator que fazem o trabalho: cada um resolve a sua pendência na base."""
    world.pending = [EXTRACTION]

    def close(_arguments):
        world.pending = [ASSEMBLY]
        return {"publicado": True}

    def assemble(_arguments):
        world.pending, world.report = [], "# Relatório consolidado\n\nBrasal..."
        return {"montado": True}

    world.on("fechar_extracao", close)
    world.on("montar_relatorio", assemble)
    model.scripts["extrator"] = [[think("A receita está na DRE."), call("buscar_trechos", consulta="receita")],
                                 [call("fechar_extracao", doc_id=DOC)], [say("Extração fechada.")]]
    model.scripts["redator"] = [[call("montar_relatorio")], [say("Relatório montado.")]]


def answering_researcher(world, agents_dir, answer: str) -> list[str]:
    """Um pesquisador que grava a resposta à pergunta que recebeu. Devolve a lista das perguntas gravadas."""
    write_agent(agents_dir, "pesquisador")
    world.policy["pesquisador"] = ["salvar_resposta"]
    asked: list[str] = []

    def save(arguments):
        asked.append(arguments["pergunta"])
        world.answers.append({"pergunta_id": "livre", "pergunta": arguments["pergunta"], "resposta": answer,
                              "fontes": ["Light.pdf, p. 15"], "gravada_em": datetime.now(UTC).isoformat()})
        return {"salva": True}

    world.on("salvar_resposta", save)
    return asked


async def wait(http, run_id: str, headers: dict) -> dict:
    for _ in range(300):
        run = (await http.get(f"/api/execucoes/{run_id}", headers=headers)).json()
        if run["estado"] not in ("na_fila", "em_andamento"):
            return run
        await asyncio.sleep(0.01)
    raise AssertionError("a execução não terminou")


# --- o caminho feliz ------------------------------------------------------------------------


async def test_report_run_goes_through_the_graph_and_returns_the_report_from_the_base(http, world, model):
    working_team(world, model)
    model.scripts["orquestrador"] = [
        [call("listar_documentos"), call("pendencias")],
        [decide(delegation("extrator", "Extraia os indicadores.", DOC))],
        [decide(delegation("redator", "Monte o relatório."))],
        [decide(terminado=True, mensagem="Relatório pronto.")],
    ]
    run = (await http.post("/api/relatorios", json={"aguardar": True}, headers=auth())).json()

    assert (run["estado"], run["rodadas"], run["erro"]) == ("completa", 1, None)
    assert run["pendencias"] == [] and run["mensagem"] == "Relatório pronto."
    # O relatório vem da base, lido pelo código; não é texto do orquestrador.
    assert run["resultado"]["relatorio"] == "# Relatório consolidado\n\nBrasal..."
    # Cada um chamou só o que é seu, e a conferência final foi feita pelo "sistema".
    assert world.called("orquestrador") == ["listar_documentos", "pendencias"]
    assert world.called("extrator") == ["buscar_trechos", "fechar_extracao"]
    assert world.called("redator") == ["montar_relatorio"]
    assert world.called("sistema")[-2:] == ["pendencias", "ler_relatorio"]
    # Tudo na mesma execução e para o mesmo usuário.
    assert {(ident.run_id, ident.user_id) for _, _, _, ident in world.calls} == {(run["execucao_id"], ANA)}
    # O orquestrador viu o relato montado pelo código, não os passos nem o texto do especialista.
    second = model.first_input("orquestrador", turn=1)
    reports = json.loads(second[second.index("["):second.rindex("]") + 1])
    assert reports == [{"agente": "extrator", "estado": "concluido", "doc_id": DOC,
                        "gravacoes": {"fechar_extracao": 1}, "pendencias": []}]

    # O trace conta quem decidiu e quem fez o quê, com o raciocínio.
    steps = run["passos"]
    assert [step["tipo"] for step in steps][:2] == ["inicio", "especialistas"] and steps[-1]["tipo"] == "fim"
    assert [(s["agente"], s["tool"]) for s in steps if s["tipo"] == "tool"] == [
        ("orquestrador", "listar_documentos"), ("orquestrador", "pendencias"),
        ("extrator", "buscar_trechos"), ("extrator", "fechar_extracao"), ("redator", "montar_relatorio")]
    assert [(s["agente"], s["texto"]) for s in steps if s["tipo"] == "raciocinio"] == [
        ("extrator", "A receita está na DRE.")]
    assert [(s["agente"], s["doc_id"], s["estado"]) for s in steps if s["tipo"] == "especialista"] == [
        ("extrator", DOC, "concluido"), ("redator", None, "concluido")]
    waves = [s for s in steps if s["tipo"] == "orquestrador"]
    assert [(s["onda"], s["decisao"]["terminado"]) for s in waves] == [(1, False), (2, False), (3, True)]
    assert [s for s in steps if s["tipo"] == "conferencia"] == [
        {"em": steps[-2]["em"], "tipo": "conferencia", "rodada": 1, "completo": True, "pendencias": 0}]

    # Tokens e custo, por agente e por documento (110 tokens por chamada ao modelo).
    usage = run["uso"]
    assert {name: entry["uso"]["chamadas"] for name, entry in usage["por_agente"].items()} == {
        "orquestrador": 4, "extrator": 3, "redator": 2}
    assert list(usage["por_documento"]) == [DOC] and usage["por_documento"][DOC]["uso"]["total"] == 330
    assert usage["total"]["uso"]["total"] == 9 * 110 and usage["modelos_sem_preco"] == []
    assert abs(usage["total"]["custo_usd"] - 9 * (50 * 2.0 + 50 * 1.0 + 10 * 10.0) / 1_000_000) < 1e-4

    # As mensagens completas de cada agente ficam guardadas, com o raciocínio.
    messages = (await http.get(f"/api/execucoes/{run['execucao_id']}/mensagens", headers=auth())).json()
    assert [entry["agente"] for entry in messages] == ["orquestrador", "extrator", "orquestrador", "redator",
                                                       "orquestrador"]
    extractor = (await http.get(f"/api/execucoes/{run['execucao_id']}/mensagens?agente=extrator",
                                headers=auth())).json()
    kinds = [part["part_kind"] for message in extractor[0]["mensagens"] for part in message["parts"]]
    assert kinds[:3] == ["user-prompt", "thinking", "tool-call"] and extractor[0]["doc_id"] == DOC
    assert (await http.get(f"/api/execucoes/{run['execucao_id']}/mensagens", headers=auth(BRUNO))).status_code == 404


async def test_one_wave_runs_its_delegations_in_parallel(http, world, model, agents_dir):
    """Duas delegações na mesma onda rodam ao mesmo tempo, e o orquestrador só decide de novo quando
    as duas voltam, com os dois relatos."""
    working_team(world, model)
    world.pending = []
    world.report = "# Relatório"
    model.scripts["orquestrador"] = [
        [decide(delegation("extrator", "Extraia.", DOC), delegation("extrator", "Extraia.", "doc-light"))],
        [decide(terminado=True)]]
    model.scripts["extrator"] = [[say("Feito.")]]
    run = (await http.post("/api/relatorios", json={"aguardar": True}, headers=auth())).json()
    assert run["estado"] == "completa"
    second = model.first_input("orquestrador", turn=1)
    reports = json.loads(second[second.index("["):second.rindex("]") + 1])
    assert sorted(report["doc_id"] for report in reports) == sorted([DOC, "doc-light"])


async def test_question_run_returns_the_answer_saved_in_this_run_only(http, world, model, agents_dir):
    answering_researcher(world, agents_dir, "A Light, com R$ 13.253.286 mil (Light.pdf, p. 15).")
    world.answers = [{"pergunta_id": "p1", "pergunta": "Antiga?", "resposta": "Resposta de ontem.", "fontes": [],
                      "gravada_em": "2020-01-01T00:00:00+00:00"}]
    model.scripts["pesquisador"] = [[call("salvar_resposta", pergunta_id="livre", pergunta="Qual a maior receita?")],
                                    [say("Resposta gravada.")]]
    model.scripts["orquestrador"] = [[decide(delegation("pesquisador", "Responda: qual a maior receita?"))],
                                     [decide(terminado=True, mensagem="Respondido.")]]
    run = (await http.post("/api/perguntas", json={"pergunta": "Qual empresa teve a maior receita?"},
                           headers=auth())).json()

    assert run["estado"] == "completa" and run["tipo"] == "pergunta"
    assert [answer["resposta"] for answer in run["resultado"]["respostas"]] == [
        "A Light, com R$ 13.253.286 mil (Light.pdf, p. 15)."]
    assert "«Qual empresa teve a maior receita?»" in model.first_input("orquestrador")


# --- a conversa -----------------------------------------------------------------------------


async def test_follow_up_question_knows_the_previous_turn(http, world, model, agents_dir):
    """"E a menor?" só tem sentido com a pergunta anterior: o orquestrador a recebe dos turnos
    guardados no checkpoint da conversa, e delega a pergunta já resolvida."""
    asked = answering_researcher(world, agents_dir, "A maior margem é a da Brasal, 16,74% (calculado).")
    model.scripts["pesquisador"] = [[call("salvar_resposta", pergunta_id="livre", pergunta="Maior margem EBITDA?")],
                                    [say("Gravada.")]]
    model.scripts["orquestrador"] = [[decide(delegation("pesquisador", "Responda: qual a maior margem EBITDA?"))],
                                     [decide(terminado=True, mensagem="Respondido.")]]
    conversation = (await http.post("/api/conversas", headers=auth())).json()["conversa_id"]
    first = (await http.post(f"/api/conversas/{conversation}/mensagens",
                             json={"texto": "Qual empresa teve a maior margem EBITDA?"}, headers=auth())).json()
    assert first["estado"] == "completa" and first["conversa_id"] == conversation
    assert first["resultado"]["respostas"][0]["resposta"].startswith("A maior margem é a da Brasal")
    assert "A conversa até aqui" not in model.first_input("orquestrador", turn=0)  # primeiro turno: nada antes

    model.scripts["pesquisador"] = [[call("salvar_resposta", pergunta_id="livre", pergunta="Menor margem EBITDA?")],
                                    [say("Gravada.")]]
    model.scripts["orquestrador"] = [[decide(delegation("pesquisador", "Responda: qual a MENOR margem EBITDA?"))],
                                     [decide(terminado=True, mensagem="Respondido.")]]
    second = (await http.post(f"/api/conversas/{conversation}/mensagens", json={"texto": "E a menor?"},
                              headers=auth())).json()
    assert second["estado"] == "completa"
    prompt = model.first_input("orquestrador", turn=2)
    assert "## A conversa até aqui" in prompt and "Pedido: Qual empresa teve a maior margem EBITDA?" in prompt
    assert "Resposta: A maior margem é a da Brasal, 16,74% (calculado)." in prompt
    assert prompt.endswith("## Pedido atual\nE a menor?")
    assert asked == ["Maior margem EBITDA?", "Menor margem EBITDA?"]
    # O especialista também lembra: na mesma conversa, recebeu a chamada anterior.
    researcher = next(s for s in second["passos"] if s["tipo"] == "especialista")
    assert researcher["memoria"]["chamadas_anteriores"] == 1

    turns = (await http.get(f"/api/conversas/{conversation}", headers=auth())).json()["turnos"]
    assert [(turn["pedido"], turn["estado"]) for turn in turns] == [
        ("Qual empresa teve a maior margem EBITDA?", "completa"), ("E a menor?", "completa")]
    assert turns[0]["respostas"][0]["fontes"] == ["Light.pdf, p. 15"]
    listed = (await http.get("/api/conversas", headers=auth())).json()
    assert [(c["conversa_id"], c["turnos"]) for c in listed] == [(conversation, 2)]

    # Um pedido sem conversa abre uma nova, e não vê os turnos desta.
    model.scripts["orquestrador"] = [[decide(terminado=True)]]
    alone = (await http.post("/api/perguntas", json={"pergunta": "E a menor?", "aguardar": True}, headers=auth())).json()
    assert alone["conversa_id"] != conversation and "A conversa até aqui" not in model.first_input("orquestrador", 3)


async def test_conversation_belongs_to_its_user(http, model):
    conversation = (await http.post("/api/conversas", headers=auth())).json()["conversa_id"]
    assert (await http.get(f"/api/conversas/{conversation}", headers=auth(BRUNO))).status_code == 404
    other = await http.post(f"/api/conversas/{conversation}/mensagens", json={"texto": "Oi"}, headers=auth(BRUNO))
    assert other.status_code == 404
    assert (await http.post("/api/execucoes", json={"pedido": "Continuar a da Ana.", "conversa_id": conversation},
                            headers=auth(BRUNO))).status_code == 404
    assert (await http.get("/api/conversas", headers=auth(BRUNO))).json() == []
    assert (await http.get("/api/conversas/conv-que-nao-existe", headers=auth())).status_code == 404


async def test_a_specialist_called_again_in_the_run_remembers_the_first_call(http, world, model):
    """Na segunda onda, o extrator do mesmo documento recebe a própria conversa da primeira."""
    working_team(world, model)
    world.pending, world.report = [], "# Relatório"
    model.scripts["extrator"] = [[call("buscar_trechos", consulta="DRE")], [say("Parei no meio.")],
                                 [say("Continuei.")]]
    model.scripts["orquestrador"] = [[decide(delegation("extrator", "Extraia.", DOC))],
                                     [decide(delegation("extrator", "Termine a extração.", DOC))],
                                     [decide(terminado=True)]]
    run = (await http.post("/api/relatorios", json={"aguardar": True}, headers=auth())).json()
    memory = [s["memoria"] for s in run["passos"] if s["tipo"] == "especialista"]
    assert [m["chamadas_anteriores"] for m in memory] == [0, 1]
    received = model.inputs["extrator"][-1]
    prompts = [p.content for m in received for p in m.parts if p.part_kind == "user-prompt"]
    assert prompts[0].startswith("Extraia.") and prompts[-1].startswith("Termine a extração.")


# --- a conferência e os freios --------------------------------------------------------------


async def test_when_something_is_left_the_orchestrator_runs_again_with_the_list(http, world, model):
    working_team(world, model)
    model.scripts["orquestrador"] = [
        [decide(terminado=True)],  # 1ª rodada: diz que terminou sem ter feito nada
        [decide(delegation("extrator", "Extraia.", DOC))], [decide(delegation("redator", "Monte."))],
        [decide(terminado=True, mensagem="Agora sim.")],
    ]
    run = (await http.post("/api/relatorios", json={"aguardar": True}, headers=auth())).json()

    assert (run["estado"], run["rodadas"], run["mensagem"]) == ("completa", 2, "Agora sim.")
    assert [(s["rodada"], s["completo"], s["pendencias"]) for s in run["passos"] if s["tipo"] == "conferencia"] == [
        (1, False, 1), (2, True, 0)]
    # A segunda rodada recebeu a lista do que falta, tirada da base.
    again = model.first_input("orquestrador", turn=1)
    assert "terminou sem concluir" in again and "extração dos indicadores (fechar_extracao)" in again and DOC in again


async def test_run_ends_incomplete_after_the_allowed_rounds_and_says_what_is_left(http, world, model):
    world.pending = [EXTRACTION]
    model.scripts["orquestrador"] = [[decide(terminado=True)]]  # nunca delega
    run = (await http.post("/api/relatorios", json={"aguardar": True}, headers=auth())).json()

    assert (run["estado"], run["rodadas"]) == ("incompleta", 3)  # 1 + 2 rodadas de pendências
    assert run["pendencias"] == [EXTRACTION] and "3 rodada(s)" in run["erro"]
    assert run["resultado"] == {"relatorio": None, "gerado_em": None}  # sem relatório parcial com cara de pronto


async def test_token_budget_of_the_run_stops_everything_and_there_is_no_new_round(http, world, model, settings):
    settings.execucao_max_tokens = 400
    world.pending = [EXTRACTION]
    model.scripts["orquestrador"] = [[call("listar_documentos")]]  # nunca decide; 110 tokens por chamada
    run = (await http.post("/api/relatorios", json={"aguardar": True}, headers=auth())).json()

    assert (run["estado"], run["rodadas"]) == ("incompleta", 1) and "400" in run["erro"]
    assert run["uso"]["total"]["uso"]["chamadas"] == 4


async def test_what_the_specialists_spend_counts_against_the_run_budget(http, world, model, settings):
    settings.execucao_max_tokens = 500
    working_team(world, model)
    model.scripts["extrator"] = [[call("buscar_trechos", consulta="x")]]  # gasta até o que resta do teto
    model.scripts["orquestrador"] = [[decide(delegation("extrator", "Extraia.", DOC))], [decide(terminado=True)]]
    run = (await http.post("/api/relatorios", json={"aguardar": True}, headers=auth())).json()

    assert run["estado"] == "incompleta" and run["rodadas"] == 1
    specialist = next(s for s in run["passos"] if s["tipo"] == "especialista")
    assert specialist["estado"] == "orcamento_esgotado" and specialist["uso"]["chamadas"] == 4  # 390 restavam: 4 x 110
    assert "teto de tokens" in run["erro"]  # o orquestrador não chegou a chamar o modelo de novo


async def test_delegation_to_an_unknown_specialist_comes_back_as_an_error_report(http, world, model):
    world.pending, world.report = [], "# Relatório"
    model.scripts["orquestrador"] = [[decide(delegation("ninguem", "Faça algo."))], [decide(terminado=True)]]
    run = (await http.post("/api/relatorios", json={"aguardar": True}, headers=auth())).json()
    assert run["estado"] == "completa"
    assert "não há especialista 'ninguem'" in model.first_input("orquestrador", turn=1)


async def test_run_does_not_start_with_an_agent_that_has_no_policy_entry(http, world, model, agents_dir):
    write_agent(agents_dir, "tradutor")  # arquivo criado, política não
    run = (await http.post("/api/relatorios", json={"aguardar": True}, headers=auth())).json()
    assert run["estado"] == "falhou" and "tradutor" in run["erro"] and "policy.yaml" in run["erro"]
    assert not model.inputs  # nenhum modelo foi chamado

    world.policy["tradutor"] = ["buscar_trechos"]  # a entrada na política resolve, sem reiniciar
    working_team(world, model)
    world.pending = []
    world.report = "# Relatório"
    again = (await http.post("/api/relatorios", json={"aguardar": True}, headers=auth())).json()
    assert again["estado"] == "completa"
    # O agente novo entrou na equipe do orquestrador, com a descrição do arquivo dele.
    listed = (await http.get("/api/agentes", headers=auth())).json()
    assert listed["orquestrador"]["chama"] == ["extrator", "redator", "tradutor"]
    assert listed["orquestrador"]["tools"] == ["listar_documentos", "pendencias"]
    assert [(e["nome"], e["tools"]) for e in listed["especialistas"]][-1] == ("tradutor", ["buscar_trechos"])


async def test_run_fails_clearly_without_the_orchestrator_file(http, agents_dir):
    (agents_dir / "orquestrador.md").write_text("sem cabeçalho", encoding="utf-8")
    run = (await http.post("/api/relatorios", json={"aguardar": True}, headers=auth())).json()
    assert run["estado"] == "falhou" and "orquestrador.md" in run["erro"] and "falta o cabeçalho" in run["erro"]


# --- a API ----------------------------------------------------------------------------------


async def test_free_request_runs_in_the_background_and_belongs_to_its_user(http, world, model):
    model.scripts["orquestrador"] = [[call("listar_documentos")],
                                     [decide(terminado=True, mensagem="Há um documento: Brasal.")]]
    created = await http.post("/api/execucoes", json={"pedido": "Quais documentos eu tenho?"}, headers=auth())
    assert created.status_code == 202 and created.json()["estado"] == "na_fila"
    run_id = created.json()["execucao_id"]

    run = await wait(http, run_id, auth())
    assert (run["estado"], run["tipo"], run["mensagem"]) == ("completa", "livre", "Há um documento: Brasal.")
    assert model.first_input("orquestrador") == "Quais documentos eu tenho?"
    assert "user_id" not in run and "tok" not in json.dumps(run)  # nem o usuário interno nem o token aparecem

    # Outro usuário não vê nem lista a execução.
    assert (await http.get(f"/api/execucoes/{run_id}", headers=auth(BRUNO))).status_code == 404
    assert (await http.get("/api/execucoes", headers=auth(BRUNO))).json() == []
    assert [entry["execucao_id"] for entry in (await http.get("/api/execucoes", headers=auth())).json()] == [run_id]


async def test_final_message_loses_words_in_another_alphabet(http, model):
    """O modelo pequeno escreveu "Não մնou pendência" na mensagem de estado de várias execuções reais."""
    model.scripts["orquestrador"] = [[decide(
        terminado=True, mensagem="Relatório concluído. Não մնou pendência no crédito a العملاء, na 1ª rodada.")]]
    created = await http.post("/api/execucoes", json={"pedido": "Quais documentos eu tenho?"}, headers=auth())
    run = await wait(http, created.json()["execucao_id"], auth())
    assert run["mensagem"] == "Relatório concluído. Não pendência no crédito a na 1ª rodada."


async def test_api_requires_a_valid_indexer_token_with_time_left(http, model):
    assert (await http.post("/api/relatorios")).status_code in (401, 403)
    invalid = await http.post("/api/relatorios", headers={"Authorization": "Bearer token-de-outro-lugar"})
    assert invalid.status_code == 401 and "login" in invalid.json()["detail"]
    # Token válido, mas perto de vencer: a execução nem começa.
    expiring = await http.post("/api/relatorios", headers=auth(minutes=5))
    assert expiring.status_code == 401 and "vence em" in expiring.json()["detail"]
    assert (await http.get("/api/execucoes", headers=auth())).json() == [] and not model.inputs

    health = (await http.get("/health")).json()
    assert health["status"] == "ok" and health["agentes"] == ["extrator", "orquestrador", "redator"]
    assert health["modelos"] == {"padrao": MODEL_NAME}


def test_runs_survive_a_restart_and_one_caught_in_the_middle_is_marked(tmp_path):
    runs = Runs(tmp_path)
    done, stuck = runs.create(ANA, "livre", "Pedido um."), runs.create(ANA, "relatorio", "Pedido dois.")
    done.estado, stuck.estado = "completa", "em_andamento"
    runs.save(done)
    runs.save(stuck)
    runs.trace(done.id).add("inicio", execucao=done.id)

    reloaded = Runs(tmp_path)
    assert isinstance(reloaded.get(done.id, ANA), Run) and reloaded.get(done.id, ANA).estado == "completa"
    assert reloaded.get(stuck.id, ANA).estado == "interrompida" and "reiniciado" in reloaded.get(stuck.id, ANA).erro
    assert reloaded.get(done.id, BRUNO) is None
    assert [step["tipo"] for step in reloaded.trace(done.id).read()] == ["inicio"]
    assert "user_token" not in (tmp_path / "execucoes" / f"{done.id}.json").read_text(encoding="utf-8")
