"""Candidatos a inconsistência da fonte: as regras sobre trechos reais dos documentos (os que
estão no gabarito) e sobre frases certas parecidas, que não podem ser marcadas."""
from decimal import Decimal

from app.regras.inconsistencias import Indicator, find, year_of
from tests.conftest import ANA, DOC_ANA, chunk, payload
from tests.test_extracao import call, fill

LIGHT_P8 = ("Em 2022, o EBITDA CVM1 da distribuidora foi de R$1.817 milhões, uma redução de R$2.996 milhões em "
            "relação aos R$1.179 milhões apurados em 2021. Essa retração foi devida, principalmente, a provisões de "
            "-R$2.513 milhões no período.")
LIGHT_P9 = ("Em 2022, o EBITDA CVM foi de R$1.180 milhões, apresentando uma redução R$3.001 milhões se comparado a "
            "2021. Essa retração foi devida, principalmente, a provisões de - R$2.513 milhões no período.")
LIGHT_P10 = ("A dívida líquida da Companhia em 31 de dezembro de 2022 totalizou R$9.032 milhões, aumento de 23% em "
             "comparação a 2021, de R$7.353 milhões.\n\nO indicador de covenants Dívida Líquida/EBITDA ajustado para "
             "fins de covenants encerrou 2021 em 3,32x, menor do que no 2021 (3,48x).")
LIGHT_P21 = """PARA OS EXERCÍCIOS FINDOS EM 31 DE DEZEMBRO DE 2022 E DE 2021 (Em milhares de reais)

| Demonstrações de Resultados Abrangentes | Controladora - 2022 | Controladora - 2021 | Consolidado - 2021 | Consolidado - 2021 |
|---|---|---|---|---|
| Lucro (Prejuízo) líquido do exercício | (5.672.203) | 397.945 | (5.672.203) | 397.945 |"""
BRASAL_P49 = ("Com base nesses montantes em 31 de dezembro de 2022, definimos o Cenário Provável para os 12 meses de "
              "2020 (Cenário I). A partir do Cenário I (Provável), simulamos acréscimo de 25% (Cenário II).")
MILLET_P41 = """O quadro a seguir demonstra os riscos de liquidez por faixa de vencimento e refletem o fluxo financeiro da Companhia e suas controladas em 31 de dezembro de 2021:

| 2022 | Valor contábil | Fluxo financeiro projetado | Até 12 meses |
|---|---|---|---|
| Empréstimos e Financiamentos | 24.176 | 24.176 | 18.115 |"""
MILLET_P42 = """Os valores justos têm sido apurados para propósitos de mensuração e/ou divulgação.

|  | Controladora - 31/12/2021 | Controladora - 31/12/2021 | Consolidado - 31/12/2022 | Consolidado - 31/12/2021 |
|---|---|---|---|---|
| Caixa e Equivalente de Caixa | 355 | 871 | 10.656 | 8.194 |"""

# Parecidos com os de cima, mas certos.
CERTOS = [
    ("text", ("A dívida líquida em 31 de dezembro de 2022 totalizou R$9.032 milhões, aumento de 23% em comparação "
              "a 2021, de R$7.353 milhões.")),
    ("text", "A receita foi de R$1.325 milhões, um aumento de R$264 milhões em relação aos R$1.061 milhões de 2021."),
    ("text", "O EBITDA foi de R$ 500 mil, redução de R$ 100 mil frente aos R$ 600 mil do ano anterior."),
    ("text", ("A ANEEL, em 13 de outubro de 2022, abriu consulta pública em relação a RTE e em 13 de dezembro de "
              "2022 efetivou a RTE.")),
    ("text", "A alavancagem encerrou 2022 em 3,32x, menor do que em 2021 (3,48x)."),
    ("text", "Definimos o cenário provável para os próximos 12 meses de 2023, com base em 31 de dezembro de 2022."),
    ("table", ("Seguem as movimentações nos exercícios findos em 31 de dezembro de 2022 e de 2021:\n\n"
               "| SALDO EM 31.12.2020 | (2.648.925) |\n|---|---|\n| Adições | (10) |")),
    ("table", ("Balanço social\n\n| Base | 2022 Valor | 2022 Valor | 2022 Valor | 2021 Valor | 2021 Valor | 2021 Valor |\n"
               "|---|---|---|---|---|---|---|\n| Receita | 1 | 2 | 3 | 4 | 5 | 6 |")),
    ("table", ("Em 31 de dezembro de 2022 e 2021 (em milhares de reais)\n\n| | 31/12/2022 | 31/12/2021 |\n"
               "|---|---|---|\n| Caixa | 1 | 2 |")),
]


def chunks_of(*texts: tuple[str, str]) -> list[dict]:
    return [chunk(i, kind, 10 + i, [], text) for i, (kind, text) in enumerate(texts)]


def rules(found) -> list[tuple[int, str]]:
    return [(c.chunk_index, c.regra) for c in found]


def test_the_known_source_errors_become_candidates_with_a_quote_from_the_chunk():
    chunks = chunks_of(("text", LIGHT_P8), ("text", LIGHT_P10), ("table", LIGHT_P21), ("text", BRASAL_P49),
                       ("table", MILLET_P41), ("table", MILLET_P42))
    found = find(chunks, "2022")
    assert rules(found) == [(0, "conta_na_frase"), (1, "ano_comparado_com_ele_mesmo"), (2, "data_do_quadro"),
                            (3, "projecao_para_o_passado"), (4, "data_do_quadro"), (5, "data_do_quadro")]
    assert "dá uma diferença de R$ 638 milhões" in found[0].motivo and "redução de R$2.996 milhões" in found[0].motivo
    assert "2021 com 2021" in found[1].motivo
    assert '"Consolidado - 2021"' in found[2].motivo
    assert "para 2020, num documento de 2022" in found[3].motivo
    assert "em 2021, e as colunas são de 2022" in found[4].motivo
    # O trecho existe no chunk (espaços normalizados): o validador pode copiá-lo como citação.
    for candidate in found:
        assert candidate.trecho in " ".join(chunks[candidate.chunk_index]["text"].split())


def test_correct_sentences_and_tables_are_not_candidates():
    assert find(chunks_of(*CERTOS), "2022") == []


def test_a_change_in_the_text_is_checked_against_the_two_periods_of_the_extraction():
    ebitda = Indicator("EBITDA", ("ebitda",), atual=Decimal(1180000000), anterior=Decimal(1821411000))
    found = find(chunks_of(("text", LIGHT_P9)), "2022", [ebitda])
    assert rules(found) == [(0, "variacao_contra_extracao")]
    assert "diferença de R$ -641 milhões" in found[0].motivo and "redução de R$3.001 milhões" in found[0].motivo
    # A mesma frase com a variação certa não é candidata; sem a extração, a regra não tem com o que comparar.
    right = LIGHT_P9.replace("R$3.001 milhões", "R$641 milhões")
    assert find(chunks_of(("text", right)), "2022", [ebitda]) == []
    assert find(chunks_of(("text", LIGHT_P9)), "2022") == []


def test_the_year_of_the_document_comes_from_the_period():
    assert (year_of("2022"), year_of("3T25"), year_of("4T 24"), year_of(None), year_of("anual")) == \
        (2022, 2025, 2024, None, None)


async def test_the_validator_gets_the_candidates_of_the_document(governance, indexer):
    doc = indexer.documents[(ANA, DOC_ANA)]
    doc["chunks"].append(chunk(10, "text", 12, ["Desempenho"],
                               "A receita líquida foi de R$1.325 milhões, um aumento de R$400 milhões sobre 2021."))
    doc["meta"]["chunk_count"] = len(doc["chunks"])
    await fill(governance)
    result = payload(await call(governance, "candidatos_inconsistencia", {"doc_id": DOC_ANA}, agent="validador"))
    assert [(c["chunk_index"], c["pagina"], c["regra"]) for c in result["candidatos"]] == [
        (10, 12, "variacao_contra_extracao")]
    assert "Receita líquida: a extração leu" in result["candidatos"][0]["motivo"]
    assert "registrar_alerta" in result["como_usar"]
