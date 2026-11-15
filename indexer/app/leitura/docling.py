"""Parser local: PDF → ParsedDocument com Docling (PARSER=docling). Só o parse-worker importa
este módulo, e só quando esse é o parser escolhido (ver parsing.py).

O Docling entrega os elementos na ordem de leitura, com rótulo (título, parágrafo,
tabela, figura) e página. Aqui eles são convertidos para o esquema próprio do projeto,
para que as etapas seguintes não dependam do Docling.
"""
import gc
import logging
import os
from importlib.metadata import version
from itertools import pairwise
from pathlib import Path

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    AcceleratorDevice,
    AcceleratorOptions,
    PdfPipelineOptions,
    TableFormerMode,
)
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc import (
    DocItemLabel,
    DoclingDocument,
    ListItem,
    PictureItem,
    SectionHeaderItem,
    TableData,
    TableItem,
    TextItem,
)

from app.config import PIPELINE_VERSION, Settings
from app.leitura.ocr import FigureOcr, read_figures
from app.leitura.parsing import MIN_NATIVE_CHARS, ProgressCallback, check_system_fonts, native_pages
from app.schemas import ParsedDocument, ParsedItem

log = logging.getLogger(__name__)

_HEADING_LABELS = {DocItemLabel.TITLE, DocItemLabel.SECTION_HEADER}
_SKIP_LABELS = {DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER, DocItemLabel.CAPTION}
# Correção de cabeçalho de tabela (ver fix_header_spans e merge_split_cells), em pontos do PDF.
_SPAN_MAX_WIDTH = 1.5  # texto mais estreito que isto, em colunas, não cobre duas colunas
_WORD_GAP_MAX = 8.0  # espaço entre duas palavras de um mesmo texto


class DoclingParser:
    """Mantém os conversores carregados: os modelos sobem uma vez por processo, não por PDF."""

    name = "docling"
    page_price_usd = 0.0  # roda na máquina: não há custo por página

    def __init__(self, settings: Settings) -> None:
        check_system_fonts()  # o modelo de layout trabalha sobre a página renderizada
        self._settings = settings
        self._converters: dict[bool, DocumentConverter] = {}
        self._ocr = FigureOcr()

    def _converter(self, ocr: bool) -> DocumentConverter:
        if ocr not in self._converters:
            options = PdfPipelineOptions()
            # Os PDFs têm texto nativo; OCR só é ligado para documento com página escaneada.
            options.do_ocr = ocr
            options.do_table_structure = True
            options.table_structure_options.mode = (
                TableFormerMode.ACCURATE if self._settings.table_mode == "accurate" else TableFormerMode.FAST
            )
            options.table_structure_options.do_cell_matching = True
            options.generate_picture_images = True  # recortes para a transcrição de gráficos
            options.images_scale = 2.0
            options.accelerator_options = AcceleratorOptions(
                num_threads=self._settings.parse_threads or os.cpu_count() or 4,
                device=AcceleratorDevice.AUTO,  # usa GPU se houver
            )
            self._converters[ocr] = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
            )
        return self._converters[ocr]

    def warmup(self) -> None:
        """Carrega os modelos do Docling e do OCR dos recortes antes do primeiro job."""
        self._converter(ocr=False).initialize_pipeline(InputFormat.PDF)
        self._ocr.warmup()

    def read_figures(self, parsed: ParsedDocument, out_dir: Path) -> int:
        """OCR (RapidOCR) dos recortes de gráfico: o Docling não devolve o texto de dentro das
        figuras. Também completa um parsed.json em cache feito antes de o OCR existir."""
        return read_figures(parsed, out_dir, self._ocr, self._settings.figure_min_area_ratio)

    def parse(self, pdf_path: Path, doc_id: str, file_name: str, out_dir: Path,
              on_progress: ProgressCallback | None = None) -> ParsedDocument:
        pages = native_pages(pdf_path)
        ocr_pages = [p.page for p in pages if len(p.native_text.strip()) < MIN_NATIVE_CHARS]
        converter = self._converter(ocr=bool(ocr_pages))
        total = len(pages)
        batch = max(1, self._settings.parse_page_batch)

        # Em lotes de páginas: o Docling mantém as páginas renderizadas até o fim de cada
        # conversão, então converter o PDF inteiro de uma vez faz a memória crescer com o
        # número de páginas. O layout é detectado página a página, o lote não muda o resultado.
        items: list[ParsedItem] = []
        figure_count = 0
        for start in range(1, total + 1, batch):
            end = min(start + batch - 1, total)

            def callback(event, done_before: int = start - 1) -> None:  # progresso do Docling
                if on_progress and getattr(event, "kind", None) == "page_completed":
                    on_progress(min(done_before + event.completed_pages, total), total)

            result = converter.convert(pdf_path, page_range=(start, end), progress_callback=callback)
            batch_items, figure_count = _convert_items(result.document, out_dir, figure_count)
            items.extend(batch_items)
            del result
            gc.collect()
            if on_progress:
                on_progress(end, total)
        return ParsedDocument(
            doc_id=doc_id,
            file_name=file_name,
            page_count=len(pages),
            parser=f"docling {version('docling')}",
            pipeline_version=PIPELINE_VERSION,
            items=items,
            pages=pages,
            ocr_pages=ocr_pages,
        )


def _item_text(node: TextItem) -> str:
    """Texto do item, com o marcador de lista numerada de volta.

    O Docling separa o marcador ("3.1", "b)") do texto do item de lista. Título de nota que o
    layout rotula como item de lista perderia a numeração, e é ela que define a seção.
    """
    if isinstance(node, ListItem) and node.enumerated:
        marker = (node.marker or "").strip()
        if marker and not node.text.lstrip().startswith(marker):
            return f"{marker} {node.text.strip()}"
    return node.text


def fix_header_spans(data: TableData) -> int:
    """Corrige célula de cabeçalho que o modelo de tabelas estendeu por colunas demais.

    Na DRE da Brasal o cabeçalho tem três linhas: "Controladora | Consolidado", depois
    "2022 | 2021 | 2022 | 2021" e, só sob as colunas de 2021, "(não auditado)". O modelo às
    vezes dá a essa última célula a largura de duas colunas, e o cabeçalho passa a dizer que
    2022 também não foi auditado.

    Só é candidata a célula que está na linha logo *abaixo* de cabeçalhos de uma coluna só:
    cabeçalho de grupo ("Controladora") fica acima das colunas que agrupa, nunca abaixo. (Um
    segundo bloco no meio da tabela, com o seu próprio "Consolidado", tem linhas de dados logo
    acima, não cabeçalhos, e por isso não é candidato.) A célula é reduzida a uma coluna quando
    o texto é estreito demais para cobrir duas e está escrito em cima de uma delas. Devolve
    quantas mudou."""
    headers = [cell for cell in data.table_cells if cell.column_header and cell.bbox is not None]
    changed = 0
    for cell in headers:
        first, last = cell.start_col_offset_idx, cell.end_col_offset_idx
        if last - first < 2:
            continue
        # Referência: os cabeçalhos de uma coluna só na linha de cima, dentro da largura da célula.
        columns: dict[int, float] = {}
        for other in headers:
            column = other.start_col_offset_idx
            if (other.end_col_offset_idx - column == 1 and first <= column < last
                    and other.end_row_offset_idx == cell.start_row_offset_idx and column not in columns):
                columns[column] = _center(other)
        if len(columns) != last - first:
            continue
        pitch = min(b - a for a, b in pairwise(sorted(columns.values())))
        column, distance = min(((col, abs(_center(cell) - x)) for col, x in columns.items()),
                               key=lambda pair: pair[1])
        if cell.bbox.r - cell.bbox.l < _SPAN_MAX_WIDTH * pitch and distance < pitch / 2:
            cell.start_col_offset_idx, cell.end_col_offset_idx, cell.col_span = column, column + 1, 1
            changed += 1
    return changed


def merge_split_cells(data: TableData) -> int:
    """Junta um texto que o modelo de tabelas partiu entre duas colunas vizinhas.

    "(não auditado)", escrito sobre a coluna de 2021, às vezes sai como "(não" na coluna de
    2022 e "auditado)" na de 2021. O sinal é estreito de propósito: uma célula que é só o
    começo de um parêntese ("(não"), a célula ao lado, na mesma linha, que é só o fim dele
    ("auditado)"), e um espaço de palavra entre as duas. Um formulário com várias opções
    "( ) direção ( X ) gerências" na mesma linha não se encaixa e fica como está. O texto
    inteiro vai para a coluna sobre a qual ele está centrado. Devolve quantos juntou."""
    cells = [cell for cell in data.table_cells if cell.bbox is not None
             and cell.end_col_offset_idx - cell.start_col_offset_idx == 1]
    merged = 0
    for left in cells:
        opening = left.text.strip()
        if not opening.startswith("(") or ")" in opening:
            continue
        for right in cells:
            closing = right.text.strip()
            if (right.start_row_offset_idx != left.start_row_offset_idx
                    or right.start_col_offset_idx != left.end_col_offset_idx
                    or not closing.endswith(")") or "(" in closing
                    or not 0 <= right.bbox.l - left.bbox.r <= _WORD_GAP_MAX):
                continue
            centers = {}
            for side in (left, right):
                others = [_center(c) for c in cells if c is not side and c.text.strip()
                          and c.start_col_offset_idx == side.start_col_offset_idx
                          and c.start_row_offset_idx != side.start_row_offset_idx]
                centers[id(side)] = sorted(others)[len(others) // 2] if others else _center(side)
            middle = (left.bbox.l + right.bbox.r) / 2
            keep, drop = ((left, right) if abs(middle - centers[id(left)]) < abs(middle - centers[id(right)])
                          else (right, left))
            keep.text = f"{left.text.strip()} {right.text.strip()}"
            keep.bbox.l, keep.bbox.r = left.bbox.l, right.bbox.r
            drop.text = ""
            merged += 1
            break
    return merged


def _center(cell) -> float:
    return (cell.bbox.l + cell.bbox.r) / 2


def _page_span(prov: list) -> tuple[int, int | None]:
    """Página em que o item começa e, se ele atravessa a quebra, a página em que termina.

    O Docling junta num item só o parágrafo que começa numa página e continua na seguinte, com
    uma procedência por página. Guardar só a primeira faria um trecho da página 28 ser citado
    como página 27."""
    first = prov[0].page_no
    last = max(entry.page_no for entry in prov)
    return first, (last if last > first else None)


def _convert_items(document: DoclingDocument, out_dir: Path,
                   figure_count: int = 0) -> tuple[list[ParsedItem], int]:
    """Itens do documento (ou de um lote de páginas) no esquema próprio. `figure_count` é o
    total de figuras já gravadas nos lotes anteriores; devolve o total atualizado."""
    figures_dir = out_dir / "figures"
    items: list[ParsedItem] = []

    for node, _depth in document.iterate_items():
        prov = getattr(node, "prov", None)
        if not prov:
            continue
        page_no, page_end = _page_span(prov)
        label = getattr(node, "label", None)

        if isinstance(node, TableItem):
            merge_split_cells(node.data)
            fix_header_spans(node.data)
            markdown = node.export_to_markdown(doc=document)
            if markdown.strip():
                items.append(ParsedItem(type="table", text=markdown, page=page_no))

        elif isinstance(node, PictureItem):
            image = node.get_image(document)
            if image is None:
                continue
            figure_count += 1
            figures_dir.mkdir(parents=True, exist_ok=True)
            relative = f"figures/p{page_no:04d}_{figure_count:03d}.png"
            image.save(out_dir / relative, format="PNG")
            page = document.pages.get(page_no)
            page_area = (page.size.width * page.size.height) if page else 0
            bbox = prov[0].bbox
            area_ratio = round((bbox.width * bbox.height) / page_area, 4) if page_area else None
            items.append(ParsedItem(type="figure", text=node.caption_text(document) or "",
                                    page=page_no, image_path=relative, area_ratio=area_ratio))

        elif isinstance(node, TextItem):
            if label in _SKIP_LABELS or not node.text.strip():
                continue
            if label in _HEADING_LABELS:
                level = node.level if isinstance(node, SectionHeaderItem) else 1
                items.append(ParsedItem(type="heading", text=node.text, page=page_no, level=level))
            else:
                items.append(ParsedItem(type="text", text=_item_text(node), page=page_no, page_end=page_end))
    return items, figure_count
