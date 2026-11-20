"""A extração no formato enxuto: só os campos que o teste pede (item 2), e só o que está escrito
nos documentos.

É uma visão do JSON completo publicado por `fechar_extracao`. Nada aqui é calculado: não entram
os valores por fórmula, nem a variação calculada pelos dois períodos, nem os alertas e as notas
do agente (que falam do documento, mas não são texto dele). Tudo isso continua no JSON completo.

    {
      "empresa": "...", "documento": "Light.pdf",
      "periodo_referencia": "2022", "periodo_comparavel": "2021",
      "receita_liquida": {"status": "encontrado", "valor": ..., "unidade": ...,
                          "origem": {"pagina": ..., "trecho": ...},
                          "periodo_comparavel": {"valor": ..., "origem": {...}},
                          "variacao_informada": {"valor": "23%", "origem": {...}}},
      "ebitda": {"status": "não encontrado", "valor": null, "motivo": ..., "busca": {...}},
      ...
    }

A variação contra o período comparável aparece como a empresa a escreve (`variacao_informada`),
quando escreve; e o valor do período comparável, como está no documento, vai ao lado, para quem
quiser fazer a conta.
"""
from typing import Any

from app.regras.campos import MAIN_FIELDS, atual

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


def _value(value: dict[str, Any]) -> dict[str, Any]:
    """Um valor como está no documento: o número, como foi escrito, a unidade e a origem."""
    return {"valor": _number(value["valor_numerico"]), "valor_como_escrito": value["valor"],
            "unidade": value["unidade"], "periodo": value["periodo"], "escopo": value.get("escopo"),
            "origem": _origin(value)}


def compact_field(block: dict[str, Any]) -> dict[str, Any]:
    informed = block.get("informado")
    if not informed or informed["status"] != "encontrado":
        return {"status": NOT_FOUND, "valor": None,
                "motivo": (informed or {}).get("motivo") or "o campo não foi extraído",
                "busca": (informed or {}).get("evidencia")}
    view: dict[str, Any] = {"status": "encontrado", **_value(informed)}
    previous = block.get("comparavel")
    view["periodo_comparavel"] = _value(previous) if previous else None
    stated = (block.get("variacao") or {}).get("informada")
    view["variacao_informada"] = {"valor": stated["valor"], "origem": _origin(stated)} if stated else None
    return view


def compact(payload: dict[str, Any]) -> dict[str, Any]:
    """O JSON enxuto de um documento, a partir do JSON completo publicado."""
    meta, indicators = payload["documento"], payload["indicadores"]
    result: dict[str, Any] = {
        "empresa": meta.get("empresa"), "documento": meta.get("arquivo"),
        "periodo_referencia": meta.get("periodo_referencia"), "periodo_comparavel": meta.get("periodo_comparavel"),
    }
    # No financeiro, os campos do item 2 do teste, na ordem dele; nos outros domínios, os principais do pacote.
    for campo in (FIELDS if atual().financeiro else MAIN_FIELDS):
        result[campo] = compact_field(indicators[campo])
    return result
