"""Operator-provisioned accounts for the local sharing foundations.

Personal codes are random bearer credentials, not user-chosen passwords. A hosted
identity provider, recovery and public onboarding remain a later phase 2 decision.
"""

import secrets
from dataclasses import dataclass
from datetime import UTC

from sqlalchemy import delete, select

from mental_loader.storage import (
    Household,
    LoginSession,
    Membership,
    OAuthAttempt,
    PushSubscription,
    User,
    digest,
)


@dataclass(frozen=True)
class Principal:
    household: Household
    user_id: str | None
    role: str
    conversation_id: str | None = None
    runtime_scope: bool = False

    @property
    def id(self):
        return self.household.id

    @property
    def name(self):
        return self.household.name

    @property
    def timezone(self):
        return self.household.timezone


def enrolled(db, household_id):
    return (
        db.scalar(
            select(Membership.user_id).where(Membership.household_id == household_id).limit(1)
        )
        is not None
    )


def subscription_authorized(db, subscription, allow_legacy=True):
    if subscription.user_id:
        from mental_loader.storage import now

        session = (
            db.get(LoginSession, subscription.session_hash) if subscription.session_hash else None
        )
        if session is None:
            return False
        expiry = (
            session.expires_at.replace(tzinfo=UTC)
            if session.expires_at.tzinfo is None
            else session.expires_at
        )
        return (
            session.user_id == subscription.user_id
            and session.household_id == subscription.household_id
            and expiry > now()
            and db.get(Membership, (subscription.household_id, subscription.user_id)) is not None
        )
    return allow_legacy and not enrolled(db, subscription.household_id)


def provision_account(db, household_id, name, role):
    if role not in {"owner", "member"}:
        raise ValueError("Role must be owner or member")
    if not name.strip() or len(name) > 120:
        raise ValueError("Name must contain between 1 and 120 characters")
    # Serialize enrollment with membership changes to preserve an owner.
    household = db.scalar(select(Household).where(Household.id == household_id).with_for_update())
    if household is None:
        raise ValueError("Unknown household")
    if not enrolled(db, household_id) and role != "owner":
        raise ValueError("The first account must be an owner")
    code = secrets.token_urlsafe(32)
    user = User(name=name.strip(), access_hash=digest(code))
    db.add(user)
    db.flush()
    db.add(Membership(household_id=household_id, user_id=user.id, role=role))
    return user, code


def remove_member(db, household_id, user_id):
    db.scalar(select(Household).where(Household.id == household_id).with_for_update())
    member = db.get(Membership, (household_id, user_id))
    if member is None:
        raise ValueError("Member not found")
    owners = list(
        db.scalars(
            select(Membership).where(
                Membership.household_id == household_id, Membership.role == "owner"
            )
        )
    )
    if member.role == "owner" and len(owners) == 1:
        raise ValueError("The last owner cannot be removed")
    db.delete(member)
    for login in db.scalars(
        select(LoginSession).where(
            LoginSession.household_id == household_id, LoginSession.user_id == user_id
        )
    ):
        db.delete(login)


def recovery_code(db, user):
    code = secrets.token_urlsafe(32)
    user.recovery_hash = digest(code)
    return code


def grant_membership(db, household_id, user_id, role):
    if role not in {"owner", "member"}:
        raise ValueError("Role must be owner or member")
    household = db.scalar(select(Household).where(Household.id == household_id).with_for_update())
    if household is None or db.get(User, user_id) is None:
        raise ValueError("Unknown household or account")
    existing = db.get(Membership, (household_id, user_id))
    if existing:
        if existing.role == "owner" and role == "member":
            owners = list(
                db.scalars(
                    select(Membership.user_id).where(
                        Membership.household_id == household_id,
                        Membership.role == "owner",
                    )
                )
            )
            if len(owners) == 1:
                raise ValueError("The last owner cannot be demoted")
        existing.role = role
    else:
        if not enrolled(db, household_id) and role != "owner":
            raise ValueError("The first account must be an owner")
        db.add(Membership(household_id=household_id, user_id=user_id, role=role))


def rotate_credentials(db, user):
    """One-time recovery or operator reset invalidates sessions and device delivery."""
    access = secrets.token_urlsafe(32)
    user.access_hash = digest(access)
    recovery = recovery_code(db, user)
    for model, column in (
        (LoginSession, LoginSession.user_id),
        (PushSubscription, PushSubscription.user_id),
        (OAuthAttempt, OAuthAttempt.user_id),
    ):
        db.execute(delete(model).where(column == user.id))
    return {"access_code": access, "recovery_code": recovery}
