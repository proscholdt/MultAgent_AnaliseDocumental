"""Tabelas do servidor MCP. A chave de tudo é usuário + documento: o doc_id é o hash do
arquivo e se repete quando dois usuários enviam o mesmo PDF."""
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def now() -> datetime:
    return datetime.now(UTC)


class AuditRow(Base):
    """Uma linha por listagem ou chamada de tool. Só é acrescentada, nunca alterada."""

    __tablename__ = "auditoria"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    run_id: Mapped[str] = mapped_column(String(64))
    agent: Mapped[str] = mapped_column(String(40))
    user_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(8))  # "lista" | "chamada"
    tool: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # ok | recusada (política ou limite) | invalida (argumentos) | rejeitada (portão) | erro
    outcome: Mapped[str] = mapped_column(String(12))
    detail: Mapped[str] = mapped_column(Text, default="")
    arguments: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    policy_digest: Mapped[str] = mapped_column(String(16))

    __table_args__ = (Index("ix_auditoria_run_agent", "run_id", "agent"),)
