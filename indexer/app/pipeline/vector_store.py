"""Qdrant: coleção com vetor denso + esparso e isolamento por usuário.

O filtro por user_id é montado aqui dentro, em toda leitura e escrita. Nenhuma rota
consegue consultar o índice sem passar um user_id, então não há como "esquecer" o filtro.
"""
import logging
import uuid
from typing import Any

from qdrant_client import AsyncQdrantClient, models

from app.config import Settings
from app.pipeline.embedder import SparseVec

log = logging.getLogger(__name__)

DENSE = "dense"
SPARSE = "bm25"
_NAMESPACE = uuid.UUID("6f1d2c7e-5b0a-4c3e-9d43-2a1f0e8b7c55")
_UPSERT_BATCH = 64


def point_id(user_id: str, doc_id: str, chunk_index: int) -> str:
    """Id determinístico: reindexar o mesmo documento sobrescreve os mesmos pontos."""
    return str(uuid.uuid5(_NAMESPACE, f"{user_id}:{doc_id}:{chunk_index}"))


def _match(key: str, value: Any) -> models.FieldCondition:
    return models.FieldCondition(key=key, match=models.MatchValue(value=value))


def tenant_filter(user_id: str, doc_id: str | None = None) -> models.Filter:
    must = [_match("user_id", user_id)]
    if doc_id is not None:
        must.append(_match("doc_id", doc_id))
    return models.Filter(must=must)


class VectorStore:
    def __init__(self, client: AsyncQdrantClient, settings: Settings) -> None:
        self._client = client
        self._collection = settings.qdrant_collection
        self._dim = settings.embedding_dim

    @classmethod
    def from_settings(cls, settings: Settings) -> "VectorStore":
        client = AsyncQdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key, timeout=60)
        return cls(client, settings)

    async def close(self) -> None:
        await self._client.close()

    async def ping(self) -> bool:
        try:
            await self._client.get_collections()
            return True
        except Exception:  # noqa: BLE001
            return False

    async def ensure_collection(self) -> None:
        if await self._client.collection_exists(self._collection):
            return
        try:
            await self._client.create_collection(
                collection_name=self._collection,
                vectors_config={DENSE: models.VectorParams(size=self._dim, distance=models.Distance.COSINE)},
                # IDF calculado pelo Qdrant sobre a coleção inteira: é o que faz do vetor
                # esparso um BM25 de verdade, e não só frequência de termos.
                sparse_vectors_config={SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)},
            )
        except Exception as exc:  # noqa: BLE001 - API e workers podem criar ao mesmo tempo
            if not await self._client.collection_exists(self._collection):
                raise
            log.info("Coleção já criada por outro processo: %s", exc)
            return
        # is_tenant agrupa os pontos de cada usuário no armazenamento (multi-tenancy do Qdrant).
        await self._client.create_payload_index(
            self._collection, "user_id",
            field_schema=models.KeywordIndexParams(type=models.KeywordIndexType.KEYWORD, is_tenant=True),
        )
        for name in ("doc_id", "company", "chunk_type", "extraction_method"):
            await self._client.create_payload_index(self._collection, name,
                                                    field_schema=models.PayloadSchemaType.KEYWORD)
        await self._client.create_payload_index(self._collection, "page_start",
                                                field_schema=models.PayloadSchemaType.INTEGER)

    async def replace_document(self, user_id: str, doc_id: str, payloads: list[dict[str, Any]],
                               dense: list[list[float]], sparse: list[SparseVec]) -> None:
        """Apaga os pontos antigos do documento e grava os novos. Apagar antes cobre o caso
        de a reindexação gerar menos chunks que a indexação anterior."""
        await self.delete_document(user_id, doc_id)
        points = [
            models.PointStruct(
                id=point_id(user_id, doc_id, payload["chunk_index"]),
                vector={DENSE: dense_vector,
                        SPARSE: models.SparseVector(indices=sparse_vector.indices, values=sparse_vector.values)},
                payload={**payload, "user_id": user_id, "doc_id": doc_id},
            )
            for payload, dense_vector, sparse_vector in zip(payloads, dense, sparse, strict=True)
        ]
        for start in range(0, len(points), _UPSERT_BATCH):
            await self._client.upsert(self._collection, points=points[start:start + _UPSERT_BATCH], wait=True)

    async def delete_document(self, user_id: str, doc_id: str) -> None:
        await self._client.delete(
            self._collection,
            points_selector=models.FilterSelector(filter=tenant_filter(user_id, doc_id)),
            wait=True,
        )

    async def count(self, user_id: str, doc_id: str | None = None) -> int:
        result = await self._client.count(self._collection, count_filter=tenant_filter(user_id, doc_id),
                                          exact=True)
        return result.count

    async def get_chunks(self, user_id: str, doc_id: str, offset: int, limit: int) -> list[dict[str, Any]]:
        """Página de chunks em ordem. Como os ids são determinísticos, basta pedir os ids
        do intervalo — e um id só existe se pertencer a este usuário."""
        ids = [point_id(user_id, doc_id, index) for index in range(offset, offset + limit)]
        records = await self._client.retrieve(self._collection, ids=ids, with_payload=True)
        payloads = [r.payload for r in records if r.payload and r.payload.get("user_id") == user_id]
        return sorted(payloads, key=lambda p: p["chunk_index"])

    async def hybrid_search(self, user_id: str, dense: list[float], sparse: SparseVec, limit: int,
                            doc_id: str | None = None) -> list[tuple[float, dict[str, Any]]]:
        """Busca densa e busca BM25 em paralelo, fundidas por Reciprocal Rank Fusion."""
        scope = tenant_filter(user_id, doc_id)
        candidates = max(limit * 4, 20)
        response = await self._client.query_points(
            self._collection,
            prefetch=[
                models.Prefetch(query=dense, using=DENSE, limit=candidates, filter=scope),
                models.Prefetch(query=models.SparseVector(indices=sparse.indices, values=sparse.values),
                                using=SPARSE, limit=candidates, filter=scope),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            query_filter=scope,
            limit=limit,
            with_payload=True,
        )
        return [(point.score, point.payload or {}) for point in response.points]
