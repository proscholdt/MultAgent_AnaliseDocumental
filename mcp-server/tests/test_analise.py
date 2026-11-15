"""Análise qualitativa e alertas de inconsistência na fonte: a citação é literal e conferida."""
from tests.conftest import DOC_ANA, headers, payload
from tests.test_extracao import fill

DEBT, PERFORMANCE, RISK, MESSAGE = 3, 7, 8, 9  # chunks de texto do documento de exemplo
GROWTH = "Em 2022, a receita líquida cresceu 30% em relação a 2021."
OUTLOOK = "Para 2023, a Companhia espera manter o crescimento das vendas e reduzir o endividamento."
LIQUIDITY = "O Grupo está exposto ao risco de liquidez, monitorado pela tesouraria com base nas projeções de fluxo de caixa"


async def call(governance, agent, name, arguments):
    return await governance.call_tool(headers(agent), name, arguments)


def item(categoria, afirmacao, chunk, citacao, tom=None):
    return {"doc_id": DOC_ANA, "categoria": categoria, "afirmacao": afirmacao, "chunk_index": chunk,
            "citacao": citacao, "tom": tom}


async def test_item_needs_a_literal_quote_and_gets_its_source_kind_from_the_section(governance):
    saved = payload(await call(governance, "analista", "salvar_item_analise", item(
        "risco", "A empresa cita o risco de liquidez, acompanhado pela tesouraria.", RISK, LIQUIDITY)))
    assert saved["salvo"] == {"categoria": "risco", "pagina": 35, "fonte": "nota explicativa", "tom": None}
    message = payload(await call(governance, "analista", "salvar_item_analise", item(
        "motivo_resultado", "As vendas se recuperaram e os custos de energia subiram.", MESSAGE,
        "O ano foi marcado pela recuperação das vendas e pelo aumento dos custos de energia.")))
    assert message["salvo"]["fonte"] == "relatório" and message["salvo"]["pagina"] == 3

    for arguments, reason in [
        (item("risco", "A empresa cita o risco de liquidez.", RISK, "O Grupo tem exposição relevante ao risco de liquidez no período"),
         "não está no chunk"),  # paráfrase
        (item("risco", "A empresa cita o risco de liquidez.", RISK, "risco de liquidez"), "curta demais"),
        (item("risco", "A empresa cita o risco de liquidez.", 99, LIQUIDITY), "de 0 a 9"),
    ]:
        refused = await call(governance, "analista", "salvar_item_analise", arguments)
        assert refused.is_error and reason in payload(refused)["erro"]


async def test_numbers_in_the_statement_must_be_written_in_the_quote(governance):
    ok = await call(governance, "analista", "salvar_item_analise", item(
        "motivo_resultado", "A receita cresceu 30% no ano.", PERFORMANCE, GROWTH))
    assert not ok.is_error
    invented = await call(governance, "analista", "salvar_item_analise", item(
        "motivo_resultado", "A receita cresceu 35%, puxada por R$ 1.200 milhões em vendas novas.", PERFORMANCE, GROWTH))
    assert invented.is_error and "35, 1.200" in payload(invented)["erro"]


async def test_tone_needs_a_classification_and_is_one_per_document(governance):
    missing = await call(governance, "analista", "salvar_item_analise", item(
        "tom", "A administração fala em recuperação e em metas para o ano seguinte.", MESSAGE, OUTLOOK))
    assert missing.is_error and "obrigatório na categoria 'tom'" in payload(missing)["erro"]
    misplaced = await call(governance, "analista", "salvar_item_analise", item(
        "risco", "A empresa cita o risco de liquidez.", RISK, LIQUIDITY, tom="negativo"))
    assert misplaced.is_error
    for tone in ("neutro", "positivo"):
        await call(governance, "analista", "salvar_item_analise", item(
            "tom", "A administração fala em recuperação e em metas para o ano seguinte.", MESSAGE, OUTLOOK, tom=tone))
    analysis = payload(await call(governance, "analista", "ler_analise", {"doc_id": DOC_ANA}))["analise"]
    assert [entry["tom"] for entry in analysis["tom"]] == ["positivo"]  # o segundo substituiu o primeiro


async def test_absence_needs_the_pages_where_the_terms_appear_to_be_discarded(governance):
    refused = await call(governance, "analista", "registrar_ausencia_analise", {
        "doc_id": DOC_ANA, "categoria": "guidance", "motivo": "O documento não traz projeções para o próximo ano.",
        "paginas_descartadas": []})
    assert refused.is_error and "não descartou: 35" in payload(refused)["erro"]
    assert "projeções de fluxo de caixa" in payload(refused)["problemas"][0]
    accepted = payload(await call(governance, "analista", "registrar_ausencia_analise", {
        "doc_id": DOC_ANA, "categoria": "guidance", "paginas_descartadas": [35],
        "motivo": "A p. 35 fala de projeções de caixa da tesouraria, não de guidance."}))
    assert accepted["evidencia"]["paginas_descartadas"] == [35]
    # Categoria sem termos fixos (o motivo do resultado) é aceita com o motivo.
    plain = await call(governance, "analista", "registrar_ausencia_analise", {
        "doc_id": DOC_ANA, "categoria": "motivo_resultado", "paginas_descartadas": [],
        "motivo": "O documento não tem comentário da administração sobre o resultado."})
    assert not plain.is_error

    # Achar um item depois substitui a ausência; registrar ausência depois apaga os itens.
    await call(governance, "analista", "salvar_item_analise", item(
        "guidance", "A empresa espera manter o crescimento das vendas.", MESSAGE, OUTLOOK))
    analysis = payload(await call(governance, "analista", "ler_analise", {"doc_id": DOC_ANA}))["analise"]
    assert [entry.get("status") for entry in analysis["guidance"]] == [None]
    assert analysis["motivo_resultado"][0]["status"] == "nao_encontrado" and analysis["risco"] == []


async def test_source_inconsistency_is_flagged_with_a_quote_and_never_twice(governance):
    quote = "totalizou R$9.032 milhões, aumento de 23% em comparação a 2021, de R$7.353 milhões"
    alert = {"doc_id": DOC_ANA, "chunk_index": DEBT, "citacao": quote,
             "descricao": "De 7.353 para 9.032 o aumento é de cerca de 23%: confira se o percentual bate."}
    assert payload(await call(governance, "validador", "registrar_alerta", alert))["registrado"] is True
    again = payload(await call(governance, "validador", "registrar_alerta", alert))
    assert again["registrado"] is False and "já estava" in again["aviso"]
    invented = await call(governance, "validador", "registrar_alerta", {
        **alert, "descricao": "O texto diz 9.032, mas a tabela da nota traz 8.750 milhões."})
    assert invented.is_error and "8.750" in payload(invented)["erro"]

    alerts = payload(await call(governance, "validador", "ler_alertas", {"doc_id": DOC_ANA}))["alertas"]
    assert [(a["tipo"], a["codigo"], a["detalhes"]["pagina"], a["registrado_por"]) for a in alerts] == [
        ("fonte", "inconsistencia_na_fonte", 10, "validador")]
    assert alerts[0]["detalhes"]["citacao"] == quote


async def test_review_is_concluded_only_after_the_extraction_and_alerts_are_never_erased(governance):
    early = await call(governance, "validador", "concluir_revisao", {
        "doc_id": DOC_ANA, "observacao": "Relatório e notas de dívida lidos."})
    assert early.is_error and "depois da extração" in payload(early)["erro"]

    await fill(governance)
    await call(governance, "extrator", "fechar_extracao", {"doc_id": DOC_ANA})
    done = payload(await call(governance, "validador", "concluir_revisao", {
        "doc_id": DOC_ANA, "observacao": "Relatório e notas de dívida lidos; nada além dos alertas automáticos."}))
    assert done == {"concluida": True, "inconsistencias_registradas": 0}
    before = payload(await call(governance, "validador", "ler_alertas", {"doc_id": DOC_ANA}))["alertas"]
    assert {a["tipo"] for a in before} == {"validacao", "fonte"}

    # Fechar de novo com outro valor não apaga o que já foi sinalizado.
    await call(governance, "extrator", "fechar_extracao", {"doc_id": DOC_ANA})
    after = payload(await call(governance, "validador", "ler_alertas", {"doc_id": DOC_ANA}))["alertas"]
    assert [a["mensagem"] for a in after] == [a["mensagem"] for a in before]


async def test_each_agent_only_reaches_its_own_gate(governance):
    """A política decide: o analista não grava indicador, o extrator não grava análise."""
    for agent, tool_name in (("analista", "salvar_indicador"), ("extrator", "salvar_item_analise"),
                             ("pesquisador", "registrar_alerta"), ("validador", "salvar_resumo"),
                             ("redator", "fechar_extracao"), ("orquestrador", "buscar_trechos")):
        refused = await call(governance, agent, tool_name, {})
        assert refused.is_error and f"não está liberada para o agente '{agent}'" in payload(refused)["erro"]
