"""Do que foi guardado ao JSON da extração: o que o código calcula e o que ele valida.

Aqui não há LLM. As fórmulas são fixas e poucas; os operandos são valores que já passaram pelo
portão (lidos da célula ou conferidos no trecho). O que a empresa informa e o que o código
calcula ficam em blocos separados: o teste pede que informação ausente venha como "não
encontrado", sem estimativa. Um EBITDA calculado nunca aparece como informado.

As validações (item 5 do teste) comparam o informado com o calculado e geram alertas. Nada é
corrigido.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.fields import FIELDS, MAIN_FIELDS
from app.numbers import format_br, in_reais

MARGIN_TOLERANCE = Decimal("0.2")  # pontos percentuais
VARIATION_TOLERANCE = Decimal("0.5")  # pontos percentuais
LEVERAGE_TOLERANCE = Decimal("0.05")  # múltiplo
EBITDA_TOLERANCE = Decimal("0.05")  # fração do maior dos dois valores


@dataclass(frozen=True)
class Value:
    """Um valor guardado, como as fórmulas o enxergam."""

    campo: str
    periodo: str
    medida: str = "valor"
    status: str = "encontrado"
    valor: Decimal | None = None
    texto: str | None = None
    tipo: str | None = "valor"
    unidade: str | None = None
    unidade_origem: str | None = None
    escopo: str | None = None
    origem: str | None = None
    pagina: int | None = None
    pagina_final: int | None = None
    chunk_index: int | None = None
    secao: str = ""
    linha: str | None = None
    coluna: str | None = None
    trecho: str | None = None
    motivo: str | None = None
    evidencia: dict | None = None
    nota: str | None = None
    alternativa_consolidada: bool = False
    periodo_confirmado: bool = True

    def view(self) -> dict[str, Any]:
        """O valor no JSON da extração, com a origem (página e trecho) que o teste exige."""
        if self.status != "encontrado":
            return {"status": "nao_encontrado", "motivo": self.motivo, "evidencia": self.evidencia}
        origin: dict[str, Any] = {"tipo": self.origem, "pagina": self.pagina, "chunk_index": self.chunk_index,
                                  "secao": self.secao, "trecho": self.trecho}
        if self.pagina_final and self.pagina_final != self.pagina:
            origin["pagina_final"] = self.pagina_final
        if self.origem == "tabela":
            origin.update(linha=self.linha, coluna=self.coluna)
        view: dict[str, Any] = {
            "status": "encontrado", "valor": self.texto, "valor_numerico": float(self.valor),
            "unidade": self.unidade, "periodo": self.periodo, "escopo": self.escopo, "origem": origin}
        if self.nota:
            view["nota_do_agente"] = self.nota
        return view


@dataclass(frozen=True)
class AlertDraft:
    codigo: str
    severidade: str  # "atencao" | "informacao"
    mensagem: str
    campo: str | None = None
    detalhes: dict = field(default_factory=dict)

    def view(self) -> dict[str, Any]:
        return {"codigo": self.codigo, "severidade": self.severidade, "campo": self.campo,
                "mensagem": self.mensagem, "detalhes": self.detalhes}


def _money(value: Value | None) -> Decimal | None:
    """O valor em reais, se for dinheiro com unidade conhecida."""
    if value is None or value.status != "encontrado" or value.valor is None:
        return None
    return in_reais(value.valor, value.unidade)


def _back(amount: Decimal, unit: str | None) -> Decimal:
    """De reais para a unidade em que o resultado vai ser mostrado."""
    one = in_reais(Decimal(1), unit)
    return amount / one if one else amount


def _operand(value: Value) -> dict[str, Any]:
    return {"campo": value.campo, "periodo": value.periodo, "valor": value.texto, "unidade": value.unidade,
            "pagina": value.pagina, "escopo": value.escopo}


class Extraction:
    """Monta os indicadores de um documento a partir dos valores guardados."""

    def __init__(self, values: list[Value], reference: str, comparable: str | None) -> None:
        self._values = {(v.campo, v.periodo, v.medida): v for v in values}
        self.reference, self.comparable = reference, comparable
        self.alerts: list[AlertDraft] = []

    def get(self, campo: str, periodo: str | None, medida: str = "valor") -> Value | None:
        return self._values.get((campo, periodo or "", medida))

    def found(self, campo: str, periodo: str | None) -> Value | None:
        value = self.get(campo, periodo)
        return value if value is not None and value.status == "encontrado" else None

    def missing(self) -> list[str]:
        """Campos principais ainda sem valor nem "não encontrado" no período de referência."""
        return [campo for campo in MAIN_FIELDS if self.get(campo, self.reference) is None]

    # -- fórmulas fixas ------------------------------------------------------------------------

    def _sum(self, campo: str, periodo: str, formula: str, plus: list[str], minus: list[str] = ()) -> dict | None:
        """Soma (e subtrai) valores de apoio, todos em módulo: no fluxo de caixa uma aquisição
        vem negativa e a depreciação positiva, e na DRE é o contrário."""
        operands = [self.found(name, periodo) for name in (*plus, *minus)]
        if any(operand is None for operand in operands):
            return None
        amounts = [_money(operand) for operand in operands]
        if any(amount is None for amount in amounts):
            self.alerts.append(AlertDraft(
                "calculo_sem_unidade", "atencao", campo=campo,
                mensagem=f"{FIELDS[campo].label} não foi calculado: um dos valores de origem não tem unidade "
                         "monetária declarada."))
            return None
        scopes = {operand.escopo for operand in operands if operand.escopo}
        if len(scopes) > 1:
            self.alerts.append(AlertDraft(
                "escopos_diferentes", "atencao", campo=campo,
                mensagem=f"{FIELDS[campo].label} calculado com valores de escopos diferentes "
                         f"({', '.join(sorted(scopes))}).", detalhes={"periodo": periodo}))
        keep_sign = campo == "ebitda"  # o resultado operacional pode ser negativo; o resto é em módulo
        total = Decimal(0)
        for index, (operand, amount) in enumerate(zip(operands, amounts, strict=True)):
            signed = amount if (keep_sign and index == 0) else abs(amount)
            total += signed if index < len(plus) else -signed
        unit = operands[0].unidade
        result = _back(total, unit)
        return {"valor": format_br(result), "valor_numerico": float(result), "unidade": unit, "periodo": periodo,
                "formula": formula, "operandos": [_operand(operand) for operand in operands]}

    def calculated(self, campo: str, periodo: str | None) -> dict | None:
        """O valor calculado de um campo, quando a empresa não o informa e os operandos existem."""
        if periodo is None:
            return None
        if campo == "ebitda":
            return self._sum(campo, periodo, "resultado antes do resultado financeiro + depreciação e amortização",
                             ["resultado_antes_financeiro", "depreciacao_amortizacao"])
        if campo == "divida_liquida":
            return self._sum(campo, periodo,
                             "empréstimos e financiamentos (circulante + não circulante) − caixa e equivalentes",
                             ["emprestimos_circulante", "emprestimos_nao_circulante"], ["caixa_equivalentes"])
        if campo == "capex":
            if self.found("capex_imobilizado_e_intangivel", periodo) is not None:
                return self._sum(campo, periodo, "aquisição de imobilizado e intangível (linha única do fluxo de caixa)",
                                 ["capex_imobilizado_e_intangivel"])
            return self._sum(campo, periodo, "aquisição de imobilizado + aquisição de intangível",
                             ["capex_imobilizado", "capex_intangivel"])
        if campo == "margem_ebitda":
            return self._ratio(campo, periodo, "ebitda", "receita_liquida", "EBITDA / receita líquida × 100", 100, "%")
        if campo == "alavancagem":
            return self._ratio(campo, periodo, "divida_liquida", "ebitda", "dívida líquida / EBITDA", 1, "x")
        return None

    def _amount(self, campo: str, periodo: str) -> tuple[Decimal, dict, str] | None:
        """Valor em reais de um campo, informado ou, na falta, calculado. Diz qual dos dois."""
        informed = self.found(campo, periodo)
        if informed is not None:
            amount = _money(informed)
            return (amount, _operand(informed), "informado") if amount is not None else None
        computed = self.calculated(campo, periodo)
        if computed is None:
            return None
        amount = in_reais(Decimal(str(computed["valor_numerico"])), computed["unidade"])
        operand = {"campo": campo, "periodo": periodo, "valor": computed["valor"], "unidade": computed["unidade"],
                   "formula": computed["formula"]}
        return (amount, operand, "calculado") if amount is not None else None

    def _ratio(self, campo: str, periodo: str, top: str, bottom: str, formula: str, factor: int,
               unit: str) -> dict | None:
        numerator, denominator = self._amount(top, periodo), self._amount(bottom, periodo)
        if numerator is None or denominator is None or denominator[0] == 0:
            return None
        if campo == "alavancagem" and denominator[0] < 0:
            return None  # EBITDA negativo: o múltiplo não tem sentido
        result = (numerator[0] / denominator[0] * factor).quantize(Decimal("0.01"))
        return {"valor": f"{format_br(result, 2)}{unit}", "valor_numerico": float(result), "unidade": unit,
                "periodo": periodo, "formula": formula,
                "operandos": [{**numerator[1], "base": numerator[2]}, {**denominator[1], "base": denominator[2]}]}

    # -- variação contra o período comparável --------------------------------------------------

    def variation(self, campo: str) -> dict | None:
        current, previous = self.found(campo, self.reference), self.found(campo, self.comparable)
        informed = self.found_measure(campo, "variacao_informada")
        computed = None
        if current is not None and previous is not None:
            computed = self._difference(current, previous)
        elif current is not None and self.comparable:
            self.alerts.append(AlertDraft(
                "sem_comparavel", "informacao", campo=campo,
                mensagem=f"{FIELDS[campo].label}: sem o valor de {self.comparable}, a variação não foi calculada."))
        if computed is None and informed is None:
            return None
        if computed is not None and informed is not None and computed["unidade"] == "%":
            gap = abs(Decimal(str(computed["valor_numerico"])) - informed.valor)
            if gap > VARIATION_TOLERANCE:
                self.alerts.append(AlertDraft(
                    "variacao_divergente", "atencao", campo=campo,
                    mensagem=f"{FIELDS[campo].label}: a variação informada ({informed.texto}) não bate com a "
                             f"calculada pelos dois períodos ({computed['valor']}).",
                    detalhes={"informada": informed.texto, "calculada": computed["valor"],
                              "pagina": informed.pagina, "trecho": informed.trecho}))
        return {"calculada": computed, "informada": informed.view() if informed else None}

    def found_measure(self, campo: str, medida: str) -> Value | None:
        value = self.get(campo, self.reference, medida)
        return value if value is not None and value.status == "encontrado" else None

    def _difference(self, current: Value, previous: Value) -> dict | None:
        if current.tipo in ("percentual", "multiplo"):  # margem e alavancagem: diferença simples
            unit = "p.p." if current.tipo == "percentual" else "x"
            delta = (current.valor - previous.valor).quantize(Decimal("0.01"))
            return {"valor": f"{format_br(delta, 2)} {unit}", "valor_numerico": float(delta), "unidade": unit,
                    "formula": f"{current.texto} − {previous.texto}"}
        now, before = _money(current), _money(previous)
        if now is None or before is None or before == 0:
            return None
        percent = ((now - before) / abs(before) * 100).quantize(Decimal("0.01"))
        return {"valor": f"{format_br(percent, 2)}%", "valor_numerico": float(percent), "unidade": "%",
                "formula": f"({current.texto} − {previous.texto}) / |{previous.texto}| × 100",
                "mudanca_de_sinal": (now < 0) != (before < 0)}

    # -- validações ----------------------------------------------------------------------------

    def _check_informed_against_calculated(self) -> None:
        ebitda = self.found("ebitda", self.reference)
        informed_amount = _money(ebitda)
        from_statement = self.calculated("ebitda", self.reference) if informed_amount is not None else None
        if from_statement:
            computed_amount = in_reais(Decimal(str(from_statement["valor_numerico"])), from_statement["unidade"])
            largest = max(abs(informed_amount), abs(computed_amount))
            if largest and abs(informed_amount - computed_amount) / largest > EBITDA_TOLERANCE:
                self.alerts.append(AlertDraft(
                    "ebitda_divergente", "atencao", campo="ebitda",
                    mensagem=f"O EBITDA informado ({ebitda.texto} {ebitda.unidade}) não bate com o resultado antes do "
                             f"resultado financeiro mais depreciação e amortização ({from_statement['valor']} "
                             f"{from_statement['unidade']}). Pode ser um EBITDA ajustado, o valor de uma controlada "
                             "ou um sinal omitido no texto.",
                    detalhes={"informado": f"{ebitda.texto} {ebitda.unidade}", "pagina": ebitda.pagina,
                              "calculado": f"{from_statement['valor']} {from_statement['unidade']}",
                              "trecho": ebitda.trecho}))
        margin = self.found("margem_ebitda", self.reference)
        if margin is not None and self.found("ebitda", self.reference) is not None:
            computed = self.calculated("margem_ebitda", self.reference)
            if computed and abs(Decimal(str(computed["valor_numerico"])) - margin.valor) > MARGIN_TOLERANCE:
                self.alerts.append(AlertDraft(
                    "margem_divergente", "atencao", campo="margem_ebitda",
                    mensagem=f"A margem EBITDA informada ({margin.texto}) não bate com EBITDA dividido pela "
                             f"receita ({computed['valor']}).",
                    detalhes={"informada": margin.texto, "calculada": computed["valor"], "pagina": margin.pagina}))
        leverage = self.found("alavancagem", self.reference)
        if (leverage is not None and self.found("divida_liquida", self.reference) is not None
                and self.found("ebitda", self.reference) is not None):
            computed = self.calculated("alavancagem", self.reference)
            if computed and abs(Decimal(str(computed["valor_numerico"])) - leverage.valor) > LEVERAGE_TOLERANCE:
                self.alerts.append(AlertDraft(
                    "alavancagem_divergente", "atencao", campo="alavancagem",
                    mensagem=f"A alavancagem informada ({leverage.texto}) não bate com dívida líquida dividida "
                             f"pelo EBITDA informados ({computed['valor']}). Pode ser um EBITDA ajustado.",
                    detalhes={"informada": leverage.texto, "calculada": computed["valor"], "pagina": leverage.pagina}))

    def _check_each_value(self) -> None:
        for value in self._values.values():
            if value.status != "encontrado":
                continue
            info = FIELDS[value.campo]
            label = f"{info.label} ({value.periodo})"
            if value.tipo == "valor" and value.unidade is None:
                self.alerts.append(AlertDraft(
                    "unidade_nao_declarada", "atencao", campo=value.campo,
                    mensagem=f"{label}: a fonte não declara a unidade (mil, milhões) deste valor.",
                    detalhes={"pagina": value.pagina}))
            elif value.unidade_origem == "inherited":
                self.alerts.append(AlertDraft(
                    "unidade_herdada", "informacao", campo=value.campo,
                    mensagem=f"{label}: a unidade ({value.unidade}) vem de uma convenção declarada em página "
                             "anterior, não da própria página.", detalhes={"pagina": value.pagina}))
            if value.escopo == "controladora" and value.alternativa_consolidada:
                self.alerts.append(AlertDraft(
                    "escopo_controladora", "atencao", campo=value.campo,
                    mensagem=f"{label}: foi usado o valor da controladora, e a mesma tabela tem a coluna do "
                             "consolidado.", detalhes={"coluna": value.coluna, "pagina": value.pagina}))
            if value.origem == "tabela" and info.section and info.section not in value.secao.casefold():
                self.alerts.append(AlertDraft(
                    "secao_inesperada", "informacao", campo=value.campo,
                    mensagem=f"{label}: o valor veio da seção '{value.secao}', e não de uma demonstração de "
                             f"{info.section}.", detalhes={"pagina": value.pagina}))
            if not value.periodo_confirmado:
                self.alerts.append(AlertDraft(
                    "periodo_nao_confirmado", "atencao", campo=value.campo,
                    mensagem=f"{label}: a fonte citada não escreve o período {value.periodo}. Confira se o valor é "
                             "mesmo desse período.", detalhes={"pagina": value.pagina, "trecho": value.trecho}))

    def _check_units(self) -> None:
        units = {value.unidade for campo in MAIN_FIELDS
                 if (value := self.found(campo, self.reference)) is not None and value.tipo == "valor"
                 and value.unidade}
        if len(units) > 1:
            self.alerts.append(AlertDraft(
                "unidades_diferentes", "informacao",
                mensagem=f"Os indicadores estão em unidades diferentes ({', '.join(sorted(units))}). As contas "
                         "entre eles foram feitas em reais.", detalhes={"unidades": sorted(units)}))

    # -- resultado -----------------------------------------------------------------------------

    def indicators(self) -> dict[str, Any]:
        """Os 8 indicadores, cada um com o informado, o comparável, a variação e o calculado."""
        result: dict[str, Any] = {}
        for campo in MAIN_FIELDS:
            informed = self.get(campo, self.reference)
            previous = self.found(campo, self.comparable)
            computed = None
            # O calculado só aparece quando a empresa não informa, ou onde ele serve de conferência
            # do informado: o EBITDA pela demonstração e as razões (margem e alavancagem).
            if informed is None or informed.status != "encontrado" or campo in ("ebitda", "margem_ebitda",
                                                                                 "alavancagem"):
                computed = self.calculated(campo, self.reference)
                if computed is not None:
                    previous_computed = self.calculated(campo, self.comparable)
                    computed["comparavel"] = previous_computed
            result[campo] = {
                "rotulo": FIELDS[campo].label,
                "informado": informed.view() if informed else None,
                "comparavel": previous.view() if previous else None,
                "variacao": self.variation(campo),
                "calculado": computed,
            }
        self._check_informed_against_calculated()
        self._check_each_value()
        self._check_units()
        return result

    def support(self) -> dict[str, Any]:
        """Valores de apoio guardados, por campo e período: são os operandos do que foi calculado."""
        result: dict[str, dict[str, Any]] = {}
        for (campo, periodo, medida), value in self._values.items():
            if campo not in MAIN_FIELDS and medida == "valor" and value.status == "encontrado":
                result.setdefault(campo, {"rotulo": FIELDS[campo].label})[periodo] = value.view()
        return result
