"""OCR dos recortes de figura. Só o parse-worker importa este módulo (RapidOCR/onnxruntime).

Gráfico colado no PDF como imagem não tem os rótulos na camada de texto. O OCR lê o recorte e
guarda cada trecho com a sua posição em `ParsedItem.ocr_words`. Na indexação isso serve para
conferir o que o modelo de visão transcreveu: o texto confirma números e rótulos, e a posição
confirma qual rótulo pertence a qual número (ver figures.py).
"""
import logging
from pathlib import Path

import numpy as np
from PIL import Image

from app.schemas import OcrWord, ParsedDocument

log = logging.getLogger(__name__)


class FigureOcr:
    """Mantém o motor de OCR carregado: os modelos sobem uma vez por processo."""

    def __init__(self) -> None:
        self._engine = None

    def warmup(self) -> None:
        """Carrega os modelos e roda uma leitura em branco, para falhar na subida e não no job."""
        self._load()(np.full((64, 256, 3), 255, dtype=np.uint8))

    def _load(self):
        if self._engine is None:
            from rapidocr import RapidOCR

            self._engine = RapidOCR()
        return self._engine

    def read(self, image_path: Path) -> list[OcrWord]:
        """Trechos de texto lidos na imagem, cada um com a caixa que o contém."""
        with Image.open(image_path) as image:
            pixels = np.asarray(image.convert("RGB"))
        result = self._load()(pixels)
        texts = result.txts or []
        boxes = result.boxes if result.boxes is not None else []
        words: list[OcrWord] = []
        for text, box in zip(texts, boxes, strict=False):
            xs = [float(point[0]) for point in box]
            ys = [float(point[1]) for point in box]
            words.append(OcrWord(text=text, x0=min(xs), y0=min(ys), x1=max(xs), y1=max(ys)))
        return words


def read_figures(parsed: ParsedDocument, doc_dir: Path, ocr: FigureOcr, min_area_ratio: float) -> int:
    """Preenche `ocr_words` das figuras candidatas a transcrição que ainda não foram lidas.

    Idempotente: figura já lida não é lida de novo. Serve tanto para o parse novo quanto para
    completar um parsed.json em cache feito antes de o OCR (ou as posições) existir. Devolve
    quantas leu.
    """
    done = 0
    for item in parsed.items:
        if (item.type != "figure" or not item.image_path or item.ocr_words is not None
                or (item.area_ratio or 0) < min_area_ratio):
            continue
        try:
            item.ocr_words = ocr.read(doc_dir / item.image_path)
        except Exception as exc:  # noqa: BLE001 - sem OCR a figura só perde a segunda conferência
            log.warning("OCR falhou em %s: %s", item.image_path, exc)
            item.ocr_words = []
        done += 1
    return done
