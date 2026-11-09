"""Newsletters: write one, send yourself a test, then schedule it or send it to everyone.

Admins only. "Send" means scheduling for now: the send worker picks up due newsletters, copies
each into the outbox once per subscriber (with their own unsubscribe link and the
List-Unsubscribe headers mail apps use for their unsubscribe button), then delivers them at
LUMAWEN_MAIL_PER_SECOND. Run it with: lumawen-admin send-emails --loop
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, literal, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .auth import DB, AdminUser, Email, normalize_email
from .config import get_settings
from .models import Newsletter, OutboxEmail, Subscriber

log = logging.getLogger("lumawen.newsletters")
router = APIRouter(prefix="/admin/newsletters", tags=["admin"])

TESTS_PER_HOUR = 20
FOOTER = (
    "\n\n--\n"
    "You're getting this because you signed up for Lumawen updates at lumawen.com.\n"
    "Unsubscribe: "
)

Subject = Field(min_length=1, max_length=200)
Body = Field(min_length=1, max_length=100_000)


class NewsletterIn(BaseModel):
    subject: str = Subject
    body_text: str = Body


class NewsletterPatch(BaseModel):
    subject: str | None = Field(None, min_length=1, max_length=200)
    body_text: str | None = Field(None, min_length=1, max_length=100_000)


class ScheduleIn(BaseModel):
    send_at: datetime | None = None  # None = now


class TestIn(BaseModel):
    to: Email | None = None  # None = the signed-in admin


class Delivery(BaseModel):
    pending: int = 0
    sent: int = 0
    failed: int = 0


class NewsletterOut(BaseModel):
    id: uuid.UUID
    subject: str
    body_text: str
    status: str
    send_at: datetime | None
    queued_at: datetime | None
    recipients: int | None
    delivery: Delivery
    created_at: datetime
    updated_at: datetime


class NewsletterPage(BaseModel):
    total: int
    newsletters: list[NewsletterOut]


def _now() -> datetime:
    return datetime.now(UTC)


def _unsubscribe_base() -> str:
    return f"{get_settings().public_base_url.rstrip('/')}/api/list/unsubscribe?token="


def _deliveries(db: Session, ids: list[uuid.UUID]) -> dict[uuid.UUID, Delivery]:
    out: dict[uuid.UUID, Delivery] = {i: Delivery() for i in ids}
    if not ids:
        return out
    rows = db.execute(
        select(OutboxEmail.newsletter_id, OutboxEmail.status, func.count())
        .where(OutboxEmail.newsletter_id.in_(ids), OutboxEmail.kind == "newsletter")
        .group_by(OutboxEmail.newsletter_id, OutboxEmail.status)
    ).all()
    for newsletter_id, status, count in rows:
        setattr(out[newsletter_id], status, count)
    return out


def _out(n: Newsletter, delivery: Delivery) -> NewsletterOut:
    return NewsletterOut(
        id=n.id,
        subject=n.subject,
        body_text=n.body_text,
        status=n.status,
        send_at=n.send_at,
        queued_at=n.queued_at,
        recipients=n.recipients,
        delivery=delivery,
        created_at=n.created_at,
        updated_at=n.updated_at,
    )


def _get(db: Session, newsletter_id: uuid.UUID, lock: bool = False) -> Newsletter:
    query = select(Newsletter).where(Newsletter.id == newsletter_id)
    if lock:
        query = query.with_for_update()
    n = db.scalar(query)
    if n is None:
        raise HTTPException(404, "No such newsletter.")
    return n


def _not_sent(n: Newsletter) -> None:
    if n.status == "sent":
        raise HTTPException(409, "This newsletter was already sent.")


# ---- the send worker's part ------------------------------------------------------------------


def queue_due_newsletters(db: Session) -> int:
    """Copy each due newsletter into the outbox, once per subscriber. Returns emails queued."""
    queued = 0
    while True:
        n = db.scalar(
            select(Newsletter)
            .where(Newsletter.status == "scheduled", Newsletter.send_at <= _now())
            .order_by(Newsletter.send_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if n is None:
            db.commit()
            return queued
        unsubscribe = literal(_unsubscribe_base()) + Subscriber.unsubscribe_token
        rows = select(
            Subscriber.email,
            literal(n.subject),
            literal(n.body_text + FOOTER) + unsubscribe,
            literal("newsletter"),
            literal(n.id),
            unsubscribe,
        ).where(Subscriber.status == "subscribed")
        count = len(
            db.execute(
                pg_insert(OutboxEmail)
                .from_select(
                    [
                        "to_address",
                        "subject",
                        "body_text",
                        "kind",
                        "newsletter_id",
                        "list_unsubscribe",
                    ],
                    rows,
                )
                .on_conflict_do_nothing(
                    index_elements=["newsletter_id", "to_address"],
                    index_where=OutboxEmail.kind == "newsletter",
                )
                .returning(OutboxEmail.id)
            ).all()
        )
        n.status = "sent"
        n.queued_at = _now()
        n.recipients = count
        db.commit()
        queued += count
        log.info("newsletter %s queued for %s subscribers", n.id, count)


# ---- admin routes ----------------------------------------------------------------------------


@router.get("", response_model=NewsletterPage)
def list_newsletters(
    _: AdminUser,
    db: DB,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> NewsletterPage:
    total = db.scalar(select(func.count()).select_from(Newsletter))
    rows = db.scalars(
        select(Newsletter)
        .order_by(Newsletter.created_at.desc(), Newsletter.id)
        .limit(limit)
        .offset(offset)
    ).all()
    deliveries = _deliveries(db, [n.id for n in rows])
    return NewsletterPage(total=total, newsletters=[_out(n, deliveries[n.id]) for n in rows])


@router.post("", status_code=201, response_model=NewsletterOut)
def create_newsletter(body: NewsletterIn, admin: AdminUser, db: DB) -> NewsletterOut:
    n = Newsletter(subject=body.subject, body_text=body.body_text, created_by=admin.id)
    db.add(n)
    db.commit()
    db.refresh(n)
    return _out(n, Delivery())


@router.get("/{newsletter_id}", response_model=NewsletterOut)
def get_newsletter(newsletter_id: uuid.UUID, _: AdminUser, db: DB) -> NewsletterOut:
    n = _get(db, newsletter_id)
    return _out(n, _deliveries(db, [n.id])[n.id])


@router.patch("/{newsletter_id}", response_model=NewsletterOut)
def edit_newsletter(
    newsletter_id: uuid.UUID, body: NewsletterPatch, _: AdminUser, db: DB
) -> NewsletterOut:
    n = _get(db, newsletter_id, lock=True)
    _not_sent(n)
    if body.subject is not None:
        n.subject = body.subject
    if body.body_text is not None:
        n.body_text = body.body_text
    db.commit()
    db.refresh(n)
    return _out(n, Delivery())


@router.delete("/{newsletter_id}", status_code=204)
def delete_newsletter(newsletter_id: uuid.UUID, _: AdminUser, db: DB) -> Response:
    n = _get(db, newsletter_id, lock=True)
    _not_sent(n)
    db.delete(n)
    db.commit()
    return Response(status_code=204)


@router.post("/{newsletter_id}/test", response_model=NewsletterOut)
def send_test(newsletter_id: uuid.UUID, body: TestIn, admin: AdminUser, db: DB) -> NewsletterOut:
    """Send one copy, marked [Test], to the admin (or another address) right away."""
    n = _get(db, newsletter_id)
    to = normalize_email(body.to) if body.to else admin.email
    recent = db.scalar(
        select(func.count())
        .select_from(OutboxEmail)
        .where(
            OutboxEmail.kind == "newsletter_test",
            OutboxEmail.created_at > _now() - timedelta(hours=1),
        )
    )
    if recent >= TESTS_PER_HOUR:
        raise HTTPException(429, f"At most {TESTS_PER_HOUR} test emails an hour.")
    db.add(
        OutboxEmail(
            to_address=to,
            subject=f"[Test] {n.subject}"[:200],
            body_text=n.body_text + FOOTER + "(the subscriber's own unsubscribe link goes here)",
            kind="newsletter_test",
        )
    )
    db.commit()
    return _out(n, _deliveries(db, [n.id])[n.id])


@router.post("/{newsletter_id}/schedule", response_model=NewsletterOut)
def schedule(newsletter_id: uuid.UUID, body: ScheduleIn, _: AdminUser, db: DB) -> NewsletterOut:
    """Send to everyone subscribed at send_at (or now). Can be cancelled until then."""
    n = _get(db, newsletter_id, lock=True)
    _not_sent(n)
    send_at = body.send_at or _now()
    if send_at.tzinfo is None:
        raise HTTPException(422, "send_at needs a time zone, e.g. 2026-11-20T18:00:00-06:00.")
    if send_at > _now() + timedelta(days=365):
        raise HTTPException(422, "send_at must be within a year.")
    n.status = "scheduled"
    n.send_at = send_at
    db.commit()
    db.refresh(n)
    return _out(n, Delivery())


@router.post("/{newsletter_id}/cancel", response_model=NewsletterOut)
def cancel(newsletter_id: uuid.UUID, _: AdminUser, db: DB) -> NewsletterOut:
    """Back to draft. Only before the worker has picked it up."""
    n = _get(db, newsletter_id, lock=True)
    _not_sent(n)
    n.status = "draft"
    n.send_at = None
    db.commit()
    db.refresh(n)
    return _out(n, Delivery())
