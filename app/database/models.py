"""Durable, relational audit records for conversations and diagnosis executions."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    status: Mapped[str] = mapped_column(String(30), default="open", nullable=False, index=True)
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )
    diagnosis_runs: Mapped[list["DiagnosisRun"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class DiagnosisRun(Base):
    __tablename__ = "diagnosis_runs"
    __table_args__ = (
        Index("ix_diagnosis_runs_conversation_created", "conversation_id", "created_at"),
        Index("ix_diagnosis_runs_request_id", "request_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    intent: Mapped[str] = mapped_column(String(40), nullable=False)
    route_taken: Mapped[str] = mapped_column(String(40), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    needs_escalation: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    conversation: Mapped[Conversation] = relationship(back_populates="diagnosis_runs")
    tool_runs: Mapped[list["ToolRun"]] = relationship(
        back_populates="diagnosis_run", cascade="all, delete-orphan"
    )
    citations: Mapped[list["Citation"]] = relationship(
        back_populates="diagnosis_run", cascade="all, delete-orphan"
    )
    escalations: Mapped[list["Escalation"]] = relationship(
        back_populates="diagnosis_run", cascade="all, delete-orphan"
    )


class ToolRun(Base):
    __tablename__ = "tool_runs"
    __table_args__ = (Index("ix_tool_runs_diagnosis_run", "diagnosis_run_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    diagnosis_run_id: Mapped[str] = mapped_column(
        ForeignKey("diagnosis_runs.id", ondelete="CASCADE"), nullable=False
    )
    tool_name: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    input_summary: Mapped[dict] = mapped_column(JSON, nullable=False)
    output_summary: Mapped[dict] = mapped_column(JSON, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    diagnosis_run: Mapped[DiagnosisRun] = relationship(back_populates="tool_runs")


class Citation(Base):
    __tablename__ = "citations"
    __table_args__ = (Index("ix_citations_diagnosis_run", "diagnosis_run_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    diagnosis_run_id: Mapped[str] = mapped_column(
        ForeignKey("diagnosis_runs.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(500), nullable=False)
    topic: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(100), nullable=False)
    diagnosis_run: Mapped[DiagnosisRun] = relationship(back_populates="citations")


class Escalation(Base):
    __tablename__ = "escalations"
    __table_args__ = (Index("ix_escalations_diagnosis_run", "diagnosis_run_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    diagnosis_run_id: Mapped[str] = mapped_column(
        ForeignKey("diagnosis_runs.id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_decision: Mapped[str | None] = mapped_column(String(30), nullable=True)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    diagnosis_run: Mapped[DiagnosisRun] = relationship(back_populates="escalations")
