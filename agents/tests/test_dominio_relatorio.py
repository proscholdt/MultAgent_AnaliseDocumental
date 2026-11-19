"""As regras do relatório, fora do grafo: sem LangGraph, A2A nem MCP."""
import ast
from pathlib import Path

from app.dominio.relatorio import (
    hold_early,
    hold_report_only,
    latin_only,
    official_task,
    report_question_ids,
)

PERGUNTAS = {"p1": "Qual empresa teve a maior margem EBITDA?", "p2": "O que explicou a variação da receita?"}


def test_dominio_nao_importa_infraestrutura():
    pasta = Path(__file__).resolve().parents[1] / "app" / "dominio"
    proibidos = ("langgraph", "a2a", "pydantic_ai", "httpx", "app.orquestrador", "app.nucleo", "app.especialistas")
    for arquivo in pasta.glob("*.py"):
        for node in ast.walk(ast.parse(arquivo.read_text(encoding="utf-8"))):
            nomes = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module] if isinstance(node, ast.ImportFrom) and node.module else []
            for nome in nomes:
                assert not nome.startswith(proibidos), f"{arquivo.name} importa {nome}"


def test_numa_pergunta_o_redator_nao_sai():
    go, held = hold_report_only([{"agente": "pesquisador", "tarefa": "x"}, {"agente": "redator", "tarefa": "y"}])
    assert [d["agente"] for d in go] == ["pesquisador"]
    assert held[0]["agente"] == "redator" and held[0]["estado"] == "nao_delegado"


def test_delegacao_antes_da_hora_volta_com_o_que_ja_pode_ser_feito():
    pendencias = [
        {"agente": "extrator", "o_que_falta": "extração", "doc_id": "a", "empresa": "Alfa", "liberada": True},
        {"agente": "pesquisador", "o_que_falta": "resposta", "liberada": False, "aguarda": "extração de Alfa"},
    ]
    go, held = hold_early([{"agente": "pesquisador", "tarefa": "p1"}, {"agente": "extrator", "doc_id": "a"}],
                          pendencias)
    assert [d["agente"] for d in go] == ["extrator"]
    assert "extração de Alfa" in held[0]["erro"] and "extrator: extração (Alfa)" in held[0]["erro"]


def test_delegacao_sem_item_na_lista_segue():
    go, held = hold_early([{"agente": "analista", "doc_id": "a"}], [])
    assert go and not held


def test_tarefa_que_cita_uma_pergunta_vira_o_enunciado_oficial():
    item = official_task({"agente": "pesquisador", "tarefa": "pergunta_id: p2. Responda: qual a maior margem?"},
                         PERGUNTAS)
    assert "«O que explicou a variação da receita?»" in item["tarefa"]
    assert "pergunta_id 'p2'" in item["tarefa"] and item["tarefa_do_orquestrador"].startswith("pergunta_id")


def test_tarefa_com_varias_perguntas_leva_os_enunciados_juntos():
    item = official_task({"tarefa": "Responda p1 e P2 (e p1 de novo)"}, PERGUNTAS)
    assert report_question_ids("Responda p1 e P2 (e p1 de novo)") == ["p1", "p2"]
    assert "p1: «Qual empresa" in item["tarefa"] and "p2: «O que explicou" in item["tarefa"]


def test_tarefa_sem_pergunta_conhecida_fica_como_esta():
    item = {"tarefa": "Responda p9 e p12"}
    assert official_task(item, PERGUNTAS) is item


def test_palavra_em_outro_alfabeto_sai_da_mensagem():
    assert latin_only("Não մնou pendência") == "Não pendência"


def test_pergunta_nova_do_dominio_e_reconhecida_sem_mudar_codigo():
    item = official_task({"tarefa": "Responda a p6"}, {**PERGUNTAS, "p6": "Qual o maior patrimônio líquido?"})
    assert "pergunta_id 'p6'" in item["tarefa"]
