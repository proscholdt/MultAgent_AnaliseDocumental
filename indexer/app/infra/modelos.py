"""Tabelas: usuários, documentos e jobs. Chaves em texto (UUID) para rodar igual em Postgres e SQLite."""
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app import pacotes


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Collection(Base):
    """Uma coleção de documentos do mesmo domínio (dominios/<id>/): relatórios e comparações
    acontecem dentro dela. O domínio é escolhido na criação e não muda."""

    __tablename__ = "collections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    nome: Mapped[str] = mapped_column(String(120))
    dominio: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Document(Base):
    """Um PDF de um usuário. A chave composta garante o isolamento: o mesmo arquivo
    enviado por dois usuários são dois documentos distintos."""

    __tablename__ = "documents"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    file_name: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(Integer)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # A coleção e o domínio dela. Documento de antes das coleções: sem coleção, domínio padrão.
    collection_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    dominio: Mapped[str] = mapped_column(String(40), default=pacotes.PADRAO)
    # Os metadados do pacote do domínio, por nome (ex.: {"paciente": ..., "data_exame": ...}).
    metadados: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # O domínio que o conteúdo sugere e o aviso quando ele não é o da coleção.
    dominio_sugerido: Mapped[str | None] = mapped_column(String(40), nullable=True)
    aviso_dominio: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Os metadados com papel (entidade, recorte, tipo), com os nomes de antes dos pacotes: quem já
    # lia company/period continua lendo, em qualquer domínio.
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    period: Mapped[str | None] = mapped_column(String(64), nullable=True)
    doc_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    default_unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # De onde veio cada metadado: "user" | "llm" | "regex" | None, mais a evidência do LLM.
    metadata_source: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Valores informados pelo usuário (upload ou PATCH); sempre vencem a detecção automática.
    overrides: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|processing|indexed|failed
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    doc_id: Mapped[str] = mapped_column(String(16), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="full")  # full | reindex
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|parsing|indexing|done|failed
    stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    failed_stage: Mapped[str | None] = mapped_column(String(16), nullable=True)  # parse | index
    progress_current: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    timings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # segundos por etapa
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # tokens e custo estimado
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
