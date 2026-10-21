"""Create durable support-copilot conversation history tables."""

import sqlalchemy as sa

from alembic import op

revision = "20261019_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
    )
    op.create_index("ix_conversations_status", "conversations", ["status"])
    op.create_table(
        "messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(36),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "diagnosis_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(36),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("intent", sa.String(40), nullable=False),
        sa.Column("route_taken", sa.String(40), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("needs_escalation", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_diagnosis_runs_conversation_created",
        "diagnosis_runs",
        ["conversation_id", "created_at"],
    )
    op.create_index("ix_diagnosis_runs_request_id", "diagnosis_runs", ["request_id"])
    op.create_table(
        "tool_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "diagnosis_run_id",
            sa.String(36),
            sa.ForeignKey("diagnosis_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tool_name", sa.String(80), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("input_summary", sa.JSON(), nullable=False),
        sa.Column("output_summary", sa.JSON(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_type", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_tool_runs_diagnosis_run", "tool_runs", ["diagnosis_run_id"])
    op.create_table(
        "citations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "diagnosis_run_id",
            sa.String(36),
            sa.ForeignKey("diagnosis_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("document_id", sa.String(255), nullable=False),
        sa.Column("source", sa.String(500), nullable=False),
        sa.Column("topic", sa.String(255), nullable=False),
        sa.Column("version", sa.String(100), nullable=False),
    )
    op.create_index("ix_citations_diagnosis_run", "citations", ["diagnosis_run_id"])
    op.create_table(
        "escalations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "diagnosis_run_id",
            sa.String(36),
            sa.ForeignKey("diagnosis_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_escalations_diagnosis_run", "escalations", ["diagnosis_run_id"])


def downgrade() -> None:
    op.drop_table("escalations")
    op.drop_table("citations")
    op.drop_table("tool_runs")
    op.drop_table("diagnosis_runs")
    op.drop_table("messages")
    op.drop_table("conversations")
