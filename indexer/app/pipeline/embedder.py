"""Embeddings: denso (semântico, OpenAI) e esparso (palavras-chave, BM25).

Os dois são calculados sobre o mesmo texto (cabeçalho de contexto + conteúdo) e gravados
no mesmo ponto do Qdrant, para a busca híbrida com RRF do sistema do agente.
"""
import hashlib
import math
import re
from dataclasses import dataclass
from typing import Protocol

from openai import AsyncOpenAI

from app.config import Settings


@dataclass
class SparseVec:
    indices: list[int]
    values: list[float]


class DenseEmbedder(Protocol):
    async def embed(self, texts: list[str]) -> tuple[list[list[float]], int]:
        """Devolve os vetores e o total de tokens cobrados."""
        ...


class SparseEmbedder(Protocol):
    def embed(self, texts: list[str]) -> list[SparseVec]: ...

    def embed_query(self, text: str) -> SparseVec: ...


class OpenAIDenseEmbedder:
    def __init__(self, settings: Settings) -> None:
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY não configurada (defina no .env).")
        self._client = AsyncOpenAI(api_key=settings.openai_api_key, max_retries=3, timeout=60)
        self._model = settings.openai_embedding_model
        self._batch = settings.embedding_batch_size

    async def embed(self, texts: list[str]) -> tuple[list[list[float]], int]:
        vectors: list[list[float]] = []
        tokens = 0
        for start in range(0, len(texts), self._batch):
            response = await self._client.embeddings.create(model=self._model,
                                                            input=texts[start:start + self._batch])
            vectors.extend(item.embedding for item in sorted(response.data, key=lambda d: d.index))
            tokens += response.usage.total_tokens
        return vectors, tokens


class Bm25SparseEmbedder:
    """BM25 do FastEmbed, com stemmer e stopwords em português. O IDF não é calculado aqui:
    a coleção do Qdrant usa Modifier.IDF e aplica o IDF global na hora da busca."""

    def __init__(self, settings: Settings) -> None:
        from fastembed import SparseTextEmbedding

        self._model = SparseTextEmbedding(model_name="Qdrant/bm25", language=settings.bm25_language)

    def embed(self, texts: list[str]) -> list[SparseVec]:
        return [SparseVec(e.indices.tolist(), e.values.tolist()) for e in self._model.embed(texts)]

    def embed_query(self, text: str) -> SparseVec:
        embedding = next(iter(self._model.query_embed(text)))
        return SparseVec(embedding.indices.tolist(), embedding.values.tolist())


# ---------------------------------------------------------------------------
# Implementações sem rede: usadas nos testes e com EMBEDDING_PROVIDER=fake.
# ---------------------------------------------------------------------------

_WORD = re.compile(r"\w+", re.UNICODE)


def _token_hash(token: str, modulo: int) -> int:
    return int.from_bytes(hashlib.md5(token.encode()).digest()[:4], "big") % modulo


class FakeDenseEmbedder:
    """Saco de palavras projetado por hash. Não tem semântica real, mas é determinístico e
    aproxima textos com palavras em comum, o que basta para exercitar o pipeline."""

    def __init__(self, dim: int) -> None:
        self._dim = dim

    async def embed(self, texts: list[str]) -> tuple[list[list[float]], int]:
        vectors = []
        for text in texts:
            vector = [0.0] * self._dim
            for token in _WORD.findall(text.lower()):
                vector[_token_hash(token, self._dim)] += 1.0
            norm = math.sqrt(sum(v * v for v in vector)) or 1.0
            vectors.append([v / norm for v in vector])
        return vectors, 0


class FakeSparseEmbedder:
    def embed(self, texts: list[str]) -> list[SparseVec]:
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text: str) -> SparseVec:
        counts: dict[int, float] = {}
        for token in _WORD.findall(text.lower()):
            index = _token_hash(token, 2**31 - 1)
            counts[index] = counts.get(index, 0.0) + 1.0
        return SparseVec(list(counts), list(counts.values()))


def build_dense_embedder(settings: Settings) -> DenseEmbedder:
    if settings.embedding_provider == "fake":
        return FakeDenseEmbedder(settings.embedding_dim)
    return OpenAIDenseEmbedder(settings)


def build_sparse_embedder(settings: Settings) -> SparseEmbedder:
    return Bm25SparseEmbedder(settings)
