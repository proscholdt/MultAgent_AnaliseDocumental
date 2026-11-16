"""A intenção do chat por roteamento semântico: as regras do roteador (com um embedding de mentira,
por contagem de palavras) e o que muda numa execução quando ele decide ou não decide."""
import os
import re
import zlib
from pathlib import Path

import pytest

from app.orquestrador.intencao import Roteador, apelidos, empresas_citadas, plain, seguimento
from tests.conftest import DOC, auth, call, decide, delegation, say
from tests.test_orquestrador import answering_researcher, company_team

ROTAS = """
limiar: 0.5
margem: 0.05
max_palavras_seguimento: 8
modelo_embedding: falso
rotas:
  relatorio: [gere o relatório consolidado, monte o relatório completo, relatório da empresa]
  pergunta: [qual empresa teve maior margem ebitda, qual foi a receita líquida, quanto foi o lucro]
  fora_do_escopo: [olá tudo bem, obrigado pela ajuda, conte uma piada]
"""
DOCUMENTOS = [{"doc_id": "d-brasal", "empresa": "Brasal Brasília Holding S.A.", "arquivo": "Brasal.pdf"},
              {"doc_id": "d-light", "empresa": "LIGHT S.A.", "arquivo": "Light.pdf"},
              {"doc_id": "d-millet", "empresa": "Millet Participações S.A.", "arquivo": "Millet.pdf"}]


async def palavras(textos: list[str], _modelo: str) -> tuple[list[list[float]], int]:
    """Um embedding de mentira: conta as palavras (de 3 letras ou mais) em 128 posições."""
    vetores = []
    for texto in textos:
        vetor = [0.0] * 128
        for palavra in re.findall(r"[a-z]{3,}", plain(texto)):
            vetor[zlib.crc32(palavra.encode()) % 128] += 1.0
        vetores.append(vetor)
    return vetores, 3 * len(textos)


@pytest.fixture
def rotas(tmp_path: Path) -> Path:
    path = tmp_path / "intencoes.yaml"
    path.write_text(ROTAS, encoding="utf-8")
    return path


def test_the_company_is_found_by_its_own_words_not_by_the_generic_ones():
    assert apelidos(DOCUMENTOS[0]) == {"brasal"} and apelidos(DOCUMENTOS[2]) == {"millet"}
    assert [d["doc_id"] for d in empresas_citadas("relatório da MILLET", DOCUMENTOS)] == ["d-millet"]
    assert [d["doc_id"] for d in empresas_citadas("compare a Light e a Brasal", DOCUMENTOS)] == ["d-brasal", "d-light"]
    assert empresas_citadas("relatório da holding de Brasília", DOCUMENTOS) == []


def test_a_follow_up_starts_like_a_continuation_and_is_short():
    assert seguimento("e a menor?", "qual a maior margem?", 8)
    assert seguimento("Por que?", "qual a maior margem?", 8)
    assert not seguimento("e a menor?", None, 8)  # sem pedido anterior, não há o que continuar
    assert not seguimento("obrigado!", "qual a maior margem?", 8)
    assert not seguimento("e quanto foi a receita líquida, o lucro e o EBITDA de cada uma delas?", "x", 8)


async def test_a_clear_message_is_decided_by_the_router(rotas):
    roteador = Roteador(rotas, palavras)
    escolha = await roteador.escolher("qual foi a receita líquida da Millet", None, DOCUMENTOS)
    assert (escolha.intencao, escolha.rota, escolha.doc_id) == ("pergunta", "pergunta", None)
    assert escolha.exemplo == "qual foi a receita líquida" and escolha.semelhanca >= 0.5
    fora = await roteador.escolher("olá, tudo bem?", None, DOCUMENTOS)
    assert fora.intencao == "fora_do_escopo"


async def test_a_report_names_one_company_or_it_is_the_consolidated_one(rotas):
    roteador = Roteador(rotas, palavras)
    one = await roteador.escolher("gere o relatório da Millet", None, DOCUMENTOS)
    assert (one.intencao, one.doc_id, one.empresas) == ("relatorio_empresa", "d-millet", ["Millet Participações S.A."])
    two = await roteador.escolher("gere o relatório da Light e da Brasal", None, DOCUMENTOS)
    assert (two.intencao, two.doc_id) == ("relatorio", None)
    none = await roteador.escolher("gere o relatório consolidado", None, DOCUMENTOS)
    assert (none.intencao, none.doc_id) == ("relatorio", None)


async def test_without_certainty_the_router_leaves_it_to_the_orchestrator(rotas):
    roteador = Roteador(rotas, palavras)
    vaga = await roteador.escolher("hmm", None, DOCUMENTOS)
    assert vaga.intencao is None and "abaixo do limiar" in vaga.motivo
    rotas.write_text(ROTAS.replace("margem: 0.05", "margem: 1.01"), encoding="utf-8")  # margem que nada alcança
    os.utime(rotas, (rotas.stat().st_atime, rotas.stat().st_mtime + 5))
    empate = await roteador.escolher("qual foi a receita líquida", None, DOCUMENTOS)
    assert empate.intencao is None and "empatados" in empate.motivo


async def test_a_follow_up_is_read_with_the_previous_request(rotas):
    roteador = Roteador(rotas, palavras)
    escolha = await roteador.escolher("e a menor?", "qual empresa teve maior margem ebitda", DOCUMENTOS)
    assert (escolha.intencao, escolha.seguimento) == ("pergunta", True)
    assert escolha.texto == "qual empresa teve maior margem ebitda e a menor?"
    # A empresa da própria mensagem vale mais que a do pedido anterior.
    outra = await roteador.escolher("e da Millet?", "gere o relatório da Light", DOCUMENTOS)
    assert (outra.intencao, outra.doc_id) == ("relatorio_empresa", "d-millet")


async def test_the_routes_file_is_read_again_when_it_changes_and_a_broken_one_keeps_the_last(rotas):
    roteador = Roteador(rotas, palavras)
    first = await roteador.escolher("conte uma piada", None, DOCUMENTOS)
    assert first.intencao == "fora_do_escopo" and first.tokens == 3 * 9 + 3  # as 9 frases e a mensagem
    again = await roteador.escolher("conte uma piada", None, DOCUMENTOS)
    assert again.tokens == 3  # as frases já têm embedding
    rotas.write_text("rotas: {relatorio: [x]}", encoding="utf-8")
    os.utime(rotas, (rotas.stat().st_atime, rotas.stat().st_mtime + 5))
    kept = await roteador.escolher("conte uma piada", None, DOCUMENTOS)
    assert kept.intencao == "fora_do_escopo" and "faltam frases de exemplo" in roteador.erro


# --- numa execução ---------------------------------------------------------------------------


@pytest.fixture
def com_roteador(api, world, rotas):
    world.policy["sistema"].append("listar_documentos")
    api.state.deps.roteador = Roteador(rotas, palavras)
    return api.state.deps.roteador


async def test_out_of_scope_decided_by_the_router_does_not_call_the_orchestrator(http, model, com_roteador):
    conversation = (await http.post("/api/conversas", headers=auth())).json()["conversa_id"]
    run = (await http.post(f"/api/conversas/{conversation}/mensagens", json={"texto": "olá, tudo bem?"},
                           headers=auth())).json()
    assert (run["estado"], run["tipo"]) == ("completa", "fora_do_escopo")
    assert run["mensagem"].startswith("Isso está fora do escopo deste sistema.")
    assert "orquestrador" not in model.inputs  # nenhuma chamada ao LLM
    steps = {s["tipo"]: s for s in run["passos"]}
    assert steps["intencao"]["fonte"] == "roteador" and steps["roteador"]["decidiu"] is True
    assert run["uso"]["por_agente"]["roteador"]["uso"]["chamadas"] == 1


async def test_a_report_of_one_company_is_routed_with_the_company_found_by_the_rule(http, world, model, com_roteador):
    company_team(world, model)
    # O orquestrador diz outra intenção e outra empresa: vale a do roteador.
    model.scripts["orquestrador"] = [
        [decide(delegation("redator", "Monte o relatório da empresa.", DOC), intencao="pergunta")],
        [decide(terminado=True, mensagem="Pronto.", intencao="pergunta")]]
    conversation = (await http.post("/api/conversas", headers=auth())).json()["conversa_id"]
    run = (await http.post(f"/api/conversas/{conversation}/mensagens", json={"texto": "gere o relatório da Brasal"},
                           headers=auth())).json()
    assert (run["estado"], run["tipo"]) == ("completa", "relatorio_empresa"), run["erro"]
    assert run["resultado"]["doc_id"] == DOC
    assert "Produza o relatório de UMA empresa" in model.first_input("orquestrador")
    assert [s["fonte"] for s in run["passos"] if s["tipo"] == "intencao"] == ["roteador"]


async def test_without_certainty_the_orchestrator_decides_as_before(http, world, model, agents_dir, com_roteador):
    answering_researcher(world, agents_dir, "A Brasal.")
    model.scripts["orquestrador"] = [[decide(delegation("pesquisador", "Qual teve maior margem?"))],
                                     [decide(terminado=True)]]
    model.scripts["pesquisador"] = [[call("salvar_resposta", pergunta="hmm")], [say("Gravada.")]]
    conversation = (await http.post("/api/conversas", headers=auth())).json()["conversa_id"]
    run = (await http.post(f"/api/conversas/{conversation}/mensagens", json={"texto": "hmm"},
                           headers=auth())).json()
    steps = [s for s in run["passos"] if s["tipo"] in ("roteador", "intencao")]
    assert steps[0]["tipo"] == "roteador" and steps[0]["decidiu"] is False
    assert steps[1]["fonte"] == "llm" and steps[1]["intencao"] == "pergunta"
    assert "diga em `intencao` o que ela pede" in model.first_input("orquestrador")
