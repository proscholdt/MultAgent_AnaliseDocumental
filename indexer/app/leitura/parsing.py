"""Etapa de parse: PDF → ParsedDocument. Aqui ficam o contrato comum aos parsers, o que os
dois compartilham e a escolha entre eles (variável PARSER).

- "docling" (docling.py): roda na máquina, sem custo por página, minutos por documento
  em CPU, imagem de 4 GB.
- "azure" (azure.py): Azure AI Document Intelligence. O parse roda no serviço (tempo
  não medido aqui), é pago por página, e o PDF é enviado ao recurso da Azure.

Os dois devolvem o mesmo ParsedDocument, então o resto do pipeline não sabe qual rodou. Este
módulo não importa nenhum dos dois: quem escolhe é `build_parser`, que só carrega o escolhido.
"""
import logging
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from pypdf import PdfReader

from app import pacotes
from app.config import Settings
from app.plugins.interface import PluginIndexador
from app.schemas import ParsedDocument, ParsedPage

log = logging.getLogger(__name__)
# Sem o fontTools instalado, o pypdf avisa a cada fonte CFF que a codificação pode sair
# incompleta: são dezenas de linhas por documento no log do worker. O texto extraído é
# conferido pela auditoria (os números das tabelas batem com ele), então o aviso fica de fora.
logging.getLogger("pypdf._cmap").setLevel(logging.ERROR)

ProgressCallback = Callable[[int, int], None]  # (páginas concluídas, total)

# Página com menos caracteres que isto é tratada como escaneada: não tem camada de texto.
MIN_NATIVE_CHARS = 20
_LINUX_FONT_DIRS = ("/usr/share/fonts", "/usr/local/share/fonts", "~/.fonts", "~/.local/share/fonts")
_NUMBER = re.compile(r"\d[\d.,]*\d")


class ParserError(Exception):
    """Erro de parse que não adianta tentar de novo: configuração ausente, credencial recusada
    ou documento que o serviço não aceita."""


class DocumentParser(Protocol):
    name: str  # "docling" | "azure": também é o nome da pasta de cache
    page_price_usd: float  # custo estimado por página; zero para o parser local

    def warmup(self) -> None:
        """Carrega o que for pesado antes do primeiro job."""

    def parse(self, pdf_path: Path, doc_id: str, file_name: str, out_dir: Path,
              on_progress: ProgressCallback | None = None, plugin: PluginIndexador | None = None) -> ParsedDocument:
        """Lê o PDF e grava os recortes de figura em `out_dir`/figures. `plugin`: o do domínio do
        documento, para o parser que separa títulos na leitura (o do Azure)."""

    def read_figures(self, parsed: ParsedDocument, out_dir: Path) -> int:
        """Completa o texto das figuras (com posição) que ainda não foi lido. Devolve quantas leu."""


def variante_do_parse(parser: str, dominio: str | None) -> str | None:
    """O JSON do parse depende do domínio só no parser que separa títulos na leitura (o Azure, pelo
    plugin do domínio); o do Docling vale para qualquer domínio. O do domínio padrão é o de sempre."""
    return dominio if parser == "azure" and dominio and dominio != pacotes.PADRAO else None


def variante_das_figuras(dominio: str | None) -> str | None:
    """As transcrições das figuras dependem do domínio (o prompt diz de que documentos elas são)."""
    return dominio if dominio and dominio != pacotes.PADRAO else None


def build_parser(settings: Settings) -> DocumentParser:
    """O parser escolhido em PARSER. Só o módulo dele é importado: a imagem leve não tem o
    Docling, e a escolha do Docling não exige o SDK da Azure configurado."""
    if settings.parser_problem:
        raise ParserError(settings.parser_problem)
    if settings.parser == "azure":
        from app.leitura.azure import AzureParser

        return AzureParser(settings)
    try:
        from app.leitura.docling import DoclingParser
    except ImportError as exc:
        raise ParserError(
            "PARSER=docling, mas o Docling não está instalado nesta imagem. Use a imagem do alvo "
            "'parse' (docker-compose.yml padrão) ou escolha PARSER=azure."
        ) from exc
    return DoclingParser(settings)


def check_system_fonts() -> None:
    """Falha cedo se o Linux não tiver nenhuma fonte instalada.

    PDF que referencia Arial ou Times sem embutir a fonte é renderizado com "caixinhas" quando
    não há substituto no sistema. Para o Docling isso degrada o layout em silêncio: páginas
    inteiras viram "figura" e as tabelas somem. Para o parser da Azure, estraga o recorte dos
    gráficos, que é renderizado aqui. O texto extraído continua certo, então o erro não
    apareceria em lugar nenhum. Windows e macOS já trazem essas fontes.
    """
    if not sys.platform.startswith("linux"):
        return
    for directory in _LINUX_FONT_DIRS:
        root = Path(directory).expanduser()
        if root.is_dir() and any(root.rglob("*.[to]tf")):
            return
    raise RuntimeError(
        "Nenhuma fonte de sistema encontrada. Instale fontes (ex.: apt-get install fonts-liberation) "
        "antes de subir o parse-worker; sem elas as páginas são renderizadas com letras faltando."
    )


def native_pages(pdf_path: Path) -> list[ParsedPage]:
    """Camada de texto de cada página, via pypdf. É a referência exata usada para conferir
    metadados, números de gráficos e, na auditoria, os números das tabelas. Inclui os rótulos
    de gráficos vetoriais, que os parsers omitem por estarem dentro de uma região de figura."""
    reader = PdfReader(str(pdf_path))
    pages: list[ParsedPage] = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # noqa: BLE001 - página com fluxo de conteúdo inválido
            log.warning("Falha ao extrair texto nativo da página %d: %s", number, exc)
            text = ""
        pages.append(ParsedPage(page=number, native_text=text))
    return pages


def unconfirmed_table_numbers(parsed: ParsedDocument) -> int:
    """Quantos números das tabelas (três dígitos ou mais) não existem na camada de texto da
    página. Com o Docling, que lê essa mesma camada, tem de dar zero. Com um parser que lê a
    página por OCR, é a medida direta de quantos números ele pode ter lido errado. Páginas sem
    camada de texto (escaneadas) ficam de fora: não há com o que comparar."""
    native = {page.page: re.sub(r"\s+", "", page.native_text) for page in parsed.pages}
    missing = 0
    for item in parsed.items:
        page_text = native.get(item.page, "")
        if item.type != "table" or len(page_text) < MIN_NATIVE_CHARS:
            continue
        for number in _NUMBER.findall(item.text):
            if len(re.sub(r"\D", "", number)) >= 3 and number not in page_text:
                missing += 1
    return missing
