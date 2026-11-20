"""Como os registros da base aparecem para os agentes: os alertas e a análise em dicionários.

Regra pura: recebe qualquer objeto com os atributos (a linha do banco ou um dublê de teste).
"""
from typing import Any

from app.regras.campos import CATEGORIES, atual


def alert_view(alert: Any) -> dict[str, Any]:
    return {"tipo": alert.tipo, "codigo": alert.codigo, "severidade": alert.severidade, "campo": alert.campo,
            "mensagem": alert.mensagem, "detalhes": alert.detalhes, "registrado_por": alert.agent}


def analysis_view(items: list[Any]) -> dict[str, list[dict[str, Any]]]:
    """Os itens em vigor, por categoria, na ordem das categorias (e na ordem que o plugin do domínio
    der dentro de cada uma)."""
    plugin = atual().plugin
    grouped: dict[str, list[dict[str, Any]]] = {categoria: [] for categoria in CATEGORIES}
    for item in items:
        if item.status == "encontrado":
            # Um item gravado antes de uma regra do domínio pode aparecer em outra categoria.
            categoria = plugin.categoria_na_leitura(item.categoria, item.citacao or "", grouped)
            grouped[categoria].append({
                "afirmacao": item.afirmacao, "citacao": item.citacao, "pagina": item.pagina,
                "chunk_index": item.chunk_index, "secao": item.secao, "fonte": item.fonte, "tom": item.tom})
        else:
            grouped[item.categoria].append({"status": "nao_encontrado", "motivo": item.motivo,
                                            "evidencia": item.evidencia})
    plugin.ordenar_analise(grouped)
    return grouped
