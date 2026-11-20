"""Um domínio que não é financeiro, de ponta a ponta no servidor: o pacote `laudos` (só YAML, sem
plugin). O domínio e a coleção vêm do token do agente; as listas que o modelo vê, a validação, a
leitura da célula, a unidade e o fechamento da extração seguem o pacote, e as regras das
demonstrações (período na coluna, escopo, valores de apoio) ficam de fora."""
import pytest

from tests.conftest import ANA, DOC_ANA, chunk, document, headers, payload

DOC_LAUDO = "1a2b3c4d5e6f7a8b"
COLECAO = "col-laudos"
LAUDO = ("Bioquímica\n\n| Exame | Resultado | Valores de referência |\n|---|---|---|\n"
         "| Glicose | 92 mg/dL | 70 a 99 mg/dL |\n| Colesterol total | 231 mg/dL | inferior a 190 mg/dL |")


@pytest.fixture
def laudo(indexer):
    chunks = [
        chunk(0, "text", 1, [], "Laboratório Vida Clínica. Paciente: Maria Souza. Data da coleta: 12/03/2024.",
              unit=None, unit_source=None),
        chunk(1, "table", 1, ["Bioquímica"], LAUDO, unit=None, unit_source=None),
        chunk(2, "text", 1, ["Observações"], "Observação: amostra levemente lipêmica; recomenda-se repetir o "
              "colesterol em jejum de 12 horas.", unit=None, unit_source=None),
        chunk(3, "text", 1, ["Creatinina"], "Material: soro. Método: enzimático. Resultado: 0,92 mg/dL "
              "Valores de referência: 0,6 a 1,1 mg/dL", unit=None, unit_source=None),
    ]
    doc = document(DOC_LAUDO, ANA, "laudo-maria.pdf", "Maria Souza", chunks)
    doc["meta"].update(period="12/03/2024", doc_type=None, collection_id=COLECAO, dominio="laudos",
                       metadata_source={"paciente": {"source": "llm", "page": 1}})
    indexer.documents[(ANA, DOC_LAUDO)] = doc
    return doc


def laudos(agent: str = "extrator") -> dict[str, str]:
    return headers(agent, dom="laudos", col=COLECAO)


async def call(governance, name, arguments, agent="extrator"):
    return await governance.call_tool(laudos(agent), name, arguments)


async def test_the_model_sees_the_lists_of_its_domain(governance):
    def enums(tools, tool, prop):
        return next(t for t in tools if t.name == tool).input_schema["properties"][prop]["enum"]

    no_laudo = await governance.list_tools(laudos())
    assert enums(no_laudo, "salvar_indicador", "campo")[:3] == ["glicose", "hemoglobina_glicada", "colesterol_total"]
    assert "ebitda" not in enums(no_laudo, "salvar_indicador", "campo")
    financeiro = await governance.list_tools(headers("extrator"))  # sem domínio no token: o padrão
    assert "ebitda" in enums(financeiro, "salvar_indicador", "campo")
    pesquisador = await governance.list_tools(laudos("pesquisador"))
    assert enums(pesquisador, "salvar_resposta", "pergunta_id") == ["p1", "p2", "p3", "p4", "livre"]


async def test_a_lab_result_is_read_from_the_cell_with_the_unit_of_the_package(governance, laudo):
    saved = payload(await call(governance, "salvar_indicador", {
        "doc_id": DOC_LAUDO, "campo": "glicose", "origem": "tabela", "chunk_index": 1,
        "rotulo_linha": "Glicose", "rotulo_coluna": "Resultado"}))
    assert saved["salvo"]["valor_numerico"] == 92 and saved["salvo"]["unidade"] == "mg/dL"
    # Sem período contábil: o valor é do recorte do documento (a data da coleta), sem ano anterior.
    assert saved["salvo"]["periodo"] == "12/03/2024" and saved["ano_anterior_gravado_pelo_servidor"] is None
    assert not [aviso for aviso in saved["avisos"] if "mil, milhões" in aviso or "controladora" in aviso]


async def test_a_result_in_running_text_does_not_ask_for_the_period(governance, laudo):
    # A data do laudo não está na frase do resultado, e não precisa: o valor é do documento.
    saved = payload(await call(governance, "salvar_indicador", {
        "doc_id": DOC_LAUDO, "campo": "creatinina", "origem": "texto", "chunk_index": 3,
        "trecho": "Resultado: 0,92 mg/dL Valores de referência", "valor": "0,92"}))
    assert saved["salvo"]["valor_numerico"] == 0.92 and saved["salvo"]["unidade"] == "mg/dL"
    assert not [aviso for aviso in saved["avisos"] if "período" in aviso], saved["avisos"]


async def test_a_field_of_another_domain_is_refused_with_the_options(governance, laudo):
    refused = await call(governance, "salvar_indicador", {
        "doc_id": DOC_LAUDO, "campo": "ebitda", "origem": "tabela", "chunk_index": 1,
        "rotulo_linha": "Glicose", "rotulo_coluna": "Resultado"})
    assert refused.is_error
    problema = payload(refused)["problemas"][0]
    assert "não existe neste domínio (laudos)" in problema and "glicose" in problema


async def test_the_collection_limits_the_documents(governance, laudo):
    listed = payload(await call(governance, "listar_documentos", {}, agent="leitor"))["documentos"]
    assert [d["doc_id"] for d in listed] == [DOC_LAUDO]
    # O documento financeiro da Ana existe, mas não é desta coleção.
    other = await call(governance, "ler_chunk", {"doc_id": DOC_ANA, "chunk_index": 0}, agent="leitor")
    assert other.is_error and "não é da coleção desta execução" in payload(other)["erro"]


async def test_the_extraction_closes_without_the_financial_support_values(governance, laudo):
    for campo, row in (("glicose", "Glicose"), ("colesterol_total", "Colesterol total")):
        assert not (await call(governance, "salvar_indicador", {
            "doc_id": DOC_LAUDO, "campo": campo, "origem": "tabela", "chunk_index": 1,
            "rotulo_linha": row, "rotulo_coluna": "Resultado"})).is_error
    for campo in ("hemoglobina_glicada", "hdl", "ldl", "triglicerideos", "creatinina", "hemoglobina"):
        result = await call(governance, "salvar_nao_encontrado", {
            "doc_id": DOC_LAUDO, "campo": campo, "motivo": "O laudo traz só glicose e colesterol total.",
            "paginas_descartadas": []})
        assert not result.is_error, payload(result)
    closed = await call(governance, "fechar_extracao", {"doc_id": DOC_LAUDO, "apoio_ausente": None})
    assert not closed.is_error, payload(closed)
    body = payload(closed)
    assert body["publicada"] and body["empresa"] == "Maria Souza" and body["periodo"] == "12/03/2024"
    assert "92" in body["indicadores"]["glicose"] and "mg/dL" in body["indicadores"]["glicose"]
    assert not [alerta for alerta in body["alertas"] if "apoio" in alerta]


async def test_the_analysis_uses_the_categories_of_the_domain(governance, laudo):
    saved = await call(governance, "salvar_item_analise", {
        "doc_id": DOC_LAUDO, "categoria": "recomendacao", "afirmacao": "O laboratório recomenda repetir o "
        "colesterol em jejum.", "chunk_index": 2, "citacao": "recomenda-se repetir o colesterol em jejum de 12 horas",
        "tom": None}, agent="analista")
    assert not saved.is_error, payload(saved)
    analysis = payload(await call(governance, "ler_analise", {"doc_id": DOC_LAUDO}, agent="analista"))["analise"]
    assert list(analysis) == ["fora_da_referencia", "observacao", "recomendacao"]
    assert analysis["recomendacao"][0]["pagina"] == 1
