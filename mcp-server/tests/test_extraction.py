"""Portões da extração: o que precisa conferir para um valor entrar na base, o que o servidor
calcula ao fechar e o que ele sinaliza."""
import pytest

from tests.conftest import BRUNO, DOC_ANA, DOC_BRUNO, headers, payload

DRE, DFC, TEXT, BALANCE, PERFORMANCE = 1, 2, 3, 6, 7  # chunks do documento de exemplo


async def call(governance, name, arguments, agent="extrator", **who):
    return await governance.call_tool(headers(agent, **who), name, arguments)


def cell(campo, row, column, *, chunk=DRE, periodo="2022", **extra):
    return {"doc_id": DOC_ANA, "campo": campo, "periodo": periodo, "origem": "tabela", "chunk_index": chunk,
            "rotulo_linha": row, "rotulo_coluna": column, **extra}


def text(campo, snippet, value, *, chunk=TEXT, periodo="2022", **extra):
    return {"doc_id": DOC_ANA, "campo": campo, "periodo": periodo, "origem": "texto", "chunk_index": chunk,
            "trecho": snippet, "valor": value, **extra}


async def save(governance, arguments):
    result = await call(governance, "salvar_indicador", arguments)
    assert not result.is_error, payload(result)
    return payload(result)


async def rejected(governance, name, arguments) -> dict:
    result = await call(governance, name, arguments)
    assert result.is_error, payload(result)
    return payload(result)


# --- valor de tabela: o agente aponta, o servidor lê --------------------------------------------


async def test_table_value_is_read_by_the_server_from_the_cell_the_agent_points_at(governance):
    saved = (await save(governance, cell("receita_liquida", "Receita operacional líquida", "Consolidado - 2022")))["salvo"]
    assert saved == {"campo": "receita_liquida", "periodo": "2022", "medida": "valor", "valor": "1.325.059",
                     "valor_numerico": 1325059.0, "unidade": "R$ mil", "escopo": "consolidado", "pagina": 8,
                     "linha": "Receita operacional líquida", "coluna": "Consolidado - 2022"}
    # Parênteses são negativo: o custo entra com sinal.
    cost = await save(governance, cell("resultado_antes_financeiro", "Custos dos produtos vendidos", "Consolidado - 2022"))
    assert cost["salvo"]["valor_numerico"] == -839903.0


async def test_agent_cannot_type_the_number_of_a_table_cell(governance):
    error = await rejected(governance, "salvar_indicador", cell(
        "receita_liquida", "Receita operacional líquida", "Consolidado - 2022", valor="9.999.999"))
    assert "o servidor lê a célula" in error["erro"]


async def test_wrong_year_empty_cell_and_unknown_labels_are_refused(governance):
    # A coluna de 2021 pedida como 2022: é o erro que uma conferência "o número existe na página" deixaria passar.
    year = await rejected(governance, "salvar_indicador", cell(
        "receita_liquida", "Receita operacional líquida", "Consolidado - 2021 - (não auditado)"))
    assert "é de 2021, não de 2022" in year["erro"]
    empty = await rejected(governance, "salvar_indicador", cell(
        "receita_liquida", "Receita operacional líquida", "Controladora - 2022"))
    assert "não tem um número: está escrito '-'" in empty["erro"]
    label = await rejected(governance, "salvar_indicador", cell("receita_liquida", "Receita líquida", "Consolidado - 2022"))
    assert "Receita operacional líquida" in label["opcoes"]
    not_table = await rejected(governance, "salvar_indicador", cell("receita_liquida", "x", "y", chunk=TEXT))
    assert "não é uma tabela" in not_table["erro"]
    out_of_range = await rejected(governance, "salvar_indicador", cell("receita_liquida", "x", "y", chunk=99))
    assert "de 0 a 9" in out_of_range["erro"]
    other_year = await rejected(governance, "salvar_indicador", cell(
        "receita_liquida", "Receita operacional líquida", "Consolidado - 2022", periodo="2019"))
    assert "Este documento é de 2022" in other_year["erro"]


async def test_repeated_row_label_needs_the_occurrence(governance):
    ambiguous = await rejected(governance, "salvar_indicador", cell(
        "emprestimos_circulante", "Empréstimos e financiamentos", "Consolidado - 31/12/2022", chunk=BALANCE))
    assert "aparece 2 vezes como linha" in ambiguous["erro"]
    short = await save(governance, cell("emprestimos_circulante", "Empréstimos e financiamentos",
                                        "Consolidado - 31/12/2022", chunk=BALANCE, ocorrencia_linha=1))
    long = await save(governance, cell("emprestimos_nao_circulante", "Empréstimos e financiamentos",
                                       "Consolidado - 31/12/2022", chunk=BALANCE, ocorrencia_linha=2))
    assert (short["salvo"]["valor"], long["salvo"]["valor"]) == ("1.849", "4.100")


# --- valor de texto: trecho literal, com o número inteiro -----------------------------------------


async def test_text_value_needs_a_literal_snippet_that_contains_the_number(governance):
    snippet = "A dívida líquida da Companhia em 31 de dezembro de 2022 totalizou R$9.032 milhões"
    saved = (await save(governance, text("divida_liquida", snippet, "9.032")))["salvo"]
    assert (saved["valor"], saved["valor_numerico"], saved["unidade"], saved["pagina"]) == (
        "9.032", 9032.0, "R$ milhões", 10)  # a unidade vem do trecho, não do agente

    paraphrase = await rejected(governance, "salvar_indicador", text(
        "divida_liquida", "A dívida líquida da Companhia totalizou R$9.032 milhões em 2022", "9.032"))
    assert "não está no chunk" in paraphrase["erro"]
    invented = await rejected(governance, "salvar_indicador", text("divida_liquida", snippet, "9.132"))
    assert "9.132 não está no trecho" in invented["erro"]
    piece = await rejected(governance, "salvar_indicador", text("divida_liquida", snippet, "032"))
    assert "032 não está no trecho" in piece["erro"]  # pedaço de outro número não vale
    kind = await rejected(governance, "salvar_indicador", text(
        "divida_liquida", "aumento de 23% em comparação a 2021, de R$7.353 milhões", "23%"))
    assert "é um valor em dinheiro" in kind["erro"]


async def test_multiple_and_informed_variation_keep_their_own_units(governance):
    leverage = await save(governance, text("alavancagem", "O indicador Dívida Líquida/EBITDA encerrou em 3,32x", "3,32x"))
    assert (leverage["salvo"]["valor"], leverage["salvo"]["unidade"]) == ("3,32x", "x")
    # O trecho não escreve "2022": o valor entra, mas com o aviso de que o período não foi confirmado.
    assert "não escreve o período 2022" in leverage["avisos"][0]
    variation = await save(governance, text(
        "divida_liquida", "A dívida líquida da Companhia em 31 de dezembro de 2022 totalizou R$9.032 milhões, aumento "
        "de 23% em comparação a 2021", "23%", medida="variacao_informada"))
    assert (variation["salvo"]["medida"], variation["salvo"]["unidade"]) == ("variacao_informada", "%")


# --- não encontrado: com evidência ----------------------------------------------------------------


async def test_not_found_is_refused_while_the_term_appears_on_pages_not_discarded(governance, deps):
    refused = await rejected(governance, "salvar_nao_encontrado", {
        "doc_id": DOC_ANA, "campo": "ebitda", "motivo": "A empresa não divulga EBITDA neste documento.",
        "paginas_descartadas": []})
    assert "páginas que você não descartou: 9, 10" in refused["erro"]
    assert any("Dívida Líquida/EBITDA encerrou em 3,32x" in problem for problem in refused["problemas"])

    accepted = payload(await call(governance, "salvar_nao_encontrado", {
        "doc_id": DOC_ANA, "campo": "ebitda", "paginas_descartadas": [9, 10, 33],
        "motivo": "As p. 9 e 10 citam a margem e o múltiplo, mas não o valor do EBITDA."}))
    assert accepted["evidencia"] == {"termos_procurados": ["ebitda", "lajida"], "paginas_com_o_termo": [9, 10],
                                     "paginas_descartadas": [9, 10]}  # a p. 33 não tinha o termo: não conta
    row = (await deps.store.values(next(iter({r.user_id for r in await deps.audit.rows()})), DOC_ANA,
                                   (await deps.documents.get(await _caller(deps), DOC_ANA)).fingerprint))[-1]
    assert (row.campo, row.status, row.valor) == ("ebitda", "nao_encontrado", None)


async def _caller(deps):
    from app.auth import identify
    return await identify(headers("extrator"), deps.settings, deps.users)


async def test_term_absent_from_the_document_is_accepted_at_once(governance):
    """O documento do Bruno tem um parágrafo só: nenhum termo de EBITDA aparece nele."""
    accepted = payload(await call(governance, "salvar_nao_encontrado", {
        "doc_id": DOC_BRUNO, "campo": "ebitda", "motivo": "Procurei por EBITDA e LAJIDA no documento inteiro.",
        "paginas_descartadas": []}, user_token="tok-bruno", user_id=BRUNO))
    assert accepted["evidencia"] == {"termos_procurados": ["ebitda", "lajida"], "paginas_com_o_termo": [],
                                     "paginas_descartadas": []}


async def test_not_found_replaces_a_previous_value_and_exists_only_for_the_eight_indicators(governance):
    await save(governance, text("margem_ebitda", "A margem EBITDA foi de 10,0% no ano", "10,0%", chunk=PERFORMANCE))
    await call(governance, "salvar_nao_encontrado", {
        "doc_id": DOC_ANA, "campo": "margem_ebitda", "paginas_descartadas": [9],
        "motivo": "A p. 9 cita uma margem sem dizer de qual EBITDA."})
    missing = await rejected(governance, "fechar_extracao", {"doc_id": DOC_ANA})
    assert not any(problem.startswith("margem_ebitda") for problem in missing["problemas"])  # tem status
    # Valor de apoio não tem "não encontrado": simplesmente não é gravado.
    support = await rejected(governance, "salvar_nao_encontrado", {
        "doc_id": DOC_ANA, "campo": "caixa_equivalentes", "motivo": "Não achei o caixa no balanço.",
        "paginas_descartadas": []})
    assert support["erro"] == "Argumentos inválidos."


# --- fechar: calcular, validar e publicar ----------------------------------------------------------


async def fill(governance) -> None:
    """Uma extração completa do documento de exemplo: receita e lucro da DRE, caixa do fluxo,
    dívida líquida e alavancagem do texto; EBITDA e margem não informados; capex pelos componentes."""
    for arguments in [
        cell("receita_liquida", "Receita operacional líquida", "Consolidado - 2022"),
        cell("receita_liquida", "Receita operacional líquida", "Consolidado - 2021 - (não auditado)", periodo="2021"),
        cell("lucro_liquido", "Lucro líquido do exercício", "Consolidado - 2022"),
        cell("lucro_liquido", "Lucro líquido do exercício", "Consolidado - 2021 - (não auditado)", periodo="2021"),
        cell("fluxo_caixa_operacional", "Caixa líquido gerado pelas atividades operacionais", "Consolidado - 2022", chunk=DFC),
        cell("resultado_antes_financeiro", "Lucro operacional antes das receitas e despesas financeiras", "Consolidado - 2022"),
        cell("depreciacao_amortizacao", "Depreciações e amortizações", "Consolidado - 2022", chunk=DFC),
        cell("capex_imobilizado", "Aquisição de imobilizado", "Consolidado - 2022", chunk=DFC),
        cell("capex_intangivel", "Aquisição de intangível", "Consolidado - 2022", chunk=DFC),
        text("divida_liquida", "A dívida líquida da Companhia em 31 de dezembro de 2022 totalizou R$9.032 milhões", "9.032"),
        text("alavancagem", "O indicador Dívida Líquida/EBITDA encerrou em 3,32x", "3,32x"),
    ]:
        await save(governance, arguments)
    # O capex não é dado como um valor único, mas os componentes estão na p. 11: o agente a descarta.
    for campo, pages in (("ebitda", [9, 10]), ("margem_ebitda", [9]), ("capex", [11])):
        result = await call(governance, "salvar_nao_encontrado", {
            "doc_id": DOC_ANA, "campo": campo, "paginas_descartadas": pages,
            "motivo": "O documento não traz este indicador como um valor único."})
        assert not result.is_error, payload(result)


async def test_closing_needs_every_indicator_to_have_a_value_or_a_not_found(governance):
    await save(governance, cell("receita_liquida", "Receita operacional líquida", "Consolidado - 2022"))
    missing = await rejected(governance, "fechar_extracao", {"doc_id": DOC_ANA})
    assert "Faltam 7 dos 8 indicadores" in missing["erro"]
    assert "ebitda: EBITDA" in missing["problemas"] and not any("receita" in p for p in missing["problemas"])


async def test_closing_computes_with_fixed_formulas_and_keeps_informed_apart_from_calculated(governance):
    await fill(governance)
    closed = payload(await call(governance, "fechar_extracao", {"doc_id": DOC_ANA}))
    assert closed["publicada"] and closed["periodo"] == "2022"
    assert closed["indicadores"]["receita_liquida"] == "informado: 1.325.059 R$ mil (p. 8); variação: 24,84%"
    # EBITDA: a empresa não informa; o servidor calcula pela DRE e mostra à parte.
    assert closed["indicadores"]["ebitda"] == "não encontrado; calculado: 221.854 R$ mil"

    full = payload(await call(governance, "ler_extracao", {"doc_id": DOC_ANA, "detalhe": "completo"}))
    extraction = full["documentos"][0]["extracao"]
    assert extraction["documento"]["periodo_referencia"] == "2022" and extraction["documento"]["periodo_comparavel"] == "2021"
    indicators = extraction["indicadores"]

    revenue = indicators["receita_liquida"]
    assert revenue["informado"]["origem"] == {
        "tipo": "tabela", "pagina": 8, "chunk_index": DRE, "secao": "Demonstrações dos resultados",
        "trecho": "|  | Notas | Controladora - 2022 | Controladora - 2021 - (não auditado) | Consolidado - 2022 | "
                  "Consolidado - 2021 - (não auditado) |\n| Receita operacional líquida | 21 | - | - | 1.325.059 | 1.061.410 |",
        "linha": "Receita operacional líquida", "coluna": "Consolidado - 2022"}
    assert revenue["comparavel"]["valor"] == "1.061.410" and revenue["calculado"] is None
    assert revenue["variacao"]["calculada"] == {
        "valor": "24,84%", "valor_numerico": 24.84, "unidade": "%", "mudanca_de_sinal": False,
        "formula": "(1.325.059 − 1.061.410) / |1.061.410| × 100"}

    ebitda = indicators["ebitda"]
    assert ebitda["informado"]["status"] == "nao_encontrado"
    assert ebitda["informado"]["evidencia"]["paginas_descartadas"] == [9, 10]
    assert ebitda["calculado"]["valor"] == "221.854" and ebitda["calculado"]["unidade"] == "R$ mil"
    assert ebitda["calculado"]["formula"] == "resultado antes do resultado financeiro + depreciação e amortização"
    assert [(o["campo"], o["valor"], o["pagina"]) for o in ebitda["calculado"]["operandos"]] == [
        ("resultado_antes_financeiro", "192.840", 8), ("depreciacao_amortizacao", "29.014", 11)]

    # Margem: EBITDA calculado sobre a receita informada; cada operando diz de onde veio.
    margin = indicators["margem_ebitda"]["calculado"]
    assert margin["valor"] == "16,74%" and [o["base"] for o in margin["operandos"]] == ["calculado", "informado"]
    # Capex: soma dos dois componentes do fluxo de caixa, em módulo.
    assert indicators["capex"]["calculado"]["valor"] == "17.732"
    # Alavancagem informada (3,32x) e, ao lado, a que sai dos números: 9.032 milhões / 221,854 milhões.
    leverage = indicators["alavancagem"]
    assert leverage["informado"]["valor"] == "3,32x" and leverage["calculado"]["valor"] == "40,71x"
    assert set(extraction["valores_de_apoio"]) == {"resultado_antes_financeiro", "depreciacao_amortizacao",
                                                  "capex_imobilizado", "capex_intangivel"}


async def test_validations_flag_what_does_not_add_up_and_fix_nothing(governance):
    await fill(governance)
    # A empresa escreve "margem EBITDA de 10,0%" e "receita cresceu 30%"; os números dizem outra coisa.
    await save(governance, text("ebitda", "O EBITDA de 2022 somou R$ 485.156 mil", "485.156", chunk=PERFORMANCE))
    await save(governance, text("margem_ebitda", "A margem EBITDA foi de 10,0% no ano", "10,0%", chunk=PERFORMANCE))
    await save(governance, text("receita_liquida", "Em 2022, a receita líquida cresceu 30% em relação a 2021", "30%",
                                chunk=PERFORMANCE, medida="variacao_informada"))
    await save(governance, cell("lucro_liquido", "Lucro líquido do exercício", "Controladora - 2022"))
    await save(governance, cell("fluxo_caixa_operacional", "Lucro bruto", "Consolidado - 2022"))

    closed = payload(await call(governance, "fechar_extracao", {"doc_id": DOC_ANA}))
    alerts = payload(await call(governance, "ler_extracao", {"doc_id": DOC_ANA, "detalhe": "completo"}))[
        "documentos"][0]["extracao"]["alertas_de_validacao"]
    codes = {alert["codigo"]: alert for alert in alerts}
    assert "10,0%" in codes["margem_divergente"]["mensagem"] and "36,61%" in codes["margem_divergente"]["mensagem"]
    assert codes["variacao_divergente"]["detalhes"] == {
        "informada": "30%", "calculada": "24,84%", "pagina": 9,
        "trecho": "Em 2022, a receita líquida cresceu 30% em relação a 2021"}
    assert "valor da controladora" in codes["escopo_controladora"]["mensagem"]
    assert "Demonstrações dos resultados" in codes["secao_inesperada"]["mensagem"]  # caixa operacional tirado da DRE
    assert codes["unidades_diferentes"]["detalhes"]["unidades"] == ["R$ mil", "R$ milhões"]
    # Dois trechos citados não escrevem o ano: o do múltiplo e o da margem.
    assert {a["campo"] for a in alerts if a["codigo"] == "periodo_nao_confirmado"} == {"alavancagem", "margem_ebitda"}
    assert codes["alavancagem_divergente"]["detalhes"]["informada"] == "3,32x"
    # O EBITDA que a empresa escreve contra o que sai da demonstração (resultado operacional + depreciação).
    assert codes["ebitda_divergente"]["detalhes"] == {
        "informado": "485.156 R$ mil", "calculado": "221.854 R$ mil", "pagina": 9,
        "trecho": "O EBITDA de 2022 somou R$ 485.156 mil"}
    # Nada foi corrigido: os valores informados continuam como a fonte os escreve.
    assert closed["indicadores"]["margem_ebitda"].startswith("informado: 10,0% %")


async def test_reindexed_document_makes_what_was_extracted_stale(governance, indexer):
    await fill(governance)
    await call(governance, "fechar_extracao", {"doc_id": DOC_ANA})
    assert payload(await call(governance, "ler_extracao", {}))["documentos"][0]["extracao"]["alertas"] >= 1

    indexer.documents[next(k for k in indexer.documents if k[1] == DOC_ANA)]["meta"]["indexed_at"] = "2026-11-20T08:00:00Z"
    stale = payload(await call(governance, "ler_extracao", {"doc_id": DOC_ANA}))["documentos"][0]
    assert stale["extracao"] is None and "reindexado" in stale["desatualizada"]
    # Os valores lidos no índice antigo não contam: chunk_index e texto podem ter mudado.
    assert "Faltam 8 dos 8" in (await rejected(governance, "fechar_extracao", {"doc_id": DOC_ANA}))["erro"]


async def test_extraction_of_one_user_does_not_exist_for_another(governance):
    await fill(governance)
    await call(governance, "fechar_extracao", {"doc_id": DOC_ANA})
    other = {"user_token": "tok-bruno", "user_id": BRUNO}
    assert payload(await call(governance, "ler_extracao", {}, **other))["documentos"][0]["extracao"] is None
    denied = await call(governance, "ler_extracao", {"doc_id": DOC_ANA}, **other)
    assert denied.is_error and f"Não existe o documento '{DOC_ANA}'" in payload(denied)["erro"]
    # O erro mostra os documentos que existem para quem chamou, e só esses.
    assert payload(denied)["opcoes"] == [f"{DOC_BRUNO} (Outra Empresa S.A.)"]


# --- portões acrescentados depois da primeira execução real dos agentes --------------------------


async def test_the_source_must_be_about_the_indicator_asked_for(governance):
    """'Alavancagem' e 'margem' nomeiam contas diferentes em cada empresa. Na primeira execução real
    entrou como alavancagem um '76%' (dívida sobre capital) e um '0,08' (dívida sobre patrimônio)."""
    other_row = await rejected(governance, "salvar_indicador", cell("ebitda", "Lucro bruto", "Consolidado - 2022"))
    assert "a linha apontada não menciona 'ebitda' nem 'lajida'" in other_row["erro"]
    assert "salvar_nao_encontrado" in other_row["erro"]
    other_ratio = await rejected(governance, "salvar_indicador", text(
        "alavancagem", "Em 2022, a receita líquida cresceu 30% em relação a 2021", "30%", chunk=PERFORMANCE))
    assert "é um múltiplo (ex.: 3,32x), e o número citado (30%) é um percentual" in other_ratio["erro"]
    other_text = await rejected(governance, "salvar_indicador", text(
        "divida_liquida", "aumento de 23% em comparação a 2021, de R$7.353 milhões", "7.353", periodo="2021"))
    assert "a frase apontada não menciona 'divida liquida'" in other_text["erro"]


async def test_value_of_a_part_of_the_group_needs_a_note_saying_so(governance):
    """Na Light, o EBITDA da distribuidora (p. 8) entrou como o da companhia; o consolidado estava na p. 9."""
    snippet = "O EBITDA da distribuidora foi de R$ 1.817 milhões"
    part = await rejected(governance, "salvar_indicador", text("ebitda", snippet, "1.817", chunk=PERFORMANCE))
    assert "fala em 'distribuidora'" in part["erro"] and "consolidado" in part["erro"]
    noted = await save(governance, text("ebitda", snippet, "1.817", chunk=PERFORMANCE,
                                        nota="EBITDA só da distribuidora; a empresa não informa o consolidado."))
    assert noted["salvo"]["valor"] == "1.817" and noted["salvo"]["unidade"] == "R$ milhões"


async def test_one_cell_cannot_be_saved_under_two_fields(governance):
    """Na Brasal, a linha única 'Imobilizado e intangível' foi gravada como imobilizado e como
    intangível, e o capex calculado saiu dobrado."""
    row, column = "Aquisição de imobilizado", "Consolidado - 2022"
    await save(governance, cell("capex_imobilizado", row, column, chunk=DFC))
    twice = await rejected(governance, "salvar_indicador", cell("capex_intangivel", row, column, chunk=DFC))
    assert "já está gravada como 'capex_imobilizado'" in twice["erro"]
    assert "grave-a no campo 'capex_imobilizado_e_intangivel'" in twice["erro"]

    # A linha única substitui os dois campos separados (e vice-versa): não há soma em dobro.
    await save(governance, cell("capex_imobilizado_e_intangivel", row, column, chunk=DFC))
    await fill_except_capex(governance)
    extraction = payload(await call(governance, "ler_extracao", {"doc_id": DOC_ANA, "detalhe": "completo"}))[
        "documentos"][0]["extracao"]
    capex = extraction["indicadores"]["capex"]["calculado"]
    assert capex["valor"] == "6.263" and "linha única" in capex["formula"]
    assert "capex_imobilizado" not in extraction["valores_de_apoio"]

    # O mesmo vale entre outros campos: a célula da receita não pode virar também o lucro.
    await save(governance, cell("receita_liquida", "Receita operacional líquida", column))
    again = await rejected(governance, "salvar_indicador", cell("lucro_liquido", "Receita operacional líquida", column))
    assert "já está gravada como 'receita_liquida'" in again["erro"]


async def fill_except_capex(governance) -> None:
    for arguments in [
        cell("receita_liquida", "Receita operacional líquida", "Consolidado - 2022"),
        cell("lucro_liquido", "Lucro líquido do exercício", "Consolidado - 2022"),
        cell("fluxo_caixa_operacional", "Caixa líquido gerado pelas atividades operacionais", "Consolidado - 2022", chunk=DFC),
    ]:
        await save(governance, arguments)
    for campo, pages in (("ebitda", [9, 10]), ("margem_ebitda", [9]), ("capex", [11]), ("divida_liquida", [10]),
                         ("alavancagem", [10])):
        result = await call(governance, "salvar_nao_encontrado", {
            "doc_id": DOC_ANA, "campo": campo, "paginas_descartadas": pages,
            "motivo": "O documento não traz este indicador como um valor único."})
        assert not result.is_error, payload(result)
    assert not (await call(governance, "fechar_extracao", {"doc_id": DOC_ANA})).is_error


async def test_previous_year_is_read_by_the_server_from_the_sibling_column(governance):
    """Na primeira execução real o extrator esqueceu o ano anterior em dois dos três documentos, e
    o relatório saiu sem variação. Agora quem lê a coluna irmã é o servidor."""
    saved = await save(governance, cell("receita_liquida", "Receita operacional líquida", "Consolidado - 2022"))
    assert saved["ano_anterior_gravado_pelo_servidor"] == {
        "periodo": "2021", "valor": "1.061.410", "coluna": "Consolidado - 2021 - (não auditado)"}
    balance = await save(governance, cell("caixa_equivalentes", "Caixa e equivalentes de caixa",
                                          "Consolidado - 31/12/2022", chunk=BALANCE))
    assert balance["ano_anterior_gravado_pelo_servidor"]["coluna"] == "Consolidado - 31/12/2021"
    # A coluna irmã é a do mesmo escopo: a da controladora não serve para um valor do consolidado.
    assert balance["ano_anterior_gravado_pelo_servidor"]["valor"] == "174.602"

    await fill_except_capex(governance)
    revenue = payload(await call(governance, "ler_extracao", {"doc_id": DOC_ANA, "detalhe": "completo"}))[
        "documentos"][0]["extracao"]["indicadores"]["receita_liquida"]
    assert revenue["comparavel"]["valor"] == "1.061.410" and revenue["variacao"]["calculada"]["valor"] == "24,84%"
    assert revenue["comparavel"]["origem"]["coluna"] == "Consolidado - 2021 - (não auditado)"

    # Valor de texto não tem coluna irmã: não há o que o servidor leia, e nada é inventado.
    from_text = await save(governance, text(
        "divida_liquida", "A dívida líquida da Companhia em 31 de dezembro de 2022 totalizou R$9.032 milhões", "9.032"))
    assert from_text["ano_anterior_gravado_pelo_servidor"] is None


async def test_words_in_another_alphabet_are_refused(governance):
    """O modelo pequeno soltou 'crédito a العملاء' num resumo e 'Não մն' numa mensagem."""
    refused = await rejected(governance, "salvar_nao_encontrado", {
        "doc_id": DOC_ANA, "campo": "capex", "paginas_descartadas": [11],
        "motivo": "A empresa não informa o capex aos العملاء em nenhuma página."})
    assert "palavras em outro alfabeto (العملاء)" in refused["erro"]
    # Ordinais e símbolos comuns em português passam.
    accepted = await call(governance, "salvar_nao_encontrado", {
        "doc_id": DOC_ANA, "campo": "capex", "paginas_descartadas": [11],
        "motivo": "Na 1ª leitura e na nº 2 não há um total de capex; só as linhas de aquisição."})
    assert not accepted.is_error, payload(accepted)


@pytest.mark.parametrize("arguments, problem", [
    ({"doc_id": DOC_ANA, "campo": "lucro", "periodo": "2022", "origem": "tabela", "chunk_index": 1}, "campo"),
    ({"doc_id": DOC_ANA, "campo": "ebitda", "periodo": "22", "origem": "tabela", "chunk_index": 1}, "periodo"),
    ({"doc_id": DOC_ANA, "campo": "ebitda", "periodo": "2022", "origem": "imagem", "chunk_index": 1}, "origem"),
])
async def test_arguments_outside_the_enumerations_are_refused_naming_the_field(governance, arguments, problem):
    error = await rejected(governance, "salvar_indicador", arguments)
    assert any(item.startswith(f"{problem}:") for item in error["problemas"])
