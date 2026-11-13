"""A extração no formato enxuto: só os campos que o teste pede (item 2), cada um com a origem.

É uma visão do JSON completo publicado por `fechar_extracao`, sem nenhuma conta nova: o código
só escolhe e renomeia. O completo continua sendo a fonte; este é para ler e entregar.

    {
      "empresa": "...", "documento": "Light.pdf",
      "periodo_referencia": "2022", "periodo_comparavel": "2021",
      "receita_liquida": {"status": "encontrado", "valor": ..., "unidade": ..., "variacao": {...},
                          "origem": {"pagina": ..., "trecho": ...}},
      "ebitda": {"status": "não encontrado", "valor": null, "motivo": ..., "calculado_pelo_sistema": {...}},
      ...
    }

Regra do teste: o que não existe no documento vem como "não encontrado", nunca estimado. O valor
calculado por fórmula fixa (quando os valores de origem existem) fica num bloco à parte,
`calculado_pelo_sistema`, com a fórmula e as páginas: não ocupa o lugar do valor informado.
"""
from typing import Any

from app.report import source_pages

# Os campos do item 2 do teste, na ordem em que ele os lista.
FIELDS = ("receita_liquida", "ebitda", "margem_ebitda", "lucro_liquido", "divida_liquida", "alavancagem",
          "fluxo_caixa_operacional", "capex")
NOT_FOUND = "não encontrado"


def _number(value: float | None) -> float | int | None:
    if value is None:
        return None
    return int(value) if float(value).is_integer() else value


def _snippet(origin: dict[str, Any]) -> str:
    """O trecho citado. Num valor de tabela, a linha da tabela (o cabeçalho vai em `coluna`)."""
    text = origin.get("trecho") or ""
    return text.splitlines()[-1] if origin.get("tipo") == "tabela" and text else text


def _origin(value: dict[str, Any]) -> dict[str, Any]:
    origin = value["origem"]
    view: dict[str, Any] = {"pagina": origin["pagina"], "trecho": _snippet(origin), "tipo": origin["tipo"]}
    if origin.get("tipo") == "tabela":
        view.update(linha=origin.get("linha"), coluna=origin.get("coluna"))
    return view


def _variation(block: dict[str, Any], comparable_period: str | None) -> dict[str, Any] | None:
    variation = block.get("variacao") or {}
    computed, informed = variation.get("calculada"), variation.get("informada")
    if not computed and not informed:
        return None
    view: dict[str, Any] = {"contra": comparable_period}
    if computed:
        sign = "+" if computed["valor_numerico"] > 0 else ""
        view.update(valor=computed["valor_numerico"], texto=f"{sign}{computed['valor']}", unidade=computed["unidade"],
                    tipo="calculada pelos dois períodos", formula=computed["formula"])
        if computed.get("mudanca_de_sinal"):
            view["observacao"] = "o sinal mudou entre os dois períodos: o percentual não tem leitura direta"
        previous = block.get("comparavel")
        if previous:
            view["valor_comparavel"] = {"valor": _number(previous["valor_numerico"]),
                                        "valor_como_escrito": previous["valor"], "unidade": previous["unidade"],
                                        "origem": _origin(previous)}
    if informed:
        view["informada_pela_empresa"] = {"valor": informed["valor"], "origem": _origin(informed)}
    return view


def _computed(indicators: dict[str, Any], campo: str, file_name: str) -> dict[str, Any] | None:
    computed = indicators[campo].get("calculado")
    if not computed:
        return None
    return {"valor": _number(computed["valor_numerico"]), "valor_como_escrito": computed["valor"],
            "unidade": computed["unidade"], "formula": computed["formula"],
            "origem": {"documento": file_name, "paginas": source_pages(indicators, campo, "calculado")},
            "observacao": "calculado por fórmula fixa a partir de valores do documento; não é informado pela empresa"}


def compact_field(indicators: dict[str, Any], campo: str, file_name: str, comparable: str | None,
                  alerts: list[dict[str, Any]]) -> dict[str, Any]:
    block = indicators[campo]
    informed = block.get("informado")
    if informed and informed["status"] == "encontrado":
        view: dict[str, Any] = {
            "status": "encontrado", "valor": _number(informed["valor_numerico"]),
            "valor_como_escrito": informed["valor"], "unidade": informed["unidade"],
            "periodo": informed["periodo"], "escopo": informed.get("escopo"), "origem": _origin(informed),
            "variacao": _variation(block, comparable)}
        if informed.get("nota_do_agente"):
            view["nota"] = informed["nota_do_agente"]
    else:
        view = {"status": NOT_FOUND, "valor": None,
                "motivo": (informed or {}).get("motivo") or "o campo não foi extraído",
                "busca": (informed or {}).get("evidencia")}
    computed = _computed(indicators, campo, file_name)
    if computed:
        view["calculado_pelo_sistema"] = computed
    mine = [alert["mensagem"] for alert in alerts if alert.get("campo") == campo and alert["severidade"] == "atencao"]
    if mine:
        view["alertas"] = mine
    return view


def compact(payload: dict[str, Any]) -> dict[str, Any]:
    """O JSON enxuto de um documento, a partir do JSON completo publicado."""
    meta, indicators = payload["documento"], payload["indicadores"]
    alerts = payload.get("alertas_de_validacao") or []
    result: dict[str, Any] = {
        "empresa": meta.get("empresa"), "documento": meta.get("arquivo"),
        "periodo_referencia": meta.get("periodo_referencia"), "periodo_comparavel": meta.get("periodo_comparavel"),
    }
    for campo in FIELDS:
        result[campo] = compact_field(indicators, campo, meta.get("arquivo"), meta.get("periodo_comparavel"), alerts)
    general = [alert["mensagem"] for alert in alerts if not alert.get("campo") and alert["severidade"] == "atencao"]
    if general:
        result["alertas"] = general
    return result
