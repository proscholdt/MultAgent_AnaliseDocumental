"""PDF → ParsedDocument com Docling. Só o parse-worker importa este módulo.

O Docling entrega os elementos na ordem de leitura, com rótulo (título, parágrafo,
tabela, figura) e página. Aqui eles são convertidos para o esquema próprio do projeto,
para que as etapas seguintes não dependam do Docling.
"""
import logging
import os
from collections.abc import Callable
from importlib.metadata import version
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
    PictureItem,
    SectionHeaderItem,
    TableItem,
    TextItem,
)
from pypdf import PdfReader

from app.config import PIPELINE_VERSION, Settings
from app.schemas import ParsedDocument, ParsedItem, ParsedPage

log = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], None]  # (páginas concluídas, total)

# Página com menos caracteres que isto é tratada como escaneada e passa por OCR.
_MIN_NATIVE_CHARS = 20
_HEADING_LABELS = {DocItemLabel.TITLE, DocItemLabel.SECTION_HEADER}
_SKIP_LABELS = {DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER, DocItemLabel.CAPTION}


class PdfParser:
    """Mantém os conversores carregados: os modelos sobem uma vez por processo, não por PDF."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._converters: dict[bool, DocumentConverter] = {}

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
        """Carrega os modelos antes do primeiro job."""
        self._converter(ocr=False).initialize_pipeline(InputFormat.PDF)

    def parse(self, pdf_path: Path, doc_id: str, file_name: str, out_dir: Path,
              on_progress: ProgressCallback | None = None) -> ParsedDocument:
        pages = _native_pages(pdf_path)
        ocr_pages = [p.page for p in pages if len(p.native_text.strip()) < _MIN_NATIVE_CHARS]

        def callback(event) -> None:  # eventos de progresso do Docling
            if on_progress and getattr(event, "kind", None) == "page_completed":
                on_progress(event.completed_pages, event.total_pages)

        result = self._converter(ocr=bool(ocr_pages)).convert(pdf_path, progress_callback=callback)
        items = _convert_items(result.document, out_dir)
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


def _native_pages(pdf_path: Path) -> list[ParsedPage]:
    """Camada de texto de cada página, via pypdf. Inclui os rótulos de gráficos vetoriais,
    que o Docling omite por estarem dentro de uma região de figura."""
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


def _convert_items(document: DoclingDocument, out_dir: Path) -> list[ParsedItem]:
    figures_dir = out_dir / "figures"
    items: list[ParsedItem] = []
    figure_count = 0

    for node, _depth in document.iterate_items():
        prov = getattr(node, "prov", None)
        if not prov:
            continue
        page_no = prov[0].page_no
        label = getattr(node, "label", None)

        if isinstance(node, TableItem):
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
                items.append(ParsedItem(type="text", text=node.text, page=page_no))
    return items
