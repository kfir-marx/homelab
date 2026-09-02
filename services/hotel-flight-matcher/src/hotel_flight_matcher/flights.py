from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from .models import (
    Flight,
    FlightConfiguration,
    FlightMatch,
    HotelBooking,
    ScoreComponents,
)


def load_flights(path: Path) -> FlightConfiguration:
    return FlightConfiguration.model_validate_json(path.read_text(encoding="utf-8"))


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
    booking: HotelBooking, configuration: FlightConfiguration, threshold: float
) -> list[FlightMatch]:
    matches: list[FlightMatch] = []
    for flight in configuration.flights:
        location, location_reason = _location_score(booking, flight)
        dates, date_reason = _date_score(booking, flight)
        probability = 0.0
        if booking.is_hotel_booking and booking.booking_status != "cancelled":
            probability = booking.confidence * (0.60 * location + 0.40 * dates)
        probability = round(max(0.0, min(1.0, probability)), 4)
        matches.append(
            FlightMatch(
                flight_id=flight.id,
                flight_label=flight.label,
                probability=probability,
                related=probability >= threshold,
                components=ScoreComponents(
                    booking_confidence=booking.confidence,
                    location=location,
                    dates=dates,
                ),
                explanation=f"{location_reason}; {date_reason}",
            )
        )
    return sorted(matches, key=lambda match: match.probability, reverse=True)


def serialize_schema() -> str:
    return json.dumps(HotelBooking.model_json_schema(), separators=(",", ":"))
