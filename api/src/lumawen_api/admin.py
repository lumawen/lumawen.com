"""Admin-only API. The dashboard's admin tabs (users, mailing list, messages) build on this."""

import csv
import io
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import delete, func, select

from .auth import DB, AdminUser, UserOut
from .models import Subscriber, User

router = APIRouter(prefix="/admin", tags=["admin"])


class UserPage(BaseModel):
    total: int
    users: list[UserOut]


@router.get("/users", response_model=UserPage)
def list_users(
    _: AdminUser,
    db: DB,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> UserPage:
    total = db.scalar(select(func.count()).select_from(User))
    users = db.scalars(
        select(User).order_by(User.created_at.desc()).limit(limit).offset(offset)
    ).all()
    return UserPage(total=total, users=[UserOut.of(u) for u in users])


# ---- mailing list ----------------------------------------------------------------------------

StatusFilter = Literal["pending", "subscribed", "unsubscribed"]


class SubscriberOut(BaseModel):
    id: uuid.UUID
    email: str
    status: str
    source: str
    created_at: datetime
    confirmed_at: datetime | None
    unsubscribed_at: datetime | None

    @classmethod
    def of(cls, s: Subscriber) -> "SubscriberOut":
        return cls(
            id=s.id,
            email=s.email,
            status=s.status,
            source=s.source,
            created_at=s.created_at,
            confirmed_at=s.confirmed_at,
            unsubscribed_at=s.unsubscribed_at,
        )


class SubscriberPage(BaseModel):
    total: int
    counts: dict[str, int]
    subscribers: list[SubscriberOut]


def _filtered(query, status: StatusFilter | None, q: str | None):
    if status:
        query = query.where(Subscriber.status == status)
    if q:
        query = query.where(Subscriber.email.contains(q.strip().lower(), autoescape=True))
    return query


@router.get("/subscribers", response_model=SubscriberPage)
def list_subscribers(
    _: AdminUser,
    db: DB,
    status: StatusFilter | None = None,
    q: str | None = Query(None, max_length=320),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> SubscriberPage:
    counts = dict(
        db.execute(select(Subscriber.status, func.count()).group_by(Subscriber.status)).all()
    )
    total = db.scalar(_filtered(select(func.count()).select_from(Subscriber), status, q))
    rows = db.scalars(
        _filtered(select(Subscriber), status, q)
        .order_by(Subscriber.created_at.desc(), Subscriber.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return SubscriberPage(
        total=total,
        counts={s: counts.get(s, 0) for s in ("pending", "subscribed", "unsubscribed")},
        subscribers=[SubscriberOut.of(s) for s in rows],
    )


def _cell(value: object) -> str:
    """Spreadsheet-safe: a cell starting with = + - @ would run as a formula in Excel."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@router.get("/subscribers.csv")
def export_subscribers(
    _: AdminUser, db: DB, status: StatusFilter | None = "subscribed"
) -> Response:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["email", "status", "source", "created_at", "confirmed_at", "unsubscribed_at"])
    for s in db.scalars(_filtered(select(Subscriber), status, None).order_by(Subscriber.email)):
        writer.writerow(
            [
                _cell(v)
                for v in (
                    s.email,
                    s.status,
                    s.source,
                    s.created_at,
                    s.confirmed_at,
                    s.unsubscribed_at,
                )
            ]
        )
    return Response(
        out.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="lumawen-subscribers.csv"',
            "Cache-Control": "no-store",
        },
    )


@router.delete("/subscribers/{subscriber_id}", status_code=204)
def delete_subscriber(subscriber_id: uuid.UUID, _: AdminUser, db: DB) -> Response:
    """Erase someone completely (a privacy request). To just stop emails, they unsubscribe."""
    result = db.execute(delete(Subscriber).where(Subscriber.id == subscriber_id))
    if result.rowcount == 0:
        raise HTTPException(404, "No such subscriber.")
    db.commit()
    return Response(status_code=204)
