"""Busca híbrida de conferência. A busca "de verdade" é do sistema do agente; esta rota
existe para provar, pelo Swagger, que o índice responde a denso + BM25 fundidos por RRF."""
import asyncio

from fastapi import APIRouter

from app.api.deps import EmbeddersDep, StoreDep
from app.schemas import SearchHit, SearchRequest, SearchResponse
from app.security import CurrentUser

router = APIRouter(prefix="/api/search", tags=["Busca (debug)"])


@router.post("", response_model=SearchResponse, summary="Busca híbrida (denso + BM25, RRF)")
async def search(body: SearchRequest, user: CurrentUser, store: StoreDep,
                 embedders: EmbeddersDep) -> SearchResponse:
    """Só retorna chunks dos documentos do usuário autenticado."""
    dense, sparse = embedders
    (vectors, _), sparse_vector = await asyncio.gather(
        dense.embed([body.query]),
        asyncio.to_thread(sparse.embed_query, body.query),
    )
    results = await store.hybrid_search(user.id, vectors[0], sparse_vector, body.limit, body.doc_id)
    hits = [SearchHit(score=score, **{name: payload.get(name) for name in SearchHit.model_fields
                                      if name != "score"})
            for score, payload in results]
    return SearchResponse(query=body.query, hits=hits)
