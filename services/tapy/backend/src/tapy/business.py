from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .database import (
    BookingPerson,
    BookingPersonRole,
    ContactPoint,
    FlightReservation,
    FlightSegment,
    FlightTicket,
    IngestionSource,
    MessageDelivery,
    OpportunityRecipient,
    OpportunityTicket,
    OrganizationMembership,
    TicketSegment,
    TravelBooking,
    UpsellOpportunity,
    UpsellSendBatch,
)
from .models import (
    BookingCreate,
    BookingView,
    ContactView,
    EmailForAnalysis,
    FlightBooking,
    OpportunityView,
    PersonView,
    RecipientView,
    ReservationView,
    SegmentView,
    TicketView,
)


def normalized_phone(value: str) -> str:
    value = value.strip()
    prefix = "+" if value.startswith("+") else ""
    digits = re.sub(r"\D", "", value)
    return prefix + digits


def normalized_contact(channel: str, value: str) -> str:
    return value.strip().casefold() if channel == "email" else normalized_phone(value)


def stable_hash(*parts: object) -> str:
    return hashlib.sha256("\x1f".join(str(part) for part in parts).encode()).hexdigest()


def ensure_agent(session: Session, organization_id: str, user_id: str) -> None:
    membership = session.get(OrganizationMembership, (organization_id, user_id))
    if not membership or membership.status != "active":
        raise ValueError("assigned agent is not an active member of this organization")


def create_booking(
    session: Session, organization_id: str, actor_user_id: str, body: BookingCreate
) -> TravelBooking:
    agent_id = body.assigned_agent_id or actor_user_id
    ensure_agent(session, organization_id, agent_id)
    booking = TravelBooking(
        organization_id=organization_id,
        assigned_agent_id=agent_id,
        internal_reference=body.internal_reference,
        external_reference=body.external_reference,
        status=body.status,
        attributes=body.attributes,
    )
    session.add(booking)
    session.flush()
    for index, item in enumerate(body.people):
        person = BookingPerson(
            organization_id=organization_id,
            booking_id=booking.id,
            identity_key=stable_hash("manual", index, item.display_name),
            display_name=item.display_name.strip(),
            given_name=item.given_name,
            family_name=item.family_name,
        )
        session.add(person)
        session.flush()
        for role in item.roles:
            session.add(
                BookingPersonRole(
                    organization_id=organization_id,
                    person_id=person.id,
                    role=role.role,
                    source_method=role.source_method,
                    confidence=role.confidence,
                    reason=role.reason,
                )
            )
        for contact in item.contacts:
            session.add(
                ContactPoint(
                    organization_id=organization_id,
                    person_id=person.id,
                    channel=contact.channel,
                    raw_value=contact.value,
                    display_value=contact.value.strip(),
                    normalized_value=normalized_contact(contact.channel, contact.value),
                    is_primary=contact.is_primary,
                )
            )
    for reservation_item in body.reservations:
        reservation = FlightReservation(
            organization_id=organization_id,
            booking_id=booking.id,
            pnr=reservation_item.pnr.strip(),
            status=reservation_item.status,
        )
        session.add(reservation)
        session.flush()
        for sequence, segment_item in enumerate(reservation_item.segments, 1):
            departure = segment_item.departure_at.astimezone(UTC)
            segment = FlightSegment(
                organization_id=organization_id,
                reservation_id=reservation.id,
                sequence=sequence,
                airline=segment_item.airline,
                flight_number=segment_item.flight_number,
                origin_code=segment_item.origin_code.upper(),
                destination_code=segment_item.destination_code.upper(),
                departure_at=departure,
                arrival_at=segment_item.arrival_at.astimezone(UTC)
                if segment_item.arrival_at
                else None,
                fingerprint=stable_hash(
                    segment_item.airline,
                    segment_item.flight_number,
                    segment_item.origin_code.upper(),
                    segment_item.destination_code.upper(),
                    departure.isoformat(),
                ),
            )
            session.add(segment)
    return booking


@dataclass
class IngestionOutcome:
    booking_ids: list[str]
    opportunity_ids: list[str]


def ingest_flight_booking(
    session: Session,
    *,
    organization_id: str,
    agent_id: str,
    mailbox_id: str,
    provider: str,
    email: EmailForAnalysis,
    facts: FlightBooking,
) -> IngestionOutcome:
    """Resolve one booking graph; current policy creates one opportunity per ticket."""
    ensure_agent(session, organization_id, agent_id)
    seen = session.scalar(
        select(IngestionSource.id).where(
            IngestionSource.mailbox_id == mailbox_id,
            IngestionSource.provider_message_id == email.message_id,
        )
    )
    if seen:
        return IngestionOutcome([], [])

    pnrs = sorted(reservation.pnr.strip() for reservation in facts.reservations)
    booking_reference = facts.booking_reference or ("|".join(pnrs) if pnrs else None)
    booking = (
        session.scalar(
            select(TravelBooking).where(
                TravelBooking.organization_id == organization_id,
                TravelBooking.external_reference == booking_reference,
            )
        )
        if booking_reference
        else None
    )
    if booking is None:
        booking = TravelBooking(
            organization_id=organization_id,
            assigned_agent_id=agent_id,
            external_reference=booking_reference,
            status=facts.booking_status,
        )
        session.add(booking)
        session.flush()
    else:
        booking.status = facts.booking_status
        if facts.booking_status == "modified":
            for reservation in session.scalars(
                select(FlightReservation).where(FlightReservation.booking_id == booking.id)
            ):
                reservation.status = "modified"
        elif facts.booking_status == "cancelled":
            for reservation in session.scalars(
                select(FlightReservation).where(FlightReservation.booking_id == booking.id)
            ):
                reservation.status = "cancelled"
            for opportunity in session.scalars(
                select(UpsellOpportunity).where(
                    UpsellOpportunity.booking_id == booking.id,
                    UpsellOpportunity.status.in_(["open", "contacted"]),
                )
            ):
                opportunity.status = "closed"
                opportunity.close_reason = "booking_cancelled"

    people: dict[str, BookingPerson] = {}
    contacts: dict[str, ContactPoint] = {}
    for item in facts.people:
        identity_key = stable_hash("provider-person", item.source_id)
        person = session.scalar(
            select(BookingPerson).where(
                BookingPerson.booking_id == booking.id,
                BookingPerson.identity_key == identity_key,
            )
        )
        if not person:
            person = BookingPerson(
                organization_id=organization_id,
                booking_id=booking.id,
                identity_key=identity_key,
                display_name=item.display_name,
                given_name=item.given_name,
                family_name=item.family_name,
            )
            session.add(person)
            session.flush()
        people[item.source_id] = person
        explicit_roles = item.roles or []
        if not explicit_roles:
            existing_role = session.scalar(
                select(BookingPersonRole.id).where(
                    BookingPersonRole.person_id == person.id,
                    BookingPersonRole.role == "traveler",
                    BookingPersonRole.source_method == "explicit_extraction",
                )
            )
            if not existing_role:
                session.add(
                    BookingPersonRole(
                        organization_id=organization_id,
                        person_id=person.id,
                        role="traveler",
                        source_method="explicit_extraction",
                        reason="Person was explicitly named as a ticket passenger",
                    )
                )
        for role in explicit_roles:
            existing_role = session.scalar(
                select(BookingPersonRole.id).where(
                    BookingPersonRole.person_id == person.id,
                    BookingPersonRole.role == role.role,
                    BookingPersonRole.source_method == "explicit_extraction",
                )
            )
            if not existing_role:
                session.add(
                    BookingPersonRole(
                        organization_id=organization_id,
                        person_id=person.id,
                        role=role.role,
                        source_method="explicit_extraction",
                        reason=role.reason,
                    )
                )
        for contact_item in item.contacts:
            normalized = normalized_contact(contact_item.channel, contact_item.value)
            contact = session.scalar(
                select(ContactPoint).where(
                    ContactPoint.person_id == person.id,
                    ContactPoint.channel == contact_item.channel,
                    ContactPoint.normalized_value == normalized,
                )
            )
            if not contact:
                contact = ContactPoint(
                    organization_id=organization_id,
                    person_id=person.id,
                    channel=contact_item.channel,
                    raw_value=contact_item.value,
                    display_value=contact_item.value.strip(),
                    normalized_value=normalized,
                    is_primary=contact_item.is_primary,
                    metadata_json={"provider": provider, "provider_message_id": email.message_id},
                )
                session.add(contact)
                session.flush()
            if item.source_id not in contacts or contact_item.is_primary:
                contacts[item.source_id] = contact

    reservations: dict[str, FlightReservation] = {}
    segments: dict[tuple[str, str], FlightSegment] = {}
    for reservation_item in facts.reservations:
        resolved_reservation = session.scalar(
            select(FlightReservation).where(
                FlightReservation.booking_id == booking.id,
                FlightReservation.pnr == reservation_item.pnr,
            )
        )
        if not resolved_reservation:
            resolved_reservation = FlightReservation(
                organization_id=organization_id,
                booking_id=booking.id,
                pnr=reservation_item.pnr,
                status=reservation_item.status,
            )
            session.add(resolved_reservation)
            session.flush()
        else:
            resolved_reservation.status = reservation_item.status
        reservations[reservation_item.source_id] = resolved_reservation
        for sequence, segment_item in enumerate(reservation_item.segments, 1):
            departure = segment_item.departure_at.astimezone(UTC)
            fingerprint = stable_hash(
                segment_item.airline,
                segment_item.flight_number,
                segment_item.origin_code.upper(),
                segment_item.destination_code.upper(),
                departure.isoformat(),
            )
            segment = session.scalar(
                select(FlightSegment).where(
                    FlightSegment.reservation_id == resolved_reservation.id,
                    FlightSegment.fingerprint == fingerprint,
                )
            )
            if not segment:
                segment = FlightSegment(
                    organization_id=organization_id,
                    reservation_id=resolved_reservation.id,
                    sequence=sequence,
                    airline=segment_item.airline,
                    flight_number=segment_item.flight_number,
                    origin_code=segment_item.origin_code.upper(),
                    destination_code=segment_item.destination_code.upper(),
                    departure_at=departure,
                    arrival_at=segment_item.arrival_at.astimezone(UTC)
                    if segment_item.arrival_at
                    else None,
                    fingerprint=fingerprint,
                )
                session.add(segment)
                session.flush()
            segments[(reservation_item.source_id, segment_item.source_id)] = segment

    opportunity_ids: list[str] = []
    for ticket_item in facts.tickets:
        ticket = session.scalar(
            select(FlightTicket).where(
                FlightTicket.organization_id == organization_id,
                FlightTicket.ticket_number == ticket_item.ticket_number,
            )
        )
        if not ticket:
            person = people[ticket_item.passenger_source_id]
            reservation = reservations[ticket_item.reservation_source_id]
            ticket = FlightTicket(
                organization_id=organization_id,
                booking_id=booking.id,
                reservation_id=reservation.id,
                person_id=person.id,
                ticket_number=ticket_item.ticket_number,
                amount=ticket_item.amount,
                currency=ticket_item.currency,
            )
            session.add(ticket)
            session.flush()
            covered_segments = ticket_item.segment_source_ids or [
                source_id
                for reservation_source_id, source_id in segments
                if reservation_source_id == ticket_item.reservation_source_id
            ]
            for source_id in covered_segments:
                segment = segments[(ticket_item.reservation_source_id, source_id)]
                session.add(
                    TicketSegment(
                        ticket_id=ticket.id, segment_id=segment.id, organization_id=organization_id
                    )
                )

        opportunity_id = session.scalar(
            select(OpportunityTicket.opportunity_id).where(OpportunityTicket.ticket_id == ticket.id)
        )
        if not opportunity_id and facts.booking_status != "cancelled":
            segment_values = [
                segments[(ticket_item.reservation_source_id, source_id)]
                for source_id in ticket_item.segment_source_ids
            ]
            opportunity = UpsellOpportunity(
                organization_id=organization_id,
                booking_id=booking.id,
                assigned_agent_id=agent_id,
                destination=segment_values[-1].destination_code if segment_values else None,
                service_start=segment_values[0].departure_at if segment_values else None,
                service_end=segment_values[-1].arrival_at if segment_values else None,
                currency=ticket.currency,
            )
            session.add(opportunity)
            session.flush()
            session.add(
                OpportunityTicket(
                    opportunity_id=opportunity.id,
                    ticket_id=ticket.id,
                    booking_id=booking.id,
                    organization_id=organization_id,
                )
            )
            person = people[ticket_item.passenger_source_id]
            contact = contacts.get(ticket_item.passenger_source_id)
            session.add(
                OpportunityRecipient(
                    organization_id=organization_id,
                    booking_id=booking.id,
                    opportunity_id=opportunity.id,
                    person_id=person.id,
                    contact_point_id=contact.id if contact else None,
                    selection_status="selected" if contact else "needs_contact",
                    selection_method="legacy_ticket_holder",
                    selection_reason=(
                        "Current product fallback selects the ticket holder; "
                        "no group leadership was inferred"
                    ),
                )
            )
            opportunity_ids.append(opportunity.id)

    fingerprint = stable_hash(json.dumps(facts.model_dump(mode="json"), sort_keys=True))
    session.add(
        IngestionSource(
            organization_id=organization_id,
            booking_id=booking.id,
            mailbox_id=mailbox_id,
            provider=provider,
            provider_message_id=email.message_id,
            provider_thread_id=email.thread_id,
            event_type=facts.booking_status,
            extracted_fingerprint=fingerprint,
        )
    )
    return IngestionOutcome([booking.id], opportunity_ids)


def person_view(session: Session, person: BookingPerson) -> PersonView:
    roles = session.scalars(
        select(BookingPersonRole.role).where(BookingPersonRole.person_id == person.id)
    ).all()
    contacts = session.scalars(
        select(ContactPoint).where(ContactPoint.person_id == person.id)
    ).all()
    return PersonView(
        id=person.id,
        display_name=person.display_name,
        roles=list(roles),
        contacts=[
            ContactView(
                id=item.id,
                channel=item.channel,
                display_value=item.display_value,
                normalized_value=item.normalized_value,
                is_primary=item.is_primary,
            )
            for item in contacts
        ],
    )


def ticket_view(session: Session, ticket: FlightTicket) -> TicketView:
    segment_ids = session.scalars(
        select(TicketSegment.segment_id).where(TicketSegment.ticket_id == ticket.id)
    ).all()
    return TicketView(
        id=ticket.id,
        ticket_number=ticket.ticket_number,
        person_id=ticket.person_id,
        amount=ticket.amount,
        currency=ticket.currency,
        segment_ids=list(segment_ids),
    )


def booking_view(session: Session, booking: TravelBooking) -> BookingView:
    people = session.scalars(
        select(BookingPerson).where(BookingPerson.booking_id == booking.id)
    ).all()
    reservations = session.scalars(
        select(FlightReservation).where(FlightReservation.booking_id == booking.id)
    ).all()
    reservation_views: list[ReservationView] = []
    for reservation in reservations:
        segment_rows = session.scalars(
            select(FlightSegment)
            .where(FlightSegment.reservation_id == reservation.id)
            .order_by(FlightSegment.sequence)
        ).all()
        ticket_rows = session.scalars(
            select(FlightTicket).where(FlightTicket.reservation_id == reservation.id)
        ).all()
        reservation_views.append(
            ReservationView(
                id=reservation.id,
                pnr=reservation.pnr,
                status=reservation.status,
                segments=[
                    SegmentView.model_validate(row, from_attributes=True) for row in segment_rows
                ],
                tickets=[ticket_view(session, row) for row in ticket_rows],
            )
        )
    opportunity_ids = session.scalars(
        select(UpsellOpportunity.id).where(UpsellOpportunity.booking_id == booking.id)
    ).all()
    return BookingView(
        id=booking.id,
        organization_id=booking.organization_id,
        assigned_agent_id=booking.assigned_agent_id,
        internal_reference=booking.internal_reference,
        external_reference=booking.external_reference,
        status=booking.status,
        people=[person_view(session, row) for row in people],
        reservations=reservation_views,
        opportunity_ids=list(opportunity_ids),
        created_at=booking.created_at,
        updated_at=booking.updated_at,
    )


def opportunity_view(session: Session, opportunity: UpsellOpportunity) -> OpportunityView:
    ticket_rows = session.scalars(
        select(FlightTicket)
        .join(OpportunityTicket, OpportunityTicket.ticket_id == FlightTicket.id)
        .where(OpportunityTicket.opportunity_id == opportunity.id)
    ).all()
    recipient_rows = session.scalars(
        select(OpportunityRecipient)
        .where(OpportunityRecipient.opportunity_id == opportunity.id)
        .order_by(OpportunityRecipient.priority, OpportunityRecipient.created_at)
    ).all()
    recipients: list[RecipientView] = []
    for recipient in recipient_rows:
        person = session.get(BookingPerson, recipient.person_id)
        assert person
        recipients.append(
            RecipientView(
                id=recipient.id,
                person=person_view(session, person),
                contact_point_id=recipient.contact_point_id,
                selection_status=recipient.selection_status,
                selection_method=recipient.selection_method,
                selection_reason=recipient.selection_reason,
                confidence=recipient.confidence,
                priority=recipient.priority,
            )
        )
    return OpportunityView(
        id=opportunity.id,
        organization_id=opportunity.organization_id,
        booking_id=opportunity.booking_id,
        assigned_agent_id=opportunity.assigned_agent_id,
        product_type=opportunity.product_type,
        destination=opportunity.destination,
        service_start=opportunity.service_start,
        service_end=opportunity.service_end,
        status=opportunity.status,
        close_reason=opportunity.close_reason,
        potential_revenue=opportunity.potential_revenue,
        potential_commission=opportunity.potential_commission,
        won_revenue=opportunity.won_revenue,
        won_commission=opportunity.won_commission,
        currency=opportunity.currency,
        version=opportunity.version,
        tickets=[ticket_view(session, row) for row in ticket_rows],
        recipients=recipients,
        created_at=opportunity.created_at,
        updated_at=opportunity.updated_at,
    )


def metric_values(session: Session, organization_id: str, user_id: str | None) -> dict[str, object]:
    filters = [UpsellOpportunity.organization_id == organization_id]
    if user_id:
        filters.append(UpsellOpportunity.assigned_agent_id == user_id)
    opportunities = session.scalars(select(UpsellOpportunity).where(*filters)).all()
    opportunity_ids = [row.id for row in opportunities]
    delivery_successes = delivery_failures = 0
    if opportunity_ids:
        delivery_successes = int(
            session.scalar(
                select(func.count(MessageDelivery.id))
                .join(UpsellSendBatch, UpsellSendBatch.id == MessageDelivery.batch_id)
                .where(
                    UpsellSendBatch.opportunity_id.in_(opportunity_ids),
                    MessageDelivery.status.in_(["submitted", "delivered"]),
                )
            )
            or 0
        )
        delivery_failures = int(
            session.scalar(
                select(func.count(MessageDelivery.id))
                .join(UpsellSendBatch, UpsellSendBatch.id == MessageDelivery.batch_id)
                .where(
                    UpsellSendBatch.opportunity_id.in_(opportunity_ids),
                    MessageDelivery.status == "failed",
                )
            )
            or 0
        )
    statuses = {
        name: sum(row.status == name for row in opportunities)
        for name in ("open", "contacted", "won", "declined", "expired", "closed")
    }
    won = statuses["won"]
    decided = won + statuses["declined"] + statuses["expired"] + statuses["closed"]
    monetary_totals: dict[str, dict[str, Decimal]] = {}
    for row in opportunities:
        totals = monetary_totals.setdefault(
            row.currency,
            {
                "potential_revenue": Decimal("0"),
                "potential_commission": Decimal("0"),
                "won_revenue": Decimal("0"),
                "won_commission": Decimal("0"),
            },
        )
        if row.status in {"open", "contacted"}:
            totals["potential_revenue"] += row.potential_revenue
            totals["potential_commission"] += row.potential_commission
        elif row.status == "won":
            totals["won_revenue"] += row.won_revenue
            totals["won_commission"] += row.won_commission
    return {
        "total": len(opportunities),
        "statuses": statuses,
        "delivery_successes": delivery_successes,
        "delivery_failures": delivery_failures,
        "conversion_rate": won / decided if decided else 0,
        "monetary_totals": monetary_totals,
    }
