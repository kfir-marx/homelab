from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date

from .models import Flight, FlightMatch, HotelBooking, ScoreComponents


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
        return 1.0, "hotel city matches the opportunity destination"
    if country and country == _normalized(flight.destination.country):
        return 0.6, "hotel country matches"
    return 0.0, "hotel location is absent or does not match"


def _closeness(actual: date, expected: date) -> float:
    return max(0.0, 1.0 - abs((actual - expected).days) / 7)


def _date_score(booking: HotelBooking, flight: Flight) -> tuple[float, str]:
    values: list[float] = []
    if booking.check_in_date:
        values.append(_closeness(booking.check_in_date, flight.arrival_date))
    if booking.check_out_date:
        values.append(_closeness(booking.check_out_date, flight.departure_date))
    return (
        (sum(values) / len(values), "hotel dates compared with service dates")
        if values
        else (0.0, "hotel dates were not extracted")
    )


def score_booking(
    booking: HotelBooking, flights: Sequence[Flight], threshold: float
) -> list[FlightMatch]:
    matches: list[FlightMatch] = []
    for flight in flights:
        location, location_reason = _location_score(booking, flight)
        dates, date_reason = _date_score(booking, flight)
        probability = 0.0
        if booking.is_hotel_booking_confirmation and booking.booking_status != "cancelled":
            probability = round(max(0.0, min(1.0, 0.60 * location + 0.40 * dates)), 4)
        matches.append(
            FlightMatch(
                flight_id=flight.id,
                flight_label=flight.label,
                score=probability,
                related=probability > threshold,
                components=ScoreComponents(location=location, dates=dates),
                explanation=f"{location_reason}; {date_reason}",
            )
        )
    return sorted(matches, key=lambda match: match.score, reverse=True)
