"""O que um plugin de domínio pode importar do núcleo do indexador.

O plugin mora fora do indexador (`dominios/<id>/plugin/indexador.py`) e importa daqui, e só daqui:
o que está nesta lista é estável para quem escreve plugin; o resto do núcleo pode mudar de lugar.
"""
from app.pacotes import Pacote, PacoteInvalido
from app.plugins.interface import PluginIndexador
from app.schemas import ParsedDocument, ParsedItem

__all__ = ["Pacote", "PacoteInvalido", "ParsedDocument", "ParsedItem", "PluginIndexador"]
