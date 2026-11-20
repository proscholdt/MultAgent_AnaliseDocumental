"""respostas e relatorio por colecao

Revision ID: 0003
Revises: 0002
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # As perguntas e o relatório consolidado são de uma coleção; os de antes ficam sem coleção.
    op.add_column('respostas', sa.Column('colecao_id', sa.String(length=64), nullable=True))
    op.add_column('relatorios', sa.Column('colecao_id', sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column('relatorios', 'colecao_id')
    op.drop_column('respostas', 'colecao_id')
