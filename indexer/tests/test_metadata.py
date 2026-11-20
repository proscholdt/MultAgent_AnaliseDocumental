import pytest

from app import pacotes
from app.indexacao.metadados import (
    FieldEvidence,
    detect_declared_unit,
    detect_scope_unit,
    infer_unit_from_mentions,
    is_grounded,
    llm_instructions,
    llm_schema,
    merge_metadata,
    regex_metadata,
    suggest_domain,
)
from app.schemas import ParsedDocument, ParsedItem, ParsedPage

FINANCEIRO = pacotes.pacote("financeiro")
EMPRESA, PERIODO = FINANCEIRO.metadado("entidade"), FINANCEIRO.metadado("recorte")


def laudos() -> pacotes.Pacote:
    """Um domínio que não é financeiro, montado em memória: paciente e data do exame."""
    dados = FINANCEIRO.model_dump()
    dados.update(id="laudos", nome="Laudos laboratoriais", descricao="Resultados de exames de laboratório.", plugin=None,
                 entidade={"nome": "paciente", "rotulo": "Paciente", "plural": "Pacientes"},
                 recorte={"nome": "data_exame", "rotulo": "Data do exame", "tipo": "data"},
                 documento={"tipos": ["Laudo"], "reconhecer": ["valores de referência", "material: sangue",
                                                               "método:", "laudo liberado"]},
                 metadados=[{"nome": "paciente", "rotulo": "Paciente", "papel": "entidade", "instrucao": "nome do paciente"},
                            {"nome": "data_exame", "rotulo": "Data do exame", "papel": "recorte", "tipo": "data",
                             "instrucao": "data da coleta"},
                            {"nome": "laboratorio", "rotulo": "Laboratório", "instrucao": "nome do laboratório"}])
    return pacotes.Pacote.model_validate(dados)


LAUDO_TEXTO = ("Laboratório Vida Clínica\nPaciente: Maria Souza\nData da coleta: 12/03/2024\n"
               "Material: sangue   Método: enzimático\nGlicose 92 mg/dL  Valores de referência: 70 a 99 mg/dL\n"
               "Laudo liberado em 13/03/2024")


def laudo_doc(texto: str = LAUDO_TEXTO) -> ParsedDocument:
    return ParsedDocument(doc_id="d1", file_name="laudo.pdf", page_count=1, parser="teste", pipeline_version="t",
                          items=[ParsedItem(type="text", text=texto, page=1)],
                          pages=[ParsedPage(page=1, native_text=texto)])

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
    assert regex_metadata(parsed_doc, FINANCEIRO) == {
        "empresa": "Empresa Exemplo S.A.", "periodo": "2022", "tipo_documento": "Demonstrações financeiras",
    }
    # A reserva por regex é do domínio financeiro: num laudo, nada de "S.A." ou "31 de dezembro".
    assert regex_metadata(parsed_doc, laudos()) == {}


def test_llm_value_needs_literal_evidence_on_the_cited_page(parsed_doc):
    good = FieldEvidence(value="Empresa Exemplo S.A.", page=1, snippet="Empresa Exemplo S.A.")
    wrong_page = FieldEvidence(value="Empresa Exemplo S.A.", page=2, snippet="Empresa Exemplo S.A.")
    invented = FieldEvidence(value="Outra Empresa S.A.", page=1, snippet="Outra Empresa S.A.")
    assert is_grounded(EMPRESA, good, parsed_doc)
    assert not is_grounded(EMPRESA, wrong_page, parsed_doc)
    assert not is_grounded(EMPRESA, invented, parsed_doc)

    period_ok = FieldEvidence(value="2022", page=1, snippet="Em 31 de dezembro de 2022")
    period_bad = FieldEvidence(value="2023", page=1, snippet="Em 31 de dezembro de 2022")
    assert is_grounded(PERIODO, period_ok, parsed_doc)
    assert not is_grounded(PERIODO, period_bad, parsed_doc)


def test_merge_precedence_user_then_grounded_llm_then_regex(parsed_doc):
    llm = {"empresa": {"value": "Empresa Exemplo S.A.", "page": 1, "snippet": "Empresa Exemplo S.A."},
           "periodo": {"value": "4T23", "page": 1, "snippet": "trecho que não existe no documento"},
           "tipo_documento": {"value": None, "page": None, "snippet": None}}
    # "doc_type" é o nome de antes dos pacotes: vale para o metadado com o papel de tipo.
    meta = merge_metadata(parsed_doc, {"doc_type": "Release de resultados"}, llm, FINANCEIRO)
    assert meta.company == "Empresa Exemplo S.A." and meta.source["empresa"]["source"] == "llm"
    assert meta.source["empresa"]["page"] == 1
    # Evidência inventada pelo LLM é descartada e o regex assume.
    assert meta.period == "2022" and meta.source["periodo"] == {"source": "regex"}
    assert meta.doc_type == "Release de resultados" and meta.source["tipo_documento"] == {"source": "user"}
    assert meta.default_unit == "R$ mil"
    assert meta.valores == {"empresa": "Empresa Exemplo S.A.", "periodo": "2022",
                            "tipo_documento": "Release de resultados"}


def test_merge_without_llm_falls_back_to_regex(parsed_doc):
    meta = merge_metadata(parsed_doc, {}, None, FINANCEIRO)
    assert (meta.company, meta.period) == ("Empresa Exemplo S.A.", "2022")


def test_the_llm_schema_and_instructions_come_from_the_domain():
    pacote = laudos()
    assert set(llm_schema(pacote).model_fields) == {"paciente", "data_exame", "laboratorio"}
    instrucoes = llm_instructions(pacote)
    assert "Laudos laboratoriais" in instrucoes and "data_exame = data da coleta" in instrucoes
    assert "demonstrações financeiras" not in instrucoes


def test_a_date_is_grounded_only_with_day_month_and_year_in_the_snippet():
    doc, data = laudo_doc(), laudos().metadado("recorte")
    assert is_grounded(data, FieldEvidence(value="12/03/2024", page=1, snippet="Data da coleta: 12/03/2024"), doc)
    assert is_grounded(data, FieldEvidence(value="2024-03-12", page=1, snippet="Data da coleta: 12/03/2024"), doc)
    # A data de liberação não prova a da coleta.
    assert not is_grounded(data, FieldEvidence(value="12/03/2024", page=1, snippet="Laudo liberado em 13/03/2024"),
                           doc)


def test_a_non_financial_domain_gets_its_metadata_and_roles():
    llm = {"paciente": {"value": "Maria Souza", "page": 1, "snippet": "Paciente: Maria Souza"},
           "data_exame": {"value": "12/03/2024", "page": 1, "snippet": "Data da coleta: 12/03/2024"},
           "laboratorio": {"value": "Laboratório Vida Clínica", "page": 1, "snippet": "Laboratório Vida Clínica"}}
    meta = merge_metadata(laudo_doc(), {}, llm, laudos())
    assert meta.dominio == "laudos" and meta.valores["laboratorio"] == "Laboratório Vida Clínica"
    # Os papéis respondem pelos nomes de antes: a entidade e o recorte do domínio.
    assert (meta.company, meta.period, meta.doc_type) == ("Maria Souza", "12/03/2024", None)
    assert meta.default_unit is None  # sem "R$" no laudo


def test_the_domain_suggestion_warns_when_the_pdf_looks_like_another_domain():
    todos = {"financeiro": FINANCEIRO, "laudos": laudos()}
    sugerido, aviso = suggest_domain(laudo_doc(), todos, "financeiro")
    assert sugerido == "laudos" and "Laudos laboratoriais" in aviso and "coleção certa" in aviso
    assert suggest_domain(laudo_doc(), todos, "laudos") == ("laudos", None)
    # Um PDF sem nenhuma frase típica de domínio nenhum: nada a dizer.
    assert suggest_domain(laudo_doc("texto qualquer"), todos, "financeiro") == (None, None)
