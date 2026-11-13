"""Documentos do usuário autenticado: upload, consulta, correção de metadados e remoção."""
import logging
from pathlib import PurePath
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from fastapi.concurrency import run_in_threadpool

from app import repo, storage
from app.api.deps import PublisherDep, SessionDep, SettingsDep, StoreDep
from app.models import Document, Job
from app.schemas import (
    ChunkOut,
    ChunkPage,
    DocumentOut,
    DocumentPatch,
    IndexRequested,
    JobOut,
    ParseRequested,
    UploadAccepted,
)
from app.security import CurrentUser

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/documents", tags=["Documentos"])

_OVERRIDE_FIELDS = ("company", "period", "doc_type")


async def _require_document(session, user_id: str, doc_id: str) -> Document:
    document = await repo.get_document(session, user_id, doc_id)
    if document is None:
        # 404 também para documento de outro usuário: não confirma que o id existe.
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Documento não encontrado.")
    return document


async def publish_or_fail(publisher, topic: str, message, session, job_id: str, stage: str) -> None:
    """Publica o job; se o Kafka recusar, o job fica `failed` em vez de "na fila" sem estar."""
    try:
        await publisher.publish(topic, message.user_id, message)
    except Exception as exc:
        log.exception("Falha ao publicar em %s", topic)
        await repo.mark_job_failed(session, job_id, stage, f"Fila indisponível: {exc}")
        await session.commit()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Fila de processamento indisponível; tente novamente.") from exc


@router.post("", response_model=UploadAccepted, status_code=status.HTTP_202_ACCEPTED,
             summary="Envia um PDF para indexação")
async def upload_document(
    user: CurrentUser,
    session: SessionDep,
    publisher: PublisherDep,
    settings: SettingsDep,
    file: Annotated[UploadFile, File(description="Demonstração financeira em PDF.")],
    company: Annotated[str | None, Form(description="Opcional: força o nome da empresa.")] = None,
    period: Annotated[str | None, Form(description="Opcional: força o período (ex.: 2022, 3T25).")] = None,
) -> UploadAccepted:
    """Valida o arquivo, grava no armazenamento do usuário e enfileira o processamento.
    Responde na hora; acompanhe por `GET /api/jobs/{job_id}`.

    Reenviar o mesmo arquivo reindexa o mesmo `doc_id` (o parse vem do cache). O PDF é lido
    pelo parser configurado em `PARSER` (veja `GET /health`): Docling, na máquina, ou Azure
    Document Intelligence."""
    if settings.parser_problem:
        # Sem isto o job ficaria na fila para sempre: o parse-worker não sobe sem a configuração.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=settings.parser_problem)
    limit = settings.max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE,
                            detail=f"Arquivo maior que {settings.max_upload_mb} MB.")
    try:
        page_count = await run_in_threadpool(storage.validate_pdf, data)
    except storage.InvalidPdf as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    doc_id = storage.compute_doc_id(data)
    await run_in_threadpool(storage.save_upload, user.id, doc_id, data)
    file_name = PurePath(file.filename or "documento.pdf").name[:255]
    overrides = {k: v.strip() for k, v in (("company", company), ("period", period)) if v and v.strip()}

    document = await repo.get_document(session, user.id, doc_id)
    if document is None:
        document = Document(user_id=user.id, doc_id=doc_id, file_name=file_name, size_bytes=len(data),
                            page_count=page_count, overrides=overrides, metadata_source={})
        session.add(document)
    else:
        document.file_name = file_name
        document.overrides = {**(document.overrides or {}), **overrides}
    document.status = "queued"

    job = Job(user_id=user.id, doc_id=doc_id, kind="full", progress_total=page_count,
              timings={}, usage={})
    session.add(job)
    await session.commit()

    message = ParseRequested(job_id=job.id, user_id=user.id, doc_id=doc_id)
    await publish_or_fail(publisher, settings.kafka_topic_parse, message, session, job.id, "parse")
    return UploadAccepted(doc_id=doc_id, job_id=job.id)


@router.get("", response_model=list[DocumentOut], summary="Lista os documentos do usuário")
async def list_documents(user: CurrentUser, session: SessionDep) -> list[Document]:
    return await repo.list_documents(session, user.id)


@router.get("/{doc_id}", response_model=DocumentOut, summary="Detalhe de um documento")
async def get_document(doc_id: str, user: CurrentUser, session: SessionDep) -> Document:
    return await _require_document(session, user.id, doc_id)


@router.get("/{doc_id}/jobs", response_model=list[JobOut], summary="Histórico de jobs do documento")
async def list_document_jobs(doc_id: str, user: CurrentUser, session: SessionDep) -> list[Job]:
    await _require_document(session, user.id, doc_id)
    return await repo.list_jobs(session, user.id, doc_id)


@router.patch("/{doc_id}", response_model=UploadAccepted, status_code=status.HTTP_202_ACCEPTED,
              summary="Corrige metadados e reindexa")
async def patch_document(doc_id: str, body: DocumentPatch, user: CurrentUser, session: SessionDep,
                         publisher: PublisherDep, settings: SettingsDep) -> UploadAccepted:
    """Empresa, período e tipo entram no cabeçalho de contexto de cada chunk (e portanto nos
    embeddings), então a correção reindexa o documento. O parse é reaproveitado do cache:
    o job entra direto na fila de indexação. String vazia remove a correção manual."""
    document = await _require_document(session, user.id, doc_id)
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Informe ao menos um campo.")
    overrides = dict(document.overrides or {})
    for name in _OVERRIDE_FIELDS:
        if name in changes:
            value = (changes[name] or "").strip()
            if value:
                overrides[name] = value
            else:
                overrides.pop(name, None)
    document.overrides = overrides
    document.status = "queued"

    job = Job(user_id=user.id, doc_id=doc_id, kind="reindex", progress_total=document.page_count or 0,
              timings={}, usage={})
    session.add(job)
    await session.commit()

    message = IndexRequested(job_id=job.id, user_id=user.id, doc_id=doc_id, reason="metadata_changed")
    await publish_or_fail(publisher, settings.kafka_topic_index, message, session, job.id, "index")
    return UploadAccepted(doc_id=doc_id, job_id=job.id)


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Remove um documento")
async def delete_document(doc_id: str, user: CurrentUser, session: SessionDep, store: StoreDep) -> None:
    """Apaga os pontos no Qdrant, os registros no banco e os arquivos em disco."""
    await _require_document(session, user.id, doc_id)
    await store.delete_document(user.id, doc_id)
    await repo.delete_document_rows(session, user.id, doc_id)
    await session.commit()
    await run_in_threadpool(storage.delete_document_files, user.id, doc_id)


@router.get("/{doc_id}/chunks", response_model=ChunkPage, summary="Prévia dos chunks indexados")
async def list_chunks(
    doc_id: str, user: CurrentUser, session: SessionDep, store: StoreDep,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ChunkPage:
    """Lê os chunks direto do Qdrant, em ordem. Serve para conferir o que foi indexado:
    página, seção, unidade e o texto literal."""
    document = await _require_document(session, user.id, doc_id)
    payloads = await store.get_chunks(user.id, doc_id, offset, limit)
    return ChunkPage(doc_id=doc_id, total=document.chunk_count, offset=offset, limit=limit,
                     items=[ChunkOut.model_validate(p) for p in payloads])
