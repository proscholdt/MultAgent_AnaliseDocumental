import pytest

from app.pipeline.figure_ocr import read_figures
from app.pipeline.figures import (
    FigurePoint,
    FigureSeries,
    FigureTranscription,
    digits,
    verify_transcription,
)
from app.pipeline.metadata import (
    DocMetaLLM,
    FieldEvidence,
    detect_declared_unit,
    infer_unit_from_mentions,
    is_grounded,
    merge_metadata,
    regex_metadata,
)
from app.schemas import ParsedDocument, ParsedItem

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


# --- figuras ---------------------------------------------------------------

PAGE_TEXT = "Perdas Totais/Carga Fio 26,51% 21,53% 9.101 dez/22"


def transcription(**overrides) -> FigureTranscription:
    base = {
        "tipo": "grafico", "titulo": "Evolução das Perdas Totais [GWh]", "legivel": True, "notas": [],
        "series": [FigureSeries(nome="Perdas Totais/Carga Fio", pontos=[
            FigurePoint(rotulo="Dez/22", valor="26,51%"),
            FigurePoint(rotulo="Patamar regulatório", valor="21.53%"),  # ponto no lugar da vírgula
            FigurePoint(rotulo="Set/22", valor="25,98%"),  # não existe no texto da página
        ])],
    }
    return FigureTranscription(**{**base, **overrides})


def test_digits_key_ignores_separators():
    assert digits("26,51%") == digits("26.51") == "2651"
    assert digits("N/D") == ""


def test_only_values_present_in_page_text_are_indexed():
    result = verify_transcription(transcription(), PAGE_TEXT)
    assert "Dez/22: 26,51%" in result.text
    assert "Patamar regulatório: 21.53%" in result.text  # mesmo número, separador diferente
    assert "25,98%" not in result.text
    assert result.unverified_values == ["Perdas Totais/Carga Fio / Set/22: 25,98%"]
    assert result.verified_ratio == pytest.approx(0.667, abs=0.001)
    assert "2 de 3 valores conferidos" in result.text


def test_decorative_and_empty_figures_are_skipped():
    assert verify_transcription(transcription(tipo="decorativo"), PAGE_TEXT) is None
    assert verify_transcription(transcription(titulo=None, series=[]), PAGE_TEXT) is None


def test_illegible_figure_keeps_only_the_title():
    result = verify_transcription(transcription(legivel=False), PAGE_TEXT)
    assert "Evolução das Perdas Totais" in result.text and "26,51%" not in result.text
    assert result.verified_ratio is None


def test_value_read_by_ocr_confirms_a_chart_that_is_an_image():
    """Gráfico colado como imagem: o número não está no texto da página, mas o OCR do recorte
    leu o mesmo valor que o modelo de visão."""
    result = verify_transcription(transcription(), PAGE_TEXT, ocr_text="Set/22\n25.98%\n9.101")
    assert "Set/22: 25,98%" in result.text
    assert result.unverified_values == []
    assert result.verified_ratio == 1.0
    assert result.verified_by_ocr == 1  # os outros dois já estavam no texto da página
    assert "3 de 3 valores conferidos: 2 no texto da página e 1 por OCR do recorte" in result.text


def test_ocr_does_not_confirm_what_it_did_not_read():
    series = [FigureSeries(nome="PECLD/ROB", pontos=[
        FigurePoint(rotulo="Dez/21", valor="3,0%"),  # OCR leu "3" e "0" soltos: não é prova
        FigurePoint(rotulo="Dez/22", valor="6%"),  # um dígito só nunca é confirmado por OCR
        FigurePoint(rotulo="Dez/23", valor="14,68%"),  # OCR leu outro número
    ])]
    result = verify_transcription(transcription(series=series), "sem números", ocr_text="%0°\n6\n3\n14,86%")
    assert result.verified_ratio == 0.0 and result.verified_by_ocr == 0
    assert len(result.unverified_values) == 3
    for value in ("3,0%", "6%", "14,68%"):
        assert value not in result.text


def test_notes_with_numbers_follow_the_same_rule():
    """Nota é texto livre do modelo de visão; número em nota também precisa conferir."""
    notas = ["Comercial 2022: Cativo 4.008", "Industrial 2022: Cativo 9.999", "Valores em GWh"]
    result = verify_transcription(transcription(notas=notas), PAGE_TEXT, ocr_text="Cativo\n4.008\n2022")
    assert "Nota: Comercial 2022: Cativo 4.008" in result.text
    assert "Nota: Valores em GWh" in result.text  # sem número, nada a conferir
    assert "9.999" not in result.text
    assert "Nota: Industrial 2022: Cativo 9.999" in result.unverified_values


def test_figure_ocr_reads_only_candidates_and_only_once(tmp_path):
    class FakeOcr:
        def __init__(self):
            self.calls = []

        def read(self, path):
            self.calls.append(path.name)
            if path.name == "quebrado.png":
                raise OSError("imagem corrompida")
            return "14,68%"

    doc = ParsedDocument(doc_id="d", file_name="f.pdf", page_count=1, parser="t", pipeline_version="x", items=[
        ParsedItem(type="figure", page=1, image_path="figures/grafico.png", area_ratio=0.15),
        ParsedItem(type="figure", page=1, image_path="figures/logo.png", area_ratio=0.01),
        ParsedItem(type="figure", page=1, image_path="figures/quebrado.png", area_ratio=0.10),
        ParsedItem(type="figure", page=1, image_path="figures/lido.png", area_ratio=0.10, ocr_text="já lido"),
        ParsedItem(type="text", page=1, text="parágrafo"),
    ])
    ocr = FakeOcr()
    assert read_figures(doc, tmp_path, ocr, min_area_ratio=0.04) == 2
    assert ocr.calls == ["grafico.png", "quebrado.png"]
    assert [item.ocr_text for item in doc.items] == ["14,68%", None, "", "já lido", None]
    assert read_figures(doc, tmp_path, ocr, min_area_ratio=0.04) == 0  # nada a refazer


def test_hallucinated_numbers_never_reach_the_index():
    result = verify_transcription(transcription(), "página sem nenhum número relevante")
    assert result.verified_ratio == 0.0
    assert len(result.unverified_values) == 3
    for value in ("26,51", "21.53", "25,98"):
        assert value not in result.text
