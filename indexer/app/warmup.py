"""Baixa uma vez os modelos e recursos usados em execução.

    uv run --group parse python -m app.warmup

No Docker isso acontece no build da imagem; este módulo serve para rodar fora dele.
"""
import time

from app.config import get_settings


def main() -> None:
    settings = get_settings()

    started = time.perf_counter()
    import tiktoken

    tiktoken.get_encoding("cl100k_base")
    print(f"tiktoken ok ({time.perf_counter() - started:.1f}s)")

    started = time.perf_counter()
    from app.pipeline.embedder import Bm25SparseEmbedder

    Bm25SparseEmbedder(settings).embed_query("receita líquida")
    print(f"BM25 ({settings.bm25_language}) ok ({time.perf_counter() - started:.1f}s)")

    try:
        from app.pipeline.parser import PdfParser
    except ImportError:
        print("Docling não instalado (grupo 'parse'); pulando os modelos de parse.")
        return
    started = time.perf_counter()
    PdfParser(settings).warmup()
    print(f"modelos do Docling ok ({time.perf_counter() - started:.1f}s)")

    started = time.perf_counter()
    from app.pipeline.figure_ocr import FigureOcr

    FigureOcr().warmup()
    print(f"modelos de OCR ok ({time.perf_counter() - started:.1f}s)")


if __name__ == "__main__":
    main()
