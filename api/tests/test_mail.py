"""The email outbox and send worker."""

from email.message import EmailMessage

from helpers import outbox, register
from sqlalchemy import select

from lumawen_api.mail import ConsoleSender, send_due
from lumawen_api.models import OutboxEmail


def test_worker_sends_queued_email(client, db):
    register(client, "mail@example.com")
    captured: list[EmailMessage] = []
    assert send_due(db, captured.append) == 1
    [msg] = captured
    assert msg["To"] == "mail@example.com"
    assert msg["From"] == "Lumawen <hello@lumawen.com>"
    assert "token=" in msg.get_content()
    [row] = outbox("mail@example.com")
    assert row.status == "sent" and row.sent_at is not None
    assert send_due(db, captured.append) == 0  # nothing sent twice


def test_worker_retries_then_gives_up(client, db):
    register(client, "flaky@example.com")

    def broken(msg: EmailMessage) -> None:
        raise OSError("connection refused")

    assert send_due(db, broken) == 0
    row = db.scalar(select(OutboxEmail))
    assert row.status == "pending" and row.attempts == 1 and "connection refused" in row.last_error
    assert send_due(db, broken) == 0  # backing off: not due yet
    assert row.attempts == 1

    row.attempts = 4
    row.send_after = row.created_at
    db.commit()
    send_due(db, broken)
    db.refresh(row)
    assert row.status == "failed"


def test_console_sender_does_not_raise(client, db):
    register(client, "console@example.com")
    assert send_due(db, ConsoleSender()) == 1
