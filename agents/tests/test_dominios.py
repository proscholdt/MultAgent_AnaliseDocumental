"""O domínio da coleção na execução: os tokens levam o escopo ao servidor MCP, o pacote fala com
cada agente, e o que o usuário lê (o fora do escopo) é do domínio."""
import shutil

import jwt
import pytest

from app import pacotes
from app.dominio.relatorio import OUT_OF_SCOPE, out_of_scope
from app.nucleo.agentes import AgentFileError, parse_agent, with_domain
from app.nucleo.tokens import Identity, a2a_token, mcp_token
from app.orquestrador.entradas import with_team
from tests.conftest import ANA, BRUNO, auth, decide


def _claims(token: str) -> dict:
    return jwt.decode(token, options={"verify_signature": False, "verify_aud": False})


def test_the_tokens_carry_the_collection_and_the_domain(settings):
    scoped = Identity("exec-1", ANA, "tok", dominio="laudos", colecao_id="col-laudos")
    for token in (mcp_token(settings, "extrator", scoped), a2a_token(settings, "extrator", scoped)):
        claims = _claims(token)
        assert (claims["dom"], claims["col"]) == ("laudos", "col-laudos")
    # Sem coleção, como antes dos domínios: nada de escopo no token.
    plain = _claims(mcp_token(settings, "extrator", Identity("exec-1", ANA, "tok")))
    assert "dom" not in plain and "col" not in plain


@pytest.fixture
def dominios(tmp_path, monkeypatch):
    """Uma cópia de dominios/ com prompts no pacote de laudos."""
    destino = tmp_path / "dominios"
    shutil.copytree(pacotes.pasta_dos_dominios(), destino)
    prompts = destino / "laudos" / "prompts"
    shutil.rmtree(prompts, ignore_errors=True)  # sem os prompts reais: o teste escreve os seus
    prompts.mkdir()
    monkeypatch.setenv("DOMINIOS_PATH", str(destino))
    return prompts


def _agent(nome: str = "extrator") -> object:
    return parse_agent(f"---\nnome: {nome}\ndescricao: Um agente de teste, que extrai campos de um documento.\nmodelo: gpt-5.4-nano\nmax_turnos: 5\n---\nMétodo do agente: aponte a origem de cada valor, cite a página e não invente nada.\n",
                       f"{nome}.md")


def test_the_package_completes_or_replaces_the_prompt_of_an_agent(dominios):
    (dominios / "extrator.md").write_text("---\nmodo: substitui\n---\nVocê extrai exames de laudos.\n", encoding="utf-8")
    (dominios / "validador.md").write_text("Neste domínio, confira a faixa de referência.\n", encoding="utf-8")
    assert with_domain(_agent("extrator"), "laudos").prompt == "Você extrai exames de laudos."
    assert with_domain(_agent("validador"), "laudos").prompt == ("Método do agente: aponte a origem de cada valor, cite a página e não invente nada.\n\n"
                                                                "Neste domínio, confira a faixa de referência.")
    # Sem arquivo no pacote, o agente fica como está.
    assert with_domain(_agent("redator"), "laudos").prompt == "Método do agente: aponte a origem de cada valor, cite a página e não invente nada."
    # Sem domínio, vale o padrão: o método do agente mais o complemento do financeiro.
    padrao = with_domain(_agent("extrator"), None).prompt
    assert padrao.startswith("Método do agente") and "demonstrações financeiras" in padrao
    (dominios / "redator.md").write_text("---\nmodo: troca\n---\nx\n", encoding="utf-8")
    with pytest.raises(AgentFileError, match="troca"):
        with_domain(_agent("redator"), "laudos")


def test_without_domain_the_default_package_speaks(dominios):
    padrao = dominios.parent.parent / pacotes.PADRAO / "prompts"
    padrao.mkdir(exist_ok=True)
    (padrao / "validador.md").write_text("No domínio padrão, confira as contas.\n", encoding="utf-8")
    assert with_domain(_agent("validador"), None).prompt.endswith("No domínio padrão, confira as contas.")


def test_the_team_list_survives_a_replaced_orchestrator_prompt(dominios):
    # O orquestrador recebe o pacote e depois a lista da equipe; o run_agent aplica o domínio de novo
    # e não pode apagar a lista (modo substitui) nem repetir o complemento.
    (dominios / "orquestrador.md").write_text("---\nmodo: substitui\n---\nVocê coordena laudos.\n", encoding="utf-8")
    catalog = {"agentes": [{"nome": "extrator", "descricao": "Extrai os exames."}]}
    spec = with_team(with_domain(_agent("orquestrador"), "laudos"), catalog, "laudos")
    again = with_domain(spec, "laudos")
    assert again.prompt.startswith("Você coordena laudos.") and "- `extrator`: Extrai os exames." in again.prompt
    (dominios / "validador.md").write_text("Confira a faixa de referência.\n", encoding="utf-8")
    twice = with_domain(with_domain(_agent("validador"), "laudos"), "laudos")
    assert twice.prompt.count("Confira a faixa de referência.") == 1


def test_out_of_scope_speaks_the_domain():
    texto, opcoes = out_of_scope("laudos")
    assert "laudos de exames" in texto and "não faz diagnóstico" in texto
    assert opcoes[0]["titulo"] == "Ver o que está fora da referência"
    assert out_of_scope(None)[0] == OUT_OF_SCOPE and "demonstrações financeiras" in OUT_OF_SCOPE


async def test_a_message_in_a_collection_runs_in_its_domain_and_the_next_one_inherits_it(http, model, dominios):
    model.scripts["orquestrador"] = [[decide(terminado=True, intencao="fora_do_escopo")]]
    conversation = (await http.post("/api/conversas", headers=auth())).json()["conversa_id"]
    first = (await http.post(f"/api/conversas/{conversation}/mensagens", headers=auth(),
                             json={"texto": "me conta uma piada", "colecao_id": "col-laudos"})).json()
    assert (first["tipo"], first["estado"]) == ("fora_do_escopo", "completa"), first
    assert "não faz diagnóstico" in first["mensagem"]
    assert first["resultado"]["opcoes"][0]["exemplo"] == "Algum resultado está fora da faixa de referência?"
    second = (await http.post(f"/api/conversas/{conversation}/mensagens", headers=auth(),
                              json={"texto": "outra piada"})).json()
    assert "não faz diagnóstico" in second["mensagem"]  # sem colecao_id: a da mensagem anterior


async def test_a_request_without_collection_runs_in_the_default_one(http, model):
    # A mesma regra do envio sem coleção no indexador: a primeira coleção do domínio padrão. Sem
    # ela, as respostas e o relatório seriam de "todos os documentos", misturando laudos e balanços.
    model.scripts["orquestrador"] = [[decide(terminado=True, intencao="fora_do_escopo")]]
    run = (await http.post("/api/perguntas", headers=auth(), json={"pergunta": "me conta uma piada"})).json()
    assert (run["colecao_id"], run["dominio"]) == ("col-demonstracoes", "financeiro"), run
    alone = (await http.post("/api/perguntas", headers=auth(BRUNO), json={"pergunta": "me conta uma piada"})).json()
    assert (alone["colecao_id"], alone["dominio"]) == (None, "financeiro")  # sem coleção: como antes delas


async def test_a_collection_of_someone_else_is_not_found(http, model):
    missing = await http.post("/api/perguntas", headers=auth(BRUNO),
                              json={"pergunta": "Qual a glicose da Maria?", "colecao_id": "col-laudos"})
    assert missing.status_code == 404 and missing.json()["detail"] == "Coleção não encontrada."
