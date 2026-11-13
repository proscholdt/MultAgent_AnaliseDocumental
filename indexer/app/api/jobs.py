"""Acompanhamento e reprocessamento de jobs."""
from fastapi import APIRouter, HTTPException, status

from app import repo, storage
from app.api.deps import PublisherDep, SessionDep, SettingsDep
from app.api.documents import publish_or_fail
from app.models import Job
from app.schemas import IndexRequested, JobOut, ParseRequested
from app.security import CurrentUser

router = APIRouter(prefix="/api/jobs", tags=["Jobs"])


async def _require_job(session, user_id: str, job_id: str) -> Job:
    job = await repo.get_job(session, user_id, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Job não encontrado.")
    return job


@router.get("/{job_id}", response_model=JobOut, summary="Estado de um job")
async def get_job(job_id: str, user: CurrentUser, session: SessionDep) -> Job:
    """`status`: queued → parsing → indexing → done (ou failed). Durante o parse,
    `progress_current`/`progress_total` são páginas. Ao final, `timings` traz os segundos
    por etapa e `usage` os tokens e o custo estimado."""
    return await _require_job(session, user.id, job_id)


@router.post("/{job_id}/retry", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED,
             summary="Reenfileira um job que falhou")
async def retry_job(job_id: str, user: CurrentUser, session: SessionDep, publisher: PublisherDep,
                    settings: SettingsDep) -> Job:
    """Volta o job para a fila da etapa que falhou. Se o parse já estava concluído,
    ele não é refeito."""
    job = await _require_job(session, user.id, job_id)
    if job.status != "failed":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Só é possível reenfileirar job com falha.")
    document = await repo.get_document(session, user.id, job.doc_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Documento não encontrado.")

    parsed_file = storage.parsed_path(user.id, job.doc_id, settings.parser)
    needs_parse = job.failed_stage == "parse" or not parsed_file.exists()
    job.status, job.stage, job.error, job.failed_stage = "queued", None, None, None
    job.attempts, job.finished_at = 0, None
    document.status = "queued"
    await session.commit()

    if needs_parse:
        message = ParseRequested(job_id=job.id, user_id=user.id, doc_id=job.doc_id)
        await publish_or_fail(publisher, settings.kafka_topic_parse, message, session, job.id, "parse")
    else:
        message = IndexRequested(job_id=job.id, user_id=user.id, doc_id=job.doc_id, reason="retry")
        await publish_or_fail(publisher, settings.kafka_topic_index, message, session, job.id, "index")
    return job
