from datetime import date

from hotel_flight_matcher.flights import score_booking
from hotel_flight_matcher.models import Destination, Flight, FlightConfiguration, HotelBooking


def config() -> FlightConfiguration:
    return FlightConfiguration(
        version=1,
        flights=[
            Flight(
                id="london",
                label="London",
                arrival_date=date(2026, 10, 12),
                departure_date=date(2026, 10, 17),
                destination=Destination(
                    city="London",
                    country="United Kingdom",
                    airport_codes=["LHR"],
                    aliases=["Greater London"],
                ),
            ),
            Flight(
                id="paris",
                label="Paris",
                arrival_date=date(2026, 11, 2),
                departure_date=date(2026, 11, 5),
                destination=Destination(city="Paris", country="France"),
            ),
        ],
    )


def test_matching_booking_scores_expected_flight_highest() -> None:
    booking = HotelBooking(
        is_hotel_booking=True,
        booking_status="confirmed",
        hotel_name="Example Hotel",
        city="London",
        country="United Kingdom",
        check_in_date=date(2026, 10, 12),
        check_out_date=date(2026, 10, 17),
        confidence=0.94,
    )
    matches = score_booking(booking, config(), 0.65)
    assert matches[0].flight_id == "london"
    assert matches[0].probability == 0.94
    assert matches[0].related is True
    assert matches[1].probability == 0


def test_cancelled_booking_never_matches() -> None:
    booking = HotelBooking(
        is_hotel_booking=True,
        booking_status="cancelled",
        city="London",
        check_in_date=date(2026, 10, 12),
        check_out_date=date(2026, 10, 17),
        confidence=0.99,
    )
    assert all(match.probability == 0 for match in score_booking(booking, config(), 0.65))


def test_country_only_match_is_lower_confidence() -> None:
    booking = HotelBooking(
        is_hotel_booking=True,
        city=None,
        country="United Kingdom",
        check_in_date=date(2026, 10, 13),
        check_out_date=date(2026, 10, 16),
        confidence=0.8,
    )
    match = score_booking(booking, config(), 0.65)[0]
    assert 0 < match.probability < 0.65
    assert match.related is False
