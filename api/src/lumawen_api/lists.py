"""The "Lumawen updates" mailing list: sign up, confirm (double opt-in), unsubscribe.

Sign-up always answers the same way, so nobody can test who is on the list. Links in emails
open a small page with a button, and only the button's POST changes anything: mail scanners
that open every link can't confirm or unsubscribe someone by accident. The unsubscribe URL also
takes a one-click POST from mail apps (RFC 8058), for newsletters sent later.
"""

import html
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .auth import CHECK_EMAIL, DB, Email, Status, _client_ip, _count_attempt, normalize_email
from .config import get_settings
from .mail import forget_address, queue_email
from .maildomain import CANT_RECEIVE, accepts_mail
from .models import OutboxEmail, Subscriber
from .security import hash_token, new_token

router = APIRouter(prefix="/list", tags=["mailing list"])
log = logging.getLogger("lumawen.lists")

CONFIRM_DAYS = 7
# Sign-ups from one address (IPv6 /64) per hour, so nobody can mail-bomb a list of strangers.
SIGNUPS_PER_IP_PER_HOUR = 10
# All confirm emails per hour, so even someone with many IPs can't use us to spam strangers.
CONFIRMS_PER_HOUR = 300
# Sign-ups never confirmed are deleted after this many days (see purge_unconfirmed).
UNCONFIRMED_DAYS = 30

Token = Annotated[str, Query(min_length=10, max_length=100)]


class SubscribeIn(BaseModel):
    email: Email
    # Honeypot: hidden on the site's form, so only bots fill it in.
    website: str = Field("", max_length=200)


def _now() -> datetime:
    return datetime.now(UTC)


def list_url(path: str, token: str) -> str:
    return f"{get_settings().public_base_url.rstrip('/')}/api/list/{path}?token={token}"


def unsubscribe_url(sub: Subscriber) -> str:
    """For newsletters: put this in the footer and the List-Unsubscribe header."""
    return list_url("unsubscribe", sub.unsubscribe_token)


def _confirms_last_hour(db) -> int:
    return db.scalar(
        select(func.count())
        .select_from(OutboxEmail)
        .where(
            OutboxEmail.kind == "list_confirm",
            OutboxEmail.created_at > _now() - timedelta(hours=1),
        )
    )


def _send_confirm(db, sub: Subscriber) -> None:
    if _confirms_last_hour(db) >= CONFIRMS_PER_HOUR:
        log.warning("hourly cap of %s confirm emails reached; not emailing", CONFIRMS_PER_HOUR)
        return
    raw, digest = new_token()
    body = (
        "Hi,\n\n"
        "Someone (hopefully you) asked to get Lumawen updates at this address. "
        "Confirm here:\n\n"
        f"{list_url('confirm', raw)}\n\n"
        f"The link works for {CONFIRM_DAYS} days. If this wasn't you, ignore this email "
        "and you won't hear from us.\n\n"
        "Lumawen\nhttps://lumawen.com\n"
    )
    if queue_email(db, sub.email, "Confirm your Lumawen updates", body, kind="list_confirm"):
        sub.confirm_token_hash = digest
        sub.confirm_expires_at = _now() + timedelta(days=CONFIRM_DAYS)


@router.post("/subscribe", status_code=202, response_model=Status)
def subscribe(body: SubscribeIn, request: Request, db: DB) -> Status:
    if body.website:
        return CHECK_EMAIL  # a bot; pretend it worked
    ip_rule = [(f"subscribe:{_client_ip(request)}", SIGNUPS_PER_IP_PER_HOUR, timedelta(hours=1))]
    if not _count_attempt(db, ip_rule):
        return CHECK_EMAIL  # same answer; nothing is sent
    email = normalize_email(body.email)
    if not accepts_mail(email):
        raise HTTPException(422, CANT_RECEIVE)
    db.execute(
        pg_insert(Subscriber)
        .values(
            id=uuid.uuid4(),
            email=email,
            status="pending",
            source="site",
            unsubscribe_token=secrets.token_urlsafe(32),
        )
        .on_conflict_do_nothing(index_elements=["email"])
    )
    sub = db.scalar(select(Subscriber).where(Subscriber.email == email).with_for_update())
    if sub is not None and sub.status != "subscribed":  # None: an admin erased it just now
        _send_confirm(db, sub)
    db.commit()
    return CHECK_EMAIL


# ---- pages behind the email links ------------------------------------------------------------

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{title} · Lumawen</title>
<style>
  body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; background: #000;
         color: #fff; font: 17px/1.6 system-ui, -apple-system, "Segoe UI", sans-serif; }}
  main {{ max-width: 30em; padding: 32px 20px; text-align: center; }}
  h1 {{ font-size: 28px; letter-spacing: -0.02em; margin: 0 0 12px; }}
  p {{ color: rgba(255,255,255,.7); margin: 0 0 24px; }}
  button {{ font: inherit; font-weight: 700; background: #fff; color: #000; border: 0;
           border-radius: 999px; padding: 12px 28px; cursor: pointer; }}
  a {{ color: #fff; }}
</style></head>
<body><main><h1>{title}</h1><p>{text}</p>{action}</main></body></html>
"""


def _page(title: str, text: str, button: str = "", form_action: str = "", status: int = 200):
    action = (
        f'<form method="post" action="{html.escape(form_action)}">'
        f'<button type="submit">{html.escape(button)}</button></form>'
        if button
        else '<a href="https://lumawen.com">lumawen.com</a>'
    )
    return HTMLResponse(
        _PAGE.format(title=html.escape(title), text=html.escape(text), action=action),
        status_code=status,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


BAD_LINK_PAGE = (
    "Link expired",
    "This link is invalid or has expired. Sign up again on lumawen.com.",
)
NOT_ON_LIST_PAGE = ("You're not on the list", "This address won't get any Lumawen updates.")


def _pending_by_token(db, token: str) -> Subscriber | None:
    sub = db.scalar(
        select(Subscriber)
        .where(Subscriber.confirm_token_hash == hash_token(token))
        .with_for_update()
    )
    if sub is None or sub.confirm_expires_at is None or sub.confirm_expires_at < _now():
        return None
    return sub


@router.get("/confirm", response_class=HTMLResponse)
def confirm_page(token: Token, db: DB):
    if _pending_by_token(db, token) is None:
        return _page(*BAD_LINK_PAGE, status=400)
    return _page(
        "Confirm your subscription",
        "One click and you'll get Lumawen updates: new builds, lessons and releases.",
        "Yes, send me updates",
        f"/api/list/confirm?token={token}",
    )


@router.post("/confirm", response_class=HTMLResponse)
def confirm(token: Token, db: DB):
    sub = _pending_by_token(db, token)
    if sub is None:
        return _page(*BAD_LINK_PAGE, status=400)
    sub.status = "subscribed"
    sub.confirmed_at = _now()
    sub.unsubscribed_at = None
    sub.confirm_token_hash = None
    sub.confirm_expires_at = None
    db.commit()
    return _page("You're on the list", "Thanks! Every email has a link to unsubscribe.")


def _by_unsubscribe_token(db, token: str) -> Subscriber | None:
    return db.scalar(
        select(Subscriber).where(Subscriber.unsubscribe_token == token).with_for_update()
    )


@router.get("/unsubscribe", response_class=HTMLResponse)
def unsubscribe_page(token: Token, db: DB):
    sub = _by_unsubscribe_token(db, token)
    if sub is None:
        return _page(*NOT_ON_LIST_PAGE, status=400)
    if sub.status == "unsubscribed":
        return _page("You're unsubscribed", "You won't get any more Lumawen updates.")
    return _page(
        "Unsubscribe",
        "Stop getting Lumawen updates at this address?",
        "Unsubscribe",
        f"/api/list/unsubscribe?token={token}",
    )


@router.post("/unsubscribe", response_class=HTMLResponse)
def unsubscribe(token: Token, db: DB):
    """The page's button, and one-click unsubscribe from mail apps (RFC 8058)."""
    sub = _by_unsubscribe_token(db, token)
    if sub is None:
        return _page(*NOT_ON_LIST_PAGE, status=400)
    if sub.status != "unsubscribed":
        sub.status = "unsubscribed"
        sub.unsubscribed_at = _now()
        forget_address(db, sub.email, newsletters_only=True)
        sub.confirm_token_hash = None
        sub.confirm_expires_at = None
        db.commit()
    return _page("You're unsubscribed", "You won't get any more Lumawen updates.")


def purge_unconfirmed(db) -> int:
    """Forget sign-ups nobody confirmed (called by the send worker)."""
    cutoff = _now() - timedelta(days=UNCONFIRMED_DAYS)
    result = db.execute(
        delete(Subscriber).where(
            Subscriber.status == "pending",
            Subscriber.created_at < cutoff,
            # Not someone who asked again lately and still has a live link.
            Subscriber.confirm_expires_at.is_(None) | (Subscriber.confirm_expires_at < _now()),
        )
    )
    db.commit()
    return result.rowcount
