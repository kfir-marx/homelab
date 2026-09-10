from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import URL, Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .config import Settings


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


JSON_DATA = JSON().with_variant(JSONB(), "postgresql")
MONEY = Numeric(18, 2)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "tapy_users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_hash: Mapped[str | None] = mapped_column(String(512))
    language: Mapped[str] = mapped_column(String(5), default="en", nullable=False)
    access_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    active_organization_id: Mapped[str | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


Agent = User


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class OrganizationMembership(Base):
    __tablename__ = "organization_memberships"
    __table_args__ = (
        CheckConstraint("role IN ('admin','agent')", name="ck_membership_role"),
        CheckConstraint("status IN ('active','inactive')", name="ck_membership_status"),
    )
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("tapy_users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(16), default="agent", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class OrganizationInvitation(Base):
    __tablename__ = "organization_invitations"
    __table_args__ = (CheckConstraint("role IN ('admin','agent')", name="ck_invitation_role"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    inviter_id: Mapped[str | None] = mapped_column(ForeignKey("tapy_users.id"))
    accepted_by: Mapped[str | None] = mapped_column(ForeignKey("tapy_users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class OrganizationAudit(Base):
    __tablename__ = "organization_audit"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("tapy_users.id"))
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    target_id: Mapped[str] = mapped_column(String(36), nullable=False)
    details: Mapped[dict[str, object]] = mapped_column(JSON_DATA, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


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
    organization_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.id"), index=True)
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
    organization_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.id"), index=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    @property
    def agent_id(self) -> str | None:
        return self.user_id


class ProcessedMessage(Base):
    """Provider idempotency record containing only derived summaries, never bodies."""

    __tablename__ = "tapy_processed_messages"
    __table_args__ = (UniqueConstraint("mailbox_id", "provider_message_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    mailbox_id: Mapped[str] = mapped_column(
        ForeignKey("tapy_mailboxes.id", ondelete="CASCADE"), index=True
    )
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    provider_message_id: Mapped[str] = mapped_column(String(512), nullable=False)
    provider_thread_id: Mapped[str | None] = mapped_column(String(512), index=True)
    event_type: Mapped[str] = mapped_column(String(20), default="confirmation", nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    result_summary: Mapped[dict[str, object]] = mapped_column(
        JSON_DATA, default=dict, nullable=False
    )
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class TravelBooking(Base):
    __tablename__ = "travel_bookings"
    __table_args__ = (
        UniqueConstraint("id", "organization_id"),
        UniqueConstraint("organization_id", "external_reference"),
        ForeignKeyConstraint(
            ["organization_id", "assigned_agent_id"],
            ["organization_memberships.organization_id", "organization_memberships.user_id"],
            name="fk_booking_agent_membership",
        ),
        CheckConstraint("status IN ('confirmed','modified','cancelled')", name="ck_booking_status"),
        Index("ix_booking_org_agent_created", "organization_id", "assigned_agent_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False)
    assigned_agent_id: Mapped[str] = mapped_column(String(36), nullable=False)
    internal_reference: Mapped[str | None] = mapped_column(String(100))
    external_reference: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(16), default="confirmed", nullable=False)
    attributes: Mapped[dict[str, object]] = mapped_column(JSON_DATA, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class BookingPerson(Base):
    __tablename__ = "booking_people"
    __table_args__ = (
        UniqueConstraint("id", "organization_id"),
        UniqueConstraint("id", "booking_id", "organization_id"),
        ForeignKeyConstraint(
            ["booking_id", "organization_id"],
            ["travel_bookings.id", "travel_bookings.organization_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("booking_id", "identity_key"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False)
    booking_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    identity_key: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    given_name: Mapped[str | None] = mapped_column(String(80))
    family_name: Mapped[str | None] = mapped_column(String(80))
    attributes: Mapped[dict[str, object]] = mapped_column(JSON_DATA, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class BookingPersonRole(Base):
    __tablename__ = "booking_person_roles"
    __table_args__ = (
        ForeignKeyConstraint(
            ["person_id", "organization_id"],
            ["booking_people.id", "booking_people.organization_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("person_id", "role", "source_method"),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_person_role_confidence",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False)
    person_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(40), nullable=False)
    source_method: Mapped[str] = mapped_column(String(40), nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ContactPoint(Base):
    __tablename__ = "contact_points"
    __table_args__ = (
        UniqueConstraint("id", "organization_id"),
        ForeignKeyConstraint(
            ["person_id", "organization_id"],
            ["booking_people.id", "booking_people.organization_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("person_id", "channel", "normalized_value"),
        CheckConstraint("channel IN ('phone','whatsapp','email')", name="ck_contact_channel"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False)
    person_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    raw_value: Mapped[str] = mapped_column(String(500), nullable=False)
    display_value: Mapped[str] = mapped_column(String(500), nullable=False)
    normalized_value: Mapped[str] = mapped_column(String(500), nullable=False)
    verification_status: Mapped[str] = mapped_column(
        String(24), default="unverified", nullable=False
    )
    consent_status: Mapped[str] = mapped_column(String(24), default="unknown", nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    metadata_json: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSON_DATA, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class FlightReservation(Base):
    __tablename__ = "flight_reservations"
    __table_args__ = (
        UniqueConstraint("id", "organization_id"),
        UniqueConstraint("id", "booking_id", "organization_id"),
        ForeignKeyConstraint(
            ["booking_id", "organization_id"],
            ["travel_bookings.id", "travel_bookings.organization_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("booking_id", "pnr"),
        CheckConstraint(
            "status IN ('confirmed','modified','cancelled')", name="ck_reservation_status"
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False)
    booking_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    pnr: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="confirmed", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class FlightSegment(Base):
    __tablename__ = "flight_segments"
    __table_args__ = (
        UniqueConstraint("id", "organization_id"),
        UniqueConstraint("id", "reservation_id", "organization_id"),
        ForeignKeyConstraint(
            ["reservation_id", "organization_id"],
            ["flight_reservations.id", "flight_reservations.organization_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("reservation_id", "fingerprint"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False)
    reservation_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    airline: Mapped[str | None] = mapped_column(String(160))
    flight_number: Mapped[str | None] = mapped_column(String(32))
    origin_code: Mapped[str] = mapped_column(String(10), nullable=False)
    destination_code: Mapped[str] = mapped_column(String(10), nullable=False)
    departure_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    arrival_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)


class FlightTicket(Base):
    __tablename__ = "flight_tickets"
    __table_args__ = (
        UniqueConstraint("id", "organization_id"),
        UniqueConstraint("id", "booking_id", "organization_id"),
        ForeignKeyConstraint(
            ["reservation_id", "booking_id", "organization_id"],
            [
                "flight_reservations.id",
                "flight_reservations.booking_id",
                "flight_reservations.organization_id",
            ],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["person_id", "booking_id", "organization_id"],
            ["booking_people.id", "booking_people.booking_id", "booking_people.organization_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("organization_id", "ticket_number"),
        CheckConstraint("amount >= 0", name="ck_ticket_amount"),
        CheckConstraint("length(currency) = 3", name="ck_ticket_currency"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False)
    booking_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    reservation_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    person_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    ticket_number: Mapped[str] = mapped_column(String(100), nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class TicketSegment(Base):
    __tablename__ = "ticket_segments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["ticket_id", "organization_id"],
            ["flight_tickets.id", "flight_tickets.organization_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["segment_id", "organization_id"],
            ["flight_segments.id", "flight_segments.organization_id"],
            ondelete="CASCADE",
        ),
    )
    ticket_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    segment_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False)


class UpsellOpportunity(Base):
    __tablename__ = "upsell_opportunities"
    __table_args__ = (
        UniqueConstraint("id", "organization_id"),
        UniqueConstraint("id", "booking_id", "organization_id"),
        ForeignKeyConstraint(
            ["booking_id", "organization_id"],
            ["travel_bookings.id", "travel_bookings.organization_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "assigned_agent_id"],
            ["organization_memberships.organization_id", "organization_memberships.user_id"],
            name="fk_opportunity_agent_membership",
        ),
        CheckConstraint(
            "status IN ('open','contacted','won','declined','expired','closed')",
            name="ck_opportunity_status",
        ),
        CheckConstraint(
            "potential_revenue >= 0 AND potential_commission >= 0 "
            "AND won_revenue >= 0 AND won_commission >= 0",
            name="ck_opportunity_money",
        ),
        CheckConstraint("length(currency) = 3", name="ck_opportunity_currency"),
        Index(
            "ix_opportunity_scope", "organization_id", "assigned_agent_id", "status", "created_at"
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False)
    booking_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    assigned_agent_id: Mapped[str] = mapped_column(String(36), nullable=False)
    product_type: Mapped[str] = mapped_column(String(40), default="hotel", nullable=False)
    destination: Mapped[str | None] = mapped_column(String(160))
    service_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    service_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False)
    close_reason: Mapped[str | None] = mapped_column(String(200))
    potential_revenue: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    potential_commission: Mapped[Decimal] = mapped_column(
        MONEY, default=Decimal("0"), nullable=False
    )
    won_revenue: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    won_commission: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    attributes: Mapped[dict[str, object]] = mapped_column(JSON_DATA, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class OpportunityTicket(Base):
    __tablename__ = "opportunity_tickets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["opportunity_id", "booking_id", "organization_id"],
            [
                "upsell_opportunities.id",
                "upsell_opportunities.booking_id",
                "upsell_opportunities.organization_id",
            ],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["ticket_id", "booking_id", "organization_id"],
            ["flight_tickets.id", "flight_tickets.booking_id", "flight_tickets.organization_id"],
            ondelete="RESTRICT",
        ),
    )
    opportunity_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    ticket_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    booking_id: Mapped[str] = mapped_column(String(36), nullable=False)
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False)


class OpportunityRecipient(Base):
    __tablename__ = "opportunity_recipients"
    __table_args__ = (
        UniqueConstraint("id", "organization_id"),
        ForeignKeyConstraint(
            ["opportunity_id", "booking_id", "organization_id"],
            [
                "upsell_opportunities.id",
                "upsell_opportunities.booking_id",
                "upsell_opportunities.organization_id",
            ],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["person_id", "booking_id", "organization_id"],
            ["booking_people.id", "booking_people.booking_id", "booking_people.organization_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["contact_point_id", "organization_id"],
            [
                "contact_points.id",
                "contact_points.organization_id",
            ],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("opportunity_id", "person_id"),
        CheckConstraint(
            "selection_status IN ('candidate','selected','excluded','needs_contact')",
            name="ck_recipient_status",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_recipient_confidence",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False)
    booking_id: Mapped[str] = mapped_column(String(36), nullable=False)
    opportunity_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    person_id: Mapped[str] = mapped_column(String(36), nullable=False)
    contact_point_id: Mapped[str | None] = mapped_column(String(36))
    selection_status: Mapped[str] = mapped_column(String(20), nullable=False)
    selection_method: Mapped[str] = mapped_column(String(40), nullable=False)
    selection_reason: Mapped[str | None] = mapped_column(String(500))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metadata_json: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSON_DATA, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class UpsellSendBatch(Base):
    __tablename__ = "upsell_send_batches"
    __table_args__ = (
        UniqueConstraint("id", "organization_id"),
        ForeignKeyConstraint(
            ["opportunity_id", "organization_id"],
            ["upsell_opportunities.id", "upsell_opportunities.organization_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "actor_user_id"],
            ["organization_memberships.organization_id", "organization_memberships.user_id"],
        ),
        UniqueConstraint("organization_id", "idempotency_key"),
        CheckConstraint("completion_policy = 'all_selected'", name="ck_batch_policy"),
        CheckConstraint(
            "status IN ('processing','completed','partial','failed')", name="ck_batch_status"
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False)
    opportunity_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    actor_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    template_name: Mapped[str] = mapped_column(String(80), nullable=False)
    template_version: Mapped[str] = mapped_column(String(40), nullable=False)
    completion_policy: Mapped[str] = mapped_column(
        String(24), default="all_selected", nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), default="processing", nullable=False)
    initiated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MessageDelivery(Base):
    __tablename__ = "message_deliveries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["batch_id", "organization_id"],
            ["upsell_send_batches.id", "upsell_send_batches.organization_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["recipient_id", "organization_id"],
            ["opportunity_recipients.id", "opportunity_recipients.organization_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("idempotency_key"),
        CheckConstraint(
            "status IN ('queued','submitted','delivered','failed')", name="ck_delivery_status"
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False)
    batch_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    recipient_id: Mapped[str] = mapped_column(String(36), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(String(160), index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(String(500))
    recipient_name_snapshot: Mapped[str] = mapped_column(String(160), nullable=False)
    destination_snapshot: Mapped[str] = mapped_column(String(500), nullable=False)
    rendered_message: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IngestionSource(Base):
    __tablename__ = "booking_ingestion_sources"
    __table_args__ = (
        ForeignKeyConstraint(
            ["booking_id", "organization_id"],
            ["travel_bookings.id", "travel_bookings.organization_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("mailbox_id", "provider_message_id", "booking_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False)
    booking_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    mailbox_id: Mapped[str | None] = mapped_column(
        ForeignKey("tapy_mailboxes.id", ondelete="SET NULL"), index=True
    )
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    provider_message_id: Mapped[str] = mapped_column(String(512), nullable=False)
    provider_thread_id: Mapped[str | None] = mapped_column(String(512))
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)
    extracted_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class NotificationRecord(Base):
    __tablename__ = "tapy_notifications"
    __table_args__ = (
        ForeignKeyConstraint(
            ["booking_id", "organization_id"],
            ["travel_bookings.id", "travel_bookings.organization_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["opportunity_id", "organization_id"],
            ["upsell_opportunities.id", "upsell_opportunities.organization_id"],
            ondelete="RESTRICT",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("tapy_users.id", ondelete="CASCADE"), index=True
    )
    organization_id: Mapped[str | None] = mapped_column(String(36), index=True)
    booking_id: Mapped[str | None] = mapped_column(String(36), index=True)
    opportunity_id: Mapped[str | None] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_agent(session: Session) -> tuple[User, str]:
    """Create an isolated development fixture; never expose through public onboarding."""
    token = "agt_" + secrets.token_urlsafe(32)
    suffix = secrets.token_hex(6)
    user = User(
        email=f"development-{suffix}@tapy.local",
        name="Development user",
        access_token_hash=token_hash(token),
    )
    session.add(user)
    session.flush()
    organization = Organization(name="Development agency", slug=f"development-{suffix}")
    session.add(organization)
    session.flush()
    session.add(
        OrganizationMembership(organization_id=organization.id, user_id=user.id, role="admin")
    )
    user.active_organization_id = organization.id
    session.flush()
    return user, token


def _database_url(settings: Settings) -> URL:
    url = make_url(settings.database_url.get_secret_value())
    if url.drivername in {"postgres", "postgresql"}:
        return url.set(drivername="postgresql+psycopg")
    return url


def make_engine(settings: Settings) -> Engine:
    engine = create_engine(_database_url(settings), pool_pre_ping=True)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _sqlite_foreign_keys(dbapi_connection: object, _record: object) -> None:
            cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def make_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


class EmailBookingEvent(Base):
    """Versioned extracted facts; bodies remain at the mailbox provider."""

    __tablename__ = "email_booking_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("tapy_users.id"), index=True)
    mailbox_id: Mapped[str] = mapped_column(String(36), index=True)
    message_id: Mapped[str] = mapped_column(String(512))
    fingerprint: Mapped[str] = mapped_column(String(64))
    source: Mapped[dict[str, object]] = mapped_column(JSON_DATA, default=dict)
    facts: Mapped[dict[str, object]] = mapped_column(JSON_DATA, default=dict)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ReconciliationDecision(Base):
    __tablename__ = "reconciliation_decisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    booking_id: Mapped[str] = mapped_column(ForeignKey("travel_bookings.id"), index=True)
    opportunity_id: Mapped[str | None] = mapped_column(ForeignKey("upsell_opportunities.id"))
    reason: Mapped[str] = mapped_column(String(100))
    evidence: Mapped[dict[str, object]] = mapped_column(JSON_DATA)
    fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class BackgroundJob(Base):
    __tablename__ = "background_jobs"
    __table_args__ = (UniqueConstraint("user_id", "organization_id", "idempotency_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("tapy_users.id"), index=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    payload: Mapped[dict[str, object]] = mapped_column(JSON_DATA, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    progress: Mapped[dict[str, object]] = mapped_column(JSON_DATA, default=dict)
    result: Mapped[dict[str, object]] = mapped_column(JSON_DATA, default=dict)
    error: Mapped[str | None] = mapped_column(String(500))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class PartnerOutcome(Base):
    """Only verified partner events may populate these post-facto amounts."""

    __tablename__ = "partner_outcomes"
    __table_args__ = (UniqueConstraint("partner", "event_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    opportunity_id: Mapped[str] = mapped_column(ForeignKey("upsell_opportunities.id"), index=True)
    partner: Mapped[str] = mapped_column(String(100))
    event_id: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30))
    booking_value: Mapped[Decimal | None] = mapped_column(MONEY)
    commission: Mapped[Decimal | None] = mapped_column(MONEY)
    currency: Mapped[str | None] = mapped_column(String(3))
    evidence: Mapped[dict[str, object]] = mapped_column(JSON_DATA, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
