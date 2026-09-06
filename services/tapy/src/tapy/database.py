from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .config import Settings


class Base(DeclarativeBase):
    pass


class Agent(Base):
    __tablename__ = "matcher_agents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    access_token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class MailboxConnection(Base):
    __tablename__ = "matcher_mailboxes"
    __table_args__ = (
        UniqueConstraint("provider", "provider_account_id"),
        UniqueConstraint("agent_id", "provider"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    agent_id: Mapped[str] = mapped_column(ForeignKey("matcher_agents.id"), index=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    provider_account_id: Mapped[str] = mapped_column(String(320), nullable=False)
    email_address: Mapped[str] = mapped_column(String(320), nullable=False)
    refresh_token: Mapped[str] = mapped_column(String(4096), nullable=False)
    scopes: Mapped[str] = mapped_column(String(1000), nullable=False)
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class OAuthState(Base):
    __tablename__ = "matcher_oauth_states"

    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("matcher_agents.id"), index=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProcessedMessage(Base):
    __tablename__ = "matcher_processed_messages"
    __table_args__ = (UniqueConstraint("mailbox_id", "provider_message_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    mailbox_id: Mapped[str] = mapped_column(ForeignKey("matcher_mailboxes.id"), index=True)
    provider_message_id: Mapped[str] = mapped_column(String(512), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    best_score: Mapped[str | None] = mapped_column(String(12))
    result_summary: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_agent(session: Session) -> tuple[Agent, str]:
    token = "agt_" + secrets.token_urlsafe(32)
    agent = Agent(access_token_hash=token_hash(token))
    session.add(agent)
    session.flush()
    return agent, token


def make_engine(settings: Settings) -> Engine:
    return create_engine(settings.database_url.get_secret_value(), pool_pre_ping=True)


def initialize(engine: Engine) -> None:
    Base.metadata.create_all(engine)


def make_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
