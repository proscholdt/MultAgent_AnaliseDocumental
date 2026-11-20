"""Respostas, resumo, pendências e o relatório final: os números vêm de marcadores que o
código preenche, e o relatório só é montado quando nada falta."""
from app.regras.campos import QUESTIONS
from tests.conftest import BRUNO, DOC_ANA, DOC_ANA_2, DOC_BRUNO, add_second_document, headers, payload
from tests.test_analise import GROWTH, LIQUIDITY, MESSAGE, OUTLOOK, PERFORMANCE, RISK, item
from tests.test_extracao import cell, fill, save


async def call(governance, agent, name, arguments=None, **who):
    return await governance.call_tool(headers(agent, **who), name, arguments or {})


async def extract(governance) -> None:
    await fill(governance)
    assert not (await call(governance, "extrator", "fechar_extracao", {"doc_id": DOC_ANA})).is_error


async def review(governance) -> None:
    await call(governance, "validador", "registrar_alerta", {
        "doc_id": DOC_ANA, "chunk_index": 3, "descricao": "O texto fala em aumento de 23% da dívida; confira com os saldos.",
        "citacao": "totalizou R$9.032 milhões, aumento de 23% em comparação a 2021, de R$7.353 milhões"})
    assert not (await call(governance, "validador", "concluir_revisao", {
        "doc_id": DOC_ANA, "observacao": "Endividamento e desempenho revisados; uma inconsistência registrada."})).is_error


async def analyse(governance) -> None:
    for arguments in [
        item("motivo_resultado", "A receita cresceu 30% no ano.", PERFORMANCE, GROWTH),
        item("risco", "A empresa cita o risco de liquidez, acompanhado pela tesouraria.", RISK, LIQUIDITY),
        item("guidance", "A empresa espera manter o crescimento das vendas e reduzir a dívida.", MESSAGE, OUTLOOK),
        item("tom", "A administração fala em recuperação e em metas para o ano seguinte.", MESSAGE, OUTLOOK, "positivo"),
    ]:
        assert not (await call(governance, "analista", "salvar_item_analise", arguments)).is_error
    assert not (await call(governance, "analista", "registrar_ausencia_analise", {
        "doc_id": DOC_ANA, "categoria": "evento_relevante", "paginas_descartadas": [],
        "motivo": "O documento não relata aquisições, reestruturações nem contingências."})).is_error


async def answer_all(governance) -> None:
    for question_id in ("p1", "p2", "p3", "p4", "p5"):
        saved = await call(governance, "pesquisador", "salvar_resposta", answer(
            question_id, f"Resposta de teste sobre a empresa, com a receita de {{{{{DOC_ANA}.receita_liquida}}}}."))
        assert not saved.is_error, payload(saved)


async def summarize(governance) -> None:
    assert not (await call(governance, "redator", "salvar_resumo", {
        "doc_id": DOC_ANA, "texto": "Receita de {{receita_liquida}} e lucro de {{lucro_liquido}} no exercício."})).is_error


def answer(question_id, text, question=None):
    """Para p1 a p5, o enunciado oficial (o servidor exige); para 'livre', uma pergunta qualquer."""
    return {"pergunta_id": question_id, "pergunta": question or QUESTIONS.get(question_id, "Pergunta de teste?"),
            "resposta": text}


# --- comparar empresas é do código ------------------------------------------------------------


async def extract_second(governance) -> None:
    """Extração do segundo documento: só receita e lucro; o resto a empresa não informa."""
    for campo, row in (("receita_liquida", "Receita líquida"), ("lucro_liquido", "Resultado do exercício")):
        for year in ("2022", "2021"):
            await save(governance, {"doc_id": DOC_ANA_2, "campo": campo, "periodo": year, "origem": "tabela",
                                    "chunk_index": 0, "rotulo_linha": row, "rotulo_coluna": f"Consolidado - {year}"})
    for campo in ("ebitda", "margem_ebitda", "divida_liquida", "alavancagem", "fluxo_caixa_operacional", "capex"):
        result = await call(governance, "extrator", "salvar_nao_encontrado", {
            "doc_id": DOC_ANA_2, "campo": campo, "paginas_descartadas": [],
            "motivo": "O documento traz só a demonstração do resultado e o contexto operacional."})
        assert not result.is_error, payload(result)
    closed = await call(governance, "extrator", "fechar_extracao", {
        "doc_id": DOC_ANA_2, "apoio_ausente": "O documento traz só a demonstração do resultado."})
    assert not closed.is_error, payload(closed)


async def test_companies_are_ranked_and_filtered_by_the_stored_values(governance, indexer):
    add_second_document(indexer)
    await extract(governance)
    await extract_second(governance)

    revenue = payload(await call(governance, "pesquisador", "comparar_campos", {
        "campo": "receita_liquida", "criterio": "maior", "limite": None}))
    assert [(row["entidade"], row["valor"], row["base"]) for row in revenue["resultado"]] == [
        ("Empresa Exemplo S.A.", "R$ 1.325.059 mil", "informado pelo documento"),
        ("Segunda S.A.", "R$ 462.127 mil", "informado pelo documento")]
    assert revenue["resultado"][0]["marcador"] == f"{{{{{DOC_ANA}.receita_liquida}}}}"

    # Margem: só uma empresa tem base, e é calculada. A outra aparece em "sem valor", com o motivo.
    margin = payload(await call(governance, "pesquisador", "comparar_campos", {
        "campo": "margem_ebitda", "criterio": "maior", "limite": None}))
    assert [(row["entidade"], row["valor"], row["base"]) for row in margin["resultado"]] == [
        ("Empresa Exemplo S.A.", "16,74%", "calculado pelo servidor")]
    assert margin["resultado"][0]["marcador"].endswith(".margem_ebitda.calculado}}")
    assert margin["sem_valor"][0]["entidade"] == "Segunda S.A."

    above = payload(await call(governance, "pesquisador", "comparar_campos", {
        "campo": "alavancagem", "criterio": "acima_de", "limite": 3}))
    assert [(row["entidade"], row["valor"]) for row in above["resultado"]] == [("Empresa Exemplo S.A.", "3,32x")]
    below = payload(await call(governance, "pesquisador", "comparar_campos", {
        "campo": "alavancagem", "criterio": "abaixo_de", "limite": 3}))
    assert below["resultado"] == []
    no_limit = await call(governance, "pesquisador", "comparar_campos", {
        "campo": "alavancagem", "criterio": "acima_de", "limite": None})
    assert no_limit.is_error


# --- respostas: marcadores e citações ----------------------------------------------------------


async def test_answer_values_come_from_markers_and_quotes_are_checked(governance):
    await extract(governance)
    await analyse(governance)
    saved = payload(await call(governance, "pesquisador", "salvar_resposta", answer(
        "p3", f"A alavancagem informada é de {{{{{DOC_ANA}.alavancagem}}}}, acima de 3x. A dívida líquida é de "
              f"{{{{{DOC_ANA}.divida_liquida}}}}, e a receita variou {{{{{DOC_ANA}.receita_liquida.variacao}}}} "
              f"[[{DOC_ANA}:3]]. O EBITDA não é informado: {{{{{DOC_ANA}.ebitda}}}}; pela DRE daria "
              f"{{{{{DOC_ANA}.ebitda.calculado}}}}.")))
    assert saved["salva"]["pergunta"] == "Alguma das empresas está com alavancagem acima de 3x? Qual?"
    assert saved["resposta"] == (
        "A alavancagem informada é de 3,32x (Exemplo.pdf, p. 10), acima de 3x. A dívida líquida é de "
        "R$ 9.032 milhões (Exemplo.pdf, p. 10), e a receita variou +24,84% (Exemplo.pdf, p. 10). O EBITDA não é "
        "informado: não informado no documento; pela DRE daria R$ 221.854 mil (calculado: resultado antes do "
        "resultado financeiro + depreciação e amortização; valores de origem em Exemplo.pdf, p. 8 e 11).")
    # As fontes não se repetem, e o valor calculado cita as páginas dos valores de que ele sai.
    assert saved["fontes"] == ["Exemplo.pdf, p. 10", "Exemplo.pdf, p. 8", "Exemplo.pdf, p. 11"]


async def test_loose_numbers_and_missing_sources_are_refused(governance):
    await extract(governance)
    await analyse(governance)
    for text, reason in [
        ("A receita foi de R$ 1.325.059 mil e a margem ficou em 16,7%, segundo a empresa.", "1.325.059, 16,7"),
        ("A empresa não comenta o motivo da variação da receita em nenhum trecho.", "não cita nenhuma fonte"),
        (f"A receita cresceu, segundo {{{{{DOC_ANA}.receita}}}}.", "campo desconhecido"),
        (f"A dívida líquida comparável foi {{{{{DOC_ANA}.divida_liquida.comparavel}}}}.", "não tem 'comparavel'"),
        ("O risco está descrito em [[aaaa000000000001:99]].", "não existe esse chunk"),
        (f"Segundo o documento do Bruno [[{DOC_BRUNO}:0]], não há guidance.", "não existe esse chunk"),
    ]:
        refused = await call(governance, "pesquisador", "salvar_resposta", answer("livre", text))
        assert refused.is_error and reason in payload(refused)["erro"], text
    # Número escrito num trecho citado pode aparecer no texto: a administração fala em 30%.
    quoted = payload(await call(governance, "pesquisador", "salvar_resposta", answer(
        "p2", f"A administração diz que a receita cresceu 30% no ano [[{DOC_ANA}:{PERFORMANCE}]].")))
    assert quoted["resposta"].endswith("cresceu 30% no ano (Exemplo.pdf, p. 9).")


async def test_free_questions_get_their_own_id_and_a_new_answer_replaces_the_old(governance):
    await extract(governance)
    text = f"O principal risco citado é o de liquidez [[{DOC_ANA}:{RISK}]]."
    first = payload(await call(governance, "pesquisador", "salvar_resposta", answer("livre", text, "Qual o maior risco?")))
    assert first["salva"]["pergunta_id"].startswith("livre:")
    await call(governance, "pesquisador", "salvar_resposta", answer("livre", text + " É monitorado pela tesouraria.",
                                                                    "qual o maior risco?"))
    answers = payload(await call(governance, "pesquisador", "ler_respostas"))["respostas"]
    assert len(answers) == 1 and answers[0]["resposta"].endswith("monitorado pela tesouraria.")
    assert answers[0]["fontes"] == ["Exemplo.pdf, p. 35"]


# --- resumo executivo --------------------------------------------------------------------------


async def test_summary_has_at_most_five_lines_and_no_typed_numbers(governance):
    early = await call(governance, "redator", "salvar_resumo", {
        "doc_id": DOC_ANA, "texto": "A receita cresceu e o lucro recuou no exercício, com dívida maior."})
    # O resumo usa a extração, a análise e os alertas: sem eles, é recusado dizendo o que falta.
    assert early.is_error and payload(early)["problemas"] == [
        "extração dos campos de Empresa Exemplo S.A.",
        "análise qualitativa de Empresa Exemplo S.A. (motivo_resultado, risco, guidance, evento_relevante, tom)"]
    await extract(governance)
    half = await call(governance, "redator", "salvar_resumo", {
        "doc_id": DOC_ANA, "texto": "A receita cresceu e o lucro recuou no exercício, com dívida maior."})
    assert half.is_error and payload(half)["problemas"][0] == "revisão das fontes de Empresa Exemplo S.A."
    await review(governance)
    await analyse(governance)
    foreign = await call(governance, "redator", "salvar_resumo", {
        "doc_id": DOC_ANA, "texto": "O principal risco da empresa é o crédito concedido a العملاء no exercício."})
    assert foreign.is_error and "outro alfabeto" in payload(foreign)["erro"]
    long = await call(governance, "redator", "salvar_resumo", {
        "doc_id": DOC_ANA, "texto": "\n".join(f"Linha de número {word} do resumo executivo." for word in "abcdef")})
    assert long.is_error and "6 linhas" in payload(long)["erro"]
    typed = await call(governance, "redator", "salvar_resumo", {
        "doc_id": DOC_ANA, "texto": "A receita líquida foi de R$ 1.325.059 mil, alta de 24,84% no ano."})
    assert typed.is_error and "1.325.059, 24,84" in payload(typed)["erro"]

    saved = payload(await call(governance, "redator", "salvar_resumo", {"doc_id": DOC_ANA, "texto": (
        "Receita líquida de {{receita_liquida}}, variação de {{receita_liquida.variacao}} sobre 2021.\n"
        "Lucro líquido de {{lucro_liquido}}; EBITDA {{ebitda}}, com {{ebitda.calculado}}.\n"
        "Alavancagem informada de {{alavancagem}}, um dos 3 pontos de atenção.")}))
    assert saved["salvo"] == {"entidade": "Empresa Exemplo S.A.", "linhas": 3}
    assert saved["resumo"].splitlines()[0] == (
        "Receita líquida de R$ 1.325.059 mil (Exemplo.pdf, p. 8), variação de +24,84% sobre 2021.")
    # No resumo o calculado sai curto, com as páginas; a fórmula fica na tabela do relatório.
    assert "EBITDA não informado no documento, com R$ 221.854 mil (calculado; Exemplo.pdf, p. 8 e 11)." in saved["resumo"]


# --- pendências e relatório -------------------------------------------------------------------


async def complete(governance) -> None:
    """Tudo o que o relatório exige, na ordem em que os agentes fariam."""
    await extract(governance)
    await review(governance)
    await analyse(governance)
    await answer_all(governance)
    await summarize(governance)


async def test_pending_list_follows_the_work_and_report_is_refused_while_something_is_missing(governance):
    first = payload(await call(governance, "orquestrador", "pendencias"))
    assert first["completo"] is False
    assert [(p["o_que_falta"].split(" (")[0], p["agente"], p["liberada"]) for p in first["pendencias"][:3]] == [
        ("extração dos campos", "extrator", True),
        ("revisão das fontes em busca de inconsistências", "validador", False),
        ("análise qualitativa", "analista", True)]
    assert first["pendencias"][1]["aguarda"] == "extração dos campos de Empresa Exemplo S.A."
    assert first["pendencias"][2]["categorias"] == ["motivo_resultado", "risco", "guidance", "evento_relevante", "tom"]
    questions = [p for p in first["pendencias"] if p["agente"] == "pesquisador"]
    assert [p["pergunta_id"] for p in questions] == ["p1", "p2", "p3", "p4", "p5"]
    # As perguntas comparam as empresas: esperam a extração e a análise de todos os documentos.
    assert not any(p["liberada"] for p in questions)
    assert questions[0]["aguarda"].startswith("extração dos campos de Empresa Exemplo S.A.; análise qualitativa")
    assert first["pendencias"][-1]["agente"] == "redator" and first["pendencias"][-1]["liberada"] is False

    refused = await call(governance, "redator", "montar_relatorio")
    assert refused.is_error and len(payload(refused)["problemas"]) == len(first["pendencias"])

    await extract(governance)
    second = payload(await call(governance, "orquestrador", "pendencias"))["pendencias"]
    assert [(p["agente"], p["liberada"]) for p in second][:2] == [("validador", True), ("analista", True)]
    assert second[-1]["agente"] == "redator" and "resumo" in second[-1]["o_que_falta"]
    assert second[-1]["aguarda"].startswith("revisão das fontes de Empresa Exemplo S.A.")

    # E a tool recusa o que chega fora de ordem, mesmo que o orquestrador mande fazer.
    early = await call(governance, "pesquisador", "salvar_resposta", answer(
        "p1", f"A maior margem é a da empresa, com {{{{{DOC_ANA}.margem_ebitda.calculado}}}}."))
    assert early.is_error and "só podem ser respondidas com a extração e a análise" in payload(early)["erro"]
    assert payload(early)["problemas"][0].startswith("análise qualitativa de Empresa Exemplo S.A.")
    # Pergunta livre do usuário não espera: responde com o que há na base.
    free = await call(governance, "pesquisador", "salvar_resposta", answer(
        "livre", f"A receita foi de {{{{{DOC_ANA}.receita_liquida}}}}.", "Qual foi a receita?"))
    assert not free.is_error, payload(free)

    await analyse(governance)
    third = payload(await call(governance, "orquestrador", "pendencias"))["pendencias"]
    assert all(p["liberada"] for p in third if p["agente"] == "pesquisador")


async def test_report_is_built_by_code_from_the_store(governance):
    await complete(governance)
    pending = payload(await call(governance, "orquestrador", "pendencias"))
    assert [p["o_que_falta"] for p in pending["pendencias"]] == ["montagem do relatório (montar_relatorio)"]

    built = payload(await call(governance, "redator", "montar_relatorio"))
    assert built["montado"] and built["entidades"] == ["Empresa Exemplo S.A."]
    assert payload(await call(governance, "orquestrador", "pendencias")) == {"completo": True, "pendencias": []}

    report = payload(await call(governance, "orquestrador", "ler_relatorio"))["relatorio"]
    for expected in [
        "## 1. Resumo executivo", "### Empresa Exemplo S.A. (2022)",
        "Receita de R$ 1.325.059 mil (Exemplo.pdf, p. 8) e lucro de R$ 174.602 mil (Exemplo.pdf, p. 8) no exercício.",
        "## 2. Indicadores comparados", "| Indicador | Empresa Exemplo S.A. |",
        "| Receita líquida | R$ 1.325.059 mil (p. 8)<br>var.: +24,84% |",
        "| EBITDA | não informado<br>calculado: R$ 221.854 mil |",
        "| Alavancagem (dívida líquida / EBITDA) | 3,32x (p. 10)<br>calculado: 40,71x |",
        "- resultado antes do resultado financeiro + depreciação e amortização",
        "## 3. Análise qualitativa", "**Riscos e pontos de atenção**",
        f'> "{LIQUIDITY}" (p. 35, nota explicativa)',
        "- **Tom: positivo.** A administração fala em recuperação",
        "- Não encontrado no documento. O documento não relata aquisições",
        "## 4. Perguntas e respostas", "### 1. Qual empresa teve a maior margem EBITDA no período?",
        "Fontes: Exemplo.pdf, p. 8.",
        "## 5. Alertas e informações não encontradas",
        ("- **EBITDA: não encontrado.** O documento não traz este indicador como um valor único. (Procurado: "
         "ebitda, lajida. Os termos aparecem nas p. 9, 10, descartadas.)"),
        "- [atenção] O texto fala em aumento de 23% da dívida; confira com os saldos. (p. 10)",
        '> "totalizou R$9.032 milhões, aumento de 23% em comparação a 2021, de R$7.353 milhões"',
        "- [informação] Os indicadores estão em unidades diferentes (R$ mil, R$ milhões).",
    ]:
        assert expected in report, expected
    assert "revisao_das_fontes_concluida" not in report and "Fontes revisadas" not in report


async def test_new_data_after_the_report_makes_it_pending_again(governance):
    await complete(governance)
    await call(governance, "redator", "montar_relatorio")
    await save(governance, cell("lucro_liquido", "Lucro líquido do exercício", "Controladora - 2022"))
    await call(governance, "extrator", "fechar_extracao", {"doc_id": DOC_ANA})
    # A extração mudou: o que foi escrito com os números antigos volta para a lista, antes do relatório.
    pending = payload(await call(governance, "orquestrador", "pendencias"))["pendencias"]
    assert [p["o_que_falta"].split(":")[0] for p in pending] == ["refazer a resposta"] * 5 + ["refazer o resumo executivo"]
    assert all(p["liberada"] for p in pending)
    stale = await call(governance, "redator", "montar_relatorio")
    assert stale.is_error and len(payload(stale)["problemas"]) == 6

    await answer_all(governance)
    await summarize(governance)
    pending = payload(await call(governance, "orquestrador", "pendencias"))["pendencias"]
    assert [p["o_que_falta"].split(":")[0] for p in pending] == ["remontar o relatório"]


async def test_another_user_has_their_own_pending_list_and_no_report(governance):
    await complete(governance)
    await call(governance, "redator", "montar_relatorio")
    other = {"user_token": "tok-bruno", "user_id": BRUNO}
    pending = payload(await call(governance, "orquestrador", "pendencias", **other))["pendencias"]
    assert {p.get("doc_id") for p in pending if p.get("doc_id")} == {DOC_BRUNO}
    assert payload(await call(governance, "orquestrador", "ler_relatorio", **other)) == {"relatorio": None}
    listed = payload(await call(governance, "pesquisador", "ler_respostas", **other))
    assert listed["respostas"] == [] and not any(q["respondida"] for q in listed["perguntas_do_relatorio"])


async def test_report_question_must_be_answered_as_it_is_written(governance):
    """Na 5ª execução real o orquestrador delegou "responda a p3" sem o enunciado, e o pesquisador
    respondeu qual empresa teve o maior lucro. Entrou como resposta da p3."""
    await extract(governance)
    await analyse(governance)
    text = f"A empresa teve lucro de {{{{{DOC_ANA}.lucro_liquido}}}}, o maior entre as analisadas."
    for wrong in ("p3", "Pergunta p3", "Qual empresa teve o maior lucro líquido no período?"):
        refused = await call(governance, "pesquisador", "salvar_resposta", answer("p3", text, wrong))
        assert refused.is_error, wrong
        assert "«Alguma das empresas está com alavancagem acima de 3x? Qual?»" in payload(refused)["erro"]
    # O enunciado oficial passa, com pequenas diferenças de escrita.
    right = await call(governance, "pesquisador", "salvar_resposta", answer(
        "p3", f"Sim: a empresa, com {{{{{DOC_ANA}.alavancagem}}}}.", "alguma das empresas está com alavancagem acima de 3x? qual"))
    assert not right.is_error, payload(right)
    # O pesquisador lê o enunciado oficial daqui, sem depender de quem delegou.
    listed = payload(await call(governance, "pesquisador", "ler_respostas"))["perguntas_do_relatorio"]
    assert [(q["pergunta_id"], q["respondida"]) for q in listed] == [
        ("p1", False), ("p2", False), ("p3", True), ("p4", False), ("p5", False)]
    assert listed[0]["pergunta"] == "Qual empresa teve a maior margem EBITDA no período?"


async def test_a_free_question_answered_later_does_not_make_the_report_stale(governance):
    """Numa conversa, cada pergunta livre fazia o relatório aparecer como desatualizado, e o
    orquestrador mandava remontá-lo a cada turno."""
    await complete(governance)
    await call(governance, "redator", "montar_relatorio")
    free = await call(governance, "pesquisador", "salvar_resposta", answer(
        "livre", f"A receita foi de {{{{{DOC_ANA}.receita_liquida}}}}.", "Qual foi a receita?"))
    assert not free.is_error, payload(free)
    assert payload(await call(governance, "orquestrador", "pendencias")) == {"completo": True, "pendencias": []}


# --- relatório de uma empresa ------------------------------------------------------------------


async def test_company_pending_list_has_only_that_company_and_no_comparative_questions(governance, indexer):
    add_second_document(indexer)
    first = payload(await call(governance, "orquestrador", "pendencias", {"doc_id": DOC_ANA}))["pendencias"]
    assert {p.get("doc_id") for p in first} == {DOC_ANA}
    assert not any(p["agente"] == "pesquisador" for p in first)
    assert [p["agente"] for p in first] == ["extrator", "validador", "analista", "redator"]

    unknown = await call(governance, "orquestrador", "pendencias", {"doc_id": "nao-existe"})
    assert unknown.is_error and any(DOC_ANA in option for option in payload(unknown)["opcoes"])

    refused = await call(governance, "redator", "montar_relatorio_documento", {"doc_id": DOC_ANA})
    assert refused.is_error and len(payload(refused)["problemas"]) == 4


async def test_company_report_is_built_by_code_without_the_other_companies(governance, indexer):
    add_second_document(indexer)
    await extract(governance)
    await review(governance)
    await analyse(governance)
    await summarize(governance)
    # As perguntas comparativas não foram respondidas e o segundo documento nem foi extraído:
    # o relatório consolidado não sai, o da empresa sai.
    pending = payload(await call(governance, "orquestrador", "pendencias", {"doc_id": DOC_ANA}))["pendencias"]
    assert [p["o_que_falta"] for p in pending] == ["montagem do relatório do documento (montar_relatorio_documento)"]
    assert (await call(governance, "redator", "montar_relatorio")).is_error

    built = payload(await call(governance, "redator", "montar_relatorio_documento", {"doc_id": DOC_ANA}))
    assert built["montado"] and built["entidade"] == "Empresa Exemplo S.A."
    assert payload(await call(governance, "orquestrador", "pendencias", {"doc_id": DOC_ANA})) == {
        "completo": True, "pendencias": []}

    report = payload(await call(governance, "orquestrador", "ler_relatorio_documento", {"doc_id": DOC_ANA}))["relatorio"]
    for expected in [
        "# Relatório de análise: Empresa Exemplo S.A.", "Período 2022, comparado com 2021.",
        "## 1. Resumo executivo", "Receita de R$ 1.325.059 mil (Exemplo.pdf, p. 8)",
        "## 2. Indicadores", "| Receita líquida | R$ 1.325.059 mil (p. 8)<br>var.: +24,84% |",
        "## 3. Análise qualitativa", f'> "{LIQUIDITY}" (p. 35, nota explicativa)',
        "## 4. Alertas e informações não encontradas", "- [atenção] O texto fala em aumento de 23% da dívida",
    ]:
        assert expected in report, expected
    assert "Perguntas e respostas" not in report and "revisao_das_fontes_concluida" not in report
    assert payload(await call(governance, "orquestrador", "ler_relatorio_documento", {"doc_id": DOC_ANA_2})) == {
        "relatorio": None}

    # Dado novo da empresa depois do relatório: ele volta para a lista.
    await save(governance, cell("lucro_liquido", "Lucro líquido do exercício", "Controladora - 2022"))
    await call(governance, "extrator", "fechar_extracao", {"doc_id": DOC_ANA})
    await summarize(governance)
    pending = payload(await call(governance, "orquestrador", "pendencias", {"doc_id": DOC_ANA}))["pendencias"]
    assert [p["o_que_falta"].split(":")[0] for p in pending] == ["remontar o relatório do documento"]
