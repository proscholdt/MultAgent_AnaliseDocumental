"""Como as linhas do banco aparecem para os agentes: os alertas e a análise em dicionários."""
from typing import Any

from app.dados.modelos import AnalysisItem
from app.regras.campos import CATEGORIES


def alert_view(alert: Any) -> dict[str, Any]:
    return {"tipo": alert.tipo, "codigo": alert.codigo, "severidade": alert.severidade, "campo": alert.campo,
            "mensagem": alert.mensagem, "detalhes": alert.detalhes, "registrado_por": alert.agent}


def analysis_view(items: list[AnalysisItem]) -> dict[str, list[dict[str, Any]]]:
    """Os itens em vigor, por categoria, na ordem das categorias."""
    grouped: dict[str, list[dict[str, Any]]] = {categoria: [] for categoria in CATEGORIES}
    for item in items:
        if item.status == "encontrado":
            grouped[item.categoria].append({
                "afirmacao": item.afirmacao, "citacao": item.citacao, "pagina": item.pagina,
                "chunk_index": item.chunk_index, "secao": item.secao, "fonte": item.fonte, "tom": item.tom})
        else:
            grouped[item.categoria].append({"status": "nao_encontrado", "motivo": item.motivo,
                                            "evidencia": item.evidencia})
    return grouped
