"""A análise qualitativa das demonstrações: a continuidade operacional não é guidance, os motivos
sob um título de receita vêm primeiro, e de que parte do documento vem cada citação."""
import re
from typing import Any

from app.plugins.api import ToolRejected

# A declaração de continuidade operacional ("condições de dar continuidade a suas operações pelos
# próximos 12 meses") é a conclusão da administração sobre um risco, exigida pela norma contábil,
# não um plano. Gravada como guidance, ela tomava o lugar da perspectiva de verdade na resposta à
# pergunta do guidance (visto na Light, p. 3, em duas execuções). A gravação recusa; a leitura
# mostra em risco o que foi gravado como guidance antes da regra.
GOING_CONCERN = re.compile(r"continuidade (?:operacional|d[ea]s? (?:suas )?opera[çc][õo]es|a (?:suas )?opera[çc][õo]es)"
                           r"|continuar (?:operando|suas opera[çc][õo]es)", re.IGNORECASE)
_NUMBERED_NOTE = re.compile(r"\d{1,2}(?:\.\d{1,2})*\.?\s")

# Quem aparece como sujeito de uma negação nas demonstrações ("A empresa não informa...").
SUJEITOS_DE_NEGACAO = ("empresa", "companhia", "administração", "demonstrações financeiras", "demonstrações")


def conferir_item_analise(categoria: str, citacao: str) -> None:
    if categoria == "guidance" and GOING_CONCERN.search(citacao):
        raise ToolRejected("Continuidade operacional não é guidance: é a conclusão da administração sobre um risco. "
                           "Grave este trecho na categoria 'risco'. Guidance é o que a administração diz que vai "
                           "fazer ou espera: planos, estratégia, investimentos, metas.")


def categoria_na_leitura(categoria: str, citacao: str, categorias: dict[str, Any]) -> str:
    """Continuidade operacional gravada como guidance antes da regra: é risco."""
    if categoria == "guidance" and "risco" in categorias and GOING_CONCERN.search(citacao or ""):
        return "risco"
    return categoria


def _revenue_first(entry: dict[str, Any]) -> int:
    if "receita" in (entry.get("secao") or "").casefold():
        return 0
    return 1 if "receita" in (entry.get("citacao") or "").casefold() else 2


def ordenar_analise(agrupado: dict[str, list[dict[str, Any]]]) -> None:
    """Os motivos sob um título de receita vêm primeiro, depois os que só falam de receita no texto:
    o primeiro é a resposta a "o que explicou a variação da receita". Na Light, o primeiro item era
    o de uma controlada (custo de energia), e os modelos tomavam o primeiro como o principal. Só o
    texto não basta: a citação da controlada também diz "a receita líquida da Light Energia"."""
    if "motivo_resultado" in agrupado:
        agrupado["motivo_resultado"].sort(key=_revenue_first)


def tipo_de_fonte(secoes: list[str]) -> str:
    """De que parte do documento o trecho vem, pela seção do chunk. É uma regra simples: nota
    numerada, parecer do auditor, demonstração; o resto é texto do relatório."""
    joined = " > ".join(secoes).casefold()
    if "auditor" in joined:
        return "parecer do auditor"
    if "notas explicativas" in joined or any(_NUMBERED_NOTE.match(section) for section in secoes):
        return "nota explicativa"
    if "balanço" in joined or "demonstraç" in joined:
        return "demonstração financeira"
    return "relatório"
