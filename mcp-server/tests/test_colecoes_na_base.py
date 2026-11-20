"""Respostas e relatório consolidado são de uma coleção: o mesmo usuário com uma coleção de laudos
e outra do financeiro tem duas p1 e dois relatórios, e um não apaga o outro."""
import sqlite3
from pathlib import Path

from sqlalchemy import select

from app.dados.banco import Database
from app.dados.modelos import Answer
from app.governanca.identidade import Caller
from tests.conftest import ANA


def _answer(colecao_id: str | None, texto: str) -> Answer:
    return Answer(user_id=ANA, colecao_id=colecao_id, run_id="run", pergunta_id="p1", pergunta="?",
                  texto=texto, renderizado=texto, citacoes=[])


def _caller(colecao_id: str | None) -> Caller:
    return Caller(agent="redator", user_id=ANA, run_id="run", user_token="t", dominio=None, colecao_id=colecao_id)


async def test_the_answers_of_one_collection_do_not_replace_the_others(deps):
    store = deps.store
    await store.save_answer(_answer("col-financeiro", "Receita da Brasal."))
    await store.save_answer(_answer("col-laudos", "Glicose do João."))
    await store.save_answer(_answer(None, "Antes das coleções."))
    assert [a.texto for a in await store.answers(ANA, "col-financeiro")] == ["Receita da Brasal."]
    assert [a.texto for a in await store.answers(ANA, "col-laudos")] == ["Glicose do João."]
    assert [a.texto for a in await store.answers(ANA, None)] == ["Antes das coleções."]
    # Responder de novo na mesma coleção substitui só a resposta dela.
    await store.save_answer(_answer("col-laudos", "Glicose do João, de novo."))
    assert [a.texto for a in await store.answers(ANA, "col-financeiro")] == ["Receita da Brasal."]
    assert [a.texto for a in await store.answers(ANA, "col-laudos")] == ["Glicose do João, de novo."]


async def test_each_collection_has_its_report(deps):
    store = deps.store
    await store.save_report(_caller("col-financeiro"), "# Demonstrações")
    await store.save_report(_caller("col-laudos"), "# Laudos")
    assert (await store.report(ANA, "col-financeiro")).markdown == "# Demonstrações"
    assert (await store.report(ANA, "col-laudos")).markdown == "# Laudos"
    assert await store.report(ANA, None) is None


async def test_an_old_sqlite_base_gains_the_new_columns(tmp_path: Path):
    path = tmp_path / "antiga.db"
    with sqlite3.connect(path) as old:  # a tabela como era antes das coleções
        old.execute("CREATE TABLE respostas (id INTEGER PRIMARY KEY, user_id VARCHAR(64), run_id VARCHAR(64), "
                    "pergunta_id VARCHAR(40), pergunta TEXT, texto TEXT, renderizado TEXT, citacoes JSON, "
                    "at DATETIME, ativo BOOLEAN)")
        old.execute("INSERT INTO respostas (user_id, run_id, pergunta_id, pergunta, texto, renderizado, citacoes, "
                    "at, ativo) VALUES ('ana', 'r', 'p1', '?', 'antiga', 'antiga', '[]', '2026-01-01', 1)")
    database = Database(f"sqlite+aiosqlite:///{path.as_posix()}")
    await database.init()
    async with database.session() as session:
        row = (await session.execute(select(Answer))).scalar_one()
    await database.close()
    assert row.texto == "antiga" and row.colecao_id is None
