"""Add review fields and pending state support to escalations."""

import sqlalchemy as sa

from alembic import op

revision = "20261019_02"
down_revision = "20261019_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "escalations", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("escalations", sa.Column("review_decision", sa.String(30), nullable=True))
    op.add_column("escalations", sa.Column("review_notes", sa.Text(), nullable=True))
    op.execute("UPDATE escalations SET status = 'pending' WHERE status = 'open'")


def downgrade() -> None:
    op.drop_column("escalations", "review_notes")
    op.drop_column("escalations", "review_decision")
    op.drop_column("escalations", "reviewed_at")
