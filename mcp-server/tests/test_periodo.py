"""O período como entidade: anual, trimestre e acumulado, cada um com o seu comparável."""
import pytest

from app.dominio.periodo import Periodo
from tests.conftest import ANA, chunk, document, headers, payload

DOC_TRI = "doc-trimestral"
DRE_TRI = """Informações trimestrais (Valores expressos em milhares de reais)

|  | Consolidado - 01/07/2025 a 30/09/2025 | Consolidado - 01/07/2024 a 30/09/2024 | Consolidado - 9M25 |
|---|---|---|---|
| Receita operacional líquida | 400.100 | 350.200 | 1.150.000 |"""


@pytest.mark.parametrize("texto, rotulo, comparavel", [
    ("2022", "2022", "2021"), ("3T25", "3T2025", "3T2024"), ("3t2025", "3T2025", "3T2024"),
    ("9M25", "9M2025", "9M2024"), ("12M2025", "2025", "2024"),
])
def test_rotulo_e_comparavel(texto, rotulo, comparavel):
    periodo = Periodo.parse(texto)
    assert (periodo.rotulo, periodo.comparavel.rotulo) == (rotulo, comparavel)


@pytest.mark.parametrize("texto", ["", "22", "5T25", "Exercício 2022", "7M25"])
def test_o_que_nao_e_periodo(texto):
    assert Periodo.parse(texto) is None


def test_anual_confere_a_coluna_so_pelo_ano_como_antes():
    assert Periodo.parse("2022").confere_coluna("Consolidado - 2022") == (True, None)
    assert Periodo.parse("2022").confere_coluna("Notas") == (False, None)
    _, motivo = Periodo.parse("2022").confere_coluna("Consolidado - 2021")
    assert "2021" in motivo


def test_trimestre_exige_o_trimestre_ou_a_data_final_na_coluna():
    terceiro = Periodo.parse("3T25")
    assert terceiro.confere_coluna("Consolidado - 01/07/2025 a 30/09/2025")[0]
    assert terceiro.confere_coluna("3T25")[0] is False  # sem o ano de 4 dígitos não há como conferir
    assert terceiro.confere_coluna("3º trimestre de 2025")[0]
    _, motivo = terceiro.confere_coluna("Consolidado - 31/12/2025")
    assert "3T2025" in motivo
    assert Periodo.parse("9M25").confere_coluna("Consolidado - 9M25 - 2025")[0]


def test_so_doze_meses_permitem_multiplo_sobre_o_ebitda():
    assert Periodo.parse("2022").doze_meses and not Periodo.parse("3T25").doze_meses


@pytest.fixture
def trimestral(indexer):
    doc = document(DOC_TRI, ANA, "ITR.pdf", "Empresa Trimestral S.A.",
                   [chunk(0, "table", 5, ["Demonstração do resultado"], DRE_TRI, unit_source="chunk")])
    doc["meta"]["period"] = "3T25"
    indexer.documents[(ANA, DOC_TRI)] = doc
    return DOC_TRI


def _celula(coluna, periodo="3T25"):
    return {"doc_id": DOC_TRI, "campo": "receita_liquida", "periodo": periodo, "origem": "tabela", "chunk_index": 0,
            "rotulo_linha": "Receita operacional líquida", "rotulo_coluna": coluna}


async def test_documento_trimestral_grava_o_trimestre_e_o_comparavel(governance, trimestral):
    result = await governance.call_tool(headers("extrator"), "salvar_indicador",
                                        _celula("Consolidado - 01/07/2025 a 30/09/2025"))
    body = payload(result)
    assert not result.is_error, body
    assert body["salvo"]["periodo"] == "3T2025"
    assert body["ano_anterior_gravado_pelo_servidor"]["periodo"] == "3T2024"
    assert body["ano_anterior_gravado_pelo_servidor"]["valor"] == "350.200"


async def test_documento_trimestral_recusa_ano_fora_do_periodo(governance, trimestral):
    result = await governance.call_tool(headers("extrator"), "salvar_indicador",
                                        _celula("Consolidado - 01/07/2025 a 30/09/2025", periodo="2025"))
    assert result.is_error and "3T2025" in payload(result)["erro"]
