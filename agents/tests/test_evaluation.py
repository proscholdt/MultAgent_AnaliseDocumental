"""A avaliação contra o gabarito: o que conta como acerto, como invenção e como estabilidade."""
import json
from pathlib import Path

from app.evaluation import check_answer, check_field, evaluate, in_reais, render, same, stability


def informed(value, unit, page, text=None, previous=None):
    block = {"informado": {"status": "encontrado", "valor": text or str(value), "valor_numerico": value, "unidade": unit,
                           "origem": {"pagina": page}}, "comparavel": None, "calculado": None}
    if previous is not None:
        block["comparavel"] = {"valor_numerico": previous, "unidade": unit}
    return block


def not_found(computed=None, unit="R$ mil"):
    return {"informado": {"status": "nao_encontrado", "motivo": "A empresa não informa."}, "comparavel": None,
            "calculado": {"valor": str(computed), "valor_numerico": computed, "unidade": unit} if computed is not None else None}


def test_amounts_are_compared_in_reais_with_a_small_tolerance():
    assert in_reais(9032, "R$ milhões") == 9_032_000_000 and in_reais(3.32, "x") == 3.32
    assert in_reais(10, "unidade desconhecida") is None
    # O mesmo valor escrito em milhões no texto e em milhares na nota.
    assert same(in_reais(9032, "R$ milhões"), in_reais(9032236, "R$ mil"), "R$ mil")
    assert not same(in_reais(1817, "R$ milhões"), in_reais(1180, "R$ milhões"), "R$ milhões")
    assert not same(in_reais(5673, "R$ milhões"), in_reais(-5672203, "R$ mil"), "R$ mil")  # o sinal conta
    assert same(16.74, 16.74, "%") and not same(16.5, 16.74, "%")


def test_informed_field_checks_value_page_and_previous_year():
    expected = {"status": "informado", "valor": 1325059, "unidade": "R$ mil", "paginas": [8], "comparavel": 1061410}
    right = check_field(expected, informed(1325059.0, "R$ mil", 8, previous=1061410.0))
    assert (right["status_certo"], right["valor_certo"], right["pagina_certa"], right["comparavel_certo"]) == (True,) * 4
    assert right["calculado_certo"] is None and right["inventado"] is False

    other_page = check_field(expected, informed(1325059.0, "R$ mil", 15))
    assert other_page["valor_certo"] and other_page["pagina_certa"] is False and other_page["comparavel_certo"] is False
    missing = check_field(expected, not_found())
    assert (missing["status_certo"], missing["valor_certo"], missing["pagina_certa"]) == (False, False, False)
    assert check_field(expected, None)["status"] == "sem_status"


def test_not_informed_field_saved_as_informed_counts_as_invention():
    expected = {"status": "nao_encontrado", "calculado": 16.74, "unidade": "%"}
    honest = check_field(expected, not_found(16.74, "%"))
    assert honest["status_certo"] and honest["calculado_certo"] and not honest["inventado"]
    invented = check_field(expected, informed(1180.0, "%", 9))
    assert invented["inventado"] and not invented["status_certo"]
    # Não informado e sem cálculo, quando o gabarito espera um: o status está certo, o cálculo não.
    no_math = check_field(expected, not_found())
    assert no_math["status_certo"] and no_math["calculado_certo"] is False


def test_alternative_answer_in_the_answer_key_is_accepted():
    expected = {"status": "nao_encontrado", "calculado": 1470292, "unidade": "R$ mil",
                "tambem_aceito": {"status": "informado", "valor": 1506, "unidade": "R$ milhões", "paginas": [10]}}
    assert check_field(expected, not_found(1470292.0))["calculado_certo"]
    alternative = check_field(expected, informed(1506.0, "R$ milhões", 10))
    assert alternative["status_certo"] and alternative["valor_certo"] and not alternative["inventado"]


def test_answers_need_the_expected_content_and_the_expected_source():
    expected = {"deve_conter": ["Light", "3,32x"], "fontes_de": ["Light.pdf"]}
    good = check_answer(expected, {"resposta": "Só a LIGHT S.A., com 3,32x (Light.pdf, p. 10).", "fontes": ["Light.pdf, p. 10"]})
    assert good == {"respondida": True, "certa": True, "faltou": [], "fontes": 1}
    wrong = check_answer(expected, {"resposta": "Nenhuma empresa está acima do limite.", "fontes": ["Brasal.pdf, p. 8"]})
    assert wrong["certa"] is False and wrong["faltou"] == ["Light", "3,32x", "fonte em Light.pdf"]
    assert check_answer(expected, None)["respondida"] is False
    either = {"deve_conter_um_de": ["crédito", "liquidez"]}
    assert check_answer(either, {"resposta": "O risco de liquidez aparece nas três.", "fontes": []})["certa"]


def snapshot(light_margin, alerts):
    return {"documentos": [{"arquivo": "Light.pdf", "alertas": alerts, "extracao": {"indicadores": {
        "receita_liquida": informed(13253286.0, "R$ mil", 20, previous=14897920.0),
        "margem_ebitda": light_margin}}}],
        "respostas": [{"pergunta_id": "p3", "resposta": "A Light, com 3,32x.", "fontes": ["Light.pdf, p. 10"]}],
        "citacoes": {"total": 4, "validas": 4, "invalidas": []}}


KEY = {"documentos": {"Light.pdf": {
    "indicadores": {
        "receita_liquida": {"status": "informado", "valor": 13253286, "unidade": "R$ mil", "paginas": [20],
                            "comparavel": 14897920},
        "margem_ebitda": {"status": "nao_encontrado"}},
    "alertas_automaticos_esperados": [{"codigo": "ebitda_divergente"}],
    "inconsistencias_na_fonte_esperadas": [{"pagina": 8, "contem": "2.996"}, {"pagina": 10, "contem": "3,32x"}]}},
    "perguntas": {"p3": {"deve_conter": ["Light", "3,32x"], "fontes_de": ["Light.pdf"]},
                  "p5": {"deve_conter": ["Light"]}}}


def test_whole_report_counts_hits_invention_alerts_and_questions():
    alerts = [{"codigo": "ebitda_divergente", "campo": "ebitda", "detalhes": {}},
              {"codigo": "inconsistencia_na_fonte", "campo": None,
               "detalhes": {"pagina": 10, "citacao": "encerrou 2021 em 3,32x, menor do que no 2021 (3,48x)"}}]
    report = evaluate(KEY, snapshot(informed(1180.0, "%", 9), alerts))
    summary = report["resumo"]
    assert summary["status_certo"] == {"acertos": 1, "de": 2, "taxa": 0.5}
    assert summary["valor_informado_certo"]["acertos"] == 1 and summary["ano_anterior_certo"]["acertos"] == 1
    assert summary["invencao"] == {"inventados": 1, "de": 1, "taxa": 1.0}
    assert summary["alertas_automaticos"] == {"acertos": 1, "de": 1}
    assert summary["inconsistencias_na_fonte"] == {"acertos": 1, "de": 2}  # a da p. 8 não foi registrada
    assert summary["perguntas_certas"] == {"acertos": 1, "de": 2}  # a p5 não foi respondida

    table = render(report)
    assert "| Invenção (não informado gravado como informado) | 1 de 1 |" in table
    assert "| Light.pdf | margem_ebitda | nao_encontrado | **informado (1180.0 %)** |" not in table  # sem valor esperado
    assert "| Light.pdf | margem_ebitda | nao_encontrado | **informado** |" in table
    assert "| p5 | **NÃO** | a resposta |" in table
    assert "| Citações que existem literalmente no trecho citado | 4 de 4 |" in table

    clean = evaluate(KEY, snapshot(not_found(), alerts))["resumo"]
    assert clean["invencao"]["inventados"] == 0 and clean["status_certo"]["acertos"] == 2


def test_stability_compares_status_and_values_between_two_runs():
    first, second = snapshot(not_found(8.9, "%"), []), snapshot(not_found(8.9, "%"), [])
    assert stability(first, second) == {"campos": 2, "iguais": 2, "diferentes": [], "taxa": 1.0}
    changed = stability(first, snapshot(informed(1180.0, "%", 9), []))
    assert changed["iguais"] == 1 and changed["diferentes"][0]["campo"] == "margem_ebitda"
    assert changed["diferentes"][0]["antes"][0] == "nao_encontrado" and changed["diferentes"][0]["depois"][0] == "informado"


def test_the_shipped_answer_key_is_complete_and_consistent():
    """O gabarito do repositório: os 8 indicadores para os 3 documentos, e as 5 perguntas."""
    key = json.loads((Path(__file__).parents[1] / "avaliacao" / "gabarito.json").read_text(encoding="utf-8"))
    fields = {"receita_liquida", "ebitda", "margem_ebitda", "lucro_liquido", "divida_liquida", "alavancagem",
              "fluxo_caixa_operacional", "capex"}
    assert set(key["documentos"]) == {"Brasal.pdf", "Millet.pdf", "Light.pdf"}
    for file_name, document in key["documentos"].items():
        assert set(document["indicadores"]) == fields, file_name
        for campo, expected in document["indicadores"].items():
            assert expected["status"] in ("informado", "nao_encontrado"), (file_name, campo)
            if expected["status"] == "informado":
                assert {"valor", "unidade", "paginas"} <= set(expected), (file_name, campo)
            else:
                assert "valor" not in expected, (file_name, campo)
    assert {q for q in key["perguntas"] if not q.startswith("_")} == {"p1", "p2", "p3", "p4", "p5"}
    # As contas do gabarito fecham com os valores que ele mesmo cita.
    brasal = key["documentos"]["Brasal.pdf"]["indicadores"]
    assert brasal["ebitda"]["calculado"] == 192840 + 29014 and brasal["divida_liquida"]["calculado"] == 1849 + 1568 - 96453
    assert round(brasal["ebitda"]["calculado"] / brasal["receita_liquida"]["valor"] * 100, 2) == brasal["margem_ebitda"]["calculado"]
    millet = key["documentos"]["Millet.pdf"]["indicadores"]
    assert millet["ebitda"]["calculado"] == 25298 + 18248 and millet["capex"]["calculado"] == 6263 + 11469
    assert round(millet["divida_liquida"]["valor"] / millet["ebitda"]["calculado"], 2) == millet["alavancagem"]["calculado"]
    assert key["documentos"]["Light.pdf"]["indicadores"]["capex"]["calculado"] == 307759 + 1162533
