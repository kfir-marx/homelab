from datetime import UTC, datetime
from pathlib import Path

from pydantic import SecretStr
from sqlalchemy import select

from tapy.business import ingest_flight_booking
from tapy.config import Settings
from tapy.database import (
    FlightReservation,
    MailboxConnection,
    UpsellOpportunity,
    make_engine,
    make_factory,
)
from tapy.migrations import upgrade_database
from tapy.models import (
    EmailForAnalysis,
    ExtractedContact,
    ExtractedPerson,
    ExtractedReservation,
    ExtractedSegment,
    ExtractedTicket,
    FlightBooking,
)


def extracted(status: str = "confirmed") -> FlightBooking:
    return FlightBooking(
        is_flight_booking_confirmation=True,
        booking_status=status,
        booking_reference="AGENCY-BOOKING-7",
        people=[
            ExtractedPerson(
                source_id="p1",
                display_name="Ada Lovelace",
                contacts=[
                    ExtractedContact(channel="email", value="ada@example.com"),
                    ExtractedContact(channel="whatsapp", value="+972 50 111 1111", is_primary=True),
                ],
            ),
            ExtractedPerson(
                source_id="p2",
                display_name="Grace Hopper",
                contacts=[ExtractedContact(channel="whatsapp", value="+972 50 222 2222")],
            ),
        ],
        reservations=[
            ExtractedReservation(
                source_id="r1",
                pnr="SHARED-PNR",
                status=status,
                segments=[
                    ExtractedSegment(
                        source_id="s1",
                        airline="El Al",
                        flight_number="LY315",
                        origin_code="TLV",
                        destination_code="LHR",
                        departure_at=datetime(2026, 10, 12, 8, tzinfo=UTC),
                        arrival_at=datetime(2026, 10, 12, 12, tzinfo=UTC),
                    )
                ],
            )
        ],
        tickets=[
            ExtractedTicket(
                ticket_number="TICKET-1",
                passenger_source_id="p1",
                reservation_source_id="r1",
                segment_source_ids=["s1"],
                amount="500.25",
                currency="EUR",
            ),
            ExtractedTicket(
                ticket_number="TICKET-2",
                passenger_source_id="p2",
                reservation_source_id="r1",
                segment_source_ids=["s1"],
                amount="600.25",
                currency="EUR",
            ),
        ],
    )


def test_projection_does_not_decide_opportunities(tmp_path: Path) -> None:
    from tapy.database import new_agent

    engine = make_engine(Settings(database_url=SecretStr(f"sqlite+pysqlite:///{tmp_path / 'db'}")))
    upgrade_database(engine)
    factory = make_factory(engine)
    with factory.begin() as session:
        user, _ = new_agent(session)
        mailbox = MailboxConnection(
            user_id=user.id,
            provider="gmail",
            provider_account_id="a",
            email_address="a@example.com",
            refresh_token="encrypted",  # noqa: S106 - inert fixture
            scopes="readonly",
        )
        session.add(mailbox)
        session.flush()
        assert user.active_organization_id
        result = ingest_flight_booking(
            session,
            organization_id=user.active_organization_id,
            agent_id=user.id,
            mailbox_id=mailbox.id,
            provider="gmail",
            email=EmailForAnalysis(message_id="1", body_text="not stored"),
            facts=extracted(),
        )
        assert len(result.booking_ids) == 1
        assert not result.opportunity_ids
        assert not session.scalars(select(UpsellOpportunity)).all()
        reservation = session.scalar(select(FlightReservation))
        assert reservation and reservation.pnr == "SHARED-PNR"
