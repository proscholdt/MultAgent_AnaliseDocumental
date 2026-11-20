"""O plugin das demonstrações financeiras no servidor MCP.

Tudo o que só vale para balanços, DREs e relatórios da administração: as fórmulas (EBITDA, dívida
líquida, capex, margem, alavancagem) e as validações delas, os valores de apoio, o escopo
consolidado ou controladora, as regras de onde cada indicador vem, as inconsistências em reais de
uma frase, a continuidade operacional e a ordem dos motivos da receita. O núcleo do servidor não
sabe nada disso: chama os ganchos de `PluginMCP` (app/plugins/interface.py).
"""
from typing import Any

from app.plugins.api import Extraction, IndexedDocument, Pacote, PluginMCP, Regra

from . import analise, fechamento, fonte, formulas, inconsistencias


class Plugin(PluginMCP):
    nome = "financeiro"
    campos_conferidos_por_calculo = formulas.CONFERIDOS_POR_CALCULO
    sujeitos_de_negacao = analise.SUJEITOS_DE_NEGACAO

    def validar_pacote(self, pacote: Pacote) -> None:
        formulas.validar_pacote(pacote)

    # -- leitura de um valor -----------------------------------------------------------------

    def escopo_da_coluna(self, rotulo: str) -> str | None:
        return fonte.escopo_da_coluna(rotulo)

    def escopo_alternativo(self, escopo: str | None, cabecalho: list[str]) -> bool:
        return fonte.escopo_alternativo(escopo, cabecalho)

    def ler_trecho(self, campo: str, trecho: str, numero: str) -> dict[str, Any]:
        return fonte.ler_trecho(campo, trecho)

    def conferir_fonte(self, args: Any, info: Any, achado: dict[str, Any], chunk: dict[str, Any],
                       documento: IndexedDocument) -> None:
        fonte.conferir_fonte(args, info, achado, chunk, documento)

    def campos_que_se_excluem(self, campo: str) -> tuple[str, ...]:
        return fonte.campos_que_se_excluem(campo)

    def dica_celula_repetida(self, campo: str, outro: str) -> str | None:
        return fonte.dica_celula_repetida(campo, outro)

    def avisos_do_valor(self, achado: dict[str, Any]) -> list[str]:
        return fonte.avisos_do_valor(achado)

    def evidencia_de_valor(self, documento: IndexedDocument, campo: str) -> list[tuple[int, str]]:
        return fechamento.informed_evidence(documento, campo)

    # -- fechamento e cálculo ------------------------------------------------------------------

    def conferir_fechamento(self, documento: IndexedDocument, extracao: Extraction,
                            apoio_ausente: str | None) -> list[str]:
        return fechamento.conferir_fechamento(documento, extracao, apoio_ausente)

    def calcular(self, extracao: Extraction, campo: str, periodo: str) -> dict[str, Any] | None:
        return formulas.calcular(extracao, campo, periodo)

    def validar(self, extracao: Extraction) -> None:
        formulas.validar(extracao)

    def campos_enxutos(self) -> tuple[str, ...]:
        # Os campos do item 2 do teste, na ordem em que ele os lista.
        return ("receita_liquida", "ebitda", "margem_ebitda", "lucro_liquido", "divida_liquida", "alavancagem",
                "fluxo_caixa_operacional", "capex")

    # -- inconsistências -------------------------------------------------------------------------

    def regras_inconsistencia(self, nucleo: list[Regra], ano: int | None, extracao: Any) -> list[Regra]:
        return inconsistencias.regras(nucleo, ano, inconsistencias.indicators_of(extracao))

    # -- análise ---------------------------------------------------------------------------------

    def conferir_item_analise(self, categoria: str, citacao: str) -> None:
        analise.conferir_item_analise(categoria, citacao)

    def categoria_na_leitura(self, categoria: str, citacao: str, categorias: dict[str, Any]) -> str:
        return analise.categoria_na_leitura(categoria, citacao, categorias)

    def ordenar_analise(self, agrupado: dict[str, list[dict[str, Any]]]) -> None:
        analise.ordenar_analise(agrupado)

    def tipo_de_fonte(self, secoes: list[str]) -> str | None:
        return analise.tipo_de_fonte(secoes)
