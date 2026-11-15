"""Custo de uma execução, a partir dos tokens contados e da tabela de preços do config/ajustes.yaml.

O preço não fica no código: muda com o tempo e com o modelo. Modelo sem preço na tabela
aparece com os tokens e com custo nulo, em vez de um número inventado.
"""
from typing import Any

from app.nucleo.executar import add_usage, empty_usage

MILLION = 1_000_000


def cost_usd(model: str, usage: dict[str, int], prices: dict[str, dict[str, float]]) -> float | None:
    price = prices.get(model)
    if not price or "entrada" not in price or "saida" not in price:
        return None
    cached = usage.get("entrada_cache", 0)
    fresh = usage.get("entrada", 0) - cached
    total = (fresh * price["entrada"] + cached * price.get("entrada_cache", price["entrada"])
             + usage.get("saida", 0) * price["saida"])
    return round(total / MILLION, 6)  # o arredondamento para exibir é feito na soma


class Ledger:
    """Soma o uso por agente e por documento ao longo de uma execução."""

    def __init__(self, prices: dict[str, dict[str, float]]) -> None:
        self.prices = prices
        self.entries: list[dict[str, Any]] = []

    def add(self, agent: str, model: str, usage: dict[str, int], doc_id: str | None = None) -> None:
        self.entries.append({"agente": agent, "modelo": model, "doc_id": doc_id, "uso": dict(usage)})

    def _grouped(self, key: str) -> dict[str, dict[str, Any]]:
        groups: dict[str, dict[str, Any]] = {}
        for entry in self.entries:
            name = entry[key]
            if name is None:
                continue
            group = groups.setdefault(name, {"uso": empty_usage(), "custo_usd": 0.0, "_sem_preco": False})
            add_usage(group["uso"], entry["uso"])
            cost = cost_usd(entry["modelo"], entry["uso"], self.prices)
            if cost is None:
                group["_sem_preco"] = True
            else:
                group["custo_usd"] += cost
        for group in groups.values():
            group["custo_usd"] = None if group.pop("_sem_preco") else round(group["custo_usd"], 4)
        return groups

    def summary(self) -> dict[str, Any]:
        total = empty_usage()
        for entry in self.entries:
            add_usage(total, entry["uso"])
        costs = [cost_usd(entry["modelo"], entry["uso"], self.prices) for entry in self.entries]
        unpriced = sorted({entry["modelo"] for entry, cost in zip(self.entries, costs, strict=True) if cost is None})
        return {
            "total": {"uso": total, "custo_usd": None if unpriced else round(sum(costs), 4)},  # type: ignore[arg-type]
            "por_agente": self._grouped("agente"),
            "por_documento": self._grouped("doc_id"),
            "modelos_sem_preco": unpriced,
        }
