from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.engine import URL, Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .config import Settings


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "tapy_users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(512))
    language: Mapped[str] = mapped_column(String(5), default="en", nullable=False)
    access_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


# Compatibility name for the iteration-one API and tests.
Agent = User


class UserSession(Base):
    __tablename__ = "tapy_user_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("tapy_users.id", ondelete="CASCADE"), index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AuthIdentity(Base):
    __tablename__ = "tapy_auth_identities"
    __table_args__ = (
        UniqueConstraint("provider", "subject"),
        UniqueConstraint("user_id", "provider"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("tapy_users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    subject: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class MailboxConnection(Base):
    __tablename__ = "tapy_mailboxes"
    __table_args__ = (
        UniqueConstraint("provider", "provider_account_id"),
        UniqueConstraint("user_id", "provider"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("tapy_users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    provider_account_id: Mapped[str] = mapped_column(String(320), nullable=False)
    email_address: Mapped[str] = mapped_column(String(320), nullable=False)
    refresh_token: Mapped[str] = mapped_column(String(4096), nullable=False)
    scopes: Mapped[str] = mapped_column(String(1000), nullable=False)
    webhook_subscription_id: Mapped[str | None] = mapped_column(String(512))
    webhook_client_state: Mapped[str | None] = mapped_column(String(256))
    webhook_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    webhook_cursor: Mapped[str | None] = mapped_column(String(512))
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    @property
    def agent_id(self) -> str:
        return self.user_id


class OAuthState(Base):
    __tablename__ = "tapy_oauth_states"

    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("tapy_users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    @property
    def agent_id(self) -> str | None:
        return self.user_id


class ProcessedMessage(Base):
    __tablename__ = "tapy_processed_messages"
    __table_args__ = (UniqueConstraint("mailbox_id", "provider_message_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    mailbox_id: Mapped[str] = mapped_column(
        ForeignKey("tapy_mailboxes.id", ondelete="CASCADE"), index=True
    )
    provider_message_id: Mapped[str] = mapped_column(String(512), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    best_score: Mapped[str | None] = mapped_column(String(12))
    result_summary: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class FlightRecord(Base):
    __tablename__ = "tapy_flights"
    __table_args__ = (UniqueConstraint("user_id", "booking_ref"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("tapy_users.id", ondelete="CASCADE"), index=True
    )
    booking_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    passenger_name: Mapped[str] = mapped_column(String(160), nullable=False)
    party_size: Mapped[int] = mapped_column(Integer, default=1)
    email: Mapped[str] = mapped_column(String(320), default="")
    phone: Mapped[str] = mapped_column(String(50), default="")
    origin: Mapped[str] = mapped_column(String(10), nullable=False)
    origin_city: Mapped[str] = mapped_column(String(100), nullable=False)
    destination_code: Mapped[str] = mapped_column(String(10), nullable=False)
    destination_city: Mapped[str] = mapped_column(String(100), nullable=False)
    destination_country: Mapped[str] = mapped_column(String(100), default="")
    arrival_date: Mapped[date] = mapped_column(Date, nullable=False)
    departure_date: Mapped[date] = mapped_column(Date, nullable=False)
    flight_cost_usd: Mapped[float] = mapped_column(Float, default=0)
    hotel_cost_usd: Mapped[float] = mapped_column(Float, default=0)
    is_open_for_upsell: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    closed_reason: Mapped[str | None] = mapped_column(String(32))
    matched_hotel: Mapped[dict[str, object] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_agent(session: Session) -> tuple[User, str]:
    token = "agt_" + secrets.token_urlsafe(32)
    suffix = secrets.token_hex(6)
    user = User(
        email=f"development-{suffix}@tapy.local",
        name="Development user",
        access_token_hash=token_hash(token),
    )
    session.add(user)
    session.flush()
    return user, token


def _database_url(settings: Settings) -> URL:
    url = make_url(settings.database_url.get_secret_value())
    if url.drivername in {"postgres", "postgresql"}:
        return url.set(drivername="postgresql+psycopg")
    return url


def make_engine(settings: Settings) -> Engine:
    return create_engine(_database_url(settings), pool_pre_ping=True)


def initialize(engine: Engine) -> None:
    Base.metadata.create_all(engine)


def make_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
