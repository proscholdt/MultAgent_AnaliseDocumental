"""Portões da extração: o que precisa conferir para um valor entrar na base, o que o servidor
calcula ao fechar e o que ele sinaliza."""
import pytest

from tests.conftest import (
    BRUNO,
    CAPITAL,
    DOC_ANA,
    DOC_BRUNO,
    EXTRA,
    add_loans_note,
    add_performance_details,
    headers,
    payload,
)

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
    result = await call(governance, "salvar_campo", arguments)
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
    spent = await save(governance, cell("capex_imobilizado", "Aquisição de imobilizado", "Consolidado - 2022", chunk=DFC))
    assert spent["salvo"]["valor_numerico"] == -6263.0


async def test_agent_cannot_type_the_number_of_a_table_cell(governance):
    error = await rejected(governance, "salvar_campo", cell(
        "receita_liquida", "Receita operacional líquida", "Consolidado - 2022", valor="9.999.999"))
    assert "o servidor lê a célula" in error["erro"]


async def test_wrong_year_empty_cell_and_unknown_labels_are_refused(governance):
    # A coluna de 2021 pedida como 2022: é o erro que uma conferência "o número existe na página" deixaria passar.
    year = await rejected(governance, "salvar_campo", cell(
        "receita_liquida", "Receita operacional líquida", "Consolidado - 2021 - (não auditado)"))
    assert "é de 2021, não de 2022" in year["erro"]
    empty = await rejected(governance, "salvar_campo", cell(
        "receita_liquida", "Receita operacional líquida", "Controladora - 2022"))
    assert "não tem um número: está escrito '-'" in empty["erro"]
    label = await rejected(governance, "salvar_campo", cell("receita_liquida", "Receita líquida", "Consolidado - 2022"))
    assert "Receita operacional líquida" in label["opcoes"]
    not_table = await rejected(governance, "salvar_campo", cell("receita_liquida", "x", "y", chunk=TEXT))
    assert "não é uma tabela" in not_table["erro"]
    out_of_range = await rejected(governance, "salvar_campo", cell("receita_liquida", "x", "y", chunk=99))
    assert "de 0 a 9" in out_of_range["erro"]
    other_year = await rejected(governance, "salvar_campo", cell(
        "receita_liquida", "Receita operacional líquida", "Consolidado - 2022", periodo="2019"))
    assert "Este documento é de 2022" in other_year["erro"]


async def test_repeated_row_label_needs_the_occurrence(governance):
    ambiguous = await rejected(governance, "salvar_campo", cell(
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

    paraphrase = await rejected(governance, "salvar_campo", text(
        "divida_liquida", "A dívida líquida da Companhia totalizou R$9.032 milhões em 2022", "9.032"))
    assert "não está no chunk" in paraphrase["erro"]
    invented = await rejected(governance, "salvar_campo", text("divida_liquida", snippet, "9.132"))
    assert "9.132 não está no trecho" in invented["erro"]
    piece = await rejected(governance, "salvar_campo", text("divida_liquida", snippet, "032"))
    assert "032 não está no trecho" in piece["erro"]  # pedaço de outro número não vale
    kind = await rejected(governance, "salvar_campo", text(
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
    from app.governanca.identidade import identify
    return await identify(headers("extrator"), deps.settings, deps.users)


async def test_term_absent_from_the_document_is_accepted_at_once(governance):
    """O documento do Bruno tem um parágrafo só: nenhum termo de EBITDA aparece nele."""
    accepted = payload(await call(governance, "salvar_nao_encontrado", {
        "doc_id": DOC_BRUNO, "campo": "ebitda", "motivo": "Procurei por EBITDA e LAJIDA no documento inteiro.",
        "paginas_descartadas": []}, user_token="tok-bruno", user_id=BRUNO))
    assert accepted["evidencia"] == {"termos_procurados": ["ebitda", "lajida"], "paginas_com_o_termo": [],
                                     "paginas_descartadas": []}


async def test_not_found_replaces_a_previous_value_and_exists_only_for_the_eight_indicators(governance, indexer):
    add_performance_details(indexer)
    informed = await save(governance, text("capex", "O valor total de investimentos no ano foi de R$ 50 milhões", "50",
                                           chunk=EXTRA))
    assert informed["salvo"]["unidade"] == "R$ milhões"
    await call(governance, "salvar_nao_encontrado", {
        "doc_id": DOC_ANA, "campo": "capex", "paginas_descartadas": [11],
        "motivo": "A frase fala em investimentos em geral, não em capex; a p. 11 traz só as linhas de aquisição."})
    missing = await rejected(governance, "fechar_extracao", {"doc_id": DOC_ANA})
    assert not any(problem.startswith("capex") for problem in missing["problemas"])  # tem status
    # Valor de apoio não tem "não encontrado": simplesmente não é gravado.
    support = await rejected(governance, "salvar_nao_encontrado", {
        "doc_id": DOC_ANA, "campo": "caixa_equivalentes", "motivo": "Não achei o caixa no balanço.",
        "paginas_descartadas": []})
    assert support["erro"] == "Argumentos inválidos."


# --- fechar: calcular, validar e publicar ----------------------------------------------------------


async def fill(governance, not_found=("ebitda", "margem_ebitda", "capex")) -> None:
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
        if campo not in not_found:
            continue
        result = await call(governance, "salvar_nao_encontrado", {
            "doc_id": DOC_ANA, "campo": campo, "paginas_descartadas": pages,
            "motivo": "O documento não traz este indicador como um valor único."})
        assert not result.is_error, payload(result)


async def test_closing_needs_every_indicator_to_have_a_value_or_a_not_found(governance):
    await save(governance, cell("receita_liquida", "Receita operacional líquida", "Consolidado - 2022"))
    missing = await rejected(governance, "fechar_extracao", {"doc_id": DOC_ANA})
    assert "Faltam 7 dos 8 campos principais" in missing["erro"]
    assert "ebitda: EBITDA" in missing["problemas"] and not any("receita" in p for p in missing["problemas"])


async def test_closing_computes_with_fixed_formulas_and_keeps_informed_apart_from_calculated(governance):
    await fill(governance)
    closed = payload(await call(governance, "fechar_extracao", {"doc_id": DOC_ANA}))
    assert closed["publicada"] and closed["periodo"] == "2022"
    assert closed["campos"]["receita_liquida"] == "informado: 1.325.059 R$ mil (p. 8); variação: 24,84%"
    # EBITDA: a empresa não informa; o servidor calcula pela DRE e mostra à parte.
    assert closed["campos"]["ebitda"] == "não encontrado; calculado: 221.854 R$ mil"

    full = payload(await call(governance, "ler_extracao", {"doc_id": DOC_ANA, "detalhe": "completo"}))
    extraction = full["documentos"][0]["extracao"]
    assert extraction["documento"]["periodo_referencia"] == "2022" and extraction["documento"]["periodo_comparavel"] == "2021"
    indicators = extraction["campos"]

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


async def test_validations_flag_what_does_not_add_up_and_fix_nothing(governance, indexer):
    add_performance_details(indexer)
    await fill(governance, not_found=("capex",))
    # A empresa escreve "margem EBITDA de 10,0%" e "receita cresceu 30%"; os números dizem outra coisa.
    await save(governance, text("ebitda", "O EBITDA de 2022 somou R$ 485.156 mil", "485.156", chunk=EXTRA))
    await save(governance, text("margem_ebitda", "A margem EBITDA foi de 10,0% no ano", "10,0%", chunk=EXTRA))
    await save(governance, text("receita_liquida", "Em 2022, a receita líquida cresceu 30% em relação a 2021", "30%",
                                chunk=PERFORMANCE, medida="variacao_informada"))
    await save(governance, cell("lucro_liquido", "Lucro líquido do exercício", "Controladora - 2022"))
    await save(governance, cell("caixa_equivalentes", "(-) Caixa e equivalente de caixa", "Consolidado - 31/12/2022",
                                chunk=CAPITAL))

    closed = payload(await call(governance, "fechar_extracao", {"doc_id": DOC_ANA}))
    alerts = payload(await call(governance, "ler_extracao", {"doc_id": DOC_ANA, "detalhe": "completo"}))[
        "documentos"][0]["extracao"]["alertas_de_validacao"]
    codes = {alert["codigo"]: alert for alert in alerts}
    assert "10,0%" in codes["margem_divergente"]["mensagem"] and "36,61%" in codes["margem_divergente"]["mensagem"]
    assert codes["variacao_divergente"]["detalhes"] == {
        "informada": "30%", "calculada": "24,84%", "pagina": 9,
        "trecho": "Em 2022, a receita líquida cresceu 30% em relação a 2021"}
    assert "valor da controladora" in codes["escopo_controladora"]["mensagem"]
    assert "22.2. Gestão de capital" in codes["secao_inesperada"]["mensagem"]  # caixa tirado de uma nota
    assert codes["unidades_diferentes"]["detalhes"]["unidades"] == ["R$ mil", "R$ milhões"]
    # Dois trechos citados não escrevem o ano: o do múltiplo e o da margem.
    assert {a["campo"] for a in alerts if a["codigo"] == "periodo_nao_confirmado"} == {"alavancagem", "margem_ebitda"}
    assert codes["alavancagem_divergente"]["detalhes"]["informada"] == "3,32x"
    # O EBITDA que a empresa escreve contra o que sai da demonstração (resultado operacional + depreciação).
    assert codes["ebitda_divergente"]["detalhes"] == {
        "informado": "485.156 R$ mil", "calculado": "221.854 R$ mil", "pagina": 9,
        "trecho": "O EBITDA de 2022 somou R$ 485.156 mil"}
    # Nada foi corrigido: os valores informados continuam como a fonte os escreve.
    assert closed["campos"]["margem_ebitda"].startswith("informado: 10,0% %")


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
    other_row = await rejected(governance, "salvar_campo", cell("ebitda", "Lucro bruto", "Consolidado - 2022"))
    assert "a linha apontada não menciona 'ebitda' nem 'lajida'" in other_row["erro"]
    assert "salvar_nao_encontrado" in other_row["erro"]
    other_ratio = await rejected(governance, "salvar_campo", text(
        "alavancagem", "Em 2022, a receita líquida cresceu 30% em relação a 2021", "30%", chunk=PERFORMANCE))
    assert "é um múltiplo (ex.: 1,5x), e o número citado (30%) é um percentual" in other_ratio["erro"]
    other_text = await rejected(governance, "salvar_campo", text(
        "divida_liquida", "aumento de 23% em comparação a 2021, de R$7.353 milhões", "7.353", periodo="2021"))
    assert "a frase apontada não menciona 'divida liquida'" in other_text["erro"]


async def test_value_of_a_part_of_the_group_needs_a_note_saying_so(governance, indexer):
    """Na Light, o EBITDA da distribuidora (p. 8) entrou como o da companhia; o consolidado estava na p. 9."""
    add_performance_details(indexer)
    snippet = "O EBITDA da distribuidora foi de R$ 1.817 milhões"
    part = await rejected(governance, "salvar_campo", text("ebitda", snippet, "1.817", chunk=EXTRA))
    assert "fala em 'distribuidora'" in part["erro"] and "consolidado" in part["erro"]
    noted = await save(governance, text("ebitda", snippet, "1.817", chunk=EXTRA,
                                        nota="EBITDA só da distribuidora; a empresa não informa o consolidado."))
    assert noted["salvo"]["valor"] == "1.817" and noted["salvo"]["unidade"] == "R$ milhões"


async def test_one_cell_cannot_be_saved_under_two_fields(governance):
    """Na Brasal, a linha única 'Imobilizado e intangível' foi gravada como imobilizado e como
    intangível, e o capex calculado saiu dobrado."""
    row, column = "Outras aquisições de imobilizado e intangível", "Consolidado - 2022"
    await save(governance, cell("capex_imobilizado", row, column, chunk=DFC))
    twice = await rejected(governance, "salvar_campo", cell("capex_intangivel", row, column, chunk=DFC))
    assert "já está gravada como 'capex_imobilizado'" in twice["erro"]
    assert "grave-a no campo 'capex_imobilizado_e_intangivel'" in twice["erro"]

    # A linha única substitui os dois campos separados (e vice-versa): não há soma em dobro.
    await save(governance, cell("capex_imobilizado_e_intangivel", row, column, chunk=DFC))
    await fill_the_rest(governance, capex=False)
    extraction = payload(await call(governance, "ler_extracao", {"doc_id": DOC_ANA, "detalhe": "completo"}))[
        "documentos"][0]["extracao"]
    capex = extraction["campos"]["capex"]["calculado"]
    assert capex["valor"] == "45.761" and "linha única" in capex["formula"]
    assert "capex_imobilizado" not in extraction["valores_de_apoio"]
    # E o campo da linha única só aceita linha que fale dos dois (na Light entrou nele a de intangível).
    only_one = await rejected(governance, "salvar_campo", cell(
        "capex_imobilizado_e_intangivel", "Aquisição de intangível", column, chunk=DFC))
    assert "tem de mencionar 'imobilizado e intangivel'" in only_one["erro"]

    # O mesmo vale entre outros campos: a célula da receita não pode virar também o lucro.
    operating = "Lucro operacional antes das receitas e despesas financeiras"  # já gravada como valor de apoio
    again = await rejected(governance, "salvar_campo", cell("lucro_liquido", operating, column))
    assert "já está gravada como 'resultado_antes_financeiro'" in again["erro"]


async def fill_the_rest(governance, capex: bool = True) -> None:
    """O que falta para fechar: os valores das demonstrações, os de apoio e os "não encontrado"."""
    lines = [
        cell("receita_liquida", "Receita operacional líquida", "Consolidado - 2022"),
        cell("lucro_liquido", "Lucro líquido do exercício", "Consolidado - 2022"),
        cell("fluxo_caixa_operacional", "Caixa líquido gerado pelas atividades operacionais", "Consolidado - 2022", chunk=DFC),
        cell("resultado_antes_financeiro", "Lucro operacional antes das receitas e despesas financeiras", "Consolidado - 2022"),
        cell("depreciacao_amortizacao", "Depreciações e amortizações", "Consolidado - 2022", chunk=DFC),
        cell("emprestimos_circulante", "Empréstimos e financiamentos", "Consolidado - 31/12/2022", chunk=BALANCE,
             ocorrencia_linha=1),
        cell("emprestimos_nao_circulante", "Empréstimos e financiamentos", "Consolidado - 31/12/2022", chunk=BALANCE,
             ocorrencia_linha=2),
        cell("caixa_equivalentes", "Caixa e equivalentes de caixa", "Consolidado - 31/12/2022", chunk=BALANCE),
    ]
    if capex:
        lines += [cell("capex_imobilizado", "Aquisição de imobilizado", "Consolidado - 2022", chunk=DFC),
                  cell("capex_intangivel", "Aquisição de intangível", "Consolidado - 2022", chunk=DFC)]
    lines += [
        text("divida_liquida", "A dívida líquida da Companhia em 31 de dezembro de 2022 totalizou R$9.032 milhões", "9.032"),
        text("alavancagem", "O indicador Dívida Líquida/EBITDA encerrou em 3,32x", "3,32x")]
    for arguments in lines:
        await save(governance, arguments)
    for campo, pages in (("ebitda", [9, 10]), ("margem_ebitda", [9]), ("capex", [11])):
        result = await call(governance, "salvar_nao_encontrado", {
            "doc_id": DOC_ANA, "campo": campo, "paginas_descartadas": pages,
            "motivo": "O documento não traz este indicador como um valor único."})
        assert not result.is_error, payload(result)
    closed = await call(governance, "fechar_extracao", {"doc_id": DOC_ANA})
    assert not closed.is_error, payload(closed)


async def test_previous_year_is_read_by_the_server_from_the_sibling_column(governance):
    """Na primeira execução real o extrator esqueceu o ano anterior em dois dos três documentos, e
    o relatório saiu sem variação. Agora quem lê a coluna irmã é o servidor."""
    saved = await save(governance, cell("receita_liquida", "Receita operacional líquida", "Consolidado - 2022"))
    assert saved["comparavel_gravado_pelo_servidor"] == {
        "periodo": "2021", "valor": "1.061.410", "coluna": "Consolidado - 2021 - (não auditado)"}
    balance = await save(governance, cell("caixa_equivalentes", "Caixa e equivalentes de caixa",
                                          "Consolidado - 31/12/2022", chunk=BALANCE))
    assert balance["comparavel_gravado_pelo_servidor"]["coluna"] == "Consolidado - 31/12/2021"
    # A coluna irmã é a do mesmo escopo: a da controladora não serve para um valor do consolidado.
    assert balance["comparavel_gravado_pelo_servidor"]["valor"] == "174.602"

    await fill_the_rest(governance)
    revenue = payload(await call(governance, "ler_extracao", {"doc_id": DOC_ANA, "detalhe": "completo"}))[
        "documentos"][0]["extracao"]["campos"]["receita_liquida"]
    assert revenue["comparavel"]["valor"] == "1.061.410" and revenue["variacao"]["calculada"]["valor"] == "24,84%"
    assert revenue["comparavel"]["origem"]["coluna"] == "Consolidado - 2021 - (não auditado)"

    # Valor de texto não tem coluna irmã: não há o que o servidor leia, e nada é inventado.
    from_text = await save(governance, text(
        "divida_liquida", "A dívida líquida da Companhia em 31 de dezembro de 2022 totalizou R$9.032 milhões", "9.032"))
    assert from_text["comparavel_gravado_pelo_servidor"] is None


async def test_what_the_company_does_not_inform_must_be_computable_or_explained(governance):
    """Na segunda execução real o extrator escreveu "as linhas de apoio foram gravadas" sem ter
    gravado: três indicadores saíram sem o valor calculado, como se a empresa só não informasse."""
    for arguments in [
        cell("receita_liquida", "Receita operacional líquida", "Consolidado - 2022"),
        cell("lucro_liquido", "Lucro líquido do exercício", "Consolidado - 2022"),
        cell("fluxo_caixa_operacional", "Caixa líquido gerado pelas atividades operacionais", "Consolidado - 2022", chunk=DFC),
        cell("resultado_antes_financeiro", "Lucro operacional antes das receitas e despesas financeiras", "Consolidado - 2022"),
        cell("depreciacao_amortizacao", "Depreciações e amortizações", "Consolidado - 2022", chunk=DFC),
        text("divida_liquida", "A dívida líquida da Companhia em 31 de dezembro de 2022 totalizou R$9.032 milhões", "9.032"),
        text("alavancagem", "O indicador Dívida Líquida/EBITDA encerrou em 3,32x", "3,32x"),
    ]:
        await save(governance, arguments)
    for campo, pages in (("ebitda", [9, 10]), ("margem_ebitda", [9]), ("capex", [11])):
        await call(governance, "salvar_nao_encontrado", {
            "doc_id": DOC_ANA, "campo": campo, "paginas_descartadas": pages,
            "motivo": "O documento não traz este indicador como um valor único."})

    refused = await rejected(governance, "fechar_extracao", {"doc_id": DOC_ANA})
    assert "Faltam valores de apoio" in refused["erro"]
    # O EBITDA tem os dois operandos e a dívida líquida é informada: não aparecem. O capex, sim.
    assert refused["problemas"] == [
        "capex: faltam capex_imobilizado e capex_intangivel (ou capex_imobilizado_e_intangivel, se for uma linha só)"]

    # Dizer "o documento não traz as linhas" não basta quando elas estão lá: o servidor mostra onde.
    easy_way_out = await rejected(governance, "fechar_extracao", {
        "doc_id": DOC_ANA, "apoio_ausente": "As linhas de aquisição não foram disponibilizadas para gravação."})
    assert "O documento traz as linhas de apoio" in easy_way_out["erro"]
    assert easy_way_out["problemas"] == [
        "capex_imobilizado: há linha que serve na p. 11 ('Aquisição de imobilizado')",
        "capex_intangivel: há linha que serve na p. 11 ('Aquisição de intangível')"]

    # Gravadas as linhas, o fechamento calcula.
    await save(governance, cell("capex_imobilizado", "Aquisição de imobilizado", "Consolidado - 2022", chunk=DFC))
    await save(governance, cell("capex_intangivel", "Aquisição de intangível", "Consolidado - 2022", chunk=DFC))
    closed = payload(await call(governance, "fechar_extracao", {"doc_id": DOC_ANA}))
    assert closed["campos"]["capex"] == "não encontrado; calculado: 17.732 R$ mil"


async def test_what_the_document_states_with_a_value_cannot_be_declared_missing(governance, indexer):
    """Na 4ª execução real a dívida líquida da Millet, que está num quadro, saiu como "não
    encontrado" ("usada como apoio"), e a alavancagem da Light também, porque a frase, com o
    erro de digitação da fonte, fala em 2021."""
    sentence = await rejected(governance, "salvar_nao_encontrado", {
        "doc_id": DOC_ANA, "campo": "alavancagem", "paginas_descartadas": [10],
        "motivo": "O texto traz o indicador só para outro período; não há o valor de 2022."})
    assert "traz Alavancagem (dívida líquida / EBITDA) com valor" in sentence["erro"] and "'nota'" in sentence["erro"]
    assert sentence["problemas"] == ["p. 10: frase 'O indicador Dívida Líquida/EBITDA encerrou em 3,32x.' (chunk 3)"]
    # Só mencionar o termo não é evidência: a página pode ser descartada, como antes.
    mention_only = await call(governance, "salvar_nao_encontrado", {
        "doc_id": DOC_ANA, "campo": "margem_ebitda", "paginas_descartadas": [9],
        "motivo": "A p. 9 diz que a margem recuou, sem informar o percentual."})
    assert not mention_only.is_error, payload(mention_only)

    add_performance_details(indexer)
    table = await rejected(governance, "salvar_nao_encontrado", {
        "doc_id": DOC_ANA, "campo": "divida_liquida", "paginas_descartadas": [10, 42],
        "motivo": "A nota de gestão de capital foi usada só como apoio; não há indicador divulgado."})
    # A linha de tabela vem primeiro; a linha da razão ("sobre capital") não conta como dívida líquida.
    assert table["problemas"][0] == f"p. 42: linha de tabela '(=) Dívida líquida (a)' (chunk {CAPITAL})"
    assert not any("sobre capital" in problem for problem in table["problemas"])
    saved = await save(governance, cell("divida_liquida", "(=) Dívida líquida (a)", "Consolidado - 31/12/2022", chunk=CAPITAL))
    assert saved["salvo"]["valor"] == "13.520" and saved["comparavel_gravado_pelo_servidor"]["valor"] == "16.877"
    # Com o trecho extra, a margem passa a ter valor escrito: aí não pode mais ser dada como ausente.
    margin = await rejected(governance, "salvar_nao_encontrado", {
        "doc_id": DOC_ANA, "campo": "margem_ebitda", "paginas_descartadas": [9], "motivo": "Não há margem informada."})
    assert any("A margem EBITDA foi de 10,0% no ano." in problem for problem in margin["problemas"])
    # E uma linha sem rótulo não identifica valor nenhum (o caixa operacional da Millet veio de uma).
    blank = await rejected(governance, "salvar_campo", cell("fluxo_caixa_operacional", " ", "Consolidado - 2022", chunk=DFC))
    assert "informe rotulo_linha" in blank["erro"] or "está vazio" in blank["erro"]
    other_line = await rejected(governance, "salvar_campo", cell(
        "fluxo_caixa_operacional", "Aquisição de imobilizado", "Consolidado - 2022", chunk=DFC))
    assert "tem de mencionar 'caixa e operacionais'" in other_line["erro"]


async def test_support_values_must_come_from_their_own_line(governance):
    """Na Brasal entrou como caixa a linha 'Caixa e bancos', um componente da nota de caixa (1.346,
    contra 96.453 do total), e a dívida líquida calculada saiu errada."""
    component = await rejected(governance, "salvar_campo", cell(
        "caixa_equivalentes", "Empréstimos e financiamentos", "Consolidado - 31/12/2022", chunk=BALANCE, ocorrencia_linha=1))
    assert "não é a deste valor" in component["erro"] and "'caixa e equivalente'" in component["erro"]
    assert "aponte a linha do total" in component["erro"]
    other_result = await rejected(governance, "salvar_campo", cell(
        "resultado_antes_financeiro", "Lucro líquido do exercício", "Consolidado - 2022"))
    assert "'antes e financeir' ou 'lucro operacional'" in other_result["erro"]
    # A linha certa passa, com ou sem acento no rótulo copiado.
    right = await save(governance, cell("caixa_equivalentes", "caixa e equivalentes de caixa", "Consolidado - 31/12/2022",
                                        chunk=BALANCE))
    assert right["salvo"]["valor"] == "96.453"


async def test_statement_field_cannot_be_declared_missing_when_the_statement_is_there(governance):
    """Na 3ª execução real o caixa operacional da Millet saiu como "não encontrado": o agente errou
    o rótulo da linha, desistiu e descartou a página do fluxo de caixa."""
    refused = await rejected(governance, "salvar_nao_encontrado", {
        "doc_id": DOC_ANA, "campo": "fluxo_caixa_operacional", "paginas_descartadas": [11],
        "motivo": "A linha do caixa operacional está vazia na demonstração do fluxo de caixa."})
    assert "o documento tem esse quadro (p. 11)" in refused["erro"] and "ler_chunk" in refused["erro"]
    # Documento sem a demonstração (o do Bruno tem um parágrafo só): aí o campo pode não existir.
    other = {"user_token": "tok-bruno", "user_id": BRUNO}
    for campo in ("receita_liquida", "lucro_liquido", "fluxo_caixa_operacional", "ebitda", "margem_ebitda",
                  "divida_liquida", "alavancagem", "capex"):
        accepted = await call(governance, "salvar_nao_encontrado", {
            "doc_id": DOC_BRUNO, "campo": campo, "paginas_descartadas": [],
            "motivo": "O documento tem um parágrafo só, sem demonstrações financeiras."}, **other)
        assert not accepted.is_error, payload(accepted)
    # Sem demonstrações não há como calcular: aqui a explicação é aceita, e vira alerta.
    needs_reason = await call(governance, "fechar_extracao", {"doc_id": DOC_BRUNO}, **other)
    assert needs_reason.is_error and len(payload(needs_reason)["problemas"]) == 3
    closed = payload(await call(governance, "fechar_extracao", {
        "doc_id": DOC_BRUNO, "apoio_ausente": "O documento não traz balanço, resultado nem fluxo de caixa."}, **other))
    alert = next(a for a in closed["alertas"] if "Sem valores de apoio" in a)
    assert "EBITDA, Dívida líquida, Capex" in alert and "não traz balanço" in alert


async def test_a_percentage_field_does_not_take_an_amount_and_statement_fields_come_from_the_table(governance, indexer):
    """Na Light, a frase "EBITDA CVM foi de R$1.180 milhões" entrou como margem EBITDA (a única
    invenção da segunda execução), e o prejuízo veio do texto, "-R$5.673 milhões", sem o sinal."""
    add_performance_details(indexer)
    amount = await rejected(governance, "salvar_campo", text(
        "margem_ebitda", "O EBITDA de 2022 somou R$ 485.156 mil", "485.156", chunk=EXTRA,
        nota="margem não explicitada; o servidor calculará"))
    assert "é um percentual, e o número citado (485.156) não traz '%'" in amount["erro"]
    assert "o servidor calcula" in amount["erro"]

    snippet = "A Companhia apurou um resultado líquido de -R$5.673 milhões no ano"
    from_text = await rejected(governance, "salvar_campo", text("lucro_liquido", snippet, "5.673", chunk=EXTRA))
    assert "vem da demonstração financeira, que é uma tabela" in from_text["erro"]
    # Com a nota, entra; e o sinal escrito antes da moeda é respeitado.
    noted = await save(governance, text("lucro_liquido", snippet, "5.673", chunk=EXTRA,
                                        nota="O documento não traz a demonstração do resultado em tabela."))
    assert (noted["salvo"]["valor_numerico"], noted["salvo"]["unidade"]) == (-5673.0, "R$ milhões")
    # A variação que a própria empresa escreve é texto mesmo: não é valor de demonstração.
    variation = await save(governance, text("receita_liquida", "Em 2022, a receita líquida cresceu 30% em relação a 2021",
                                            "30%", chunk=PERFORMANCE, medida="variacao_informada"))
    assert variation["salvo"]["unidade"] == "%"


async def test_a_table_row_cannot_be_quoted_as_text_and_capex_is_not_one_acquisition_line(governance):
    """Na 6ª execução real o lucro e o capex da Light entraram com origem='texto' sobre a linha de
    uma TABELA: o número estava no "trecho", mas a coluna era escolha do modelo, e o ano anterior
    não veio. O capex gravado era só a linha de imobilizado (307.759 de 1.470.292)."""
    row = "| Aquisição de imobilizado | - | - | (6.263) | (3.803) |"
    as_text = await rejected(governance, "salvar_campo", text("capex_imobilizado", row, "6.263", chunk=DFC))
    assert "é uma tabela: use origem='tabela'" in as_text["erro"] and "ocorrencia_coluna" in as_text["erro"]
    # O campo capex é o total que a empresa declara: uma linha de aquisição é valor de apoio.
    one_line = await rejected(governance, "salvar_campo", cell(
        "capex", "Aquisição de imobilizado", "Consolidado - 2022", chunk=DFC))
    assert "não é um total de investimentos" in one_line["erro"] and "capex_imobilizado" in one_line["erro"]
    # E a linha de aquisição, que contém um termo de busca do capex, não impede o "não encontrado".
    accepted = await call(governance, "salvar_nao_encontrado", {
        "doc_id": DOC_ANA, "campo": "capex", "paginas_descartadas": [11],
        "motivo": "As linhas de aquisição foram gravadas como apoio; não há um total de capex."})
    assert not accepted.is_error, payload(accepted)


async def test_among_ebitda_sentences_the_one_that_matches_the_statements_wins(governance, indexer):
    """Na 8ª execução real o EBITDA da Light voltou a ser o da distribuidora (com uma nota). O
    consolidado, na página seguinte, é o que bate com a demonstração: 192.840 + 29.014 no exemplo."""
    add_performance_details(indexer)
    doc = indexer.documents[next(k for k in indexer.documents if k[1] == DOC_ANA)]
    consolidated = len(doc["chunks"])
    doc["chunks"].append({**doc["chunks"][EXTRA], "chunk_index": consolidated, "page_start": 10, "page_end": 10,
                          "text": "EBITDA CVM. Em 2022, o EBITDA CVM foi de R$221,9 milhões, acima do de 2021."})
    doc["meta"]["chunk_count"] = len(doc["chunks"])
    await fill(governance, not_found=("capex",))
    await save(governance, text("margem_ebitda", "A margem EBITDA foi de 10,0% no ano", "10,0%", chunk=EXTRA))
    await save(governance, text("ebitda", "O EBITDA da distribuidora foi de R$ 1.817 milhões", "1.817", chunk=EXTRA,
                                nota="EBITDA de uma controlada (distribuidora), não o consolidado."))

    refused = await rejected(governance, "fechar_extracao", {"doc_id": DOC_ANA})
    assert "outra frase de EBITDA cujo valor bate" in refused["erro"]
    assert refused["problemas"] == [
        f"p. 10 (chunk {consolidated}): 'Em 2022, o EBITDA CVM foi de R$221,9 milhões, acima do de 2021.'"]

    await save(governance, text("ebitda", "Em 2022, o EBITDA CVM foi de R$221,9 milhões", "221,9", chunk=consolidated))
    closed = payload(await call(governance, "fechar_extracao", {"doc_id": DOC_ANA}))
    assert closed["campos"]["ebitda"].startswith("informado: 221,9 R$ milhões (p. 10)")
    # O que bate não gera alerta de divergência.
    assert not any("O EBITDA informado" in alert for alert in closed["alertas"])
    # Um EBITDA de texto que não bate e para o qual o documento não tem alternativa (um EBITDA
    # ajustado, por exemplo) entra com o alerta, sem recusa: é o caso do teste das validações.


async def test_informed_ebitda_still_needs_the_values_that_check_it(governance, indexer):
    """Numa execução real o extrator gravou o EBITDA informado da Light e pulou o resultado
    operacional e a depreciação. Sem eles a conferência não roda, e o alerta do sinal omitido no
    texto (R$1.180 milhões escrito, −1.179.644 mil na demonstração) não disparou."""
    add_performance_details(indexer)
    for arguments in [
        cell("receita_liquida", "Receita operacional líquida", "Consolidado - 2022"),
        cell("lucro_liquido", "Lucro líquido do exercício", "Consolidado - 2022"),
        cell("fluxo_caixa_operacional", "Caixa líquido gerado pelas atividades operacionais", "Consolidado - 2022", chunk=DFC),
        cell("capex_imobilizado", "Aquisição de imobilizado", "Consolidado - 2022", chunk=DFC),
        cell("capex_intangivel", "Aquisição de intangível", "Consolidado - 2022", chunk=DFC),
        text("divida_liquida", "A dívida líquida da Companhia em 31 de dezembro de 2022 totalizou R$9.032 milhões", "9.032"),
        text("alavancagem", "O indicador Dívida Líquida/EBITDA encerrou em 3,32x", "3,32x"),
        text("ebitda", "O EBITDA de 2022 somou R$ 485.156 mil", "485.156", chunk=EXTRA),
        text("margem_ebitda", "A margem EBITDA foi de 10,0% no ano", "10,0%", chunk=EXTRA),
    ]:
        await save(governance, arguments)
    await call(governance, "salvar_nao_encontrado", {
        "doc_id": DOC_ANA, "campo": "capex", "paginas_descartadas": [11],
        "motivo": "As linhas de aquisição foram gravadas como apoio; não há um total de capex."})

    refused = await rejected(governance, "fechar_extracao", {"doc_id": DOC_ANA})
    assert "nem confere o EBITDA que ela informa" in refused["erro"]
    assert refused["problemas"] == ["ebitda: faltam resultado_antes_financeiro e depreciacao_amortizacao"]
    # E a saída "o documento não traz" não vale: as duas linhas estão nas demonstrações.
    easy = await rejected(governance, "fechar_extracao", {
        "doc_id": DOC_ANA, "apoio_ausente": "A empresa já informa o EBITDA; os operandos não são necessários."})
    assert easy["problemas"][0].startswith("resultado_antes_financeiro: há linha que serve na p. 8")

    await save(governance, cell("resultado_antes_financeiro", "Lucro operacional antes das receitas e despesas financeiras",
                                "Consolidado - 2022"))
    await save(governance, cell("depreciacao_amortizacao", "Depreciações e amortizações", "Consolidado - 2022", chunk=DFC))
    closed = payload(await call(governance, "fechar_extracao", {"doc_id": DOC_ANA}))
    assert any("O EBITDA informado (485.156 R$ mil) não bate" in alert for alert in closed["alertas"])


async def test_a_loan_called_capex_is_not_the_capex(governance, indexer):
    """Na 7ª execução real a extração da Light não fechou: a nota de empréstimos tem a linha "BNDES -
    Capex", a regra do "traz com valor" a tomou por capex informado, e o campo ficou sem saída."""
    loans = add_loans_note(indexer)
    as_capex = await rejected(governance, "salvar_campo", cell(
        "capex", "BNDES - Capex", "Saldo a amortizar em 31.12.2022", chunk=loans))
    assert "não é um total de investimentos" in as_capex["erro"] and "capex_imobilizado" in as_capex["erro"]
    # O termo aparece na p. 36, e a página tem de ser descartada, como qualquer menção; mas a linha
    # do financiamento não impede dizer que a empresa não informa o capex.
    needs_page = await rejected(governance, "salvar_nao_encontrado", {
        "doc_id": DOC_ANA, "campo": "capex", "paginas_descartadas": [11],
        "motivo": "A empresa não informa um total de capex; as linhas de aquisição são valores de apoio."})
    assert "páginas que você não descartou: 36" in needs_page["erro"]
    accepted = await call(governance, "salvar_nao_encontrado", {
        "doc_id": DOC_ANA, "campo": "capex", "paginas_descartadas": [11, 36],
        "motivo": "A p. 36 traz 'Capex' só como nome de um financiamento do BNDES; não há total de capex."})
    assert not accepted.is_error, payload(accepted)


async def test_net_income_is_the_total_not_the_part_of_the_controlling_shareholders(governance, indexer):
    """A demonstração consolidada da Millet tem duas linhas "Lucro líquido do exercício": o total
    (18.891) e, depois da participação dos minoritários, a parte dos controladores (14.743). Numa
    repetição da execução final entrou a segunda."""
    doc = indexer.documents[next(k for k in indexer.documents if k[1] == DOC_ANA)]
    statement = len(doc["chunks"])
    doc["chunks"].append({**doc["chunks"][DRE], "chunk_index": statement, "page_start": 7, "page_end": 7, "text": (
        "|  | Controladora - 2022 | Controladora - 2021 | Consolidado - 2022 | Consolidado - 2021 |\n|---|---|---|---|---|\n"
        "| Resultado antes do imposto de renda | 14.743 | 5.371 | 22.961 | 3.736 |\n"
        "| Lucro líquido do exercício | 14.743 | 5.371 | 18.891 | 6.470 |\n"
        "| Participação acionistas de minoritários | - | - | (4.148) | (1.099) |\n"
        "| Lucro líquido do exercício | 14.743 | 5.371 | 14.743 | 5.371 |")})
    doc["meta"]["chunk_count"] = len(doc["chunks"])

    second = await rejected(governance, "salvar_campo", cell(
        "lucro_liquido", "Lucro líquido do exercício", "Consolidado - 2022", chunk=statement, ocorrencia_linha=2))
    assert "é a 2ª com o rótulo" in second["erro"] and "parte do lucro atribuída aos controladores" in second["erro"]
    assert "ocorrencia_linha=1" in second["erro"]
    total = await save(governance, cell(
        "lucro_liquido", "Lucro líquido do exercício", "Consolidado - 2022", chunk=statement, ocorrencia_linha=1))
    assert total["salvo"]["valor"] == "18.891" and total["comparavel_gravado_pelo_servidor"]["valor"] == "6.470"
    # Rótulo repetido sem linha de minoritários no meio (o balanço, com empréstimos de curto e de
    # longo prazo) continua valendo pela ocorrência, como antes.
    long_term = await save(governance, cell("emprestimos_nao_circulante", "Empréstimos e financiamentos",
                                            "Consolidado - 31/12/2022", chunk=BALANCE, ocorrencia_linha=2))
    assert long_term["salvo"]["valor"] == "4.100"


async def test_statement_value_comes_from_its_own_statement(governance, indexer):
    """O lucro aparece também na demonstração das mutações do patrimônio, sem colunas por ano: de
    lá o valor entra sem o ano anterior."""
    doc = indexer.documents[(next(k for k in indexer.documents if k[1] == DOC_ANA))]
    doc["chunks"].append({**doc["chunks"][DRE], "chunk_index": len(doc["chunks"]), "page_start": 10, "page_end": 10,
                          "section_path": ["Demonstração das mutações do patrimônio líquido"]})
    doc["meta"]["chunk_count"] = len(doc["chunks"])
    other = await rejected(governance, "salvar_campo", cell(
        "lucro_liquido", "Lucro líquido do exercício", "Consolidado - 2022", chunk=len(doc["chunks"]) - 1))
    assert "vem da demonstração de resultado (p. 8)" in other["erro"]
    assert "Demonstração das mutações do patrimônio líquido" in other["erro"]
    right = await save(governance, cell("lucro_liquido", "Lucro líquido do exercício", "Consolidado - 2022"))
    assert right["comparavel_gravado_pelo_servidor"]["valor"] == "275.560"


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
    error = await rejected(governance, "salvar_campo", arguments)
    assert any(item.startswith(f"{problem}:") for item in error["problemas"])


async def test_compact_view_has_only_what_the_documents_state(governance):
    """O formato enxuto: os campos do item 2 do teste, só com o que está escrito nos documentos,
    cada valor com a origem (página e trecho), ou "não encontrado". Nada calculado: nem valor por
    fórmula, nem variação calculada; nem alertas ou notas do agente."""
    await fill(governance)
    await save(governance, text("receita_liquida", "Em 2022, a receita líquida cresceu 30% em relação a 2021", "30%",
                                chunk=PERFORMANCE, medida="variacao_informada"))
    assert not (await call(governance, "fechar_extracao", {"doc_id": DOC_ANA})).is_error
    body = payload(await call(governance, "ler_extracao", {"doc_id": DOC_ANA, "detalhe": "enxuto"}))
    view = body["documentos"][0]["extracao"]

    assert list(view) == ["empresa", "documento", "periodo_referencia", "periodo_comparavel", "receita_liquida",
                          "ebitda", "margem_ebitda", "lucro_liquido", "divida_liquida", "alavancagem",
                          "fluxo_caixa_operacional", "capex"]
    assert (view["empresa"], view["documento"], view["periodo_referencia"]) == ("Empresa Exemplo S.A.", "Exemplo.pdf", "2022")

    revenue = view["receita_liquida"]
    assert (revenue["status"], revenue["valor"], revenue["valor_como_escrito"], revenue["unidade"]) == (
        "encontrado", 1325059, "1.325.059", "R$ mil")
    # Valor de tabela: o trecho é a linha da tabela; a coluna diz o ano e o escopo.
    assert revenue["origem"] == {"pagina": 8, "tipo": "tabela", "linha": "Receita operacional líquida",
                                 "coluna": "Consolidado - 2022",
                                 "trecho": "| Receita operacional líquida | 21 | - | - | 1.325.059 | 1.061.410 |"}
    # O ano anterior como está no documento, e a variação só como a empresa a escreve.
    assert revenue["periodo_comparavel"]["valor"] == 1061410
    assert revenue["periodo_comparavel"]["origem"]["coluna"] == "Consolidado - 2021 - (não auditado)"
    assert revenue["variacao_informada"] == {"valor": "30%", "origem": {
        "pagina": 9, "tipo": "texto", "trecho": "Em 2022, a receita líquida cresceu 30% em relação a 2021"}}

    # Não encontrado: explícito, sem valor, com o motivo e a busca. Nenhum valor calculado.
    ebitda = view["ebitda"]
    assert ebitda == {"status": "não encontrado", "valor": None, "motivo": ebitda["motivo"], "busca": ebitda["busca"]}
    assert ebitda["busca"]["paginas_descartadas"] == [9, 10] and ebitda["motivo"]
    assert view["capex"]["status"] == "não encontrado" and view["margem_ebitda"]["valor"] is None

    # Valor de texto: o trecho é a frase citada. Sem o ano anterior no documento, fica null.
    debt = view["divida_liquida"]
    assert (debt["valor"], debt["unidade"], debt["periodo_comparavel"], debt["variacao_informada"]) == (
        9032, "R$ milhões", None, None)
    assert debt["origem"] == {"pagina": 10, "tipo": "texto", "trecho":
                              "A dívida líquida da Companhia em 31 de dezembro de 2022 totalizou R$9.032 milhões"}
    assert (view["alavancagem"]["valor"], view["alavancagem"]["unidade"]) == (3.32, "x")

    # Nada que não esteja nos documentos: nem cálculo, nem alerta, nem nota, nem fórmula.
    text_of_view = str(view)
    for absent in ("calculado", "formula", "alertas", "nota", "valores_de_apoio", "221.854", "24,84"):
        assert absent not in text_of_view, absent


async def test_current_and_long_term_loans_come_from_where_they_are_separated(governance, indexer):
    """Na Brasal, o total dos empréstimos de uma nota (3.417 = 1.849 + 1.568) entrou como circulante
    e o não circulante do balanço foi somado de novo: a dívida líquida calculada saiu errada."""
    add_performance_details(indexer)
    total = await rejected(governance, "salvar_campo", cell(
        "emprestimos_circulante", "Total dos empréstimos com terceiros", "Consolidado - 31/12/2022", chunk=CAPITAL))
    assert "não separa circulante de não circulante" in total["erro"] and "balanço patrimonial" in total["erro"]
    # No balanço, passa.
    right = await save(governance, cell("emprestimos_circulante", "Empréstimos e financiamentos",
                                        "Consolidado - 31/12/2022", chunk=BALANCE, ocorrencia_linha=1))
    assert right["salvo"]["valor"] == "1.849"
