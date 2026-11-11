"""Dependências compartilhadas pelas rotas. Os testes trocam o que está em app.state."""
import asyncio
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import get_session
from app.messaging import Publisher
from app.pipeline.embedder import (
    DenseEmbedder,
    SparseEmbedder,
    build_dense_embedder,
    build_sparse_embedder,
)
from app.pipeline.vector_store import VectorStore

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_publisher(request: Request) -> Publisher:
    return request.app.state.publisher


def get_store(request: Request) -> VectorStore:
    return request.app.state.store


async def get_embedders(request: Request, settings: SettingsDep) -> tuple[DenseEmbedder, SparseEmbedder]:
    """Embedders da consulta, criados no primeiro uso (a API não precisa deles para indexar)."""
    state = request.app.state
    if getattr(state, "embedders", None) is None:
        try:
            dense = build_dense_embedder(settings)
            sparse = await asyncio.to_thread(build_sparse_embedder, settings)
        except RuntimeError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        state.embedders = (dense, sparse)
    return state.embedders


PublisherDep = Annotated[Publisher, Depends(get_publisher)]
StoreDep = Annotated[VectorStore, Depends(get_store)]
EmbeddersDep = Annotated[tuple[DenseEmbedder, SparseEmbedder], Depends(get_embedders)]
