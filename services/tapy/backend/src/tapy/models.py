from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


Provider = Literal["gmail", "outlook"]
AuthProvider = Literal["google", "microsoft"]
Scope = Literal["personal", "organization"]
BookingStatus = Literal["confirmed", "modified", "cancelled"]
OpportunityStatus = Literal["open", "contacted", "declined", "expired", "closed"]
RecipientStatus = Literal["candidate", "selected", "excluded", "needs_contact"]


class EmailForAnalysis(StrictModel):
    message_id: str = Field(min_length=1, max_length=512)
    thread_id: str | None = Field(default=None, max_length=512)
    subject: str = Field(default="", max_length=500)
    sender: str = Field(default="", max_length=500)
    sent_at: str | None = Field(default=None, max_length=100)
    body_text: str = Field(min_length=1, max_length=40_000)


class ExtractedContact(StrictModel):
    channel: Literal["phone", "whatsapp", "email"]
    value: str = Field(min_length=1, max_length=500)
    is_primary: bool = False


class ExtractedRole(StrictModel):
    role: Literal["traveler", "group_leader", "payer", "booker", "guardian", "decision_maker"]
    reason: str | None = Field(default=None, max_length=500)


class ExtractedPerson(StrictModel):
    source_id: str = Field(min_length=1, max_length=100)
    display_name: str = Field(min_length=1, max_length=160)
    given_name: str | None = Field(default=None, max_length=80)
    family_name: str | None = Field(default=None, max_length=80)
    roles: list[ExtractedRole] = Field(default_factory=list, max_length=20)
    contacts: list[ExtractedContact] = Field(default_factory=list, max_length=20)


class ExtractedSegment(StrictModel):
    source_id: str = Field(min_length=1, max_length=100)
    airline: str | None = Field(default=None, max_length=160)
    flight_number: str | None = Field(default=None, max_length=32)
    origin_code: str | None = Field(default=None, min_length=2, max_length=10)
    destination_code: str | None = Field(default=None, min_length=2, max_length=10)
    destination_city: str | None = None
    departure_at: datetime | None = None
    arrival_at: datetime | None = None

    @field_validator("departure_at", "arrival_at")
    @classmethod
    def timezone_required(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return None
        return value


class ExtractedReservation(StrictModel):
    source_id: str = Field(min_length=1, max_length=100)
    pnr: str | None = Field(default=None, min_length=1, max_length=100)
    status: BookingStatus = "confirmed"
    segments: list[ExtractedSegment] = Field(default_factory=list, max_length=30)


class ExtractedTicket(StrictModel):
    ticket_number: str = Field(min_length=1, max_length=100)
    reservation_source_id: str = Field(min_length=1, max_length=100)
    passenger_source_id: str = Field(min_length=1, max_length=100)
    segment_source_ids: list[str] = Field(default_factory=list, max_length=30)
    amount: Decimal = Field(default=Decimal("0"), ge=0, max_digits=18, decimal_places=2)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")


class FlightBooking(StrictModel):
    is_flight_booking_confirmation: bool
    booking_status: BookingStatus = "confirmed"
    booking_reference: str | None = Field(default=None, max_length=100)
    provider: str | None = None
    people: list[ExtractedPerson] = Field(default_factory=list, max_length=100)
    reservations: list[ExtractedReservation] = Field(default_factory=list, max_length=20)
    tickets: list[ExtractedTicket] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def references_exist(self) -> FlightBooking:
        people = {p.source_id for p in self.people}
        reservations = {r.source_id: {s.source_id for s in r.segments} for r in self.reservations}
        if len(people) != len(self.people) or len(reservations) != len(self.reservations):
            raise ValueError("source IDs must be unique")
        for ticket in self.tickets:
            if ticket.passenger_source_id not in people:
                raise ValueError("ticket passenger_source_id does not exist")
            segments = reservations.get(ticket.reservation_source_id)
            if segments is None or not set(ticket.segment_source_ids).issubset(segments):
                raise ValueError("ticket reservation or segment reference does not exist")
        return self


class HotelBooking(StrictModel):
    is_hotel_booking_confirmation: bool
    booking_status: Literal["confirmed", "modified", "cancelled", "unknown"] = "unknown"
    hotel_name: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=100)
    country: str | None = Field(default=None, max_length=100)
    check_in_date: date | None = None
    check_out_date: date | None = None
    guest_name: str | None = Field(default=None, max_length=150)
    confirmation_number: str | None = Field(default=None, max_length=100)
    provider: str | None = None
    airport_code: str | None = None
    guest_names: list[str] = Field(default_factory=list)
    guest_contacts: list[ExtractedContact] = Field(default_factory=list)
    flight_booking_reference: str | None = None


class EmailExtraction(StrictModel):
    """Facts only. Entity resolution and every business decision remain deterministic."""

    booking_event_at: datetime | None = None
    schema_version: int = 1
    metadata: dict[str, object] = Field(default_factory=dict)
    hotel_booking: HotelBooking
    flight_booking: FlightBooking


class Destination(StrictModel):
    city: str
    country: str = ""
    airport_codes: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)


class Flight(StrictModel):
    """Small itinerary projection retained only for deterministic hotel matching."""

    id: str
    label: str
    arrival_date: date
    departure_date: date
    destination: Destination


class ScoreComponents(StrictModel):
    location: float = Field(ge=0, le=1)
    dates: float = Field(ge=0, le=1)


class FlightMatch(StrictModel):
    flight_id: str
    flight_label: str
    score: float = Field(ge=0, le=1)
    related: bool
    components: ScoreComponents
    explanation: str


class AnalysisResponse(StrictModel):
    message_id: str
    booking: HotelBooking
    matches: list[FlightMatch]
    best_opportunity_id: str | None
    best_score: float = Field(ge=0, le=1)
    tickets_found: int = 0
    bookings_changed: list[str] = Field(default_factory=list)
    opportunities_created: list[str] = Field(default_factory=list)


class RegisterRequest(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=10, max_length=256)
    organization_name: str | None = Field(default=None, min_length=1, max_length=160)


class LoginRequest(StrictModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class UserUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    language: Literal["en", "he"] | None = None
    active_organization_id: str | None = None


class MailboxView(StrictModel):
    provider: Provider
    email_address: str
    webhook_active: bool


class MembershipView(StrictModel):
    organization_id: str
    organization_name: str
    role: Literal["admin", "agent"]


class OrganizationCreate(StrictModel):
    name: str = Field(min_length=1, max_length=160)


class MembershipCreate(StrictModel):
    email: EmailStr
    role: Literal["admin", "agent"] = "agent"


class UserView(StrictModel):
    id: str
    name: str
    email: str
    language: Literal["en", "he"]
    active_organization_id: str | None
    active_organization_role: Literal["admin", "agent"] | None
    invitation_required: bool = False
    memberships: list[MembershipView]
    auth_providers: list[str]
    mailboxes: list[MailboxView]


class AuthResult(StrictModel):
    user: UserView


class AgentCreated(StrictModel):
    agent_id: str
    access_token: str


class AgentView(StrictModel):
    agent_id: str
    connected_mailboxes: list[Provider]


class AuthorizationUrl(StrictModel):
    authorization_url: str


class ScanRequest(StrictModel):
    provider: Provider
    maximum_messages: int | None = Field(default=None, ge=1, le=10000)
    reprocess: bool = False


class ScanResult(StrictModel):
    provider: Provider
    messages_seen: int
    messages_skipped: int
    messages_analyzed: int
    confirmations_found: int
    matches_found: int
    flight_confirmations_found: int = 0
    flight_tickets_found: int = 0
    opportunities_created: int = 0
    results: list[AnalysisResponse]


class ContactCreate(StrictModel):
    channel: Literal["phone", "whatsapp", "email"]
    value: str = Field(min_length=1, max_length=500)
    is_primary: bool = False


class RoleCreate(StrictModel):
    role: Literal["traveler", "group_leader", "payer", "booker", "guardian", "decision_maker"]
    source_method: Literal["manual", "explicit_extraction", "inference"] = "manual"
    confidence: Decimal | None = Field(default=None, ge=0, le=1)
    reason: str | None = Field(default=None, max_length=500)


class PersonCreate(StrictModel):
    display_name: str = Field(min_length=1, max_length=160)
    given_name: str | None = None
    family_name: str | None = None
    roles: list[RoleCreate] = Field(default_factory=list)
    contacts: list[ContactCreate] = Field(default_factory=list)


class SegmentCreate(StrictModel):
    airline: str | None = None
    flight_number: str | None = None
    origin_code: str = Field(min_length=2, max_length=10)
    destination_code: str = Field(min_length=2, max_length=10)
    departure_at: datetime
    arrival_at: datetime | None = None


class ReservationCreate(StrictModel):
    pnr: str = Field(min_length=1, max_length=100)
    status: BookingStatus = "confirmed"
    segments: list[SegmentCreate] = Field(default_factory=list)


class BookingCreate(StrictModel):
    internal_reference: str | None = Field(default=None, max_length=100)
    external_reference: str | None = Field(default=None, max_length=100)
    assigned_agent_id: str | None = None
    status: BookingStatus = "confirmed"
    people: list[PersonCreate] = Field(default_factory=list)
    reservations: list[ReservationCreate] = Field(default_factory=list)
    attributes: dict[str, object] = Field(default_factory=dict)


class BookingUpdate(StrictModel):
    internal_reference: str | None = Field(default=None, max_length=100)
    assigned_agent_id: str | None = None
    status: BookingStatus | None = None
    attributes: dict[str, object] | None = None


class ContactView(StrictModel):
    id: str
    channel: str
    display_value: str
    normalized_value: str
    is_primary: bool


class PersonView(StrictModel):
    id: str
    display_name: str
    roles: list[str]
    contacts: list[ContactView]


class SegmentView(StrictModel):
    id: str
    airline: str | None
    flight_number: str | None
    origin_code: str
    destination_code: str
    departure_at: datetime
    arrival_at: datetime | None


class TicketView(StrictModel):
    id: str
    ticket_number: str
    person_id: str
    amount: Decimal
    currency: str
    segment_ids: list[str]


class ReservationView(StrictModel):
    id: str
    pnr: str
    status: str
    segments: list[SegmentView]
    tickets: list[TicketView]


class BookingView(StrictModel):
    id: str
    organization_id: str
    assigned_agent_id: str
    internal_reference: str | None
    external_reference: str | None
    status: str
    people: list[PersonView]
    reservations: list[ReservationView]
    opportunity_ids: list[str]
    created_at: datetime
    updated_at: datetime


class OpportunityCreate(StrictModel):
    booking_id: str
    ticket_ids: list[str] = Field(min_length=1)
    assigned_agent_id: str | None = None
    product_type: str = Field(default="hotel", min_length=1, max_length=40)
    destination: str | None = Field(default=None, max_length=160)
    service_start: datetime | None = None
    service_end: datetime | None = None
    potential_revenue: Decimal = Field(default=Decimal("0"), ge=0)
    potential_commission: Decimal = Field(default=Decimal("0"), ge=0)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")


class TicketCreate(StrictModel):
    reservation_id: str
    person_id: str
    ticket_number: str = Field(min_length=1, max_length=100)
    segment_ids: list[str] = Field(default_factory=list)
    amount: Decimal = Field(default=Decimal("0"), ge=0)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")


class OpportunityUpdate(StrictModel):
    status: Literal["declined", "closed"]
    close_reason: str | None = Field(default=None, max_length=200)
    version: int = Field(ge=1)


class RecipientUpdate(StrictModel):
    person_id: str
    contact_point_id: str | None = None
    selection_status: RecipientStatus
    selection_method: Literal[
        "legacy_ticket_holder", "manual", "explicit_email_role", "leader_inference_v1"
    ] = "manual"
    selection_reason: str | None = Field(default=None, max_length=500)
    confidence: Decimal | None = Field(default=None, ge=0, le=1)
    priority: int = Field(default=0, ge=0)


class RecipientView(StrictModel):
    id: str
    person: PersonView
    contact_point_id: str | None
    selection_status: str
    selection_method: str
    selection_reason: str | None
    confidence: Decimal | None
    priority: int


class OpportunityView(StrictModel):
    flight_details: dict[str, object] = Field(default_factory=dict)
    id: str
    organization_id: str
    booking_id: str
    assigned_agent_id: str
    product_type: str
    destination: str | None
    service_start: datetime | None
    service_end: datetime | None
    status: str
    close_reason: str | None
    currency: str
    version: int
    tickets: list[TicketView]
    recipients: list[RecipientView]
    created_at: datetime
    updated_at: datetime


class DeliveryView(StrictModel):
    id: str
    recipient_id: str
    channel: str
    provider: str
    provider_message_id: str | None
    status: str
    error_code: str | None
    error_message: str | None
    destination_snapshot: str


class SendBatchView(StrictModel):
    id: str
    opportunity_id: str
    completion_policy: Literal["all_selected"]
    status: str
    idempotent_replay: bool = False
    deliveries: list[DeliveryView]


class AgentMetrics(StrictModel):
    agent_id: str
    agent_name: str
    total_opportunities: int
    won_opportunities: int
    conversion_rate: float
    won_commission_by_currency: dict[str, Decimal]


class CurrencyMetrics(StrictModel):
    currency: str
    potential_revenue: Decimal
    potential_commission: Decimal
    won_revenue: Decimal
    won_commission: Decimal


class MetricsView(StrictModel):
    scope: Scope
    total_opportunities: int
    open_opportunities: int
    contacted_opportunities: int
    won_opportunities: int
    declined_opportunities: int
    expired_opportunities: int
    closed_opportunities: int
    delivery_successes: int
    delivery_failures: int
    conversion_rate: float
    monetary_totals: list[CurrencyMetrics]
    per_agent: list[AgentMetrics]


class NotificationView(StrictModel):
    id: str
    kind: str
    title: str
    message: str
    booking_id: str | None = None
    opportunity_id: str | None = None
    created_at: datetime
    read_at: datetime | None = None


class NotificationsRead(StrictModel):
    notification_ids: list[str] = Field(min_length=1, max_length=100)


class JobView(StrictModel):
    id: str
    kind: str
    status: str
    attempts: int
    progress: dict[str, object]
    result: dict[str, object]
    error: str | None
    created_at: datetime
    updated_at: datetime


class InvitationAccept(StrictModel):
    token: str = Field(min_length=20, max_length=200)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    password: str | None = Field(default=None, min_length=10, max_length=256)


class MembershipUpdate(StrictModel):
    role: Literal["admin", "agent"]
    status: Literal["active", "inactive"]
