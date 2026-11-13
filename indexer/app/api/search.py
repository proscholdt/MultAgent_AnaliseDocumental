"""Busca híbrida: denso + BM25, fundidos por RRF. É por aqui que o sistema dos agentes lê o
índice (pelo servidor MCP), e também serve para conferir o índice pelo Swagger."""
import asyncio

from fastapi import APIRouter

from app.api.deps import EmbeddersDep, StoreDep
from app.schemas import SearchHit, SearchRequest, SearchResponse
from app.security import CurrentUser

router = APIRouter(prefix="/api/search", tags=["Busca"])


@router.post("", response_model=SearchResponse, summary="Busca híbrida (denso + BM25, RRF)")
async def search(body: SearchRequest, user: CurrentUser, store: StoreDep,
                 embedders: EmbeddersDep) -> SearchResponse:
    """Só retorna chunks dos documentos do usuário autenticado. Os filtros (documento, tipo de
    chunk e intervalo de páginas) são aplicados antes da busca, nos dois vetores."""
    dense, sparse = embedders
    (vectors, _), sparse_vector = await asyncio.gather(
        dense.embed([body.query]),
        asyncio.to_thread(sparse.embed_query, body.query),
    )
    results = await store.hybrid_search(
        user.id, vectors[0], sparse_vector, body.limit, body.doc_id,
        chunk_type=body.chunk_type, page_from=body.page_from, page_to=body.page_to)
    fields = [name for name in SearchHit.model_fields if name != "score"]
    hits = [SearchHit(score=score, **{name: payload[name] for name in fields if payload.get(name) is not None})
            for score, payload in results]
    return SearchResponse(query=body.query, hits=hits)
