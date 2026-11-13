"""Parser na nuvem: PDF → ParsedDocument com o Azure AI Document Intelligence (PARSER=azure).

O modelo de layout devolve parágrafos com papel (título, cabeçalho de página...), tabelas com
as células e figuras com a região que ocupam na página. Aqui isso é convertido para o mesmo
ParsedDocument que o Docling produz, de modo que chunking, conferência de gráficos e
indexação não mudam.

Três coisas continuam sendo feitas na máquina:

- o texto nativo de cada página (pypdf), referência exata das conferências, como no Docling;
- o recorte de cada gráfico, renderizando a página com o pypdfium2. O serviço informa só a
  região; a imagem é o que vai para o modelo de visão;
- os textos de dentro de cada gráfico, com posição. Vêm das palavras que o serviço leu na
  página, reagrupadas em trechos. Se o serviço não devolver nenhum número dentro da figura
  (a documentação não diz se ele lê por OCR uma imagem colada num PDF digital) e o OCR local
  estiver instalado, ele lê o recorte.

Este módulo não importa Docling nem torch: roda na imagem leve.
"""
import io
import logging
import math
import re
from collections.abc import Callable
from pathlib import Path
from statistics import median
from typing import Any

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError
from pypdf import PdfReader, PdfWriter

from app.config import PIPELINE_VERSION, Settings
from app.pipeline.parsing import (
    MIN_NATIVE_CHARS,
    ParserError,
    ProgressCallback,
    check_system_fonts,
    native_pages,
)
from app.schemas import OcrWord, ParsedDocument, ParsedItem

log = logging.getLogger(__name__)

Box = tuple[float, float, float, float]  # x0, y0, x1, y1 na unidade da página (polegadas em PDF)
# Recorta a região de uma página e devolve (caminho relativo, pixels por unidade em x, em y,
# origem do recorte em pixels). None se não foi possível renderizar.
Crop = Callable[[int, Box, tuple[float, float]], tuple[str, float, float, tuple[int, int]] | None]

_SKIP_ROLES = {"pageHeader", "pageFooter", "pageNumber"}
_HEADING_ROLES = {"title", "sectionHeading"}
_HEADER_KINDS = {"columnHeader", "stubHead"}
_SELECTION_MARKS = {":selected:": "☒", ":unselected:": "☐"}
# Palavras da mesma linha separadas por mais que isto (em alturas de linha) são textos
# distintos: os rótulos "dez/20" e "mar/21" de um eixo, e não as palavras de "Perdas Totais".
_SEGMENT_GAP = 1.2
_ROW_OVERLAP = 0.5
_HAS_NUMBER = re.compile(r"\d\D*\d")  # dois dígitos: um solto não prova nada (ver figures.py)
# Respostas que não mudam se a chamada for repetida: credencial, endpoint ou documento recusado.
_PERMANENT_STATUS = {400, 401, 403, 404, 413}


# ---------------------------------------------------------------------------
# Geometria e texto
# ---------------------------------------------------------------------------


def _box(polygon: list[float]) -> Box:
    xs, ys = polygon[0::2], polygon[1::2]
    return min(xs), min(ys), max(xs), max(ys)


def _regions(element: dict[str, Any]) -> list[tuple[int, Box]]:
    """(página, caixa) de cada região do elemento. Parágrafo que atravessa a quebra tem duas."""
    return [(region["pageNumber"], _box(region["polygon"]))
            for region in element.get("boundingRegions") or [] if region.get("polygon")]


def _first_offset(element: dict[str, Any]) -> int | None:
    """Posição do elemento no texto corrido do documento: é o que dá a ordem de leitura."""
    spans = element.get("spans") or []
    return min(span["offset"] for span in spans) if spans else None


def _center_in(inner: Box, outer: Box) -> bool:
    x, y = (inner[0] + inner[2]) / 2, (inner[1] + inner[3]) / 2
    return outer[0] <= x <= outer[2] and outer[1] <= y <= outer[3]


def _clean(text: str) -> str:
    for mark, symbol in _SELECTION_MARKS.items():
        text = text.replace(mark, symbol)
    return text.strip()


def _cell_text(text: str) -> str:
    return " ".join(_clean(text).split()).replace("|", "\\|")


# ---------------------------------------------------------------------------
# Tabela → Markdown
# ---------------------------------------------------------------------------


def table_markdown(table: dict[str, Any]) -> str:
    """Tabela do serviço em Markdown, no mesmo formato que o Docling exporta: uma linha de
    cabeçalho por coluna, com os níveis juntados ("Controladora - 2022").

    Célula de cabeçalho que cobre várias colunas é repetida em cada uma, para o nível de cima
    entrar no nome de todas. Célula de dados que cobre várias fica só na primeira posição: um
    número repetido nas outras pareceria vários valores."""
    rows, columns = table.get("rowCount") or 0, table.get("columnCount") or 0
    cells = table.get("cells") or []
    if not rows or not columns or not cells:
        return ""
    grid = [[""] * columns for _ in range(rows)]
    is_header = [[False] * columns for _ in range(rows)]
    for cell in cells:
        row, column = cell.get("rowIndex", 0), cell.get("columnIndex", 0)
        header = cell.get("kind") in _HEADER_KINDS
        text = _cell_text(cell.get("content") or "")
        row_span = (cell.get("rowSpan") or 1) if header else 1
        column_span = (cell.get("columnSpan") or 1) if header else 1
        for r in range(row, min(rows, row + row_span)):
            for c in range(column, min(columns, column + column_span)):
                grid[r][c], is_header[r][c] = text, header

    # Linhas de cabeçalho: as do topo em que tudo o que está preenchido é cabeçalho.
    header_rows = 0
    while (header_rows < rows and any(is_header[header_rows])
           and all(is_header[header_rows][c] or not grid[header_rows][c] for c in range(columns))):
        header_rows += 1
    if header_rows:
        header = [" - ".join(dict.fromkeys(grid[r][c] for r in range(header_rows) if grid[r][c]))
                  for c in range(columns)]
        body = grid[header_rows:]
    else:  # sem cabeçalho reconhecido, a primeira linha faz o papel dele
        header, body = grid[0], grid[1:]
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in body]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Texto de dentro das figuras
# ---------------------------------------------------------------------------


def figure_segments(words: list[tuple[str, Box]], box: Box, scale: tuple[float, float],
                    origin: tuple[int, int]) -> list[OcrWord] | None:
    """Trechos de texto dentro da região da figura, em pixels do recorte.

    O serviço devolve palavra por palavra. A conferência de posição dos gráficos compara
    rótulos inteiros, então palavras vizinhas na mesma linha são juntadas num trecho só
    ("Perdas Totais"), e um espaço largo separa trechos ("dez/20" e "mar/21" num eixo).
    None = o serviço não devolveu nenhuma palavra dentro da figura."""
    inside = [(text, word_box) for text, word_box in words if text.strip() and _center_in(word_box, box)]
    if not inside:
        return None
    rows: list[list[tuple[str, Box]]] = []
    for word in sorted(inside, key=lambda w: (w[1][1] + w[1][3]) / 2):
        for row in rows:
            first = row[0][1]
            shorter = min(first[3] - first[1], word[1][3] - word[1][1])
            overlap = min(first[3], word[1][3]) - max(first[1], word[1][1])
            if shorter > 0 and overlap >= _ROW_OVERLAP * shorter:
                row.append(word)
                break
        else:
            rows.append([word])

    segments: list[OcrWord] = []

    def emit(group: list[tuple[str, Box]]) -> None:
        segments.append(OcrWord(
            text=" ".join(text.strip() for text, _ in group),
            x0=min(b[0] for _, b in group) * scale[0] - origin[0],
            y0=min(b[1] for _, b in group) * scale[1] - origin[1],
            x1=max(b[2] for _, b in group) * scale[0] - origin[0],
            y1=max(b[3] for _, b in group) * scale[1] - origin[1],
        ))

    for row in rows:
        row.sort(key=lambda w: w[1][0])
        # Altura da letra: o menor lado da caixa. Num rótulo escrito na vertical a caixa é alta
        # e estreita, e a altura dela é o comprimento do texto, não o tamanho da letra.
        height = median(min(b[2] - b[0], b[3] - b[1]) for _, b in row)
        group = [row[0]]
        for word in row[1:]:
            if word[1][0] - group[-1][1][2] > _SEGMENT_GAP * height:
                emit(group)
                group = [word]
            else:
                group.append(word)
        emit(group)
    return segments


# ---------------------------------------------------------------------------
# Resposta do serviço → itens na ordem de leitura
# ---------------------------------------------------------------------------


def convert_layout(result: dict[str, Any], *, crop: Crop, min_area_ratio: float) -> list[ParsedItem]:
    """Parágrafos, tabelas e figuras do serviço, na ordem de leitura, no esquema do projeto.

    `result` é a resposta no formato da API REST (chaves em camelCase). `crop` grava o recorte
    de uma figura e é chamado só para as que passam do tamanho mínimo: as menores são
    logotipos e nunca vão para o modelo de visão."""
    pages = {page["pageNumber"]: page for page in result.get("pages") or []}
    tables, figures = result.get("tables") or [], result.get("figures") or []
    paragraphs = result.get("paragraphs") or []

    # O que está dentro de uma tabela ou de uma figura não é texto corrido: é célula ou rótulo.
    containers = [(element.get("spans") or [], _regions(element)) for element in (*tables, *figures)]
    # Legenda de figura vai para o item da figura. Legenda de tabela e notas de rodapé (de
    # tabela ou de figura) ficam no texto, em ordem de leitura: o chunker as junta à tabela.
    figure_captions = {offset for figure in figures
                       if (offset := _first_offset(figure.get("caption") or {})) is not None}
    notes = [table.get("caption") for table in tables]
    notes += [note for element in (*tables, *figures) for note in element.get("footnotes") or []]
    kept_notes = {offset for note in notes if note and (offset := _first_offset(note)) is not None}

    def contained(offset: int, page: int, box: Box | None) -> bool:
        return any(
            any(span["offset"] <= offset < span["offset"] + span["length"] for span in spans)
            or (box is not None
                and any(number == page and _center_in(box, region) for number, region in regions))
            for spans, regions in containers
        )

    def page_of(offset: int) -> int | None:
        """Página de um trecho do texto corrido, para o parágrafo que vier sem região."""
        for number, page in pages.items():
            if any(span["offset"] <= offset < span["offset"] + span["length"]
                   for span in page.get("spans") or []):
                return number
        return None

    blocks: list[tuple[float, int, ParsedItem]] = []  # (posição na leitura, desempate, item)
    tops: dict[int, list[tuple[float, int]]] = {}  # página → (topo, posição) de cada parágrafo

    for paragraph in paragraphs:
        regions, offset = _regions(paragraph), _first_offset(paragraph)
        if offset is None:
            continue
        page, box = regions[0] if regions else (page_of(offset), None)
        if page is None:
            continue
        if box is not None:
            tops.setdefault(page, []).append((box[1], offset))
        role = paragraph.get("role")
        text = _clean(paragraph.get("content") or "")
        if role in _SKIP_ROLES or not text or offset in figure_captions:
            continue
        if offset not in kept_notes and contained(offset, page, box):
            continue
        last = max((number for number, _ in regions), default=page)
        if role in _HEADING_ROLES:
            item = ParsedItem(type="heading", text=text, page=page, level=1)
        else:
            item = ParsedItem(type="text", text=text, page=page, page_end=last if last > page else None)
        blocks.append((offset, len(blocks), item))

    first_on_page = {page: min(offset for _, offset in found) for page, found in tops.items()}
    end = len(result.get("content") or "")

    def position(element: dict[str, Any], page: int, top: float) -> float:
        """Posição na leitura. Figura sem texto não tem posição no texto corrido: fica logo
        antes do primeiro parágrafo que começa abaixo dela na página."""
        offset = _first_offset(element)
        if offset is not None:
            return offset
        below = [found for found_top, found in tops.get(page, []) if found_top >= top]
        if below:
            return min(below) - 0.5
        later = [first_on_page[number] for number in first_on_page if number > page]
        return (min(later) if later else end + page) - 0.5

    for table in tables:
        regions, markdown = _regions(table), table_markdown(table)
        if regions and markdown:
            page, box = regions[0]
            blocks.append((position(table, page, box[1]), len(blocks),
                           ParsedItem(type="table", text=markdown, page=page)))

    for figure in figures:
        regions = _regions(figure)
        if not regions or regions[0][0] not in pages:
            continue
        number, box = regions[0]
        page = pages[number]
        size = (float(page.get("width") or 0), float(page.get("height") or 0))
        area = (box[2] - box[0]) * (box[3] - box[1]) / (size[0] * size[1]) if size[0] and size[1] else 0.0
        item = ParsedItem(type="figure", text=_clean((figure.get("caption") or {}).get("content") or ""),
                          page=number, area_ratio=round(area, 4))
        if area >= min_area_ratio and (cropped := crop(number, box, size)):
            item.image_path, scale_x, scale_y, origin = cropped
            words = [(word.get("content") or "", _box(word["polygon"]))
                     for word in page.get("words") or [] if word.get("polygon")]
            segments = figure_segments(words, box, (scale_x, scale_y), origin)
            # Se o serviço não leu nenhum número dentro da figura, ou ela não tem números ou eles
            # estão numa imagem que ele não leu. Nos dois casos o que veio (um título, por
            # exemplo) não confere valor nenhum: a figura fica para o OCR local, se houver.
            if segments and any(_HAS_NUMBER.search(segment.text) for segment in segments):
                item.ocr_words = segments
        blocks.append((position(figure, number, box[1]), len(blocks), item))

    blocks.sort(key=lambda block: block[:2])
    return [item for _, _, item in blocks]


# ---------------------------------------------------------------------------
# Recorte das figuras e chamada ao serviço
# ---------------------------------------------------------------------------


class _FigureCropper:
    """Renderiza a página com o pypdfium2 e recorta a região de cada figura."""

    def __init__(self, pdf_path: Path, out_dir: Path, scale: float) -> None:
        import pypdfium2

        self._pdf = pypdfium2.PdfDocument(str(pdf_path))
        self._out_dir, self._scale = out_dir, scale
        self._rendered: tuple[int, Any] | None = None  # última página renderizada
        self._count = 0

    def __call__(self, page: int, box: Box, size: tuple[float, float]):
        try:
            if self._rendered is None or self._rendered[0] != page:
                self._rendered = (page, self._pdf[page - 1].render(scale=self._scale).to_pil())
            image = self._rendered[1]
        except Exception as exc:  # noqa: BLE001 - sem o recorte a figura só não é transcrita
            log.warning("Não foi possível renderizar a página %d: %s", page, exc)
            return None
        scale_x, scale_y = image.width / size[0], image.height / size[1]
        left, top = max(0, math.floor(box[0] * scale_x)), max(0, math.floor(box[1] * scale_y))
        right = min(image.width, math.ceil(box[2] * scale_x))
        bottom = min(image.height, math.ceil(box[3] * scale_y))
        if right - left < 2 or bottom - top < 2:
            return None
        self._count += 1
        relative = f"figures/p{page:04d}_{self._count:03d}.png"
        (self._out_dir / "figures").mkdir(parents=True, exist_ok=True)
        image.crop((left, top, right, bottom)).save(self._out_dir / relative, format="PNG")
        return relative, scale_x, scale_y, (left, top)

    def close(self) -> None:
        self._pdf.close()


def pdf_for_upload(pdf_path: Path) -> bytes:
    """Bytes do PDF a enviar. PDF só com restrição de permissões (criptografado com senha de
    abertura vazia, como o da Brasal) é regravado sem a criptografia: o serviço pede que a
    proteção seja removida antes do envio."""
    data = pdf_path.read_bytes()
    reader = PdfReader(io.BytesIO(data))
    if not reader.is_encrypted:
        return data
    reader.decrypt("")
    buffer = io.BytesIO()
    PdfWriter(clone_from=reader).write(buffer)
    return buffer.getvalue()


class AzureParser:
    name = "azure"

    def __init__(self, settings: Settings, client: DocumentIntelligenceClient | None = None) -> None:
        if client is None and not settings.azure_di_configured:
            raise ParserError("O parser da Azure exige AZURE_DI_ENDPOINT e AZURE_DI_KEY no .env.")
        check_system_fonts()  # os recortes de gráfico são renderizados aqui
        self._settings = settings
        self._client = client
        self.page_price_usd = settings.azure_di_price_per_1k_pages / 1000
        self._local_ocr: Any = None  # OCR local, só como reserva; carregado se for preciso

    def warmup(self) -> None:
        """Nada a carregar: os modelos estão no serviço."""

    def _analyze(self, data: bytes) -> dict[str, Any]:
        client = self._client or DocumentIntelligenceClient(
            self._settings.azure_di_endpoint.strip(), AzureKeyCredential(self._settings.azure_di_key.strip()))
        try:
            poller = client.begin_analyze_document(
                self._settings.azure_di_model, body=io.BytesIO(data),
                content_type="application/octet-stream", string_index_type="unicodeCodePoint",
            )
            poller.wait(self._settings.azure_di_timeout_seconds)
            if not poller.done():
                raise TimeoutError(
                    f"A análise no Azure não terminou em {self._settings.azure_di_timeout_seconds} s.")
            return poller.result().as_dict()
        except ClientAuthenticationError as exc:
            raise ParserError(f"O Azure recusou a credencial (AZURE_DI_KEY): {exc.message}") from exc
        except HttpResponseError as exc:
            if exc.status_code in _PERMANENT_STATUS:
                raise ParserError(
                    f"O Azure recusou a requisição ({exc.status_code}). Confira AZURE_DI_ENDPOINT, "
                    f"AZURE_DI_KEY e AZURE_DI_MODEL: {exc.message}") from exc
            raise  # 429, 5xx, rede: vale tentar de novo

    def parse(self, pdf_path: Path, doc_id: str, file_name: str, out_dir: Path,
              on_progress: ProgressCallback | None = None) -> ParsedDocument:
        pages = native_pages(pdf_path)
        total = len(pages)
        if on_progress:
            on_progress(0, total)
        result = self._analyze(pdf_for_upload(pdf_path))
        analyzed = len(result.get("pages") or [])
        if analyzed < total:
            raise ParserError(
                f"O Azure analisou {analyzed} de {total} páginas. O nível gratuito (F0) lê só as "
                "duas primeiras páginas de cada documento; use um recurso no nível S0.")

        cropper = _FigureCropper(pdf_path, out_dir, self._settings.figure_render_scale)
        try:
            items = convert_layout(result, crop=cropper, min_area_ratio=self._settings.figure_min_area_ratio)
        finally:
            cropper.close()
        if on_progress:
            on_progress(total, total)
        return ParsedDocument(
            doc_id=doc_id,
            file_name=file_name,
            page_count=total,
            parser=f"azure-document-intelligence {result.get('modelId') or self._settings.azure_di_model} "
                   f"{result.get('apiVersion') or ''}".strip(),
            pipeline_version=PIPELINE_VERSION,
            items=items,
            pages=pages,
            ocr_pages=[p.page for p in pages if len(p.native_text.strip()) < MIN_NATIVE_CHARS],
        )

    def read_figures(self, parsed: ParsedDocument, out_dir: Path) -> int:
        """Reserva para figura em que o serviço não devolveu nenhuma palavra: o OCR local lê o
        recorte, se estiver instalado (imagem do alvo "parse"). Na imagem leve a figura fica
        sem texto de conferência, e os valores dela que não estiverem no texto da página são
        omitidos, como manda a regra dos gráficos."""
        from app.pipeline.figure_ocr import FigureOcr, read_figures  # só numpy e Pillow no import

        pending = [item for item in parsed.items
                   if item.type == "figure" and item.image_path and item.ocr_words is None]
        if not pending:
            return 0
        if self._local_ocr is None:
            ocr = FigureOcr()
            try:
                ocr.warmup()  # é aqui que o RapidOCR é importado
                self._local_ocr = ocr
            except ImportError:
                self._local_ocr = False
        if not self._local_ocr:
            for item in pending:
                item.ocr_words = []
            log.info("%d figura(s) sem texto devolvido pelo Azure e sem OCR local nesta imagem.", len(pending))
            return 0
        return read_figures(parsed, out_dir, self._local_ocr, self._settings.figure_min_area_ratio)
