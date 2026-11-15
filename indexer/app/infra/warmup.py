"""Baixa uma vez os modelos e recursos usados em execução.

    uv run --group parse python -m app.infra.warmup

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
    from app.indexacao.embeddings import Bm25SparseEmbedder

    Bm25SparseEmbedder(settings).embed_query("receita líquida")
    print(f"BM25 ({settings.bm25_language}) ok ({time.perf_counter() - started:.1f}s)")

    from app.leitura.parsing import ParserError, build_parser

    started = time.perf_counter()
    try:
        parser = build_parser(settings)
    except ParserError as exc:
        print(f"parser não carregado: {exc}")
        return
    parser.warmup()  # Docling: modelos de layout, tabelas e OCR. Azure: nada a baixar.
    print(f"parser {parser.name} ok ({time.perf_counter() - started:.1f}s)")


if __name__ == "__main__":
    main()
