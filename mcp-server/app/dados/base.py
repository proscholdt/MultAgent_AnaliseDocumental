"""Leitura e gravação da base de dados extraídos. Tudo é filtrado por usuário: o que um
usuário extraiu não existe para outro, mesmo que seja o mesmo PDF."""
import hashlib
import json
from typing import Any

from sqlalchemy import select, update

from app.dados.banco import Database
from app.dados.modelos import (
    Alert,
    AnalysisItem,
    Answer,
    CompanyReport,
    Extraction,
    Report,
    StoredValue,
    Summary,
)
from app.governanca.identidade import Caller
from app.regras.formulas import AlertDraft


class Store:
    def __init__(self, database: Database) -> None:
        self._db = database

    # -- valores e extração --------------------------------------------------------------------

    async def save_value(self, row: StoredValue) -> None:
        """Grava um valor. O que havia para o mesmo campo, período e medida vira histórico."""
        async with self._db.session() as session:
            await session.execute(update(StoredValue).where(
                StoredValue.user_id == row.user_id, StoredValue.doc_id == row.doc_id,
                StoredValue.campo == row.campo, StoredValue.periodo == row.periodo,
                StoredValue.medida == row.medida, StoredValue.ativo.is_(True)).values(ativo=False))
            session.add(row)

    async def drop_values(self, user_id: str, doc_id: str, campo: str) -> None:
        """Desativa tudo o que há de um campo (ao marcá-lo como não encontrado)."""
        async with self._db.session() as session:
            await session.execute(update(StoredValue).where(
                StoredValue.user_id == user_id, StoredValue.doc_id == doc_id, StoredValue.campo == campo,
                StoredValue.ativo.is_(True)).values(ativo=False))

    async def values(self, user_id: str, doc_id: str, fingerprint: str) -> list[StoredValue]:
        """Valores em vigor, lidos com o índice atual. Os de um índice anterior não contam."""
        async with self._db.session() as session:
            query = select(StoredValue).where(
                StoredValue.user_id == user_id, StoredValue.doc_id == doc_id,
                StoredValue.fingerprint == fingerprint, StoredValue.ativo.is_(True)).order_by(StoredValue.id)
            return list((await session.execute(query)).scalars())

    async def publish(self, caller: Caller, doc_id: str, fingerprint: str, payload: dict[str, Any]) -> None:
        async with self._db.session() as session:
            session.add(Extraction(user_id=caller.user_id, doc_id=doc_id, fingerprint=fingerprint,
                                   run_id=caller.run_id, payload=payload))

    async def extraction(self, user_id: str, doc_id: str) -> Extraction | None:
        """A extração publicada mais recente do documento."""
        async with self._db.session() as session:
            query = select(Extraction).where(Extraction.user_id == user_id, Extraction.doc_id == doc_id) \
                .order_by(Extraction.id.desc()).limit(1)
            return (await session.execute(query)).scalar_one_or_none()

    # -- alertas -------------------------------------------------------------------------------

    async def add_alerts(self, caller: Caller, doc_id: str, fingerprint: str, tipo: str,
                         drafts: list[AlertDraft]) -> int:
        """Acrescenta alertas. O mesmo alerta (mesmo código, campo e mensagem) não entra duas
        vezes; nenhum é apagado. Devolve quantos são novos."""
        added = 0
        async with self._db.session() as session:
            known = set((await session.execute(select(Alert.digest).where(
                Alert.user_id == caller.user_id, Alert.doc_id == doc_id))).scalars())
            for draft in drafts:
                digest = hashlib.sha256(json.dumps(
                    [fingerprint, draft.codigo, draft.campo, draft.mensagem], ensure_ascii=False).encode()).hexdigest()[:32]
                if digest in known:
                    continue
                known.add(digest)
                added += 1
                session.add(Alert(user_id=caller.user_id, doc_id=doc_id, fingerprint=fingerprint, run_id=caller.run_id,
                                  agent=caller.agent, tipo=tipo, codigo=draft.codigo, severidade=draft.severidade,
                                  campo=draft.campo, mensagem=draft.mensagem, detalhes=draft.detalhes, digest=digest))
        return added

    async def alerts(self, user_id: str, doc_id: str, fingerprint: str | None = None) -> list[Alert]:
        async with self._db.session() as session:
            query = select(Alert).where(Alert.user_id == user_id, Alert.doc_id == doc_id).order_by(Alert.id)
            if fingerprint is not None:
                query = query.where(Alert.fingerprint == fingerprint)
            return list((await session.execute(query)).scalars())

    # -- análise qualitativa -------------------------------------------------------------------

    async def save_analysis(self, row: AnalysisItem, *, replace_category: bool) -> None:
        """Grava um item. "Não encontrado" substitui o que havia na categoria; um item
        encontrado só substitui um "não encontrado" anterior (uma categoria tem vários itens)."""
        async with self._db.session() as session:
            scope = [AnalysisItem.user_id == row.user_id, AnalysisItem.doc_id == row.doc_id,
                     AnalysisItem.categoria == row.categoria, AnalysisItem.ativo.is_(True)]
            if not replace_category:
                scope.append(AnalysisItem.status == "nao_encontrado")
            await session.execute(update(AnalysisItem).where(*scope).values(ativo=False))
            session.add(row)

    async def analysis(self, user_id: str, doc_id: str, fingerprint: str) -> list[AnalysisItem]:
        async with self._db.session() as session:
            query = select(AnalysisItem).where(
                AnalysisItem.user_id == user_id, AnalysisItem.doc_id == doc_id,
                AnalysisItem.fingerprint == fingerprint, AnalysisItem.ativo.is_(True)).order_by(AnalysisItem.id)
            return list((await session.execute(query)).scalars())

    # -- respostas, resumos e relatório --------------------------------------------------------

    async def save_answer(self, row: Answer) -> None:
        async with self._db.session() as session:
            await session.execute(update(Answer).where(
                Answer.user_id == row.user_id, Answer.pergunta_id == row.pergunta_id,
                Answer.ativo.is_(True)).values(ativo=False))
            session.add(row)

    async def answers(self, user_id: str) -> list[Answer]:
        async with self._db.session() as session:
            query = select(Answer).where(Answer.user_id == user_id, Answer.ativo.is_(True)).order_by(Answer.pergunta_id)
            return list((await session.execute(query)).scalars())

    async def save_summary(self, row: Summary) -> None:
        async with self._db.session() as session:
            await session.execute(update(Summary).where(
                Summary.user_id == row.user_id, Summary.doc_id == row.doc_id,
                Summary.ativo.is_(True)).values(ativo=False))
            session.add(row)

    async def summary(self, user_id: str, doc_id: str, fingerprint: str) -> Summary | None:
        async with self._db.session() as session:
            query = select(Summary).where(Summary.user_id == user_id, Summary.doc_id == doc_id,
                                          Summary.fingerprint == fingerprint, Summary.ativo.is_(True))
            return (await session.execute(query)).scalar_one_or_none()

    async def save_report(self, caller: Caller, markdown: str) -> None:
        async with self._db.session() as session:
            session.add(Report(user_id=caller.user_id, run_id=caller.run_id, markdown=markdown))

    async def report(self, user_id: str) -> Report | None:
        async with self._db.session() as session:
            query = select(Report).where(Report.user_id == user_id).order_by(Report.id.desc()).limit(1)
            return (await session.execute(query)).scalar_one_or_none()

    async def save_company_report(self, caller: Caller, doc_id: str, fingerprint: str, markdown: str) -> None:
        async with self._db.session() as session:
            session.add(CompanyReport(user_id=caller.user_id, doc_id=doc_id, fingerprint=fingerprint,
                                      run_id=caller.run_id, markdown=markdown))

    async def company_report(self, user_id: str, doc_id: str) -> CompanyReport | None:
        async with self._db.session() as session:
            query = (select(CompanyReport).where(CompanyReport.user_id == user_id, CompanyReport.doc_id == doc_id)
                     .order_by(CompanyReport.id.desc()).limit(1))
            return (await session.execute(query)).scalar_one_or_none()
