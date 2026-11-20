"""O índice de antes dos domínios plugáveis (coleção financial_docs) continua valendo: a coleção
nova, `documentos`, vira um alias dele, sem reindexar."""
from qdrant_client import AsyncQdrantClient

from app.indexacao.qdrant import LEGACY_COLLECTION, VectorStore


async def test_the_old_index_is_kept_under_the_new_name(settings):
    client = AsyncQdrantClient(location=":memory:")
    old = settings.model_copy(update={"qdrant_collection": LEGACY_COLLECTION})
    await VectorStore(client, old).ensure_collection()  # o índice como era

    await VectorStore(client, settings).ensure_collection()
    aliases = {(a.alias_name, a.collection_name) for a in (await client.get_aliases()).aliases}
    assert ("documentos", LEGACY_COLLECTION) in aliases
    # Nenhuma coleção vazia ao lado da antiga.
    assert [c.name for c in (await client.get_collections()).collections] == [LEGACY_COLLECTION]
    await VectorStore(client, settings).ensure_collection()  # na próxima subida, nada muda


async def test_a_new_index_is_created_with_the_new_name(settings):
    client = AsyncQdrantClient(location=":memory:")
    await VectorStore(client, settings).ensure_collection()
    assert await client.collection_exists("documentos") and not await client.collection_exists(LEGACY_COLLECTION)
