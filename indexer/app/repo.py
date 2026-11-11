"""Acesso a dados compartilhado pela API e pelos workers. Toda consulta filtra por user_id."""
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document, Job, utcnow


async def get_document(session: AsyncSession, user_id: str, doc_id: str) -> Document | None:
    return await session.get(Document, (user_id, doc_id))


async def list_documents(session: AsyncSession, user_id: str) -> list[Document]:
    result = await session.execute(
        select(Document).where(Document.user_id == user_id).order_by(Document.created_at.desc())
    )
    return list(result.scalars())


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
