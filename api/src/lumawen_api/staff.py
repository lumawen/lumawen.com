"""User management for staff, and the activity log.

Moderators and security can find people and suspend them; security can also sign someone
out everywhere and read the activity log; only admins change roles or delete accounts.
"""

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select

from .audit import erase_from_log, record
from .auth import (
    DB,
    AdminUser,
    SecurityUser,
    StaffUser,
    UserOut,
    _revoke_all,
)
from .mail import forget_address
from .models import ROLES, AuditEntry, AuthSession, User, UserIdentity

router = APIRouter(prefix="/admin", tags=["staff"])

Role = Literal["user", "contributor", "moderator", "security", "admin"]
assert set(Role.__args__) == set(ROLES)

# Whom each role may suspend or unsuspend. Admins can't be suspended except by an admin.
RANK = {"moderator": 1, "security": 2, "admin": 3}
CAN_SUSPEND: dict[str, set[str]] = {
    "moderator": {"user", "contributor"},
    "security": {"user", "contributor", "moderator"},
    "admin": set(ROLES),
}


class StaffUserOut(UserOut):
    suspended_at: datetime | None
    suspended_reason: str | None
    last_seen_at: datetime | None
    has_password: bool
    has_google: bool
    sessions: int


class UserPage(BaseModel):
    total: int
    counts: dict[str, int]
    users: list[StaffUserOut]


def _details(db, users: list[User]) -> list[StaffUserOut]:
    ids = [u.id for u in users]
    now = datetime.now(UTC)
    seen: dict[Any, tuple] = {
        row[0]: row[1:]
        for row in db.execute(
            select(
                AuthSession.user_id,
                func.max(AuthSession.last_used_at),
                func.count().filter(AuthSession.revoked_at.is_(None), AuthSession.expires_at > now),
            )
            .where(AuthSession.user_id.in_(ids))
            .group_by(AuthSession.user_id)
        )
    }
    google = set(
        db.scalars(
            select(UserIdentity.user_id).where(
                UserIdentity.user_id.in_(ids), UserIdentity.provider == "google"
            )
        )
    )
    out = []
    for u in users:
        last_seen, live = seen.get(u.id, (None, 0))
        out.append(
            StaffUserOut(
                **UserOut.of(u).model_dump(),
                suspended_at=u.suspended_at,
                suspended_reason=u.suspended_reason,
                last_seen_at=last_seen,
                has_password=u.password_hash is not None,
                has_google=u.id in google,
                sessions=live,
            )
        )
    return out


def _filtered(query, q: str | None, role: Role | None, suspended: bool | None):
    if q and q.strip():
        needle = q.strip().lower()
        query = query.where(
            or_(
                User.email.contains(needle, autoescape=True),
                func.lower(User.display_name).contains(needle, autoescape=True),
            )
        )
    if role:
        query = query.where(User.role == role)
    if suspended is not None:
        query = query.where(
            User.suspended_at.is_not(None) if suspended else User.suspended_at.is_(None)
        )
    return query


@router.get("/users", response_model=UserPage)
def list_users(
    _: StaffUser,
    db: DB,
    q: str | None = Query(None, max_length=320),
    role: Role | None = None,
    suspended: bool | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> UserPage:
    """Everyone with an account, newest first. q matches email or name."""
    counts = dict(db.execute(select(User.role, func.count()).group_by(User.role)).all())
    counts["suspended"] = db.scalar(
        select(func.count()).select_from(User).where(User.suspended_at.is_not(None))
    )
    total = db.scalar(_filtered(select(func.count()).select_from(User), q, role, suspended))
    users = db.scalars(
        _filtered(select(User), q, role, suspended)
        .order_by(User.created_at.desc(), User.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return UserPage(
        total=total,
        counts={r: counts.get(r, 0) for r in (*ROLES, "suspended")},
        users=_details(db, list(users)),
    )


def _target(db, user_id: uuid.UUID) -> User:
    # Lock every admin row first, always in id order, then the target: staff actions on
    # admins run one at a time and can't deadlock (see _keep_an_admin).
    db.execute(select(User.id).where(User.role == "admin").order_by(User.id).with_for_update())
    user = db.get(User, user_id, with_for_update=True, populate_existing=True)
    if user is None:
        raise HTTPException(404, "No such user.")
    return user


def _not_self(actor: User, target: User, what: str) -> None:
    if actor.id == target.id:
        raise HTTPException(400, f"You can't {what} your own account.")


def _keep_an_admin(db, target: User) -> None:
    """Refuse to demote, suspend or delete the last active admin. _target already holds every
    admin row's lock, so two admins acting on each other at once can't both succeed."""
    if target.role != "admin" or target.suspended_at is not None:
        return
    others = db.scalars(
        select(User.id).where(
            User.role == "admin", User.suspended_at.is_(None), User.id != target.id
        )
    ).all()
    if not others:
        raise HTTPException(409, "There must always be at least one active admin.")


class RoleIn(BaseModel):
    role: Role


@router.put("/users/{user_id}/role", response_model=StaffUserOut)
def set_role(user_id: uuid.UUID, body: RoleIn, admin: AdminUser, db: DB) -> StaffUserOut:
    target = _target(db, user_id)
    _not_self(admin, target, "change the role of")
    old = target.role
    if old == "admin" and body.role != "admin":
        _keep_an_admin(db, target)
    if old != body.role:
        target.role = body.role
        record(db, admin, "user.role", target.email, before=old, after=body.role)
    db.commit()
    return _details(db, [target])[0]


class SuspendIn(BaseModel):
    reason: str = Field("", max_length=200)


@router.post("/users/{user_id}/suspend", response_model=StaffUserOut)
def suspend(user_id: uuid.UUID, body: SuspendIn, actor: StaffUser, db: DB) -> StaffUserOut:
    """Block sign-in and end every session right away."""
    target = _target(db, user_id)
    _not_self(actor, target, "suspend")
    if target.role not in CAN_SUSPEND[actor.role]:
        raise HTTPException(403, f"Your role can't suspend a {target.role}.")
    _keep_an_admin(db, target)
    reason = body.reason.strip() or None
    if target.suspended_at is None:
        target.suspended_at = datetime.now(UTC)
        _revoke_all(db, target)
    elif RANK[actor.role] < RANK.get(target.suspended_by_role or "", 0):
        raise HTTPException(403, f"A {target.suspended_by_role} suspended this account.")
    target.suspended_reason = reason
    target.suspended_by_role = actor.role
    record(db, actor, "user.suspend", target.email, reason=reason)
    db.commit()
    return _details(db, [target])[0]


@router.post("/users/{user_id}/unsuspend", response_model=StaffUserOut)
def unsuspend(user_id: uuid.UUID, actor: StaffUser, db: DB) -> StaffUserOut:
    target = _target(db, user_id)
    if target.role not in CAN_SUSPEND[actor.role]:
        raise HTTPException(403, f"Your role can't unsuspend a {target.role}.")
    if RANK[actor.role] < RANK.get(target.suspended_by_role or "", 0):
        raise HTTPException(
            403, f"A {target.suspended_by_role} suspended this account, so only they can lift it."
        )
    if target.suspended_at is not None:
        target.suspended_at = None
        target.suspended_reason = None
        target.suspended_by_role = None
        record(db, actor, "user.unsuspend", target.email)
    db.commit()
    return _details(db, [target])[0]


@router.post("/users/{user_id}/sign-out", response_model=StaffUserOut)
def sign_out_everywhere(user_id: uuid.UUID, actor: SecurityUser, db: DB) -> StaffUserOut:
    """End every session (e.g. a lost phone or a suspected break-in)."""
    target = _target(db, user_id)
    if target.role == "admin" and actor.role != "admin":
        raise HTTPException(403, "Only an admin can sign out an admin.")
    _revoke_all(db, target)
    record(db, actor, "user.sign_out", target.email)
    db.commit()
    return _details(db, [target])[0]


@router.delete("/users/{user_id}", status_code=204)
def delete_user(user_id: uuid.UUID, admin: AdminUser, db: DB) -> Response:
    """Delete the account and its sessions and emails. Mailing-list sign-ups are separate."""
    target = _target(db, user_id)
    _not_self(admin, target, "delete")
    _keep_an_admin(db, target)
    forget_address(db, target.email)
    erase_from_log(db, target.email)
    record(db, admin, "user.delete", "(erased account)", role=target.role)
    db.execute(delete(User).where(User.id == target.id))  # the database cascades the rest
    db.commit()
    return Response(status_code=204)


# ---- activity log ----------------------------------------------------------------------------


class AuditOut(BaseModel):
    id: int
    created_at: datetime
    actor_email: str
    action: str
    target: str
    detail: dict[str, Any]


class AuditPage(BaseModel):
    total: int
    entries: list[AuditOut]


@router.get("/activity", response_model=AuditPage)
def activity(
    _: SecurityUser,
    db: DB,
    q: str | None = Query(None, max_length=320),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> AuditPage:
    """Staff actions, newest first. q matches who did it or to whom."""
    query = select(AuditEntry)
    if q and q.strip():
        needle = q.strip().lower()
        query = query.where(
            or_(
                func.lower(AuditEntry.actor_email).contains(needle, autoescape=True),
                func.lower(AuditEntry.target).contains(needle, autoescape=True),
            )
        )
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(AuditEntry.id.desc()).limit(limit).offset(offset)).all()
    return AuditPage(
        total=total,
        entries=[
            AuditOut(
                id=e.id,
                created_at=e.created_at,
                actor_email=e.actor_email,
                action=e.action,
                target=e.target,
                detail=e.detail,
            )
            for e in rows
        ],
    )
