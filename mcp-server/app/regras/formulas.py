"""Do que foi guardado ao JSON da extração: os valores, o comparável, a variação, e o que o plugin
do domínio calcula e valida.

Aqui não há LLM. Os operandos são valores que já passaram pelo portão (lidos da célula ou
conferidos no trecho). O que o documento informa e o que o código calcula ficam em blocos
separados: informação ausente vem como "não encontrado", sem estimativa, e um valor calculado
nunca aparece como informado.

As fórmulas e as validações são do domínio (o plugin: `calcular` e `validar`). O núcleo só
calcula a variação entre os dois períodos, quando o recorte tem comparável, e a compara com a
variação que o próprio documento escreve. Nada é corrigido.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.regras.campos import FIELDS, MAIN_FIELDS, atual
from app.regras.numeros import format_br, in_reais

VARIATION_TOLERANCE = Decimal("0.5")  # pontos percentuais


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
        """O valor no JSON da extração, com a origem (página e trecho)."""
        if self.status != "encontrado":
            return {"status": "nao_encontrado", "motivo": self.motivo, "evidencia": self.evidencia}
        origin: dict[str, Any] = {"tipo": self.origem, "pagina": self.pagina, "chunk_index": self.chunk_index,
                                  "secao": self.secao, "trecho": self.trecho}
        if self.pagina_final and self.pagina_final != self.pagina:
            origin["pagina_final"] = self.pagina_final
        if self.origem == "tabela":
            origin.update(linha=self.linha, coluna=self.coluna)
        view: dict[str, Any] = {
            "status": "encontrado", "valor": self.texto,
            "valor_numerico": float(self.valor) if self.valor is not None else None,
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


def money(value: Value | None) -> Decimal | None:
    """O valor em reais, se for dinheiro com unidade conhecida."""
    if value is None or value.status != "encontrado" or value.valor is None:
        return None
    return in_reais(value.valor, value.unidade)


def back(amount: Decimal, unit: str | None) -> Decimal:
    """De reais para a unidade em que o resultado vai ser mostrado."""
    one = in_reais(Decimal(1), unit)
    return amount / one if one else amount


def operand(value: Value) -> dict[str, Any]:
    """Um operando de um cálculo, como ele aparece no JSON."""
    return {"campo": value.campo, "periodo": value.periodo, "valor": value.texto, "unidade": value.unidade,
            "pagina": value.pagina, "escopo": value.escopo}


class Extraction:
    """Monta os campos de um documento a partir dos valores guardados."""

    def __init__(self, values: list[Value], reference: str, comparable: str | None) -> None:
        self._values = {(v.campo, v.periodo, v.medida): v for v in values}
        self.reference, self.comparable = reference, comparable
        self.alerts: list[AlertDraft] = []
        # Campos com valor no período de referência e sem o do comparável (a variação não sai).
        self.no_comparable: list[str] = []

    def get(self, campo: str, periodo: str | None, medida: str = "valor") -> Value | None:
        return self._values.get((campo, periodo or "", medida))

    def found(self, campo: str, periodo: str | None) -> Value | None:
        value = self.get(campo, periodo)
        return value if value is not None and value.status == "encontrado" else None

    def values(self) -> list[Value]:
        return list(self._values.values())

    def missing(self) -> list[str]:
        """Campos principais ainda sem valor nem "não encontrado" no período de referência."""
        return [campo for campo in MAIN_FIELDS if self.get(campo, self.reference) is None]

    # -- o que o domínio calcula ---------------------------------------------------------------

    def calculated(self, campo: str, periodo: str | None) -> dict | None:
        """O valor calculado de um campo pelo plugin do domínio, quando os operandos existem."""
        if periodo is None:
            return None
        return atual().plugin.calcular(self, campo, periodo)

    def amount(self, campo: str, periodo: str | None) -> Decimal | None:
        """Valor em reais de um campo no período, informado ou, na falta, calculado."""
        found = self.amount_and_source(campo, periodo) if periodo else None
        return found[0] if found else None

    def amount_and_source(self, campo: str, periodo: str) -> tuple[Decimal, dict, str] | None:
        """Valor em reais de um campo, informado ou, na falta, calculado. Diz qual dos dois."""
        informed = self.found(campo, periodo)
        if informed is not None:
            amount = money(informed)
            return (amount, operand(informed), "informado") if amount is not None else None
        computed = self.calculated(campo, periodo)
        if computed is None:
            return None
        amount = in_reais(Decimal(str(computed["valor_numerico"])), computed["unidade"])
        source = {"campo": campo, "periodo": periodo, "valor": computed["valor"], "unidade": computed["unidade"],
                  "formula": computed["formula"]}
        return (amount, source, "calculado") if amount is not None else None

    # -- variação contra o período comparável --------------------------------------------------

    def variation(self, campo: str) -> dict | None:
        current, previous = self.found(campo, self.reference), self.found(campo, self.comparable)
        informed = self.found_measure(campo, "variacao_informada")
        computed = None
        if current is not None and previous is not None:
            computed = self._difference(current, previous)
        elif current is not None and self.comparable:
            self.no_comparable.append(FIELDS[campo].label)
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
        if current.valor is None or previous.valor is None:
            return None
        if current.tipo in ("percentual", "multiplo"):  # razões: diferença simples
            unit = "p.p." if current.tipo == "percentual" else "x"
            delta = (current.valor - previous.valor).quantize(Decimal("0.01"))
            return {"valor": f"{format_br(delta, 2)} {unit}", "valor_numerico": float(delta), "unidade": unit,
                    "formula": f"{current.texto} − {previous.texto}"}
        now, before = money(current), money(previous)
        if now is None or before is None or before == 0:
            return None
        percent = ((now - before) / abs(before) * 100).quantize(Decimal("0.01"))
        return {"valor": f"{format_br(percent, 2)}%", "valor_numerico": float(percent), "unidade": "%",
                "formula": f"({current.texto} − {previous.texto}) / |{previous.texto}| × 100",
                "mudanca_de_sinal": (now < 0) != (before < 0)}

    # -- resultado -----------------------------------------------------------------------------

    def indicators(self) -> dict[str, Any]:
        """Os campos principais, cada um com o informado, o comparável, a variação e o calculado."""
        plugin = atual().plugin
        result: dict[str, Any] = {}
        self.no_comparable.clear()
        for campo in MAIN_FIELDS:
            informed = self.get(campo, self.reference)
            previous = self.found(campo, self.comparable)
            computed = None
            # O calculado só aparece quando o documento não informa, ou onde o plugin o usa como
            # conferência do informado.
            if informed is None or informed.status != "encontrado" or campo in plugin.campos_conferidos_por_calculo:
                computed = self.calculated(campo, self.reference)
                if computed is not None:
                    computed["comparavel"] = self.calculated(campo, self.comparable)
            result[campo] = {
                "rotulo": FIELDS[campo].label,
                "informado": informed.view() if informed else None,
                "comparavel": previous.view() if previous else None,
                "variacao": self.variation(campo),
                "calculado": computed,
            }
        plugin.validar(self)
        return result

    def support(self) -> dict[str, Any]:
        """Valores de apoio guardados, por campo e período: são os operandos do que foi calculado."""
        result: dict[str, dict[str, Any]] = {}
        for (campo, periodo, medida), value in self._values.items():
            if campo not in MAIN_FIELDS and medida == "valor" and value.status == "encontrado":
                result.setdefault(campo, {"rotulo": FIELDS[campo].label})[periodo] = value.view()
        return result
