"""Etapa de indexação: parsed.json → figuras → metadados → chunks → embeddings → Qdrant.

Não conhece Kafka nem banco; recebe tudo por parâmetro. O index-worker chama esta função
e os testes também, com embedders falsos e Qdrant em memória.
"""
import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import PIPELINE_VERSION, Settings
from app.indexacao.embeddings import DenseEmbedder, SparseEmbedder
from app.indexacao.llm import LLMClient
from app.indexacao.metadados import DocMetadata, DocMetaLLM, llm_metadata, merge_metadata
from app.indexacao.qdrant import VectorStore
from app.indexacao.trechos import Chunk, build_context_header, chunk_document
from app.leitura.figuras import transcribe_figures
from app.schemas import ParsedDocument

StageCallback = Callable[[str], Awaitable[None]]


@dataclass
class IndexResult:
    chunk_count: int
    metadata: DocMetadata
    timings: dict[str, float] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)


async def _load_llm_metadata(parsed: ParsedDocument, cache_path: Path, llm: LLMClient | None) -> DocMetaLLM | None:
    """Resposta do LLM em cache (metadata.json): reindexar não paga a chamada de novo."""
    if cache_path.exists():
        return DocMetaLLM.model_validate_json(cache_path.read_text(encoding="utf-8"))
    if llm is None:
        return None
    result = await llm_metadata(parsed, llm)
    cache_path.write_text(result.model_dump_json(), encoding="utf-8")
    return result


def build_payload(chunk: Chunk, *, file_name: str, meta: DocMetadata, indexed_at: str,
                  parser: str) -> dict[str, Any]:
    # Sem unidade encontrada o campo fica vazio. A unidade predominante do documento
    # (meta.default_unit) é só um resumo para a listagem, não um valor de reserva.
    unit = chunk.unit
    return {
        "file_name": file_name,
        "company": meta.company,
        "period": meta.period,
        "doc_type": meta.doc_type,
        "unit": unit,
        "unit_source": chunk.unit_source,
        "chunk_index": chunk.chunk_index,
        "chunk_type": chunk.chunk_type,
        "extraction_method": chunk.extraction_method,
        "parser": parser,  # "docling" ou "azure": quem leu o PDF
        "verified_ratio": chunk.verified_ratio,
        "unverified_values": chunk.unverified_values,
        "verified_by_ocr": chunk.verified_by_ocr,
        "verified_labels_ratio": chunk.verified_labels_ratio,
        "section_path": chunk.section_path,
        "page_start": chunk.page_start,
        "page_end": chunk.page_end,
        "text": chunk.text,  # conteúdo literal, sem o cabeçalho: é o que o agente cita
        "context_header": build_context_header(meta.company, meta.period, unit, chunk.section_path,
                                               chunk.page_start, chunk.page_end),
        "token_count": chunk.token_count,
        "suspicious": chunk.suspicious,
        "indexed_at": indexed_at,
        "pipeline_version": PIPELINE_VERSION,
    }


async def index_document(
    *,
    user_id: str,
    doc_id: str,
    file_name: str,
    overrides: dict[str, Any],
    parsed: ParsedDocument,
    parser: str,
    doc_dir: Path,
    figures_cache: Path,
    metadata_cache: Path,
    store: VectorStore,
    dense: DenseEmbedder,
    sparse: SparseEmbedder,
    llm: LLMClient | None,
    settings: Settings,
    on_stage: StageCallback | None = None,
) -> IndexResult:
    timings: dict[str, float] = {}

    async def stage(name: str) -> float:
        if on_stage:
            await on_stage(name)
        return time.perf_counter()

    started = await stage("figures")
    figures = await transcribe_figures(parsed, doc_dir, figures_cache, llm, settings)
    timings["figures_s"] = round(time.perf_counter() - started, 2)

    started = await stage("metadata")
    meta = merge_metadata(parsed, overrides, await _load_llm_metadata(parsed, metadata_cache, llm))
    timings["metadata_s"] = round(time.perf_counter() - started, 2)

    started = await stage("chunking")
    chunks = await asyncio.to_thread(chunk_document, parsed, figures, settings)
    indexed_at = datetime.now(UTC).isoformat()
    payloads = [build_payload(c, file_name=file_name, meta=meta, indexed_at=indexed_at, parser=parser)
                for c in chunks]
    # O cabeçalho de contexto entra nos dois embeddings: um chunk de tabela sozinho não diz
    # de qual empresa, período ou nota ele é.
    texts = [f"{payload['context_header']}\n{payload['text']}" for payload in payloads]
    timings["chunking_s"] = round(time.perf_counter() - started, 2)

    started = await stage("embedding")
    dense_vectors, embedding_tokens = await dense.embed(texts)
    sparse_vectors = await asyncio.to_thread(sparse.embed, texts)
    timings["embedding_s"] = round(time.perf_counter() - started, 2)

    started = await stage("upsert")
    await store.replace_document(user_id, doc_id, payloads, dense_vectors, sparse_vectors)
    timings["upsert_s"] = round(time.perf_counter() - started, 2)

    llm_in = llm.input_tokens if llm else 0
    llm_out = llm.output_tokens if llm else 0
    cost = (embedding_tokens * settings.embedding_price_per_1m
            + llm_in * settings.llm_input_price_per_1m
            + llm_out * settings.llm_output_price_per_1m) / 1_000_000
    usage = {
        "embedding_tokens": embedding_tokens,
        "llm_input_tokens": llm_in,
        "llm_output_tokens": llm_out,
        "cost_usd": round(cost, 6),
        "chunks": {kind: sum(1 for c in chunks if c.chunk_type == kind) for kind in ("text", "table", "figure")},
        "figures_transcribed": len(figures),
    }
    return IndexResult(chunk_count=len(chunks), metadata=meta, timings=timings, usage=usage)


def load_parsed(path: Path) -> ParsedDocument | None:
    """parsed.json do cache, se existir e for da versão atual do pipeline."""
    if not path.exists():
        return None
    try:
        parsed = ParsedDocument.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except ValueError:
        return None
    return parsed if parsed.pipeline_version == PIPELINE_VERSION else None
