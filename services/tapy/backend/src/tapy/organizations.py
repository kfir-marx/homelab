"""Invitation and member lifecycle. All tenant mutations serialize on the organization row."""

import secrets
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .auth import password_hash
from .database import (
    Organization,
    OrganizationAudit,
    OrganizationInvitation,
    OrganizationMembership,
    User,
    token_hash,
)


def lock_organization(session: Session, organization_id: str) -> None:
    # A write lock works on SQLite as well as PostgreSQL; acquire before reading members.
    session.execute(
        update(Organization)
        .where(Organization.id == organization_id)
        .values(updated_at=Organization.updated_at)
    )


def require_member(
    session: Session, organization_id: str, user_id: str, *, admin: bool = False
) -> OrganizationMembership:
    member = session.get(OrganizationMembership, (organization_id, user_id), populate_existing=True)
    if not member or member.status != "active" or (admin and member.role != "admin"):
        raise HTTPException(
            403, "active organization membership with the required role is required"
        )
    return member


def audit(
    session: Session, org: str, actor: str | None, action: str, target: str, **details: object
) -> None:
    session.add(
        OrganizationAudit(
            organization_id=org, actor_id=actor, action=action, target_id=target, details=details
        )
    )


def issue_invitation(
    session: Session, org: str, email: str, role: str, actor: str | None
) -> tuple[OrganizationInvitation, str]:
    lock_organization(session, org)
    if actor:
        require_member(session, org, actor, admin=True)
    email = email.strip().casefold()
    now = datetime.now(UTC)
    for old in session.scalars(
        select(OrganizationInvitation).where(
            OrganizationInvitation.organization_id == org,
            OrganizationInvitation.email == email,
            OrganizationInvitation.accepted_at.is_(None),
            OrganizationInvitation.revoked_at.is_(None),
        )
    ):
        old.revoked_at = now
        audit(session, org, actor, "invitation.revoked", old.id)
    token = secrets.token_urlsafe(32)
    invitation = OrganizationInvitation(
        organization_id=org,
        email=email,
        role=role,
        token_hash=token_hash(token),
        inviter_id=actor,
        expires_at=now + timedelta(days=7),
    )
    session.add(invitation)
    session.flush()
    audit(session, org, actor, "invitation.created", invitation.id, email=email, role=role)
    return invitation, token


def valid_invitation(session: Session, token: str) -> OrganizationInvitation:
    invitation = session.scalar(
        select(OrganizationInvitation)
        .where(OrganizationInvitation.token_hash == token_hash(token))
        .execution_options(populate_existing=True)
    )
    if (
        not invitation
        or invitation.accepted_at
        or invitation.revoked_at
        or invitation.expires_at.replace(tzinfo=UTC) <= datetime.now(UTC)
    ):
        raise HTTPException(410, "invitation is invalid, expired, revoked, or already accepted")
    return invitation


def accept_invitation(
    session: Session, token: str, actor: User | None, name: str | None, password: str | None
) -> User:
    invitation = valid_invitation(session, token)
    lock_organization(session, invitation.organization_id)
    invitation = valid_invitation(session, token)
    user = session.scalar(select(User).where(User.email == invitation.email))
    if user:
        if not actor or actor.id != user.id:
            raise HTTPException(401, "sign in as the invited email before accepting")
        # Possession of the email-bound invitation verifies that email, in addition to login.
    else:
        if actor:
            raise HTTPException(403, "sign out before creating the invited account")
        if not name or not name.strip() or not password:
            raise HTTPException(422, "name and a new password are required")
        user = User(
            email=invitation.email, name=name.strip(), password_hash=password_hash(password)
        )
        session.add(user)
        session.flush()
    member = session.get(OrganizationMembership, (invitation.organization_id, user.id))
    if member is None:
        member = OrganizationMembership(
            organization_id=invitation.organization_id, user_id=user.id, role=invitation.role
        )
        session.add(member)
    elif member.status != "active":
        member.status, member.role = "active", invitation.role
    # An already-active member retains their role; an invitation cannot demote the last admin.
    user.email_verified_at = datetime.now(UTC)
    user.active_organization_id = invitation.organization_id
    invitation.accepted_at, invitation.accepted_by = datetime.now(UTC), user.id
    audit(session, invitation.organization_id, user.id, "invitation.accepted", invitation.id)
    audit(
        session,
        invitation.organization_id,
        user.id,
        "membership.accepted",
        user.id,
        role=member.role,
    )
    session.flush()
    return user


def change_member(
    session: Session, org: str, actor: str, target: str, role: str, status: str
) -> None:
    lock_organization(session, org)
    require_member(session, org, actor, admin=True)
    member = session.get(OrganizationMembership, (org, target), populate_existing=True)
    if not member:
        raise HTTPException(404, "membership not found")
    if member.status == "inactive" and status == "active":
        raise HTTPException(409, "reactivation requires a new invitation")
    if (
        member.role == "admin"
        and member.status == "active"
        and (role != "admin" or status != "active")
    ):
        admins = session.scalars(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == org,
                OrganizationMembership.role == "admin",
                OrganizationMembership.status == "active",
            )
        ).all()
        if len(admins) <= 1:
            raise HTTPException(409, "the last active admin cannot be removed or demoted")
    before = {"role": member.role, "status": member.status}
    member.role, member.status = role, status
    audit(
        session, org, actor, "membership.changed", target, before=before, role=role, status=status
    )
