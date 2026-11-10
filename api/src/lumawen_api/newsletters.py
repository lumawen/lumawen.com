"""Newsletters: write one, send yourself a test, then schedule it or send it to everyone.

Contributors write and test drafts; only admins schedule, send, or delete what's scheduled or
sent. Deleting a sent newsletter archives it, and the worker deletes it 30 days later.

"Send" means scheduling for now: the send worker picks up due newsletters, copies each into the
outbox once per subscriber (with their own unsubscribe link and the List-Unsubscribe headers
mail apps use for their unsubscribe button), then delivers them at LUMAWEN_MAIL_PER_SECOND.
Run it with: lumawen-admin send-emails --loop
"""

import hashlib
import logging
import unicodedata
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import AfterValidator, BaseModel, Field
from sqlalchemy import delete, func, literal, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .audit import record
from .auth import DB, AdminUser, Email, WriterUser, normalize_email
from .config import get_settings
from .models import OUTBOX_PRIORITY, Newsletter, OutboxEmail, Subscriber
from .paging import Limit, Offset

log = logging.getLogger("lumawen.newsletters")
router = APIRouter(prefix="/admin/newsletters", tags=["admin"])

TESTS_PER_HOUR = 20
ARCHIVE_DAYS = 30
FOOTER = (
    "\n\n--\n"
    "You're getting this because you signed up for Lumawen updates at lumawen.com.\n"
    "Unsubscribe: "
)


def _one_line(subject: str) -> str:
    subject = subject.strip()
    if not subject:
        raise ValueError("The subject can't be empty.")
    if any(unicodedata.category(c) in ("Cc", "Zl", "Zp") for c in subject):
        raise ValueError("The subject must be one line, without control characters.")
    return subject


Subject = Annotated[str, Field(max_length=200), AfterValidator(_one_line)]
Body = Annotated[str, Field(min_length=1, max_length=100_000)]


class NewsletterIn(BaseModel):
    subject: Subject
    body_text: Body


class NewsletterPatch(BaseModel):
    subject: Subject | None = None
    body_text: Body | None = None


class ScheduleIn(BaseModel):
    send_at: datetime | None = None  # None = now
    # The updated_at the admin last saw: refuse if someone edited it since (so you never
    # send words you haven't read).
    seen_version: datetime | None = None


class TestIn(BaseModel):
    to: Email | None = None  # None = you (contributors can only send tests to themselves)


class Delivery(BaseModel):
    """Outbox copies by status. Copies are purged 30 days after sending (and dropped when
    someone unsubscribes first), so for older newsletters `recipients` is the lasting count."""

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
    archived_at: datetime | None
    delete_after: datetime | None  # archived: when the worker deletes it for good
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
        archived_at=n.archived_at,
        delete_after=n.archived_at + timedelta(days=ARCHIVE_DAYS) if n.archived_at else None,
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


def _body_hash(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()[:16]


def _writable(n: Newsletter, user) -> None:
    """Admins can edit until it's sent; contributors only drafts."""
    _not_sent(n)
    if n.status != "draft" and user.role != "admin":
        raise HTTPException(403, "Only an admin can change a scheduled newsletter.")


def purge_archived(db: Session) -> int:
    """Delete newsletters archived more than ARCHIVE_DAYS ago (the worker runs this hourly)."""
    result = db.execute(
        delete(Newsletter).where(Newsletter.archived_at < _now() - timedelta(days=ARCHIVE_DAYS))
    )
    db.commit()
    return result.rowcount


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
            literal(OUTBOX_PRIORITY["newsletter"]),
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
                        "priority",
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
    _: WriterUser,
    db: DB,
    archived: bool = False,
    limit: Limit = 50,
    offset: Offset = 0,
) -> NewsletterPage:
    """Newest first. archived=true lists the deleted sent ones (kept 30 days)."""
    where = Newsletter.archived_at.is_not(None) if archived else Newsletter.archived_at.is_(None)
    total = db.scalar(select(func.count()).select_from(Newsletter).where(where))
    rows = db.scalars(
        select(Newsletter)
        .where(where)
        .order_by(Newsletter.created_at.desc(), Newsletter.id)
        .limit(limit)
        .offset(offset)
    ).all()
    deliveries = _deliveries(db, [n.id for n in rows])
    return NewsletterPage(total=total, newsletters=[_out(n, deliveries[n.id]) for n in rows])


@router.post("", status_code=201, response_model=NewsletterOut)
def create_newsletter(body: NewsletterIn, writer: WriterUser, db: DB) -> NewsletterOut:
    n = Newsletter(subject=body.subject, body_text=body.body_text, created_by=writer.id)
    db.add(n)
    db.commit()
    db.refresh(n)
    return _out(n, Delivery())


@router.get("/{newsletter_id}", response_model=NewsletterOut)
def get_newsletter(newsletter_id: uuid.UUID, _: WriterUser, db: DB) -> NewsletterOut:
    n = _get(db, newsletter_id)
    return _out(n, _deliveries(db, [n.id])[n.id])


@router.patch("/{newsletter_id}", response_model=NewsletterOut)
def edit_newsletter(
    newsletter_id: uuid.UUID, body: NewsletterPatch, writer: WriterUser, db: DB
) -> NewsletterOut:
    n = _get(db, newsletter_id, lock=True)
    _writable(n, writer)
    before = n.subject, _body_hash(n.body_text)
    if body.subject is not None:
        n.subject = body.subject
    if body.body_text is not None:
        n.body_text = body.body_text
    # Every edit is logged, drafts too, with a hash of the body, so a body swapped before an
    # admin schedules it shows up in the activity log with who did it.
    after = n.subject, _body_hash(n.body_text)
    if after != before:
        record(
            db,
            writer,
            f"newsletter.edit_{n.status}",
            n.subject,
            subject_before=before[0],
            body_before=before[1],
            body_after=after[1],
        )
    db.commit()
    db.refresh(n)
    return _out(n, Delivery())


@router.delete("/{newsletter_id}", status_code=204)
def delete_newsletter(newsletter_id: uuid.UUID, writer: WriterUser, db: DB) -> Response:
    """A draft is deleted; a scheduled one is cancelled and deleted; a sent one is archived
    (hidden, restorable) and deleted for good after ARCHIVE_DAYS."""
    n = _get(db, newsletter_id, lock=True)
    if n.status != "draft" and writer.role != "admin":
        raise HTTPException(403, "Only an admin can delete a scheduled or sent newsletter.")
    if n.status == "sent":
        if n.archived_at is None:
            n.archived_at = _now()
            record(db, writer, "newsletter.archive", n.subject)
    else:
        record(db, writer, f"newsletter.delete_{n.status}", n.subject)
        db.delete(n)
    db.commit()
    return Response(status_code=204)


@router.post("/{newsletter_id}/restore", response_model=NewsletterOut)
def restore(newsletter_id: uuid.UUID, admin: AdminUser, db: DB) -> NewsletterOut:
    """Bring an archived newsletter back before it's deleted for good."""
    n = _get(db, newsletter_id, lock=True)
    if n.archived_at is not None:
        n.archived_at = None
        record(db, admin, "newsletter.restore", n.subject)
    db.commit()
    db.refresh(n)
    return _out(n, _deliveries(db, [n.id])[n.id])


@router.post("/{newsletter_id}/test", response_model=NewsletterOut)
def send_test(newsletter_id: uuid.UUID, body: TestIn, writer: WriterUser, db: DB) -> NewsletterOut:
    """Send one copy, marked [Test], to you (admins: or another address) right away."""
    n = _get(db, newsletter_id)
    to = normalize_email(body.to) if body.to else writer.email
    if to != writer.email:
        if writer.role != "admin":
            raise HTTPException(403, "Contributors can only send tests to themselves.")
        record(db, writer, "newsletter.test_to", n.subject, to=to)
    recent = db.scalar(
        select(func.count())
        .select_from(OutboxEmail)
        .where(
            OutboxEmail.kind == "newsletter_test",
            OutboxEmail.created_at > _now() - timedelta(hours=1),
        )
    )
    if recent >= TESTS_PER_HOUR * 3:  # all writers together
        raise HTTPException(429, "Too many test emails this hour. Try again later.")
    mine = db.scalar(
        select(func.count())
        .select_from(OutboxEmail)
        .where(
            OutboxEmail.kind == "newsletter_test",
            OutboxEmail.to_address == to,
            OutboxEmail.created_at > _now() - timedelta(hours=1),
        )
    )
    if mine >= TESTS_PER_HOUR:
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
def schedule(newsletter_id: uuid.UUID, body: ScheduleIn, admin: AdminUser, db: DB) -> NewsletterOut:
    """Send to everyone subscribed at send_at (or now). Can be cancelled until then."""
    n = _get(db, newsletter_id, lock=True)
    _not_sent(n)
    if body.seen_version is not None and body.seen_version != n.updated_at:
        raise HTTPException(409, "Someone changed this newsletter since you opened it. Reload it.")
    send_at = body.send_at or _now()
    if send_at.tzinfo is None:
        raise HTTPException(422, "send_at needs a time zone, e.g. 2026-11-20T18:00:00-06:00.")
    if send_at > _now() + timedelta(days=365):
        raise HTTPException(422, "send_at must be within a year.")
    n.status = "scheduled"
    n.send_at = send_at
    record(db, admin, "newsletter.schedule", n.subject, send_at=send_at.isoformat())
    db.commit()
    db.refresh(n)
    return _out(n, Delivery())


@router.post("/{newsletter_id}/cancel", response_model=NewsletterOut)
def cancel(newsletter_id: uuid.UUID, admin: AdminUser, db: DB) -> NewsletterOut:
    """Back to draft. Only before the worker has picked it up."""
    n = _get(db, newsletter_id, lock=True)
    _not_sent(n)
    if n.status == "scheduled":
        record(db, admin, "newsletter.cancel", n.subject)
    n.status = "draft"
    n.send_at = None
    db.commit()
    db.refresh(n)
    return _out(n, Delivery())
