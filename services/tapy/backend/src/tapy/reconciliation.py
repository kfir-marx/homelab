"""Email evidence is authoritative. Call in a tenant-locked transaction after every event."""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from .business import ingest_flight_booking, normalized_contact, stable_hash
from .database import (
    BookingPerson,
    ContactPoint,
    EmailBookingEvent,
    FlightTicket,
    IngestionSource,
    MailboxConnection,
    OpportunityRecipient,
    OpportunityTicket,
    Organization,
    ReconciliationDecision,
    TravelBooking,
    UpsellOpportunity,
)
from .models import EmailExtraction, EmailForAnalysis, ExtractedSegment, FlightBooking, HotelBooking


def norm(value: str | None) -> str:
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", value or "").casefold()))


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def event_time(value: str | None) -> datetime:
    if value:
        try:
            return utc(datetime.fromisoformat(value))
        except ValueError:
            try:
                return utc(parsedate_to_datetime(value))
            except (ValueError, TypeError, OverflowError):
                pass
    return datetime.now(UTC)


def mailbox_organization(session: Session, mailbox_id: str) -> str | None:
    return session.scalar(
        select(MailboxConnection.organization_id).where(MailboxConnection.id == mailbox_id)
    )


def record_email(
    session: Session,
    *,
    organization_id: str,
    agent_id: str,
    mailbox_id: str,
    provider: str,
    email: EmailForAnalysis,
    extraction: EmailExtraction,
) -> list[str]:
    from .organizations import lock_organization, require_member

    lock_organization(session, organization_id)
    require_member(session, organization_id, agent_id)
    # Serializes ingestion, derived state and API decisions across workers for this tenant.
    session.scalar(select(Organization).where(Organization.id == organization_id).with_for_update())
    mailbox = session.scalar(
        select(MailboxConnection).where(MailboxConnection.id == mailbox_id).with_for_update()
    )
    if not mailbox or not mailbox.refresh_token or mailbox.user_id != agent_id:
        raise ValueError("mailbox disconnected or not owned by this agent")
    bound_organization = mailbox_organization(session, mailbox_id)
    if not bound_organization or bound_organization != organization_id:
        raise ValueError("mailbox evidence belongs to a different organization")
    facts = extraction.model_dump(mode="json")
    fingerprint = stable_hash(json.dumps(facts, sort_keys=True))
    old = session.scalar(
        select(EmailBookingEvent)
        .where(
            EmailBookingEvent.mailbox_id == mailbox_id,
            EmailBookingEvent.message_id == email.message_id,
        )
        .order_by(EmailBookingEvent.created_at.desc(), EmailBookingEvent.id.desc())
        .limit(1)
    )
    if not old or old.fingerprint != fingerprint:
        session.add(
            EmailBookingEvent(
                organization_id=organization_id,
                agent_id=agent_id,
                mailbox_id=mailbox_id,
                message_id=email.message_id,
                fingerprint=fingerprint,
                facts=facts,
                effective_at=utc(extraction.booking_event_at)
                if extraction.booking_event_at and extraction.booking_event_at.tzinfo
                else event_time(email.sent_at),
                source={
                    "provider": provider,
                    "thread_id": email.thread_id,
                    "subject": email.subject,
                    "sender": email.sender,
                    "sent_at": email.sent_at,
                },
            )
        )
        session.flush()
    return reconcile(session, organization_id, agent_id)


def booking_key(event: EmailBookingEvent, facts: FlightBooking | HotelBooking) -> str:
    reference: str | None
    if isinstance(facts, FlightBooking):
        reference = facts.booking_reference or "|".join(
            sorted(r.pnr for r in facts.reservations if r.pnr)
        )
        kind = "flight"
    else:
        reference = facts.confirmation_number
        kind = "hotel"
    # Never merge unrelated reference-less bookings merely because they share a thread.
    identity = (
        reference.strip().casefold()
        if reference
        else stable_hash(json.dumps(facts.model_dump(mode="json"), sort_keys=True))
    )
    return stable_hash(event.agent_id, kind, norm(facts.provider), identity)


def itinerary(facts: FlightBooking) -> dict[str, object]:
    segments = sorted(
        [
            s
            for r in facts.reservations
            if r.status != "cancelled"
            for s in r.segments
            if s.departure_at
        ],
        key=lambda s: cast(datetime, s.departure_at),
    )
    details: dict[str, object] = {
        "booking_reference": facts.booking_reference,
        "travelers": [p.display_name for p in facts.people],
        "segments": [s.model_dump(mode="json") for s in segments],
        "pnrs": [r.pnr for r in facts.reservations if r.pnr],
    }
    if not segments:
        return details
    # A stay begins at the end of the outbound chain; short connections are not stays.
    stops: list[tuple[ExtractedSegment, datetime | None]] = []
    for index, segment in enumerate(segments):
        if not segment.arrival_at or not segment.destination_code:
            continue
        if index == len(segments) - 1:
            if segment.destination_code != segments[0].origin_code:
                stops.append((segment, None))
        elif (
            cast(datetime, segments[index + 1].departure_at) - segment.arrival_at
        ).total_seconds() >= 86400:
            stops.append((segment, segments[index + 1].departure_at))
    if len(stops) == 1:
        arrival, end = stops[0]
        assert arrival.arrival_at
        details.update(
            destination=arrival.destination_code,
            city=arrival.destination_city,
            start=arrival.arrival_at.isoformat(),
            end=end.isoformat() if end else None,
        )
    return details


def matches_hotel(flight: FlightBooking, trip: dict[str, object], hotel: HotelBooking) -> set[str]:
    if (
        hotel.booking_status not in {"confirmed", "modified"}
        or not hotel.check_in_date
        or not hotel.check_out_date
    ):
        return set()
    if not trip.get("start") or hotel.check_out_date <= hotel.check_in_date:
        return set()
    arrival = datetime.fromisoformat(str(trip["start"])).date()
    end = datetime.fromisoformat(str(trip["end"])).date() if trip.get("end") else None
    if not (hotel.check_in_date <= arrival < hotel.check_out_date):
        return set()
    if end and hotel.check_out_date < end:
        return set()
    locations = {norm(str(trip.get(k) or "")) for k in ("destination", "city")} - {""}
    if not locations.intersection({norm(hotel.city), norm(hotel.airport_code)} - {""}):
        return set()
    guests = {norm(n) for n in [hotel.guest_name, *hotel.guest_names] if n}
    hotel_contacts = {
        ("phone" if c.channel == "whatsapp" else c.channel, normalized_contact(c.channel, c.value))
        for c in hotel.guest_contacts
    }
    covered = set()
    for person in flight.people:
        contacts = {
            (
                "phone" if c.channel == "whatsapp" else c.channel,
                normalized_contact(c.channel, c.value),
            )
            for c in person.contacts
        }
        if norm(person.display_name) in guests or contacts.intersection(hotel_contacts):
            covered.add(norm(person.display_name))
    return covered


def reconcile(
    session: Session, organization_id: str, agent_id: str, now: datetime | None = None
) -> list[str]:
    session.scalar(select(Organization).where(Organization.id == organization_id).with_for_update())
    now = now or datetime.now(UTC)
    events = session.scalars(
        select(EmailBookingEvent)
        .where(
            EmailBookingEvent.organization_id == organization_id,
            EmailBookingEvent.agent_id == agent_id,
        )
        .order_by(EmailBookingEvent.created_at, EmailBookingEvent.id)
    ).all()
    # Reprocessing replaces a message's interpretation but retains every historical extraction.
    messages = {(e.mailbox_id, e.message_id): e for e in events}
    snapshots: dict[str, tuple[EmailBookingEvent, FlightBooking | HotelBooking]] = {}
    for event in sorted(
        messages.values(), key=lambda e: (utc(e.effective_at), utc(e.created_at), e.id)
    ):
        extraction = EmailExtraction.model_validate(event.facts)
        for facts, confirmed in (
            (extraction.flight_booking, extraction.flight_booking.is_flight_booking_confirmation),
            (extraction.hotel_booking, extraction.hotel_booking.is_hotel_booking_confirmation),
        ):
            if not confirmed:
                continue
            key = booking_key(event, facts)
            previous = snapshots.get(key)
            if previous:
                if (
                    previous[1].booking_status == "cancelled"
                    and facts.booking_status == "confirmed"
                ):
                    continue  # A forwarded confirmation is not evidence of reinstatement.
                # Cancellation-only and partial corrections preserve previously explicit facts.
                updates = {k: v for k, v in facts.model_dump().items() if v is not None and v != []}
                facts = type(facts).model_validate({**previous[1].model_dump(), **updates})
            snapshots[key] = (event, facts)
    hotels = [(e, f) for e, f in snapshots.values() if isinstance(f, HotelBooking)]
    created: list[str] = []
    active_booking_ids: set[str] = set()
    for key, (event, facts) in snapshots.items():
        if not isinstance(facts, FlightBooking):
            continue
        passenger_ids = {ticket.passenger_source_id for ticket in facts.tickets}
        traveler_people = [
            p
            for p in facts.people
            if p.source_id in passenger_ids
            or not p.roles
            or any(role.role == "traveler" for role in p.roles)
        ]
        booking = session.scalar(
            select(TravelBooking).where(
                TravelBooking.organization_id == organization_id,
                TravelBooking.external_reference == f"email:{key}",
            )
        )
        if booking is None:
            legacy_reference = facts.booking_reference or "|".join(
                sorted(r.pnr for r in facts.reservations if r.pnr)
            )
            if legacy_reference:
                booking = session.scalar(
                    select(TravelBooking)
                    .join(IngestionSource)
                    .where(
                        TravelBooking.organization_id == organization_id,
                        TravelBooking.assigned_agent_id == agent_id,
                        TravelBooking.external_reference == legacy_reference,
                        IngestionSource.provider.in_(["gmail", "outlook"]),
                    )
                    .limit(1)
                )
                if booking:
                    booking.external_reference = f"email:{key}"
                    session.flush()
        if booking is None or booking.attributes.get("event_id") != event.id:
            projection = facts.model_copy(update={"booking_reference": f"email:{key}"})
            outcome = ingest_flight_booking(
                session,
                organization_id=organization_id,
                agent_id=agent_id,
                mailbox_id=event.mailbox_id,
                provider=str(event.source["provider"]),
                email=EmailForAnalysis(
                    message_id=f"projection:{key}:{event.id}", body_text="projection"
                ),
                facts=projection,
            )
            if outcome.booking_ids:
                booking = session.get(TravelBooking, outcome.booking_ids[0])
        assert booking
        active_booking_ids.add(booking.id)
        booking.status = facts.booking_status
        booking.attributes = {
            "email_key": key,
            "facts": facts.model_dump(mode="json"),
            "event_id": event.id,
        }
        matching_facts = facts.model_copy(update={"people": traveler_people})
        trip = itinerary(matching_facts)
        existing = [
            row
            for row in session.scalars(
                select(UpsellOpportunity)
                .where(UpsellOpportunity.booking_id == booking.id)
                .order_by(UpsellOpportunity.created_at)
            )
            if not row.attributes.get("superseded_by")
        ]
        opportunity = next(
            (
                row
                for row in existing
                if row.status == "declined" or row.close_reason == "agent_dismissed"
            ),
            None,
        )
        opportunity = opportunity or next(
            (row for row in existing if row.attributes.get("outreach_started")), None
        )
        opportunity = opportunity or (existing[0] if existing else None)
        for duplicate in existing:
            if opportunity and duplicate.id != opportunity.id:
                duplicate.status, duplicate.close_reason = "closed", "duplicate_booking_opportunity"
                duplicate.attributes = {**duplicate.attributes, "superseded_by": opportunity.id}
                duplicate.version += 1
                session.add(
                    ReconciliationDecision(
                        organization_id=organization_id,
                        booking_id=booking.id,
                        opportunity_id=duplicate.id,
                        reason="duplicate_booking_opportunity",
                        evidence={
                            "canonical_opportunity_id": opportunity.id,
                            "event_ids": [event.id],
                        },
                        fingerprint=stable_hash(duplicate.id, opportunity.id),
                    )
                )
        names = {norm(p.display_name) for p in traveler_people}
        covered: set[str] = set()
        evidence_ids = [event.id]
        for hotel_event, hotel in hotels:
            matched = matches_hotel(matching_facts, trip, hotel)
            if matched:
                covered.update(matched)
                evidence_ids.append(hotel_event.id)
        reason = "flight_without_accommodation"
        if facts.booking_status == "cancelled" or (
            facts.reservations and all(r.status == "cancelled" for r in facts.reservations)
        ):
            reason = "booking_cancelled"
        elif not names or len(names) != len(traveler_people) or not trip.get("start"):
            reason = "insufficient_or_ambiguous_flight_details"
        elif datetime.fromisoformat(str(trip["end"] or trip["start"])) <= now:
            reason = "trip_expired"
        elif names.issubset(covered):
            reason = "hotel_already_booked"
        elif covered:
            # Avoid sending a group offer when only part of the group lacks accommodation.
            reason = "partial_accommodation_requires_review"
        if not opportunity and reason == "flight_without_accommodation":
            opportunity = UpsellOpportunity(
                organization_id=organization_id, booking_id=booking.id, assigned_agent_id=agent_id
            )
            session.add(opportunity)
            session.flush()
            created.append(opportunity.id)
            for ticket in session.scalars(
                select(FlightTicket).where(FlightTicket.booking_id == booking.id)
            ):
                session.add(
                    OpportunityTicket(
                        opportunity_id=opportunity.id,
                        ticket_id=ticket.id,
                        booking_id=booking.id,
                        organization_id=organization_id,
                    )
                )
            for person in session.scalars(
                select(BookingPerson).where(BookingPerson.booking_id == booking.id)
            ):
                contact = session.scalar(
                    select(ContactPoint)
                    .where(
                        ContactPoint.person_id == person.id,
                        ContactPoint.channel.in_(["phone", "whatsapp"]),
                    )
                    .order_by(ContactPoint.is_primary.desc())
                )
                session.add(
                    OpportunityRecipient(
                        organization_id=organization_id,
                        booking_id=booking.id,
                        opportunity_id=opportunity.id,
                        person_id=person.id,
                        contact_point_id=contact.id if contact else None,
                        selection_status="selected" if contact else "needs_contact",
                        selection_method="explicit_email_role",
                        selection_reason="Named traveler from flight email",
                    )
                )
        if opportunity:
            for person in session.scalars(
                select(BookingPerson).where(BookingPerson.booking_id == booking.id)
            ):
                recipient = session.scalar(
                    select(OpportunityRecipient).where(
                        OpportunityRecipient.opportunity_id == opportunity.id,
                        OpportunityRecipient.person_id == person.id,
                    )
                )
                current_person = next(
                    (
                        p
                        for p in traveler_people
                        if norm(p.display_name) == norm(person.display_name)
                    ),
                    None,
                )
                if recipient and not current_person:
                    recipient.selection_status, recipient.contact_point_id = "excluded", None
                elif current_person:
                    contacts = {
                        (c.channel, normalized_contact(c.channel, c.value))
                        for c in current_person.contacts
                    }
                    contact = next(
                        (
                            c
                            for c in session.scalars(
                                select(ContactPoint).where(ContactPoint.person_id == person.id)
                            )
                            if c.channel in {"phone", "whatsapp"}
                            and (c.channel, c.normalized_value) in contacts
                        ),
                        None,
                    )
                    if not recipient:
                        session.add(
                            OpportunityRecipient(
                                organization_id=organization_id,
                                booking_id=booking.id,
                                opportunity_id=opportunity.id,
                                person_id=person.id,
                                contact_point_id=contact.id if contact else None,
                                selection_status="selected" if contact else "needs_contact",
                                selection_method="explicit_email_role",
                            )
                        )
                    elif recipient.selection_method != "manual":
                        recipient.contact_point_id = contact.id if contact else None
                        recipient.selection_status = "selected" if contact else "needs_contact"
                    elif recipient.contact_point_id and not any(
                        c.id == recipient.contact_point_id
                        and (c.channel, c.normalized_value) in contacts
                        for c in session.scalars(
                            select(ContactPoint).where(ContactPoint.person_id == person.id)
                        )
                    ):
                        recipient.contact_point_id, recipient.selection_status = (
                            None,
                            "needs_contact",
                        )
            before = (opportunity.status, opportunity.close_reason, opportunity.attributes)
            if reason != "flight_without_accommodation" and opportunity.status in {
                "open",
                "contacted",
            }:
                opportunity.status = "expired" if reason == "trip_expired" else "closed"
                opportunity.close_reason = reason
            elif (
                reason == "flight_without_accommodation"
                and opportunity.status == "closed"
                and opportunity.close_reason
                in {
                    "hotel_already_booked",
                    "booking_cancelled",
                    "insufficient_or_ambiguous_flight_details",
                    "partial_accommodation_requires_review",
                    "source_reclassified",
                    "legacy_requires_reprocessing",
                }
            ):
                # Preserve prior outreach when evidence changes; never offer a second send.
                opportunity.status = (
                    "contacted" if opportunity.attributes.get("outreach_started") else "open"
                )
                opportunity.close_reason = None
            opportunity.destination = str(trip["destination"]) if trip.get("destination") else None
            opportunity.service_start = (
                datetime.fromisoformat(str(trip["start"])) if trip.get("start") else None
            )
            opportunity.service_end = (
                datetime.fromisoformat(str(trip["end"])) if trip.get("end") else None
            )
            opportunity.attributes = {**opportunity.attributes, "flight_details": trip}
            if before != (opportunity.status, opportunity.close_reason, opportunity.attributes):
                opportunity.version += 1
        evidence = {
            "event_ids": sorted(evidence_ids),
            "covered_travelers": sorted(covered),
            "rule_version": 1,
        }
        fingerprint = stable_hash(
            reason,
            json.dumps(evidence, sort_keys=True),
            opportunity.status if opportunity else "suppressed",
        )
        last = session.scalar(
            select(ReconciliationDecision)
            .where(ReconciliationDecision.booking_id == booking.id)
            .order_by(ReconciliationDecision.created_at.desc())
            .limit(1)
        )
        if not last or last.fingerprint != fingerprint:
            session.add(
                ReconciliationDecision(
                    organization_id=organization_id,
                    booking_id=booking.id,
                    opportunity_id=opportunity.id if opportunity else None,
                    reason=reason,
                    evidence=evidence,
                    fingerprint=fingerprint,
                )
            )
    for opportunity in session.scalars(
        select(UpsellOpportunity)
        .join(TravelBooking)
        .where(
            UpsellOpportunity.organization_id == organization_id,
            UpsellOpportunity.assigned_agent_id == agent_id,
            UpsellOpportunity.status.in_(["open", "contacted"]),
            TravelBooking.external_reference.like("email:%"),
        )
    ):
        if opportunity.booking_id not in active_booking_ids:
            opportunity.status, opportunity.close_reason = "closed", "source_reclassified"
            opportunity.version += 1
            session.add(
                ReconciliationDecision(
                    organization_id=organization_id,
                    booking_id=opportunity.booking_id,
                    opportunity_id=opportunity.id,
                    reason="source_reclassified",
                    evidence={"event_ids": [e.id for e in messages.values()]},
                    fingerprint=stable_hash(
                        "source_reclassified",
                        "legacy_requires_reprocessing",
                        opportunity.id,
                        opportunity.version,
                    ),
                )
            )
    session.flush()
    return created
