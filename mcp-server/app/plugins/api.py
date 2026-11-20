"""O que um plugin de domínio pode importar do núcleo do servidor MCP.

O plugin mora fora do servidor (`dominios/<id>/plugin/mcp/`) e importa daqui, e só daqui: o que
está nesta lista é estável para quem escreve plugin; o resto do núcleo pode mudar de lugar.
"""
from app.dominio.documento import IndexedDocument
from app.dominio.erros import ToolRejected
from app.dominio.periodo import Periodo
from app.pacotes import Pacote, PacoteInvalido
from app.plugins.interface import PluginMCP, Regra
from app.regras.campos import FIELDS, MAIN_FIELDS, Field, atual
from app.regras.evidencias import SENTENCE, mentions, squash_spaces, statement_tables
from app.regras.formulas import AlertDraft, Extraction, Value, back, money, operand
from app.regras.inconsistencias import COMPARISON
from app.regras.numeros import (
    format_br,
    in_reais,
    number_tokens,
    parse_number,
    to_decimal,
    unit_of_number,
)
from app.regras.tabelas import normalize, parse_table
from app.regras.texto import plain, squash

__all__ = [
    "COMPARISON", "FIELDS", "MAIN_FIELDS", "SENTENCE", "AlertDraft", "Extraction", "Field", "IndexedDocument",
    "Pacote", "PacoteInvalido", "Periodo", "PluginMCP", "Regra", "ToolRejected", "Value", "atual", "back",
    "format_br", "in_reais", "mentions", "money", "normalize", "number_tokens", "operand", "parse_number",
    "parse_table", "plain", "squash", "squash_spaces", "statement_tables", "to_decimal", "unit_of_number",
]
