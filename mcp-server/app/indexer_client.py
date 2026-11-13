"""Cliente da API do indexador (sistema 1). É o único caminho deste servidor até os documentos.

Toda chamada leva o JWT do usuário, então quem decide quais documentos existem para a chamada
é o indexador, como já fazia. Este servidor não fala com o Qdrant nem com o Postgres dele.
"""
from typing import Any

import httpx

from app.config import Settings
from app.errors import IndexerAuthError, IndexerError, IndexerNotFound

_PAGE = 100  # máximo de chunks por página na API do indexador


class IndexerClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._http = httpx.AsyncClient(base_url=settings.indexer_url, timeout=settings.indexer_timeout_seconds,
                                       transport=transport)

    async def close(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, path: str, token: str, **kwargs: Any) -> Any:
        try:
            response = await self._http.request(method, path, headers={"Authorization": f"Bearer {token}"},
                                                **kwargs)
        except httpx.HTTPError as exc:
            raise IndexerError(f"Indexador inacessível: {exc}") from exc
        if response.status_code == 401:
            raise IndexerAuthError("O indexador recusou o token do usuário (expirado ou inválido).")
        if response.status_code == 404:
            raise IndexerNotFound("Documento não encontrado para este usuário.")
        if response.status_code >= 400:
            raise IndexerError(f"Indexador respondeu {response.status_code}: {response.text[:200]}")
        return response.json()

    async def ping(self) -> bool:
        try:
            response = await self._http.get("/health")
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def me(self, token: str) -> dict[str, Any]:
        return await self._request("GET", "/auth/me", token)

    async def documents(self, token: str) -> list[dict[str, Any]]:
        return await self._request("GET", "/api/documents", token)

    async def document(self, token: str, doc_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/documents/{doc_id}", token)

    async def search(self, token: str, *, query: str, limit: int, doc_id: str | None = None,
                     chunk_type: str | None = None, page_from: int | None = None,
                     page_to: int | None = None) -> list[dict[str, Any]]:
        body = {"query": query, "limit": limit, "doc_id": doc_id, "chunk_type": chunk_type,
                "page_from": page_from, "page_to": page_to}
        result = await self._request("POST", "/api/search", token,
                                     json={key: value for key, value in body.items() if value is not None})
        return result["hits"]

    async def page_chunks(self, token: str, doc_id: str, page: int) -> list[dict[str, Any]]:
        result = await self._request("GET", f"/api/documents/{doc_id}/chunks", token, params={"page": page})
        return result["items"]

    async def chunks(self, token: str, doc_id: str) -> list[dict[str, Any]]:
        """Todos os chunks do documento, em ordem."""
        items: list[dict[str, Any]] = []
        while True:
            result = await self._request("GET", f"/api/documents/{doc_id}/chunks", token,
                                         params={"offset": len(items), "limit": _PAGE})
            items += result["items"]
            if len(items) >= result["total"] or not result["items"]:
                return items
