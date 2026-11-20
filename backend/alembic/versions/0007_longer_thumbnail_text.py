"""longer thumbnail text (a brief's caption is its whole title)

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0007'
down_revision: str | None = '0006'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('clip_metadata', schema=None) as batch_op:
        batch_op.alter_column('thumbnail_text', existing_type=sa.String(length=64), type_=sa.String(length=255),
                              existing_nullable=False)
    with op.batch_alter_table('thumbnails', schema=None) as batch_op:
        batch_op.alter_column('overlay_text', existing_type=sa.String(length=64), type_=sa.String(length=255),
                              existing_nullable=True)


def downgrade() -> None:
    with op.batch_alter_table('thumbnails', schema=None) as batch_op:
        batch_op.alter_column('overlay_text', existing_type=sa.String(length=255), type_=sa.String(length=64),
                              existing_nullable=True)
    with op.batch_alter_table('clip_metadata', schema=None) as batch_op:
        batch_op.alter_column('thumbnail_text', existing_type=sa.String(length=255), type_=sa.String(length=64),
                              existing_nullable=False)
