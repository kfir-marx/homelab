from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .database import FlightRecord
from .models import (
    Destination,
    Flight,
    FlightConfiguration,
    FlightMatch,
    FlightStatus,
    FlightView,
    HotelBooking,
    ScoreComponents,
)


def load_flights(path: Path) -> FlightConfiguration:
    return FlightConfiguration.model_validate_json(path.read_text(encoding="utf-8"))


class FlightRepository(Protocol):
    """Boundary for replacing static configuration with per-agent storage later."""

    def for_agent(self, agent_id: str) -> Sequence[Flight]: ...


class ConfigFlightRepository:
    def __init__(self, configuration: FlightConfiguration) -> None:
        self.configuration = configuration

    def for_agent(self, agent_id: str) -> Sequence[Flight]:
        del agent_id
        return self.configuration.flights


def record_to_flight(record: FlightRecord) -> Flight:
    return Flight(
        id=record.id,
        label=f"{record.origin}–{record.destination_code}: {record.passenger_name}",
        arrival_date=record.arrival_date,
        departure_date=record.departure_date,
        destination=Destination(
            city=record.destination_city,
            country=record.destination_country,
            airport_codes=[record.destination_code],
        ),
    )


def record_to_view(record: FlightRecord) -> FlightView:
    status: FlightStatus
    if record.is_open_for_upsell:
        status = "open"
    elif record.closed_reason == "upsold":
        status = "upsold"
    elif record.closed_reason == "declined":
        status = "declined"
    else:
        status = "past"
    return FlightView(
        id=record.id,
        booking_ref=record.booking_ref,
        passenger_name=record.passenger_name,
        party_size=record.party_size,
        email=record.email,
        phone=record.phone,
        origin=record.origin,
        origin_city=record.origin_city,
        destination_code=record.destination_code,
        destination=Destination(
            city=record.destination_city,
            country=record.destination_country,
            airport_codes=[record.destination_code],
        ),
        arrival_date=record.arrival_date,
        departure_date=record.departure_date,
        flight_cost_usd=record.flight_cost_usd,
        hotel_cost_usd=record.hotel_cost_usd,
        user_id=record.user_id,
        status=status,
        closed_reason=record.closed_reason,
        matched_hotel=record.matched_hotel,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


class DatabaseFlightRepository:
    def __init__(self, factory: sessionmaker[Session]) -> None:
        self._factory = factory

    def for_agent(self, agent_id: str) -> Sequence[Flight]:
        with self._factory() as session:
            records = session.scalars(
                select(FlightRecord).where(FlightRecord.user_id == agent_id)
            ).all()
        return [record_to_flight(record) for record in records]


def _normalized(value: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (value or "").casefold()))


def _location_score(booking: HotelBooking, flight: Flight) -> tuple[float, str]:
    city = _normalized(booking.city)
    country = _normalized(booking.country)
    destinations = {
        _normalized(flight.destination.city),
        *(_normalized(alias) for alias in flight.destination.aliases),
        *(_normalized(code) for code in flight.destination.airport_codes),
    }
    destinations.discard("")
    if city and city in destinations:
        return 1.0, "hotel city matches the flight destination"
    if country and country == _normalized(flight.destination.country):
        return 0.6, "hotel country matches; city was absent or different"
    if not city and not country:
        return 0.0, "hotel location was not extracted"
    return 0.0, "hotel location does not match the flight destination"


def _closeness(actual: date, expected: date) -> float:
    days = abs((actual - expected).days)
    return max(0.0, 1.0 - days / 7)


def _date_score(booking: HotelBooking, flight: Flight) -> tuple[float, str]:
    values: list[float] = []
    if booking.check_in_date:
        values.append(_closeness(booking.check_in_date, flight.arrival_date))
    if booking.check_out_date:
        values.append(_closeness(booking.check_out_date, flight.departure_date))
    if not values:
        return 0.0, "hotel dates were not extracted"
    score = sum(values) / len(values)
    if (
        booking.check_in_date
        and booking.check_out_date
        and booking.check_in_date <= flight.departure_date
        and booking.check_out_date >= flight.arrival_date
    ):
        score = max(score, 0.75)
    return min(score, 1.0), "hotel stay dates compared with arrival and departure"


def score_booking(
    booking: HotelBooking, flights: Sequence[Flight], threshold: float
) -> list[FlightMatch]:
    matches: list[FlightMatch] = []
    for flight in flights:
        location, location_reason = _location_score(booking, flight)
        dates, date_reason = _date_score(booking, flight)
        probability = 0.0
        if booking.is_hotel_booking_confirmation and booking.booking_status != "cancelled":
            probability = 0.60 * location + 0.40 * dates
        probability = round(max(0.0, min(1.0, probability)), 4)
        matches.append(
            FlightMatch(
                flight_id=flight.id,
                flight_label=flight.label,
                score=probability,
                related=probability > threshold,
                components=ScoreComponents(
                    location=location,
                    dates=dates,
                ),
                explanation=f"{location_reason}; {date_reason}",
            )
        )
    return sorted(matches, key=lambda match: match.score, reverse=True)


def serialize_schema() -> str:
    return json.dumps(HotelBooking.model_json_schema(), separators=(",", ":"))
