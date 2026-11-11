"""Etapa de indexação de ponta a ponta, com Qdrant em memória e embedders falsos."""
import pytest

from app import db, repo, storage
from app.models import Document, Job, User
from app.pipeline.embedder import FakeDenseEmbedder, FakeSparseEmbedder
from app.pipeline.indexing import index_document
from app.pipeline.vector_store import point_id
from app.schemas import IndexRequested, ParseRequested
from app.workers.index_worker import handle_index
from tests.conftest import FakePublisher

USER_A = "11111111-1111-1111-1111-111111111111"
USER_B = "22222222-2222-2222-2222-222222222222"


async def run_index(store, settings, parsed, user_id, tmp_path, overrides=None):
    doc_dir = tmp_path / user_id
    doc_dir.mkdir(parents=True, exist_ok=True)
    return await index_document(
        user_id=user_id, doc_id=parsed.doc_id, file_name=parsed.file_name, overrides=overrides or {},
        parsed=parsed, doc_dir=doc_dir, figures_cache=doc_dir / "figures.json",
        metadata_cache=doc_dir / "metadata.json", store=store,
        dense=FakeDenseEmbedder(settings.embedding_dim), sparse=FakeSparseEmbedder(), llm=None,
        settings=settings,
    )


async def test_index_writes_dense_and_sparse_with_full_payload(store, settings, parsed_doc, tmp_path):
    result = await run_index(store, settings, parsed_doc, USER_A, tmp_path)
    assert result.chunk_count == await store.count(USER_A, parsed_doc.doc_id) > 0
    assert result.metadata.company == "Empresa Exemplo S.A." and result.metadata.period == "2022"
    assert result.usage["chunks"]["table"] == 1

    record = (await store._client.retrieve(  # noqa: SLF001 - inspeção direta do ponto gravado
        settings.qdrant_collection, ids=[point_id(USER_A, parsed_doc.doc_id, 0)],
        with_payload=True, with_vectors=True))[0]
    assert set(record.vector) == {"dense", "bm25"}
    assert len(record.vector["dense"]) == settings.embedding_dim
    assert record.vector["bm25"].indices

    chunks = await store.get_chunks(USER_A, parsed_doc.doc_id, 0, 50)
    assert [c["chunk_index"] for c in chunks] == list(range(result.chunk_count))
    table = next(c for c in chunks if c["chunk_type"] == "table")
    assert table["user_id"] == USER_A and table["doc_id"] == parsed_doc.doc_id
    assert table["company"] == "Empresa Exemplo S.A." and table["unit"] == "R$ mil"
    assert table["page_start"] == 1 and table["extraction_method"] == "table_model"
    assert table["context_header"] == \
        "[Empresa Exemplo S.A. | 2022 | R$ mil | Demonstrações dos resultados | p. 1]"
    # O texto citável fica literal, sem o cabeçalho de contexto.
    assert not table["text"].startswith("[")


async def test_reindex_is_idempotent_and_applies_overrides(store, settings, parsed_doc, tmp_path):
    first = await run_index(store, settings, parsed_doc, USER_A, tmp_path)
    second = await run_index(store, settings, parsed_doc, USER_A, tmp_path,
                             overrides={"company": "Nome Corrigido S.A."})
    assert await store.count(USER_A, parsed_doc.doc_id) == first.chunk_count == second.chunk_count
    chunks = await store.get_chunks(USER_A, parsed_doc.doc_id, 0, 50)
    assert all(c["company"] == "Nome Corrigido S.A." for c in chunks)
    assert second.metadata.source["company"] == {"source": "user"}


async def test_reindex_with_fewer_chunks_leaves_no_orphans(store, settings, parsed_doc, tmp_path):
    await run_index(store, settings, parsed_doc, USER_A, tmp_path)
    shorter = parsed_doc.model_copy(update={"items": parsed_doc.items[:4]})
    result = await run_index(store, settings, shorter, USER_A, tmp_path)
    assert await store.count(USER_A, parsed_doc.doc_id) == result.chunk_count


async def test_hybrid_search_finds_by_keyword_and_respects_tenant(store, settings, parsed_doc, tmp_path):
    await run_index(store, settings, parsed_doc, USER_A, tmp_path)
    await run_index(store, settings, parsed_doc, USER_B, tmp_path)
    dense, sparse = FakeDenseEmbedder(settings.embedding_dim), FakeSparseEmbedder()
    query = "dívida líquida EBITDA covenants"
    vectors, _ = await dense.embed([query])

    hits = await store.hybrid_search(USER_A, vectors[0], sparse.embed_query(query), limit=3)
    assert hits and "3,32x" in hits[0][1]["text"]
    assert {payload["user_id"] for _, payload in hits} == {USER_A}

    # Usuário sem documentos não recebe nada, mesmo com o mesmo arquivo indexado por outros.
    assert await store.hybrid_search("33333333-3333-3333-3333-333333333333", vectors[0],
                                     sparse.embed_query(query), limit=3) == []
    assert await store.count(USER_A) == await store.count(USER_B)


async def test_delete_only_touches_the_given_user(store, settings, parsed_doc, tmp_path):
    await run_index(store, settings, parsed_doc, USER_A, tmp_path)
    await run_index(store, settings, parsed_doc, USER_B, tmp_path)
    await store.delete_document(USER_A, parsed_doc.doc_id)
    assert await store.count(USER_A) == 0
    assert await store.count(USER_B) > 0
    assert await store.get_chunks(USER_A, parsed_doc.doc_id, 0, 10) == []


# --- index-worker ----------------------------------------------------------


@pytest.fixture
async def queued_job(database, settings, parsed_doc):
    async with db.session_scope() as session:
        session.add(User(id=USER_A, username="ana", password_hash="x"))
        session.add(Document(user_id=USER_A, doc_id=parsed_doc.doc_id, file_name="exemplo.pdf",
                             size_bytes=10, page_count=3, overrides={}, metadata_source={}))
        job = Job(user_id=USER_A, doc_id=parsed_doc.doc_id, timings={}, usage={})
        session.add(job)
    return job.id


async def worker_run(message, store, settings, publisher):
    await handle_index(message, store=store, dense=FakeDenseEmbedder(settings.embedding_dim),
                       sparse=FakeSparseEmbedder(), publisher=publisher, settings=settings)


async def test_index_worker_completes_job_and_emits_event(queued_job, store, settings, parsed_doc):
    path = storage.parsed_path(USER_A, parsed_doc.doc_id)
    path.parent.mkdir(parents=True)
    path.write_text(parsed_doc.model_dump_json(), encoding="utf-8")
    publisher = FakePublisher()

    message = IndexRequested(job_id=queued_job, user_id=USER_A, doc_id=parsed_doc.doc_id)
    await worker_run(message, store, settings, publisher)

    async with db.session_scope() as session:
        job = await repo.get_job(session, USER_A, queued_job)
        document = await repo.get_document(session, USER_A, parsed_doc.doc_id)
        assert job.status == "done" and job.finished_at is not None
        assert {"figures_s", "metadata_s", "chunking_s", "embedding_s", "upsert_s"} <= set(job.timings)
        assert job.usage["cost_usd"] == 0 and job.usage["embedding_tokens"] == 0
        assert document.status == "indexed" and document.company == "Empresa Exemplo S.A."
        assert document.chunk_count == await store.count(USER_A, parsed_doc.doc_id) > 0
        assert document.default_unit == "R$ mil"

    event = publisher.on(settings.kafka_topic_events)[0]
    assert event.type == "document.indexed" and event.chunk_count == document.chunk_count


async def test_index_worker_sends_back_to_parse_when_cache_is_missing(queued_job, store, settings, parsed_doc):
    publisher = FakePublisher()
    message = IndexRequested(job_id=queued_job, user_id=USER_A, doc_id=parsed_doc.doc_id)
    await worker_run(message, store, settings, publisher)
    assert publisher.on(settings.kafka_topic_parse) == [
        ParseRequested(job_id=queued_job, user_id=USER_A, doc_id=parsed_doc.doc_id)]
    assert await store.count(USER_A) == 0


async def test_index_worker_ignores_deleted_document(database, store, settings):
    publisher = FakePublisher()
    await worker_run(IndexRequested(job_id="j", user_id=USER_A, doc_id="0000000000000000"),
                     store, settings, publisher)
    assert publisher.messages == []
