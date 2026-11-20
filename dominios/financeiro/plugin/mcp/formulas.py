"""As fórmulas e as validações das demonstrações financeiras.

As fórmulas são fixas e poucas; os operandos são valores que já passaram pelo portão do núcleo
(lidos da célula ou conferidos no trecho). Um EBITDA calculado nunca aparece como informado: o
núcleo põe o calculado num bloco separado.

As validações comparam o informado com o calculado e geram alertas. Nada é corrigido.
"""
from decimal import Decimal

from app.plugins.api import (
    FIELDS,
    MAIN_FIELDS,
    AlertDraft,
    Extraction,
    Pacote,
    PacoteInvalido,
    Periodo,
    back,
    format_br,
    in_reais,
    money,
    operand,
)

MARGIN_TOLERANCE = Decimal("0.2")  # pontos percentuais
LEVERAGE_TOLERANCE = Decimal("0.05")  # múltiplo
EBITDA_TOLERANCE = Decimal("0.05")  # fração do maior dos dois valores

# Os operandos e resultados das fórmulas: sem eles, as fórmulas não fecham.
CAMPOS_DAS_FORMULAS = frozenset({
    "receita_liquida", "ebitda", "margem_ebitda", "lucro_liquido", "divida_liquida", "alavancagem",
    "fluxo_caixa_operacional", "capex", "resultado_antes_financeiro", "depreciacao_amortizacao",
    "emprestimos_circulante", "emprestimos_nao_circulante", "caixa_equivalentes", "capex_imobilizado",
    "capex_intangivel", "capex_imobilizado_e_intangivel",
})

# O EBITDA pela demonstração e as razões: o calculado aparece mesmo com o informado, para conferir.
CONFERIDOS_POR_CALCULO = frozenset({"ebitda", "margem_ebitda", "alavancagem"})


def validar_pacote(pacote: Pacote) -> None:
    faltam = sorted(CAMPOS_DAS_FORMULAS - {campo.nome for campo in pacote.campos})
    if faltam:
        raise PacoteInvalido(f"{pacote.id}: faltam os campos usados pelas fórmulas: {', '.join(faltam)}")


def _sum(extraction: Extraction, campo: str, periodo: str, formula: str, plus: list[str],
         minus: list[str] = ()) -> dict | None:
    """Soma (e subtrai) valores de apoio, todos em módulo: no fluxo de caixa uma aquisição
    vem negativa e a depreciação positiva, e na DRE é o contrário."""
    operands = [extraction.found(name, periodo) for name in (*plus, *minus)]
    if any(item is None for item in operands):
        return None
    amounts = [money(item) for item in operands]
    if any(amount is None for amount in amounts):
        extraction.alerts.append(AlertDraft(
            "calculo_sem_unidade", "atencao", campo=campo,
            mensagem=f"{FIELDS[campo].label} não foi calculado: um dos valores de origem não tem unidade "
                     "monetária declarada."))
        return None
    scopes = {item.escopo for item in operands if item.escopo}
    if len(scopes) > 1:
        extraction.alerts.append(AlertDraft(
            "escopos_diferentes", "atencao", campo=campo,
            mensagem=f"{FIELDS[campo].label} calculado com valores de escopos diferentes "
                     f"({', '.join(sorted(scopes))}).", detalhes={"periodo": periodo}))
    keep_sign = campo == "ebitda"  # o resultado operacional pode ser negativo; o resto é em módulo
    total = Decimal(0)
    for index, (item, amount) in enumerate(zip(operands, amounts, strict=True)):
        signed = amount if (keep_sign and index == 0) else abs(amount)
        total += signed if index < len(plus) else -signed
    unit = operands[0].unidade
    result = back(total, unit)
    return {"valor": format_br(result), "valor_numerico": float(result), "unidade": unit, "periodo": periodo,
            "formula": formula, "operandos": [operand(item) for item in operands]}


def _ratio(extraction: Extraction, campo: str, periodo: str, top: str, bottom: str, formula: str, factor: int,
           unit: str) -> dict | None:
    numerator, denominator = extraction.amount_and_source(top, periodo), extraction.amount_and_source(bottom, periodo)
    if numerator is None or denominator is None or denominator[0] == 0:
        return None
    if campo == "alavancagem" and denominator[0] < 0:
        return None  # EBITDA negativo: o múltiplo não tem sentido
    result = (numerator[0] / denominator[0] * factor).quantize(Decimal("0.01"))
    return {"valor": f"{format_br(result, 2)}{unit}", "valor_numerico": float(result), "unidade": unit,
            "periodo": periodo, "formula": formula,
            "operandos": [{**numerator[1], "base": numerator[2]}, {**denominator[1], "base": denominator[2]}]}


def calcular(extraction: Extraction, campo: str, periodo: str) -> dict | None:
    """O valor calculado de um campo, quando a empresa não o informa e os operandos existem."""
    if campo == "ebitda":
        return _sum(extraction, campo, periodo, "resultado antes do resultado financeiro + depreciação e amortização",
                    ["resultado_antes_financeiro", "depreciacao_amortizacao"])
    if campo == "divida_liquida":
        return _sum(extraction, campo, periodo,
                    "empréstimos e financiamentos (circulante + não circulante) − caixa e equivalentes",
                    ["emprestimos_circulante", "emprestimos_nao_circulante"], ["caixa_equivalentes"])
    if campo == "capex":
        if extraction.found("capex_imobilizado_e_intangivel", periodo) is not None:
            return _sum(extraction, campo, periodo,
                        "aquisição de imobilizado e intangível (linha única do fluxo de caixa)",
                        ["capex_imobilizado_e_intangivel"])
        return _sum(extraction, campo, periodo, "aquisição de imobilizado + aquisição de intangível",
                    ["capex_imobilizado", "capex_intangivel"])
    if campo == "margem_ebitda":
        return _ratio(extraction, campo, periodo, "ebitda", "receita_liquida", "EBITDA / receita líquida × 100",
                      100, "%")
    if campo == "alavancagem":
        # O múltiplo sobre o EBITDA de um trimestre sai umas quatro vezes maior: só com 12 meses.
        period = Periodo.parse(periodo)
        if period is not None and not period.doze_meses:
            return None
        return _ratio(extraction, campo, periodo, "divida_liquida", "ebitda", "dívida líquida / EBITDA", 1, "x")
    return None


def _check_informed_against_calculated(extraction: Extraction) -> None:
    reference = extraction.reference
    ebitda = extraction.found("ebitda", reference)
    informed_amount = money(ebitda)
    from_statement = extraction.calculated("ebitda", reference) if informed_amount is not None else None
    if from_statement:
        computed_amount = in_reais(Decimal(str(from_statement["valor_numerico"])), from_statement["unidade"])
        largest = max(abs(informed_amount), abs(computed_amount))
        if largest and abs(informed_amount - computed_amount) / largest > EBITDA_TOLERANCE:
            extraction.alerts.append(AlertDraft(
                "ebitda_divergente", "atencao", campo="ebitda",
                mensagem=f"O EBITDA informado ({ebitda.texto} {ebitda.unidade}) não bate com o resultado antes do "
                         f"resultado financeiro mais depreciação e amortização ({from_statement['valor']} "
                         f"{from_statement['unidade']}). Pode ser um EBITDA ajustado, o valor de uma controlada "
                         "ou um sinal omitido no texto.",
                detalhes={"informado": f"{ebitda.texto} {ebitda.unidade}", "pagina": ebitda.pagina,
                          "calculado": f"{from_statement['valor']} {from_statement['unidade']}",
                          "trecho": ebitda.trecho}))
    margin = extraction.found("margem_ebitda", reference)
    if margin is not None and extraction.found("ebitda", reference) is not None:
        computed = extraction.calculated("margem_ebitda", reference)
        if computed and abs(Decimal(str(computed["valor_numerico"])) - margin.valor) > MARGIN_TOLERANCE:
            extraction.alerts.append(AlertDraft(
                "margem_divergente", "atencao", campo="margem_ebitda",
                mensagem=f"A margem EBITDA informada ({margin.texto}) não bate com EBITDA dividido pela "
                         f"receita ({computed['valor']}).",
                detalhes={"informada": margin.texto, "calculada": computed["valor"], "pagina": margin.pagina}))
    leverage = extraction.found("alavancagem", reference)
    if (leverage is not None and extraction.found("divida_liquida", reference) is not None
            and extraction.found("ebitda", reference) is not None):
        computed = extraction.calculated("alavancagem", reference)
        if computed and abs(Decimal(str(computed["valor_numerico"])) - leverage.valor) > LEVERAGE_TOLERANCE:
            extraction.alerts.append(AlertDraft(
                "alavancagem_divergente", "atencao", campo="alavancagem",
                mensagem=f"A alavancagem informada ({leverage.texto}) não bate com dívida líquida dividida "
                         f"pelo EBITDA informados ({computed['valor']}). Pode ser um EBITDA ajustado.",
                detalhes={"informada": leverage.texto, "calculada": computed["valor"], "pagina": leverage.pagina}))


def _check_each_value(extraction: Extraction) -> None:
    if extraction.no_comparable:
        extraction.alerts.append(AlertDraft(
            "sem_comparavel", "informacao",
            mensagem=f"Sem o valor de {extraction.comparable}, a variação não foi calculada para: "
                     f"{', '.join(dict.fromkeys(extraction.no_comparable))}.",
            detalhes={"campos": list(dict.fromkeys(extraction.no_comparable))}))
    for value in extraction.values():
        # O valor do ano anterior vem da mesma célula vizinha: repetir os avisos dele é ruído.
        if value.status != "encontrado" or value.periodo != extraction.reference:
            continue
        info = FIELDS[value.campo]
        label = f"{info.label} ({value.periodo})"
        if value.tipo == "valor" and value.unidade is None:
            extraction.alerts.append(AlertDraft(
                "unidade_nao_declarada", "atencao", campo=value.campo,
                mensagem=f"{label}: a fonte não declara a unidade (mil, milhões) deste valor.",
                detalhes={"pagina": value.pagina}))
        elif value.unidade_origem == "inherited":
            extraction.alerts.append(AlertDraft(
                "unidade_herdada", "informacao", campo=value.campo,
                mensagem=f"{label}: a unidade ({value.unidade}) vem de uma convenção declarada em página "
                         "anterior, não da própria página.", detalhes={"pagina": value.pagina}))
        if value.escopo == "controladora" and value.alternativa_consolidada:
            extraction.alerts.append(AlertDraft(
                "escopo_controladora", "atencao", campo=value.campo,
                mensagem=f"{label}: foi usado o valor da controladora, e a mesma tabela tem a coluna do "
                         "consolidado.", detalhes={"coluna": value.coluna, "pagina": value.pagina}))
        if value.origem == "tabela" and info.section and info.section not in value.secao.casefold():
            extraction.alerts.append(AlertDraft(
                "secao_inesperada", "informacao", campo=value.campo,
                mensagem=f"{label}: o valor veio da seção '{value.secao}', e não de uma demonstração de "
                         f"{info.section}.", detalhes={"pagina": value.pagina}))
        if not value.periodo_confirmado:
            extraction.alerts.append(AlertDraft(
                "periodo_nao_confirmado", "atencao", campo=value.campo,
                mensagem=f"{label}: a fonte citada não escreve o período {value.periodo}. Confira se o valor é "
                         "mesmo desse período.", detalhes={"pagina": value.pagina, "trecho": value.trecho}))


def _check_units(extraction: Extraction) -> None:
    units = {value.unidade for campo in MAIN_FIELDS
             if (value := extraction.found(campo, extraction.reference)) is not None and value.tipo == "valor"
             and value.unidade}
    if len(units) > 1:
        extraction.alerts.append(AlertDraft(
            "unidades_diferentes", "informacao",
            mensagem=f"Os indicadores estão em unidades diferentes ({', '.join(sorted(units))}). As contas "
                     "entre eles foram feitas em reais.", detalhes={"unidades": sorted(units)}))


def validar(extraction: Extraction) -> None:
    """Margem, sinal, seção e unidade monetária: as validações das demonstrações."""
    _check_informed_against_calculated(extraction)
    _check_each_value(extraction)
    _check_units(extraction)
