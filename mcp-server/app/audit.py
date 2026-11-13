"""Auditoria: quem chamou o quê, quando e com que resultado."""
from typing import Any

from sqlalchemy import func, select

from app.auth import Caller
from app.db import Database
from app.models import AuditRow

_VALUE_CHARS = 300  # argumento longo (um trecho de documento) é cortado na trilha


def _preview(arguments: dict[str, Any] | None) -> dict[str, Any] | None:
    if arguments is None:
        return None
    return {key: (value[:_VALUE_CHARS] + "…" if isinstance(value, str) and len(value) > _VALUE_CHARS else value)
            for key, value in arguments.items()}


class Audit:
    def __init__(self, database: Database) -> None:
        self._db = database

    async def record(self, caller: Caller, *, kind: str, outcome: str, policy_digest: str,
                     tool: str | None = None, detail: str = "", arguments: dict[str, Any] | None = None,
                     duration_ms: int = 0) -> None:
        async with self._db.session() as session:
            session.add(AuditRow(run_id=caller.run_id, agent=caller.agent, user_id=caller.user_id, kind=kind,
                                 tool=tool, outcome=outcome, detail=detail[:1000], arguments=_preview(arguments),
                                 duration_ms=duration_ms, policy_digest=policy_digest))

    async def calls_in_run(self, run_id: str, agent: str) -> int:
        """Chamadas de tool que o agente já fez nesta execução (qualquer resultado)."""
        async with self._db.session() as session:
            query = select(func.count()).select_from(AuditRow).where(
                AuditRow.run_id == run_id, AuditRow.agent == agent, AuditRow.kind == "chamada")
            return (await session.execute(query)).scalar_one()

    async def rows(self, *, run_id: str | None = None, agent: str | None = None,
                   limit: int = 200) -> list[AuditRow]:
        async with self._db.session() as session:
            query = select(AuditRow).order_by(AuditRow.id.desc()).limit(limit)
            if run_id:
                query = query.where(AuditRow.run_id == run_id)
            if agent:
                query = query.where(AuditRow.agent == agent)
            return list((await session.execute(query)).scalars())
