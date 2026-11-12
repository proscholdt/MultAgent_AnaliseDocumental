import pytest

from app.pipeline.metadata import (
    DocMetaLLM,
    FieldEvidence,
    detect_declared_unit,
    detect_scope_unit,
    infer_unit_from_mentions,
    is_grounded,
    merge_metadata,
    regex_metadata,
)

# --- unidade ---------------------------------------------------------------


@pytest.mark.parametrize(("text", "unit"), [
    ("(Valores expressos em milhares de reais)", "R$ mil"),
    ("(Em milhares de reais, exceto se indicado de outra forma)", "R$ mil"),
    ("(em milhares de Reais)", "R$ mil"),
    ("Valor (mil reais)", "R$ mil"),
    ("Valor (mil R$)", "R$ mil"),
    ("Efeitos no balanço patrimonial R$ mil", "R$ mil"),
    ("Valores em milhões de reais", "R$ milhões"),
    ("(R$ milhões)", "R$ milhões"),
    ("A receita cresceu no exercício.", None),
])
def test_detect_declared_unit(text, unit):
    assert detect_declared_unit(text) == unit


def test_unit_from_mentions_picks_the_dominant_one():
    text = ("o EBITDA CVM foi de R$1.180 milhões, redução de R$3.001 milhões; "
            "valor de mercado de R$1,7 bilhão")
    assert infer_unit_from_mentions(text) == "R$ milhões"
    assert infer_unit_from_mentions("prejuízo líquido de R$5.672.203 mil") == "R$ mil"
    assert infer_unit_from_mentions("sem valores monetários") is None


@pytest.mark.parametrize(("text", "unit"), [
    ("(Em milhares de Reais - R$, exceto quando indicado de outra forma)", "R$ mil"),
    ("(Valores expressos em milhares de reais)", "R$ mil"),
    ("Valores em milhões de reais", "R$ milhões"),
    ("Valor (mil R$)", None),  # cabeçalho de coluna vale para a tabela, não para o documento
    ("Efeitos no balanço patrimonial R$ mil", None),
    ("investimento de 300 milhões de reais em redes", None),  # prosa, não convenção
])
def test_scope_unit_is_only_a_declared_convention(text, unit):
    assert detect_scope_unit(text) == unit


# --- empresa / período -----------------------------------------------------


def test_regex_metadata_reads_cover_page(parsed_doc):
    assert regex_metadata(parsed_doc) == {
        "company": "Empresa Exemplo S.A.", "period": "2022", "doc_type": "Demonstrações financeiras",
    }


def test_llm_value_needs_literal_evidence_on_the_cited_page(parsed_doc):
    good = FieldEvidence(value="Empresa Exemplo S.A.", page=1, snippet="Empresa Exemplo S.A.")
    wrong_page = FieldEvidence(value="Empresa Exemplo S.A.", page=2, snippet="Empresa Exemplo S.A.")
    invented = FieldEvidence(value="Outra Empresa S.A.", page=1, snippet="Outra Empresa S.A.")
    assert is_grounded("company", good, parsed_doc)
    assert not is_grounded("company", wrong_page, parsed_doc)
    assert not is_grounded("company", invented, parsed_doc)

    period_ok = FieldEvidence(value="2022", page=1, snippet="Em 31 de dezembro de 2022")
    period_bad = FieldEvidence(value="2023", page=1, snippet="Em 31 de dezembro de 2022")
    assert is_grounded("period", period_ok, parsed_doc)
    assert not is_grounded("period", period_bad, parsed_doc)


def test_merge_precedence_user_then_grounded_llm_then_regex(parsed_doc):
    llm = DocMetaLLM(
        company=FieldEvidence(value="Empresa Exemplo S.A.", page=1, snippet="Empresa Exemplo S.A."),
        period=FieldEvidence(value="4T23", page=1, snippet="trecho que não existe no documento"),
        doc_type=FieldEvidence(value=None, page=None, snippet=None),
    )
    meta = merge_metadata(parsed_doc, {"doc_type": "Release de resultados"}, llm)
    assert meta.company == "Empresa Exemplo S.A." and meta.source["company"]["source"] == "llm"
    assert meta.source["company"]["page"] == 1
    # Evidência inventada pelo LLM é descartada e o regex assume.
    assert meta.period == "2022" and meta.source["period"] == {"source": "regex"}
    assert meta.doc_type == "Release de resultados" and meta.source["doc_type"] == {"source": "user"}
    assert meta.default_unit == "R$ mil"


def test_merge_without_llm_falls_back_to_regex(parsed_doc):
    meta = merge_metadata(parsed_doc, {}, None)
    assert (meta.company, meta.period) == ("Empresa Exemplo S.A.", "2022")
