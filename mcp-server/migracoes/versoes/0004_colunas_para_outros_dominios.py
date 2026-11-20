"""colunas para outros dominios

Revision ID: 0004
Revises: 0003
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0004'
down_revision: str | None = '0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # O período de um laudo é a data (12/03/2024); o de um documento sem recorte, "documento". O
    # valor de um campo de texto (o foro de um contrato) não cabe em 64 caracteres. Em lote: o
    # SQLite não altera coluna, e o Alembic recria a tabela.
    with op.batch_alter_table('valores') as batch:
        batch.alter_column('periodo', type_=sa.String(length=32), existing_type=sa.String(length=8),
                           existing_nullable=False)
        batch.alter_column('texto_valor', type_=sa.Text(), existing_type=sa.String(length=64),
                           existing_nullable=True)
        batch.alter_column('unidade', type_=sa.String(length=32), existing_type=sa.String(length=16),
                           existing_nullable=True)


def downgrade() -> None:
    with op.batch_alter_table('valores') as batch:
        batch.alter_column('unidade', type_=sa.String(length=16), existing_type=sa.String(length=32),
                           existing_nullable=True)
        batch.alter_column('texto_valor', type_=sa.String(length=64), existing_type=sa.Text(),
                           existing_nullable=True)
        batch.alter_column('periodo', type_=sa.String(length=8), existing_type=sa.String(length=32),
                           existing_nullable=False)
