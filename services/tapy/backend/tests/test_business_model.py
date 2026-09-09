from datetime import UTC, datetime
from pathlib import Path

from pydantic import SecretStr
from sqlalchemy import select

from tapy.business import ingest_flight_booking
from tapy.config import Settings
from tapy.database import (
    FlightReservation,
    IngestionSource,
    MailboxConnection,
    OpportunityRecipient,
    OpportunityTicket,
    Organization,
    OrganizationMembership,
    TravelBooking,
    UpsellOpportunity,
    User,
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


def test_ingestion_shared_pnr_idempotency_modification_and_cancellation(tmp_path: Path) -> None:
    engine = make_engine(
        Settings(database_url=SecretStr(f"sqlite+pysqlite:///{tmp_path / 'ingestion.sqlite'}"))
    )
    upgrade_database(engine)
    factory = make_factory(engine)
    with factory.begin() as session:
        user = User(email="agent@example.com", name="Agent")
        organization = Organization(name="Agency", slug="agency")
        session.add_all([user, organization])
        session.flush()
        session.add(
            OrganizationMembership(organization_id=organization.id, user_id=user.id, role="agent")
        )
        user.active_organization_id = organization.id
        mailbox = MailboxConnection(
            user_id=user.id,
            provider="gmail",
            provider_account_id="account-1",
            email_address=user.email,
            refresh_token="encrypted",  # noqa: S106 - inert database fixture
            scopes="gmail.readonly",
        )
        session.add(mailbox)
        session.flush()
        ids = (organization.id, user.id, mailbox.id)

    def email(message_id: str) -> EmailForAnalysis:
        return EmailForAnalysis(
            message_id=message_id,
            thread_id="thread-1",
            subject="Booking update",
            sender="airline@example.com",
            body_text="This raw email body must never be persisted.",
        )

    with factory.begin() as session:
        first = ingest_flight_booking(
            session,
            organization_id=ids[0],
            agent_id=ids[1],
            mailbox_id=ids[2],
            provider="gmail",
            email=email("message-1"),
            facts=extracted(),
        )
        assert len(first.opportunity_ids) == 2

    with factory.begin() as session:
        duplicate = ingest_flight_booking(
            session,
            organization_id=ids[0],
            agent_id=ids[1],
            mailbox_id=ids[2],
            provider="gmail",
            email=email("message-1"),
            facts=extracted(),
        )
        assert duplicate.booking_ids == []
        assert duplicate.opportunity_ids == []

    with factory.begin() as session:
        modified = ingest_flight_booking(
            session,
            organization_id=ids[0],
            agent_id=ids[1],
            mailbox_id=ids[2],
            provider="gmail",
            email=email("message-2"),
            facts=extracted("modified"),
        )
        assert modified.opportunity_ids == []

    with factory.begin() as session:
        cancelled = ingest_flight_booking(
            session,
            organization_id=ids[0],
            agent_id=ids[1],
            mailbox_id=ids[2],
            provider="gmail",
            email=email("message-3"),
            facts=extracted("cancelled"),
        )
        assert cancelled.opportunity_ids == []

    with factory() as session:
        bookings = session.scalars(select(TravelBooking)).all()
        reservations = session.scalars(select(FlightReservation)).all()
        opportunities = session.scalars(select(UpsellOpportunity)).all()
        recipients = session.scalars(select(OpportunityRecipient)).all()
        links = session.scalars(select(OpportunityTicket)).all()
        sources = session.scalars(select(IngestionSource)).all()
        assert len(bookings) == 1
        assert [item.pnr for item in reservations] == ["SHARED-PNR"]
        assert len(opportunities) == 2
        assert len(links) == 2
        assert len({item.opportunity_id for item in links}) == 2
        assert all(item.status == "closed" for item in opportunities)
        assert all(item.close_reason == "booking_cancelled" for item in opportunities)
        assert {item.selection_method for item in recipients} == {"legacy_ticket_holder"}
        assert {item.selection_status for item in recipients} == {"selected"}
        assert len(sources) == 3
        assert all("body" not in column.name for column in IngestionSource.__table__.columns)
        assert all("raw email body" not in str(item.extracted_fingerprint) for item in sources)
    engine.dispose()
