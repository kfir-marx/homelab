from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from test_business_model import extracted

from tapy.config import Settings
from tapy.database import (
    EmailBookingEvent,
    MailboxConnection,
    OpportunityRecipient,
    ReconciliationDecision,
    UpsellOpportunity,
    make_engine,
    make_factory,
    new_agent,
)
from tapy.migrations import upgrade_database
from tapy.models import EmailExtraction, EmailForAnalysis, FlightBooking, HotelBooking
from tapy.reconciliation import reconcile, record_email

Evidence = tuple[sessionmaker[Session], dict[str, str]]


@pytest.fixture
def evidence(tmp_path: Path) -> Iterator[Evidence]:
    engine = make_engine(Settings(database_url=SecretStr(f"sqlite+pysqlite:///{tmp_path / 'db'}")))
    upgrade_database(engine)
    factory = make_factory(engine)
    with factory.begin() as session:
        user, _ = new_agent(session)
        mailbox = MailboxConnection(
            user_id=user.id,
            organization_id=user.active_organization_id,
            provider="gmail",
            provider_account_id="a",
            email_address="a@example.com",
            refresh_token="encrypted",  # noqa: S106 - inert fixture
            scopes="readonly",
        )
        session.add(mailbox)
        session.flush()
        assert user.active_organization_id
        ids = dict(
            organization_id=user.active_organization_id,
            agent_id=user.id,
            mailbox_id=mailbox.id,
            provider="gmail",
        )
    yield factory, ids
    engine.dispose()


def flight() -> FlightBooking:
    facts = extracted()
    for r in facts.reservations:
        for s in r.segments:
            assert s.departure_at and s.arrival_at
            s.departure_at = s.departure_at.replace(year=2099)
            s.arrival_at = s.arrival_at.replace(year=2099)
            s.destination_city = "London"
    return facts


def hotel(**updates: object) -> HotelBooking:
    values = dict(
        is_hotel_booking_confirmation=True,
        booking_status="confirmed",
        city="London",
        check_in_date=date(2099, 10, 12),
        check_out_date=date(2099, 10, 20),
        guest_names=["Ada Lovelace", "Grace Hopper"],
        confirmation_number="HOTEL1",
    )
    return HotelBooking.model_validate({**values, **updates})


def ingest(
    evidence: Evidence,
    message: str,
    facts: FlightBooking | HotelBooking,
    sent: str = "2026-09-01T00:00:00Z",
) -> list[str]:
    factory, ids = evidence
    extraction = EmailExtraction(
        flight_booking=facts
        if isinstance(facts, FlightBooking)
        else FlightBooking(is_flight_booking_confirmation=False),
        hotel_booking=facts
        if isinstance(facts, HotelBooking)
        else HotelBooking(is_hotel_booking_confirmation=False),
    )
    with factory.begin() as session:
        return record_email(
            session,
            **ids,
            email=EmailForAnalysis(
                message_id=message,
                sent_at=sent,
                subject="Booking",
                body_text="Raw email must never be stored",
            ),
            extraction=extraction,
        )


def cards(evidence: Evidence) -> list[UpsellOpportunity]:
    with evidence[0]() as session:
        return list(session.scalars(select(UpsellOpportunity)))


@pytest.mark.parametrize("order", [("flight", "hotel"), ("hotel", "flight")])
def test_order_independent_hotel_suppression_and_audit(
    evidence: Evidence, order: tuple[str, str]
) -> None:
    for kind in order:
        ingest(evidence, kind, flight() if kind == "flight" else hotel())
    assert not [c for c in cards(evidence) if c.status == "open"]
    if order[0] == "flight":
        assert cards(evidence)[0].close_reason == "hotel_already_booked"
    with evidence[0]() as session:
        audit = list(session.scalars(select(ReconciliationDecision)))
        assert audit[-1].reason == "hotel_already_booked"
        assert len(cast(list[str], audit[-1].evidence["event_ids"])) == 2
        assert len(list(session.scalars(select(EmailBookingEvent)))) == 2
        assert "Raw email" not in str([e.facts for e in session.scalars(select(EmailBookingEvent))])


def test_idempotency_no_ticket_required_and_expiration(evidence: Evidence) -> None:
    facts = flight()
    facts.tickets = []
    assert len(ingest(evidence, "flight", facts)) == 1
    assert ingest(evidence, "flight", facts) == []
    assert ingest(evidence, "forward", facts) == []
    assert len(cards(evidence)) == 1
    with evidence[0].begin() as session:
        reconcile(
            session,
            evidence[1]["organization_id"],
            evidence[1]["agent_id"],
            now=datetime(2100, 1, 1, tzinfo=UTC),
        )
    assert cards(evidence)[0].status == "expired"


@pytest.mark.parametrize(
    "updates",
    [
        {"guest_names": ["Someone Else"]},
        {"city": "Paris"},
        {"check_in_date": date(2099, 11, 1), "check_out_date": date(2099, 11, 8)},
        {"booking_status": "cancelled"},
        {"booking_status": "unknown"},
    ],
)
def test_unrelated_or_cancelled_hotel_never_suppresses(
    evidence: Evidence, updates: dict[str, object]
) -> None:
    ingest(evidence, "hotel", hotel(**updates))
    ingest(evidence, "flight", flight())
    assert cards(evidence)[0].status == "open"


def test_delayed_confirmation_cannot_resurrect_cancelled_booking(evidence: Evidence) -> None:
    cancelled = flight().model_copy(update={"booking_status": "cancelled"})
    ingest(evidence, "cancel", cancelled, "2026-09-03T00:00:00Z")
    ingest(evidence, "older", flight(), "2026-09-01T00:00:00Z")
    assert not cards(evidence)


def test_reprocessing_can_invalidate_and_restore_but_not_undo_dismissal(evidence: Evidence) -> None:
    ingest(evidence, "flight", flight())
    ingest(evidence, "hotel", hotel())
    assert cards(evidence)[0].status == "closed"
    ingest(evidence, "hotel", HotelBooking(is_hotel_booking_confirmation=False))
    assert cards(evidence)[0].status == "open"
    with evidence[0].begin() as session:
        card = session.scalar(select(UpsellOpportunity))
        assert card
        card.status, card.close_reason = "declined", "agent_dismissed"
    ingest(evidence, "new", flight())
    assert cards(evidence)[0].status == "declined"
    ingest(evidence, "flight", FlightBooking(is_flight_booking_confirmation=False))
    assert cards(evidence)[0].status == "declined"


def test_cancelled_hotel_reopens_unsent_opportunity(evidence: Evidence) -> None:
    ingest(evidence, "flight", flight())
    ingest(evidence, "hotel", hotel())
    ingest(evidence, "cancel-hotel", hotel(booking_status="cancelled"), "2026-09-03T00:00:00Z")
    assert cards(evidence)[0].status == "open"


def test_roundtrip_destination_and_full_stay_coverage(evidence: Evidence) -> None:
    facts = flight()
    outbound = facts.reservations[0].segments[0]
    facts.reservations[0].segments.append(
        outbound.model_copy(
            update={
                "source_id": "return",
                "origin_code": "LHR",
                "destination_code": "TLV",
                "departure_at": datetime(2099, 10, 20, 9, tzinfo=UTC),
                "arrival_at": datetime(2099, 10, 20, 15, tzinfo=UTC),
            }
        )
    )
    ingest(evidence, "flight", facts)
    card = cards(evidence)[0]
    assert card.destination == "LHR"
    assert card.service_end
    assert card.service_end.date() == date(2099, 10, 20)
    ingest(evidence, "short-hotel", hotel(check_out_date=date(2099, 10, 15)))
    assert cards(evidence)[0].status == "open"
    ingest(evidence, "short-hotel", hotel())
    assert cards(evidence)[0].status == "closed"


def test_partial_group_and_missing_dates_are_not_actionable(evidence: Evidence) -> None:
    ingest(evidence, "hotel", hotel(guest_names=["Ada Lovelace"]))
    ingest(evidence, "flight", flight())
    assert not cards(evidence)
    facts = flight()
    facts.booking_reference = "different"
    facts.reservations[0].segments[0].arrival_at = None
    facts.tickets = []
    ingest(evidence, "missing", facts)
    assert not cards(evidence)


def test_corrected_contact_does_not_send_to_old_person(evidence: Evidence) -> None:
    facts = flight()
    ingest(evidence, "flight", facts)
    facts.people[0].contacts = []
    ingest(evidence, "correction", facts, "2026-09-02T00:00:00Z")
    with evidence[0]() as session:
        recipients = list(session.scalars(select(OpportunityRecipient)))
        assert sorted(r.selection_status for r in recipients) == ["needs_contact", "selected"]


def test_reprocessing_can_restore_an_earlier_interpretation(evidence: Evidence) -> None:
    facts = flight()
    ingest(evidence, "same", facts)
    ingest(evidence, "same", FlightBooking(is_flight_booking_confirmation=False))
    assert cards(evidence)[0].close_reason == "source_reclassified"
    ingest(evidence, "same", facts)
    assert cards(evidence)[0].status == "open"


def test_correction_replaces_old_segment_links(evidence: Evidence) -> None:
    from tapy.database import FlightSegment, TicketSegment

    facts = flight()
    ingest(evidence, "first", facts)
    segment = facts.reservations[0].segments[0]
    assert segment.departure_at and segment.arrival_at
    segment.departure_at = segment.departure_at.replace(day=13)
    segment.arrival_at = segment.arrival_at.replace(day=13)
    ingest(evidence, "corrected", facts, "2026-09-02T00:00:00Z")
    with evidence[0]() as session:
        rows = list(session.scalars(select(FlightSegment)))
        assert len(rows) == 1
        assert rows[0].departure_at.day == 13
        assert set(session.scalars(select(TicketSegment.segment_id))) == {rows[0].id}
    start = cards(evidence)[0].service_start
    assert start and start.day == 13


def test_booker_is_not_treated_as_a_traveler(evidence: Evidence) -> None:
    from tapy.models import ExtractedPerson, ExtractedRole

    facts = flight()
    facts.people.append(
        ExtractedPerson(
            source_id="agent", display_name="Travel Agent", roles=[ExtractedRole(role="booker")]
        )
    )
    ingest(evidence, "flight", facts)
    assert (
        "Travel Agent"
        not in cast(dict[str, list[str]], cards(evidence)[0].attributes["flight_details"])[
            "travelers"
        ]
    )
    ingest(evidence, "hotel", hotel())
    assert cards(evidence)[0].status == "closed"


@pytest.mark.parametrize("prior_status", ["open", "contacted", "declined"])
def test_upgrade_preserves_existing_outreach_and_dismissal(
    evidence: Evidence, prior_status: str
) -> None:
    from sqlalchemy import text

    from tapy.business import ingest_flight_booking

    factory, ids = evidence
    with factory.begin() as session:
        outcome = ingest_flight_booking(
            session,
            **ids,
            email=EmailForAnalysis(message_id="legacy", body_text="inert"),
            facts=flight(),
        )
        first = UpsellOpportunity(
            organization_id=ids["organization_id"],
            booking_id=outcome.booking_ids[0],
            assigned_agent_id=ids["agent_id"],
            status=prior_status,
        )
        second = UpsellOpportunity(
            organization_id=ids["organization_id"],
            booking_id=outcome.booking_ids[0],
            assigned_agent_id=ids["agent_id"],
            status="open",
        )
        session.add_all([first, second])
        session.flush()
        first_id = first.id
        session.execute(text("UPDATE alembic_version SET version_num='20260908_01'"))
    engine = factory.kw["bind"]
    upgrade_database(engine)
    ingest(evidence, "reprocessed", flight())
    with factory() as session:
        primary = session.get(UpsellOpportunity, first_id)
        assert primary
        assert primary.status == prior_status
        assert len(
            [c for c in session.scalars(select(UpsellOpportunity)) if c.status == "open"]
        ) == (1 if prior_status == "open" else 0)
