"""Email outbox: requests queue messages here, and the send worker delivers them.

Queueing in the database means a slow or down mail server never slows a request, and failed
sends are retried. Run the worker with: lumawen-admin send-emails --loop
"""

import logging
import smtplib
import ssl
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from sqlalchemy import func, select
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
        self.conn.send_message(msg)

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


def send_due(db: Session, sender: Sender, limit: int = 20, pause: float = 0.0) -> int:
    """Send up to `limit` due emails. Returns how many were sent. Safe to run in parallel."""
    settings = get_settings()
    now = datetime.now(UTC)
    rows = db.scalars(
        select(OutboxEmail)
        .where(OutboxEmail.status == "pending", OutboxEmail.send_after <= now)
        .order_by(OutboxEmail.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    sent = 0
    for row in rows:
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
        if pause:
            time.sleep(pause)
    db.commit()
    return sent


def run_worker(session_factory: Callable[[], Session], loop: bool, interval: float = 5.0) -> None:
    settings = get_settings()
    pause = 1.0 / settings.mail_per_second if settings.mail_per_second > 0 else 0.0
    while True:
        sender = make_sender(settings)
        try:
            with session_factory() as db:
                sent = send_due(db, sender, pause=pause)
        finally:
            sender.close()
        if sent:
            log.info("sent %s emails", sent)
        if not loop:
            return
        if not sent:
            time.sleep(interval)
