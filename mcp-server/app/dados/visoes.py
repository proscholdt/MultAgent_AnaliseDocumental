"""Como as linhas do banco aparecem para os agentes: os alertas e a análise em dicionários."""
from typing import Any

from app.dados.modelos import AnalysisItem
from app.regras.campos import CATEGORIES
from app.regras.texto import GOING_CONCERN


def alert_view(alert: Any) -> dict[str, Any]:
    return {"tipo": alert.tipo, "codigo": alert.codigo, "severidade": alert.severidade, "campo": alert.campo,
            "mensagem": alert.mensagem, "detalhes": alert.detalhes, "registrado_por": alert.agent}


def analysis_view(items: list[AnalysisItem]) -> dict[str, list[dict[str, Any]]]:
    """Os itens em vigor, por categoria, na ordem das categorias."""
    grouped: dict[str, list[dict[str, Any]]] = {categoria: [] for categoria in CATEGORIES}
    for item in items:
        if item.status == "encontrado":
            # Continuidade operacional gravada como guidance antes da regra: é risco (ver GOING_CONCERN).
            categoria = "risco" if item.categoria == "guidance" and GOING_CONCERN.search(item.citacao or "") \
                else item.categoria
            grouped[categoria].append({
                "afirmacao": item.afirmacao, "citacao": item.citacao, "pagina": item.pagina,
                "chunk_index": item.chunk_index, "secao": item.secao, "fonte": item.fonte, "tom": item.tom})
        else:
            grouped[item.categoria].append({"status": "nao_encontrado", "motivo": item.motivo,
                                            "evidencia": item.evidencia})
    # Os motivos sob um título de receita vêm primeiro, depois os que só falam de receita no texto: o
    # primeiro é a resposta a "o que explicou a variação da receita". Na Light, o primeiro item era o
    # de uma controlada (custo de energia), e os modelos tomavam o primeiro como o principal. Só o
    # texto não basta: a citação da controlada também diz "a receita líquida da Light Energia".
    def revenue_first(entry: dict[str, Any]) -> int:
        if "receita" in (entry.get("secao") or "").casefold():
            return 0
        return 1 if "receita" in (entry.get("citacao") or "").casefold() else 2

    grouped["motivo_resultado"].sort(key=revenue_first)
    return grouped
