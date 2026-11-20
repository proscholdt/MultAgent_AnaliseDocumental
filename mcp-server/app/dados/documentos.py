"""Documentos do usuário como este servidor os enxerga: os metadados e todos os chunks, lidos
do indexador e guardados em memória enquanto o índice daquele documento não muda.

Os portões consultam o chunk exato que o agente citou, e a busca de evidência de "não
encontrado" varre o documento inteiro. Sem este cache, cada conferência seria uma ida ao
indexador.
"""

from app.dados.indexador import IndexerClient
from app.dominio.documento import IndexedDocument, fingerprint
from app.governanca.erros import IndexerNotFound, ToolRejected
from app.governanca.identidade import Caller

_MAX_CACHED = 64


class Documents:
    def __init__(self, indexer: IndexerClient) -> None:
        self._indexer = indexer
        self._cache: dict[tuple[str, str], IndexedDocument] = {}

    async def get(self, caller: Caller, doc_id: str) -> IndexedDocument:
        """O documento, se existe para este usuário e já foi indexado. Os metadados são
        consultados a cada vez (é isso que revela uma reindexação); os chunks, só se mudaram."""
        try:
            meta = await self._indexer.document(caller.user_token, doc_id)
        except IndexerNotFound:
            # O modelo às vezes passa o nome da empresa ou um id truncado: o erro mostra os válidos.
            listed = await self._indexer.documents(caller.user_token, caller.colecao_id)
            raise ToolRejected(
                f"Não existe o documento '{doc_id}' para este usuário. Use um doc_id de listar_documentos.",
                opcoes=[f"{d['doc_id']} ({d.get('company') or d['file_name']})" for d in listed
                        if d.get("status") == "indexed"]) from None
        if caller.colecao_id and meta.get("collection_id") != caller.colecao_id:
            # Outra coleção é outro domínio e outro relatório: para esta execução, ele não existe.
            raise ToolRejected(f"O documento {doc_id} não é da coleção desta execução. Use um doc_id de "
                               "listar_documentos.")
        if meta.get("status") != "indexed":
            raise ToolRejected(f"O documento {doc_id} ainda não está indexado (status: {meta.get('status')}).")
        key, current = (caller.user_id, doc_id), fingerprint(meta)
        cached = self._cache.get(key)
        if cached is not None and cached.fingerprint == current:
            return cached
        chunks = await self._indexer.chunks(caller.user_token, doc_id)
        document = IndexedDocument(
            doc_id=doc_id, file_name=meta["file_name"], company=meta.get("company"), period=meta.get("period"),
            doc_type=meta.get("doc_type"), page_count=meta.get("page_count"),
            metadata_source=meta.get("metadata_source") or {}, fingerprint=current,
            chunks=tuple(sorted(chunks, key=lambda chunk: chunk["chunk_index"])))
        if len(self._cache) >= _MAX_CACHED:
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = document
        return document
