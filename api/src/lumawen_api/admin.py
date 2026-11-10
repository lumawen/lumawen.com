"""Admin-only API for the mailing list. Users and the activity log are in staff.py."""

import csv
import io
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import delete, func, select

from .audit import record
from .auth import DB, AdminUser
from .mail import forget_address
from .models import Subscriber
from .paging import Limit, Offset

router = APIRouter(prefix="/admin", tags=["admin"])


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
    limit: Limit = 50,
    offset: Offset = 0,
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
    admin: AdminUser, db: DB, status: StatusFilter | Literal["all"] = "subscribed"
) -> Response:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["email", "status", "source", "created_at", "confirmed_at", "unsubscribed_at"])
    for s in db.scalars(
        _filtered(select(Subscriber), None if status == "all" else status, None).order_by(
            Subscriber.email
        )
    ):
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
    record(db, admin, "subscribers.export", f"status={status}")
    db.commit()
    return Response(
        out.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="lumawen-subscribers.csv"',
            "Cache-Control": "no-store",
        },
    )


@router.delete("/subscribers/{subscriber_id}", status_code=204)
def delete_subscriber(subscriber_id: uuid.UUID, admin: AdminUser, db: DB) -> Response:
    """Erase someone completely (a privacy request). To just stop emails, they unsubscribe."""
    gone = db.execute(
        delete(Subscriber)
        .where(Subscriber.id == subscriber_id)
        .returning(Subscriber.email, Subscriber.status, Subscriber.created_at)
    ).first()
    if gone is None:
        raise HTTPException(404, "No such subscriber.")
    email, sub_status, created_at = gone
    forget_address(db, email)
    record(
        db,
        admin,
        "subscriber.erase",
        email,
        status=sub_status,
        signed_up=created_at.date().isoformat(),
    )
    db.commit()
    return Response(status_code=204)
