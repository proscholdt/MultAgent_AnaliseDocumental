"""O que um plugin de domínio pode mudar no servidor MCP.

O núcleo confere o que vale para qualquer documento: citação literal, número só por marcador ou
citação, ausência com prova, a célula lida pelo código, a unidade do pacote. O que é de um domínio
só (as fórmulas das demonstrações, a regra de que o lucro vem da demonstração do resultado, a conta
de uma frase em reais) fica num plugin, dentro do pacote: `dominios/<id>/plugin/mcp/__init__.py`
define `Plugin`, uma subclasse de `PluginMCP`.

Cada gancho tem aqui o comportamento neutro, que é o de um domínio sem plugin (`PluginGenerico`):
nada calculado, nenhuma regra a mais. O plugin sobrescreve só os ganchos de que precisa, e importa
do núcleo só o que está em `app.plugins.api`.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.dominio.documento import IndexedDocument
    from app.pacotes import Pacote
    from app.regras.formulas import Extraction

# Uma regra de inconsistência: recebe uma frase do documento e devolve o motivo, se achar.
Regra = tuple[str, Callable[[str], str | None]]


class PluginMCP:
    """O plugin neutro. Um plugin de domínio herda dele e sobrescreve o que precisa."""

    nome = "generico"

    # -- o pacote ----------------------------------------------------------------------------------

    def validar_pacote(self, pacote: Pacote) -> None:
        """Exigências do plugin aos campos do pacote (as fórmulas precisam dos operandos). Levanta
        `PacoteInvalido` com o motivo."""

    # -- leitura de um valor (salvar_campo) -------------------------------------------------------

    def escopo_da_coluna(self, rotulo: str) -> str | None:
        """A parte da entidade a que uma coluna de tabela se refere (ex.: consolidado)."""
        return None

    def escopo_alternativo(self, escopo: str | None, cabecalho: list[str]) -> bool:
        """A tabela tem uma coluna de um escopo melhor do que o escolhido?"""
        return False

    def ler_trecho(self, campo: str, trecho: str, numero: str) -> dict[str, Any]:
        """O que o trecho de texto diz do valor além do número: o escopo e se ele é negativo."""
        return {"escopo": None, "negativo": False}

    def conferir_fonte(self, args: Any, info: Any, achado: dict[str, Any], chunk: dict[str, Any],
                       documento: IndexedDocument) -> None:
        """A fonte apontada é mesmo deste campo? Levanta `ToolRejected` dizendo o que corrigir."""

    def campos_que_se_excluem(self, campo: str) -> tuple[str, ...]:
        """Campos que gravar este apaga (duas formas de registrar a mesma coisa)."""
        return ()

    def dica_celula_repetida(self, campo: str, outro: str) -> str | None:
        """O que dizer quando a célula apontada já está gravada em outro campo."""
        return None

    def avisos_do_valor(self, achado: dict[str, Any]) -> list[str]:
        """Avisos de domínio sobre um valor gravado (vão para o agente, não bloqueiam)."""
        return []

    def evidencia_de_valor(self, documento: IndexedDocument, campo: str) -> list[tuple[int, str]]:
        """Onde o documento traz o campo com valor: (página, o quê). Com evidência, o campo não pode
        ser dado como não encontrado."""
        return []

    # -- fechamento e cálculo ------------------------------------------------------------------

    def conferir_fechamento(self, documento: IndexedDocument, extracao: Extraction,
                            apoio_ausente: str | None) -> list[str]:
        """Conferências do domínio ao fechar a extração. Levanta `ToolRejected` com o que falta, ou
        devolve os campos que ficaram sem cálculo possível (o agente explicou em `apoio_ausente`)."""
        return []

    def calcular(self, extracao: Extraction, campo: str, periodo: str) -> dict[str, Any] | None:
        """O valor de um campo por fórmula, a partir dos valores guardados, ou None."""
        return None

    # Campos cujo calculado aparece mesmo quando o informado existe: serve de conferência dele.
    campos_conferidos_por_calculo: frozenset[str] = frozenset()

    def validar(self, extracao: Extraction) -> None:
        """As validações do domínio, depois de montados os campos: acrescentam alertas em
        `extracao.alerts`. Nada é corrigido."""

    def campos_enxutos(self) -> tuple[str, ...] | None:
        """Os campos da extração enxuta, na ordem; None: os campos principais do pacote."""
        return None

    # -- inconsistências da fonte ------------------------------------------------------------------

    def regras_inconsistencia(self, nucleo: list[Regra], ano: int | None, extracao: Any) -> list[Regra]:
        """As regras que varrem as frases, na ordem em que são tentadas (vale a primeira que acha).
        Recebe as do núcleo; o plugin acrescenta as suas onde quiser."""
        return nucleo

    # -- análise ---------------------------------------------------------------------------------

    def conferir_item_analise(self, categoria: str, citacao: str) -> None:
        """Recusa (`ToolRejected`) um item de análise na categoria errada."""

    def categoria_na_leitura(self, categoria: str, citacao: str, categorias: dict[str, Any]) -> str:
        """A categoria em que um item gravado aparece na leitura (para itens gravados antes de uma regra)."""
        return categoria

    def ordenar_analise(self, agrupado: dict[str, list[dict[str, Any]]]) -> None:
        """Ordena os itens de cada categoria, no lugar."""

    def tipo_de_fonte(self, secoes: list[str]) -> str | None:
        """De que parte do documento o trecho vem, pela seção (ex.: nota explicativa). None: o núcleo
        diz "documento"."""
        return None

    # Quem pode aparecer como sujeito de uma negação ("A empresa não informa..."), além dos do
    # núcleo: a afirmação que começa assim é uma ausência, e ausência tem tool própria.
    sujeitos_de_negacao: tuple[str, ...] = ()


class PluginGenerico(PluginMCP):
    """O de um pacote sem plugin: só os portões do núcleo."""
