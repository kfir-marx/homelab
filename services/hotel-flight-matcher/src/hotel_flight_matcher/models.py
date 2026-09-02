from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Destination(StrictModel):
    city: str = Field(min_length=1, max_length=100)
    country: str = Field(min_length=1, max_length=100)
    airport_codes: list[str] = Field(default_factory=list, max_length=10)
    aliases: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("airport_codes")
    @classmethod
    def normalize_airports(cls, values: list[str]) -> list[str]:
        return [value.strip().upper() for value in values if value.strip()]


class Flight(StrictModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")
    label: str = Field(min_length=1, max_length=120)
    arrival_date: date
    departure_date: date
    destination: Destination

    @model_validator(mode="after")
    def dates_are_ordered(self) -> Flight:
        if self.departure_date < self.arrival_date:
            raise ValueError("departure_date must not precede arrival_date")
        return self


class FlightConfiguration(StrictModel):
    version: int = Field(ge=1)
    flights: list[Flight] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def flight_ids_are_unique(self) -> FlightConfiguration:
        ids = [flight.id for flight in self.flights]
        if len(ids) != len(set(ids)):
            raise ValueError("flight ids must be unique")
        return self


class EmailForAnalysis(StrictModel):
    message_id: str = Field(min_length=1, max_length=128)
    thread_id: str | None = Field(default=None, max_length=128)
    subject: str = Field(default="", max_length=500)
    sender: str = Field(default="", max_length=500)
    sent_at: str | None = Field(default=None, max_length=100)
    body_text: str = Field(min_length=1, max_length=40_000)


BookingStatus = Literal["confirmed", "cancelled", "modified", "unknown"]


class HotelBooking(StrictModel):
    is_hotel_booking: bool
    booking_status: BookingStatus = "unknown"
    hotel_name: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=100)
    country: str | None = Field(default=None, max_length=100)
    check_in_date: date | None = None
    check_out_date: date | None = None
    guest_name: str | None = Field(default=None, max_length=150)
    confirmation_number: str | None = Field(default=None, max_length=100)
    confidence: float = Field(ge=0, le=1)
    evidence: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("evidence")
    @classmethod
    def bound_evidence(cls, values: list[str]) -> list[str]:
        return [" ".join(value.split())[:240] for value in values if value.strip()]


class ScoreComponents(StrictModel):
    booking_confidence: float = Field(ge=0, le=1)
    location: float = Field(ge=0, le=1)
    dates: float = Field(ge=0, le=1)


class FlightMatch(StrictModel):
    flight_id: str
    flight_label: str
    probability: float = Field(ge=0, le=1)
    related: bool
    components: ScoreComponents
    explanation: str


class AnalysisResponse(StrictModel):
    message_id: str
    booking: HotelBooking
    matches: list[FlightMatch]
    best_flight_id: str | None
    best_probability: float = Field(ge=0, le=1)
