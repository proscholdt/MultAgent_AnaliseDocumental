"""O que um plugin de domínio pode mudar no indexador.

O núcleo lê qualquer PDF: estrutura de seções e notas numeradas, tabelas com o título, cabeçalho e
rodapé corridos, sumário, a unidade em reais de cada trecho. O que é de um tipo de documento só (o
nome fixo das demonstrações financeiras, o rodapé "as notas explicativas são parte integrante", o
nome da empresa por regex) fica num plugin, dentro do pacote: `dominios/<id>/plugin/indexador.py`
define `Plugin`, uma subclasse de `PluginIndexador`.

Cada gancho tem aqui o comportamento neutro, o de um domínio sem plugin. O plugin importa do
núcleo só o que está em `app.plugins.api`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.pacotes import Pacote
    from app.schemas import ParsedDocument


class PluginIndexador:
    """O plugin neutro. Um plugin de domínio herda dele e sobrescreve o que precisa."""

    nome = "generico"

    def titulo_de_secao(self, linha: str) -> bool:
        """A linha é o nome fixo de uma seção deste tipo de documento (ex.: "Balanços patrimoniais")?
        Uma linha assim abre uma seção mesmo quando o layout a marca como texto comum."""
        return False

    def rodape_conhecido(self, texto: str) -> bool:
        """O texto é um rodapé fixo deste tipo de documento, que não é conteúdo?"""
        return False

    def linha_de_subtitulo(self, texto: str) -> bool:
        """O texto, marcado como título pelo layout, é na verdade o subtítulo de uma tabela (o
        período, a unidade)? Vai como texto, para o alto da tabela."""
        return False

    def metadados_reserva(self, parsed: ParsedDocument, pacote: Pacote) -> dict[str, str]:
        """Metadados por regra, quando o LLM não acha ou não prova: {nome do metadado: valor}."""
        return {}

    def instrucao_figuras(self) -> str | None:
        """A frase que diz ao modelo de visão de que documentos são as figuras. None: "documentos"."""
        return None


class PluginGenerico(PluginIndexador):
    """O de um pacote sem plugin do indexador."""
