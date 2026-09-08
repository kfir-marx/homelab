from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


Provider = Literal["gmail", "outlook"]
AuthProvider = Literal["google", "microsoft"]
FlightStatus = Literal["open", "upsold", "declined", "past"]
NotificationKind = Literal[
    "flight_added",
    "upsell_sent",
    "whatsapp_failed",
    "flight_add_failed",
]


class Destination(StrictModel):
    city: str = Field(min_length=1, max_length=100)
    country: str = Field(default="", max_length=100)
    airport_codes: list[str] = Field(default_factory=list, max_length=10)
    aliases: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("airport_codes")
    @classmethod
    def normalize_airports(cls, values: list[str]) -> list[str]:
        return [value.strip().upper() for value in values if value.strip()]


class Flight(StrictModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")
    label: str = Field(min_length=1, max_length=160)
    arrival_date: date
    departure_date: date
    destination: Destination

    @model_validator(mode="after")
    def dates_are_ordered(self) -> Flight:
        if self.departure_date < self.arrival_date:
            raise ValueError("departure_date must not precede arrival_date")
        return self


class FlightView(StrictModel):
    id: str
    booking_ref: str = Field(min_length=1, max_length=64)
    passenger_name: str = Field(min_length=1, max_length=160)
    party_size: int = Field(default=1, ge=1, le=99)
    email: str = Field(default="", max_length=320)
    phone: str = Field(default="", max_length=50)
    origin: str = Field(min_length=1, max_length=10)
    origin_city: str = Field(min_length=1, max_length=100)
    destination_code: str = Field(min_length=1, max_length=10)
    destination: Destination
    arrival_date: date
    departure_date: date
    flight_cost_usd: float = Field(default=0, ge=0)
    hotel_cost_usd: float = Field(default=0, ge=0)
    user_id: str
    status: FlightStatus
    closed_reason: str | None = None
    matched_hotel: dict[str, object] | None = None
    created_at: datetime
    updated_at: datetime


class FlightCreate(StrictModel):
    booking_ref: str | None = Field(default=None, max_length=64)
    passenger_name: str = Field(min_length=1, max_length=160)
    party_size: int = Field(default=1, ge=1, le=99)
    email: str = Field(default="", max_length=320)
    phone: str = Field(default="", max_length=50)
    origin: str = Field(min_length=1, max_length=10)
    origin_city: str = Field(min_length=1, max_length=100)
    destination_code: str = Field(min_length=1, max_length=10)
    destination_city: str = Field(min_length=1, max_length=100)
    destination_country: str = Field(default="", max_length=100)
    arrival_date: date
    departure_date: date
    flight_cost_usd: float = Field(default=0, ge=0)
    hotel_cost_usd: float = Field(default=0, ge=0)

    @model_validator(mode="after")
    def dates_are_ordered(self) -> FlightCreate:
        if self.departure_date < self.arrival_date:
            raise ValueError("departure_date must not precede arrival_date")
        return self


class FlightStatusUpdate(StrictModel):
    status: Literal["open", "declined"]


class FlightConfiguration(StrictModel):
    """Legacy import format retained for one-off development data imports."""

    version: int = Field(ge=1)
    flights: list[Flight] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def flight_ids_are_unique(self) -> FlightConfiguration:
        ids = [flight.id for flight in self.flights]
        if len(ids) != len(set(ids)):
            raise ValueError("flight ids must be unique")
        return self


class EmailForAnalysis(StrictModel):
    message_id: str = Field(min_length=1, max_length=512)
    thread_id: str | None = Field(default=None, max_length=512)
    subject: str = Field(default="", max_length=500)
    sender: str = Field(default="", max_length=500)
    sent_at: str | None = Field(default=None, max_length=100)
    body_text: str = Field(min_length=1, max_length=40_000)


BookingStatus = Literal["confirmed", "cancelled", "modified", "unknown"]


class HotelBooking(StrictModel):
    """The only facts an LLM is allowed to decide or extract."""

    is_hotel_booking_confirmation: bool
    booking_status: BookingStatus = "unknown"
    hotel_name: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=100)
    country: str | None = Field(default=None, max_length=100)
    check_in_date: date | None = None
    check_out_date: date | None = None
    guest_name: str | None = Field(default=None, max_length=150)
    confirmation_number: str | None = Field(default=None, max_length=100)


class FlightTicket(StrictModel):
    """One passenger ticket extracted from a flight-confirmation email."""

    booking_ref: str | None = Field(default=None, max_length=64)
    ticket_number: str | None = Field(default=None, max_length=64)
    airline: str | None = Field(default=None, max_length=160)
    outbound_flight_number: str | None = Field(default=None, max_length=32)
    return_flight_number: str | None = Field(default=None, max_length=32)
    passenger_name: str | None = Field(default=None, max_length=160)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=50)
    origin_code: str | None = Field(default=None, max_length=10)
    origin_city: str | None = Field(default=None, max_length=100)
    destination_code: str | None = Field(default=None, max_length=10)
    destination_city: str | None = Field(default=None, max_length=100)
    destination_country: str | None = Field(default=None, max_length=100)
    departure_at: datetime | None = None
    arrival_at: datetime | None = None
    return_at: datetime | None = None
    return_arrival_at: datetime | None = None
    flight_cost_usd: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)


class FlightBooking(StrictModel):
    is_flight_booking_confirmation: bool
    booking_status: BookingStatus = "unknown"
    tickets: list[FlightTicket] = Field(default_factory=list, max_length=50)


class EmailExtraction(StrictModel):
    """Strict LLM output; deterministic application code owns all actions."""

    hotel_booking: HotelBooking
    flight_booking: FlightBooking

    @property
    def is_hotel_booking_confirmation(self) -> bool:
        return self.hotel_booking.is_hotel_booking_confirmation

    @property
    def hotel_name(self) -> str | None:
        return self.hotel_booking.hotel_name


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
    best_flight_id: str | None
    best_score: float = Field(ge=0, le=1)
    flight_tickets_found: int = 0
    flights_added: list[str] = Field(default_factory=list)


class RegisterRequest(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=10, max_length=256)


class LoginRequest(StrictModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class UserUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    language: Literal["en", "he"] | None = None


class MailboxView(StrictModel):
    provider: Provider
    email_address: str
    webhook_active: bool


class UserView(StrictModel):
    id: str
    name: str
    email: str
    language: Literal["en", "he"]
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
    maximum_messages: int | None = Field(default=None, ge=1, le=100)


class ScanResult(StrictModel):
    provider: Provider
    messages_seen: int
    messages_skipped: int
    messages_analyzed: int
    confirmations_found: int
    matches_found: int
    flight_confirmations_found: int = 0
    flight_tickets_found: int = 0
    flights_added: int = 0
    results: list[AnalysisResponse]


class NotificationView(StrictModel):
    id: str
    kind: NotificationKind
    title: str
    message: str
    flight_id: str | None = None
    created_at: datetime
    read_at: datetime | None = None


class NotificationsRead(StrictModel):
    notification_ids: list[str] = Field(min_length=1, max_length=100)


class UpsellResult(StrictModel):
    flight: FlightView
    message_sid: str


class MetricsView(StrictModel):
    total_flights: int
    upsold_count: int
    open_count: int
    declined_count: int
    hotel_on_file_count: int
    closing_rate: float
    net_profit: float
    potential_profit: float
    total_hotel_revenue: float
