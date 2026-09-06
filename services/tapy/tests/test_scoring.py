from datetime import date

from tapy.flights import ConfigFlightRepository, score_booking
from tapy.models import Destination, Flight, FlightConfiguration, HotelBooking


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
        is_hotel_booking_confirmation=True,
        booking_status="confirmed",
        hotel_name="Example Hotel",
        city="London",
        country="United Kingdom",
        check_in_date=date(2026, 10, 12),
        check_out_date=date(2026, 10, 17),
    )
    matches = score_booking(booking, config().flights, 0.90)
    assert matches[0].flight_id == "london"
    assert matches[0].score == 1
    assert matches[0].related is True
    assert matches[1].score == 0


def test_threshold_is_strictly_above_configured_value() -> None:
    booking = HotelBooking(
        is_hotel_booking_confirmation=True,
        city="London",
        check_in_date=date(2026, 10, 12),
    )
    assert score_booking(booking, config().flights, 1.0)[0].related is False


def test_cancelled_booking_never_matches() -> None:
    booking = HotelBooking(
        is_hotel_booking_confirmation=True,
        booking_status="cancelled",
        city="London",
        check_in_date=date(2026, 10, 12),
        check_out_date=date(2026, 10, 17),
    )
    assert all(match.score == 0 for match in score_booking(booking, config().flights, 0.90))


def test_repository_boundary_accepts_agent_id() -> None:
    repository = ConfigFlightRepository(config())
    assert repository.for_agent("future-agent")[0].id == "london"
