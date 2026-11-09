"""Email outbox: requests queue messages here, and the send worker delivers them.

Queueing in the database means a slow or down mail server never slows a request, and failed
sends are retried. Run the worker with: lumawen-admin send-emails --loop
"""

import logging
import signal
import smtplib
import ssl
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .models import OutboxEmail

log = logging.getLogger("lumawen.mail")
MAX_ATTEMPTS = 5


def recent_count(db: Session, to_address: str, kind: str) -> int:
    since = datetime.now(UTC) - timedelta(hours=1)
    return db.scalar(
        select(func.count())
        .select_from(OutboxEmail)
        .where(
            OutboxEmail.to_address == to_address,
            OutboxEmail.kind == kind,
            OutboxEmail.created_at > since,
        )
    )


def queue_email(db: Session, to_address: str, subject: str, body: str, kind: str) -> bool:
    """Add an email to the outbox, unless this address got too many of this kind lately."""
    if recent_count(db, to_address, kind) >= get_settings().emails_per_hour:
        log.info("rate limited %s email to %s", kind, to_address)
        return False
    db.add(OutboxEmail(to_address=to_address, subject=subject, body_text=body, kind=kind))
    return True


def build_message(row: OutboxEmail, settings: Settings) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = settings.mail_from
    msg["To"] = row.to_address
    msg["Subject"] = row.subject
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain="lumawen.com")
    if row.list_unsubscribe:
        # Newsletters: mail apps show their own unsubscribe button (RFC 2369 + RFC 8058).
        msg["List-Unsubscribe"] = f"<{row.list_unsubscribe}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    else:
        msg["Auto-Submitted"] = "auto-generated"
    msg.set_content(row.body_text)
    return msg


Sender = Callable[[EmailMessage], None]


class ConsoleSender:
    """Development and tests: log the email instead of sending it."""

    def __call__(self, msg: EmailMessage) -> None:
        log.info("email (not sent, console backend) to=%s subject=%s", msg["To"], msg["Subject"])

    def close(self) -> None:
        pass


class SmtpSender:
    """Sends through one SMTP connection, opened on first use."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.conn: smtplib.SMTP | None = None

    def _connect(self) -> smtplib.SMTP:
        s = self.settings
        context = ssl.create_default_context()
        if s.smtp_port == 465:
            conn: smtplib.SMTP = smtplib.SMTP_SSL(
                s.smtp_host, s.smtp_port, timeout=30, context=context
            )
        else:
            conn = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30)
            conn.starttls(context=context)
        if s.smtp_username:
            conn.login(s.smtp_username, s.smtp_password)
        return conn

    def __call__(self, msg: EmailMessage) -> None:
        if self.conn is None:
            self.conn = self._connect()
        try:
            self.conn.send_message(msg)
        except (smtplib.SMTPServerDisconnected, OSError):
            self.conn = None  # reconnect for the next email
            raise

    def close(self) -> None:
        if self.conn is not None:
            try:
                self.conn.quit()
            except smtplib.SMTPException:
                pass
            self.conn = None


def make_sender(settings: Settings) -> ConsoleSender | SmtpSender:
    if settings.mail_backend == "smtp":
        return SmtpSender(settings)
    if settings.is_production:
        log.warning("LUMAWEN_MAIL_BACKEND is not smtp: emails are only logged, not sent")
    return ConsoleSender()


def _still_subscribed(db: Session, row: OutboxEmail) -> bool:
    from .models import Subscriber  # newsletters only

    status = db.scalar(select(Subscriber.status).where(Subscriber.email == row.to_address))
    return status == "subscribed"


def send_due(
    db: Session,
    sender: Sender,
    limit: int = 20,
    pause: float = 0.0,
    stop: threading.Event | None = None,
) -> int:
    """Send up to `limit` due emails. Returns how many were sent. Safe to run in parallel.

    Each email is locked, sent and committed on its own, so a crash or kill can resend at most
    the one email that was in flight.
    """
    settings = get_settings()
    sent = 0
    for _ in range(limit):
        if stop is not None and stop.is_set():
            break
        now = datetime.now(UTC)
        row = db.scalar(
            select(OutboxEmail)
            .where(OutboxEmail.status == "pending", OutboxEmail.send_after <= now)
            .order_by(OutboxEmail.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            break
        if row.kind == "newsletter" and not _still_subscribed(db, row):
            db.delete(row)  # unsubscribed (or erased) after the newsletter was queued
            db.commit()
            continue
        try:
            sender(build_message(row, settings))
        except Exception as exc:  # noqa: BLE001 (any failure is retried later)
            row.attempts += 1
            row.last_error = f"{type(exc).__name__}: {exc}"[:1000]
            if row.attempts >= MAX_ATTEMPTS:
                row.status = "failed"
                log.error("giving up on email %s to %s: %s", row.id, row.to_address, exc)
            else:
                row.send_after = now + timedelta(minutes=2**row.attempts)
                log.warning("email %s failed (attempt %s): %s", row.id, row.attempts, exc)
        else:
            row.status = "sent"
            row.sent_at = datetime.now(UTC)
            sent += 1
        db.commit()
        if pause:
            time.sleep(pause)
    db.commit()
    return sent


# Sent and failed emails are deleted after this many days (they hold addresses and links).
KEEP_SENT_DAYS = 30


def purge_outbox(db: Session) -> int:
    cutoff = datetime.now(UTC) - timedelta(days=KEEP_SENT_DAYS)
    result = db.execute(
        delete(OutboxEmail).where(
            OutboxEmail.status.in_(("sent", "failed")), OutboxEmail.created_at < cutoff
        )
    )
    db.commit()
    return result.rowcount


def forget_address(db: Session, email: str, newsletters_only: bool = False) -> None:
    """Drop queued newsletters to an address (on unsubscribe), or every list email to it and
    every copy already sent (erase). Pending account emails (confirm, reset) are kept."""
    query = delete(OutboxEmail).where(OutboxEmail.to_address == email)
    if not newsletters_only:
        query = query.where(
            OutboxEmail.kind.in_(("list_confirm", "newsletter")) | (OutboxEmail.status != "pending")
        )
    if newsletters_only:
        query = query.where(OutboxEmail.kind == "newsletter", OutboxEmail.status == "pending")
    db.execute(query)


def _stop_on_signals() -> threading.Event:
    """pm2 stops the worker with SIGINT (deploys) or SIGTERM. Finish the email in flight, then
    exit, so an email that went out is always marked sent and never goes out twice."""
    stop = threading.Event()

    def handler(signum, frame) -> None:
        log.info("stopping after this batch")
        stop.set()

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)
    return stop


def run_worker(session_factory: Callable[[], Session], loop: bool, interval: float = 5.0) -> None:
    settings = get_settings()
    pause = 1.0 / settings.mail_per_second if settings.mail_per_second > 0 else 0.0
    from .auth import purge_throttle  # here to avoid an import cycle
    from .lists import purge_unconfirmed
    from .newsletters import queue_due_newsletters

    stop = _stop_on_signals() if loop else threading.Event()
    last_purge = 0.0
    while not stop.is_set():
        if time.monotonic() - last_purge > 3600:
            with session_factory() as db:
                purge_throttle(db)
                purge_unconfirmed(db)
                purge_outbox(db)
            last_purge = time.monotonic()
        try:
            with session_factory() as db:
                queue_due_newsletters(db)
        except Exception:  # keep sign-up and reset emails flowing whatever happened here
            log.exception("could not queue newsletters")
        sender = make_sender(settings)
        try:
            with session_factory() as db:
                sent = send_due(db, sender, pause=pause, stop=stop)
        finally:
            sender.close()
        if sent:
            log.info("sent %s emails", sent)
        if not loop:
            return
        if not sent:
            stop.wait(interval)
    log.info("stopped")
