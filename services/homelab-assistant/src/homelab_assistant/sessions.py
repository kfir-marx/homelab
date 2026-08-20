from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    delete,
    func,
    select,
    text,
    update,
)
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class Base(DeclarativeBase):
    pass


class StoredSession(Base):
    __tablename__ = "assistant_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    human_id: Mapped[str] = mapped_column(String(6), unique=True, nullable=False)
    owner_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    topic: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    parent_session_id: Mapped[str | None] = mapped_column(ForeignKey("assistant_sessions.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    warning_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ActiveSession(Base):
    __tablename__ = "assistant_active_sessions"

    owner_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("assistant_sessions.id"), nullable=False)


class StoredMessage(Base):
    __tablename__ = "assistant_messages"
    __table_args__ = (UniqueConstraint("session_id", "ordinal"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("assistant_sessions.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    reasoning: Mapped[str | None] = mapped_column(String(20))
    external_job_id: Mapped[str | None] = mapped_column(String(40))
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PendingAction(Base):
    __tablename__ = "assistant_pending_actions"

    owner_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExternalJob(Base):
    __tablename__ = "assistant_external_jobs"

    public_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    session_id: Mapped[str] = mapped_column(ForeignKey("assistant_sessions.id"), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    reasoning: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


@dataclass(frozen=True)
class SessionRecord:
    id: str
    human_id: str
    owner_id: int
    topic: str
    status: str
    parent_session_id: str | None
    created_at: str
    updated_at: str
    prompt_tokens: int
    warning_sent: bool


@dataclass(frozen=True)
class MessageRecord:
    role: str
    content: str
    provider: str
    model: str
    reasoning: str | None
    job_id: str | None
    prompt_tokens: int | None
    completion_tokens: int | None


class SessionStore:
    def __init__(self, database_url: str) -> None:
        self.engine = create_engine(database_url, pool_pre_ping=True)
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(self.engine, expire_on_commit=False)
        self._install_immutability_trigger()

    def _install_immutability_trigger(self) -> None:
        with self.engine.begin() as connection:
            if self.engine.dialect.name == "sqlite":
                connection.execute(
                    text(
                        "CREATE TRIGGER IF NOT EXISTS assistant_messages_immutable_update "
                        "BEFORE UPDATE ON assistant_messages BEGIN "
                        "SELECT RAISE(ABORT, 'messages are immutable'); END"
                    )
                )
                connection.execute(
                    text(
                        "CREATE TRIGGER IF NOT EXISTS assistant_messages_immutable_delete "
                        "BEFORE DELETE ON assistant_messages BEGIN "
                        "SELECT RAISE(ABORT, 'messages are immutable'); END"
                    )
                )
            elif self.engine.dialect.name == "postgresql":
                connection.execute(
                    text(
                        """
                        CREATE OR REPLACE FUNCTION reject_assistant_message_mutation()
                        RETURNS trigger LANGUAGE plpgsql AS $$
                        BEGIN
                          RAISE EXCEPTION 'assistant messages are append-only';
                        END;
                        $$;
                        """
                    )
                )
                connection.execute(
                    text(
                        "DROP TRIGGER IF EXISTS assistant_messages_immutable ON assistant_messages"
                    )
                )
                connection.execute(
                    text(
                        "CREATE TRIGGER assistant_messages_immutable "
                        "BEFORE UPDATE OR DELETE ON assistant_messages "
                        "FOR EACH ROW EXECUTE FUNCTION reject_assistant_message_mutation()"
                    )
                )

    @staticmethod
    def _record(item: StoredSession) -> SessionRecord:
        created_at = item.created_at
        updated_at = item.updated_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)
        return SessionRecord(
            id=item.id,
            human_id=item.human_id,
            owner_id=item.owner_id,
            topic=item.topic,
            status=item.status,
            parent_session_id=item.parent_session_id,
            created_at=created_at.isoformat(),
            updated_at=updated_at.isoformat(),
            prompt_tokens=item.prompt_tokens,
            warning_sent=item.warning_sent,
        )

    def _find(self, session: Session, identifier: str, owner_id: int) -> StoredSession | None:
        return session.scalar(
            select(StoredSession).where(
                StoredSession.owner_id == owner_id,
                (StoredSession.id == identifier)
                | (func.lower(StoredSession.human_id) == identifier.casefold()),
            )
        )

    def create(
        self, owner_id: int, topic: str = "Untitled", parent_session_id: str | None = None
    ) -> SessionRecord:
        for _ in range(20):
            now = datetime.now(UTC)
            stored = StoredSession(
                id=str(uuid.uuid4()),
                human_id="".join(secrets.choice(CROCKFORD) for _ in range(6)),
                owner_id=owner_id,
                topic=topic[:120],
                status="active",
                parent_session_id=parent_session_id,
                created_at=now,
                updated_at=now,
            )
            try:
                with self.factory.begin() as session:
                    session.add(stored)
                    session.flush()
                    active = session.get(ActiveSession, owner_id)
                    if active:
                        active.session_id = stored.id
                    else:
                        session.add(ActiveSession(owner_id=owner_id, session_id=stored.id))
                return self._record(stored)
            except IntegrityError:
                continue
        raise RuntimeError("could not allocate a unique session ID")

    def get(self, identifier: str, owner_id: int) -> SessionRecord:
        with self.factory() as session:
            stored = self._find(session, identifier, owner_id)
            if not stored:
                raise KeyError("session not found")
            return self._record(stored)

    def active(self, owner_id: int, create: bool = True) -> SessionRecord | None:
        with self.factory() as session:
            stored = session.scalar(
                select(StoredSession)
                .join(ActiveSession, ActiveSession.session_id == StoredSession.id)
                .where(ActiveSession.owner_id == owner_id, StoredSession.owner_id == owner_id)
            )
            if stored:
                return self._record(stored)
        return self.create(owner_id) if create else None

    def activate(self, owner_id: int, identifier: str) -> SessionRecord:
        with self.factory.begin() as session:
            stored = self._find(session, identifier, owner_id)
            if not stored:
                raise KeyError("session not found")
            if stored.status == "deleted":
                raise ValueError("deleted sessions cannot be continued")
            active = session.get(ActiveSession, owner_id)
            if active:
                active.session_id = stored.id
            else:
                session.add(ActiveSession(owner_id=owner_id, session_id=stored.id))
            result = self._record(stored)
        return result

    def list_sessions(self, owner_id: int) -> list[SessionRecord]:
        with self.factory() as session:
            stored = session.scalars(
                select(StoredSession)
                .where(StoredSession.owner_id == owner_id, StoredSession.status != "deleted")
                .order_by(StoredSession.updated_at.desc())
                .limit(30)
            ).all()
            return [self._record(item) for item in stored]

    def messages(self, session_id: str) -> list[MessageRecord]:
        with self.factory() as session:
            stored = session.scalars(
                select(StoredMessage)
                .where(StoredMessage.session_id == session_id)
                .order_by(StoredMessage.ordinal)
            ).all()
            return [
                MessageRecord(
                    role=item.role,
                    content=item.content,
                    provider=item.provider,
                    model=item.model,
                    reasoning=item.reasoning,
                    job_id=item.external_job_id,
                    prompt_tokens=item.prompt_tokens,
                    completion_tokens=item.completion_tokens,
                )
                for item in stored
            ]

    def append(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        provider: str,
        model: str,
        reasoning: str | None = None,
        job_id: str | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
    ) -> None:
        now = datetime.now(UTC)
        with self.factory.begin() as session:
            stored = session.get(StoredSession, session_id)
            if not stored:
                raise KeyError("session not found")
            ordinal = session.scalar(
                select(func.coalesce(func.max(StoredMessage.ordinal), 0) + 1).where(
                    StoredMessage.session_id == session_id
                )
            )
            session.add(
                StoredMessage(
                    id=str(uuid.uuid4()),
                    session_id=session_id,
                    ordinal=int(ordinal or 1),
                    role=role,
                    content=content,
                    provider=provider,
                    model=model,
                    reasoning=reasoning,
                    external_job_id=job_id,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    created_at=now,
                )
            )
            stored.updated_at = now
            if prompt_tokens is not None:
                stored.prompt_tokens = prompt_tokens
                stored.total_prompt_tokens += prompt_tokens
            if completion_tokens is not None:
                stored.total_completion_tokens += completion_tokens

    def update_topic(self, owner_id: int, identifier: str, topic: str) -> SessionRecord:
        with self.factory.begin() as session:
            stored = self._find(session, identifier, owner_id)
            if not stored:
                raise KeyError("session not found")
            stored.topic = topic.strip()[:120]
            stored.updated_at = datetime.now(UTC)
            result = self._record(stored)
        return result

    def compact(
        self,
        owner_id: int,
        source: SessionRecord,
        summary: str,
        *,
        provider: str,
        model: str,
    ) -> SessionRecord:
        for _ in range(20):
            now = datetime.now(UTC)
            child = StoredSession(
                id=str(uuid.uuid4()),
                human_id="".join(secrets.choice(CROCKFORD) for _ in range(6)),
                owner_id=owner_id,
                topic=f"{source.topic} (compacted)"[:120],
                status="active",
                parent_session_id=source.id,
                created_at=now,
                updated_at=now,
            )
            try:
                with self.factory.begin() as session:
                    parent = session.get(StoredSession, source.id, with_for_update=True)
                    if not parent or parent.owner_id != owner_id or parent.status == "deleted":
                        raise ValueError("source session is unavailable")
                    session.add(child)
                    session.flush()
                    session.add(
                        StoredMessage(
                            id=str(uuid.uuid4()),
                            session_id=child.id,
                            ordinal=1,
                            role="handover",
                            content=summary,
                            provider=provider,
                            model=model,
                            created_at=now,
                        )
                    )
                    parent.status = "compacted"
                    parent.updated_at = now
                    active = session.get(ActiveSession, owner_id)
                    if active:
                        active.session_id = child.id
                    else:
                        session.add(ActiveSession(owner_id=owner_id, session_id=child.id))
                    session.execute(delete(PendingAction).where(PendingAction.owner_id == owner_id))
                return self._record(child)
            except IntegrityError:
                continue
        raise RuntimeError("could not allocate a unique compacted session ID")

    def set_status(self, owner_id: int, identifier: str, status: str) -> SessionRecord:
        if status not in {"active", "archived", "compacted", "deleted"}:
            raise ValueError("invalid session status")
        with self.factory.begin() as session:
            stored = self._find(session, identifier, owner_id)
            if not stored:
                raise KeyError("session not found")
            stored.status = status
            if status in {"archived", "deleted"}:
                session.execute(
                    delete(ActiveSession).where(
                        ActiveSession.owner_id == owner_id,
                        ActiveSession.session_id == stored.id,
                    )
                )
            result = self._record(stored)
        return result

    def warn_once(self, session_id: str) -> bool:
        with self.factory.begin() as session:
            changed = cast(
                CursorResult[Any],
                session.execute(
                    update(StoredSession)
                    .where(StoredSession.id == session_id, StoredSession.warning_sent.is_(False))
                    .values(warning_sent=True)
                ),
            ).rowcount
        return bool(changed)

    def pending(self, owner_id: int) -> tuple[str, dict[str, Any]] | None:
        with self.factory() as session:
            stored = session.get(PendingAction, owner_id)
            return (stored.kind, dict(stored.payload)) if stored else None

    def set_pending(self, owner_id: int, kind: str, payload: dict[str, Any]) -> None:
        with self.factory.begin() as session:
            stored = session.get(PendingAction, owner_id)
            if stored:
                stored.kind = kind
                stored.payload = payload
                stored.created_at = datetime.now(UTC)
            else:
                session.add(
                    PendingAction(
                        owner_id=owner_id,
                        kind=kind,
                        payload=payload,
                        created_at=datetime.now(UTC),
                    )
                )

    def clear_pending(self, owner_id: int) -> None:
        with self.factory.begin() as session:
            session.execute(delete(PendingAction).where(PendingAction.owner_id == owner_id))

    def track_external(
        self, public_id: str, owner_id: int, session_id: str, model: str, reasoning: str
    ) -> None:
        with self.factory.begin() as session:
            if not session.get(ExternalJob, public_id):
                session.add(
                    ExternalJob(
                        public_id=public_id,
                        owner_id=owner_id,
                        session_id=session_id,
                        model=model,
                        reasoning=reasoning,
                        status="queued",
                        created_at=datetime.now(UTC),
                    )
                )

    def external_pending(self) -> list[dict[str, Any]]:
        with self.factory() as session:
            stored = session.scalars(
                select(ExternalJob).where(ExternalJob.status.in_(["queued", "running"]))
            ).all()
            return [
                {
                    "public_id": item.public_id,
                    "owner_id": item.owner_id,
                    "session_id": item.session_id,
                }
                for item in stored
            ]

    def external_done(self, public_id: str, status: str) -> None:
        with self.factory.begin() as session:
            stored = session.get(ExternalJob, public_id)
            if stored:
                stored.status = status
