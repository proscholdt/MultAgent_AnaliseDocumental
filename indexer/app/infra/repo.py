"""Acesso a dados compartilhado pela API e pelos workers. Toda consulta filtra por user_id."""
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.modelos import Collection, Document, Job, utcnow


async def get_document(session: AsyncSession, user_id: str, doc_id: str) -> Document | None:
    return await session.get(Document, (user_id, doc_id))


async def list_documents(session: AsyncSession, user_id: str, collection_id: str | None = None) -> list[Document]:
    query = select(Document).where(Document.user_id == user_id)
    if collection_id is not None:
        query = query.where(Document.collection_id == collection_id)
    result = await session.execute(query.order_by(Document.created_at.desc()))
    return list(result.scalars())


async def get_collection(session: AsyncSession, user_id: str, collection_id: str) -> Collection | None:
    collection = await session.get(Collection, collection_id)
    return collection if collection is not None and collection.user_id == user_id else None


async def list_collections(session: AsyncSession, user_id: str) -> list[tuple[Collection, int]]:
    """As coleções do usuário, com o número de documentos de cada uma."""
    counts = (select(Document.collection_id, func.count().label("total")).where(Document.user_id == user_id)
              .group_by(Document.collection_id).subquery())
    result = await session.execute(
        select(Collection, func.coalesce(counts.c.total, 0)).outerjoin(counts, counts.c.collection_id == Collection.id)
        .where(Collection.user_id == user_id).order_by(Collection.created_at))
    return [(collection, int(total)) for collection, total in result.all()]


async def default_collection(session: AsyncSession, user_id: str, dominio: str, nome: str) -> Collection:
    """A coleção de quem envia sem escolher uma: a primeira do domínio padrão, criada na hora."""
    result = await session.execute(select(Collection).where(Collection.user_id == user_id,
                                                            Collection.dominio == dominio)
                                   .order_by(Collection.created_at).limit(1))
    collection = result.scalars().first()
    if collection is None:
        collection = Collection(user_id=user_id, nome=nome, dominio=dominio)
        session.add(collection)
        await session.flush()
    return collection


async def get_job(session: AsyncSession, user_id: str, job_id: str) -> Job | None:
    job = await session.get(Job, job_id)
    # Job de outro usuário é tratado como inexistente.
    return job if job is not None and job.user_id == user_id else None


async def list_jobs(session: AsyncSession, user_id: str, doc_id: str) -> list[Job]:
    result = await session.execute(
        select(Job).where(Job.user_id == user_id, Job.doc_id == doc_id).order_by(Job.created_at.desc())
    )
    return list(result.scalars())


async def delete_document_rows(session: AsyncSession, user_id: str, doc_id: str) -> None:
    await session.execute(delete(Job).where(Job.user_id == user_id, Job.doc_id == doc_id))
    await session.execute(delete(Document).where(Document.user_id == user_id, Document.doc_id == doc_id))


async def update_job(session: AsyncSession, job_id: str, **fields: Any) -> Job | None:
    """Atualiza campos do job. `timings` e `usage` são mesclados, não substituídos."""
    job = await session.get(Job, job_id)
    if job is None:
        return None
    for name, value in fields.items():
        if name in ("timings", "usage") and isinstance(value, dict):
            setattr(job, name, {**(getattr(job, name) or {}), **value})
        else:
            setattr(job, name, value)
    job.updated_at = utcnow()
    return job


async def mark_job_failed(session: AsyncSession, job_id: str, stage: str, error: str) -> Job | None:
    job = await update_job(session, job_id, status="failed", failed_stage=stage,
                           error=error[:4000], finished_at=utcnow())
    if job is not None:
        doc = await get_document(session, job.user_id, job.doc_id)
        if doc is not None:
            doc.status = "failed"
    return job
