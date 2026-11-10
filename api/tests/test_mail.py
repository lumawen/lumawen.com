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
    assert "token=" in msg.get_body(preferencelist=("plain",)).get_content()
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


def test_email_has_dark_html_with_inline_logo(client, db):
    register(client, "html@example.com")
    captured: list[EmailMessage] = []
    send_due(db, captured.append)
    [msg] = captured
    assert msg.get_content_type() == "multipart/alternative"
    text = msg.get_body(preferencelist=("plain",))
    assert "token=" in text.get_content()  # plain text is still there for every client
    html = msg.get_body(preferencelist=("html",)).get_content()
    assert 'bgcolor="#000000"' in html and "color-scheme" in html
    assert "Confirm my email" in html  # the lone link became a button
    assert "cid:lumawen-logo@lumawen.com" in html
    [logo] = [p for p in msg.walk() if p.get_content_type() == "image/png"]
    assert logo["Content-ID"] == "<lumawen-logo@lumawen.com>"
    assert logo.get_content()[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_html_escapes_and_splits_footer():
    from lumawen_api.emailhtml import render_html

    html = render_html(
        "Hi <b>there</b> & see https://lumawen.com/x.\n\n--\nUnsubscribe: https://u.example/t",
        "News",
        "newsletter",
    )
    assert "&lt;b&gt;there&lt;/b&gt; &amp;" in html
    assert '<a href="https://lumawen.com/x"' in html  # trailing period not in the link
    assert "border-top" in html and 'href="https://u.example/t"' in html


def _row(to: str, subject: str, kind: str, **extra) -> OutboxEmail:
    return OutboxEmail(to_address=to, subject=subject, body_text="x", kind=kind, **extra)


def _row(to: str, subject: str, kind: str, **extra) -> OutboxEmail:
    return OutboxEmail(to_address=to, subject=subject, body_text="x", kind=kind, **extra)


def test_resets_jump_ahead_of_a_signup_flood_and_newsletters_go_last(db):
    for i in range(5):
        db.add(_row(f"n{i}@example.com", "News", "newsletter"))
    for i in range(30):
        db.add(_row(f"f{i}@example.com", "Confirm", "list_confirm"))
    db.add(_row("me@example.com", "Reset", "reset"))
    db.commit()
    captured: list[EmailMessage] = []
    send_due(db, captured.append, limit=31)
    assert captured[0]["To"] == "me@example.com"
    assert [m["Subject"] for m in captured[1:]] == ["Confirm"] * 30
    # The newsletters waited for everything else.
    assert len(outbox(kind="newsletter")) == 5
    assert all(r.status == "pending" for r in outbox(kind="newsletter"))


def test_a_backlog_is_logged(db, caplog):
    from datetime import UTC, datetime, timedelta

    from lumawen_api.mail import check_backlog

    assert check_backlog(db) is False
    old = datetime.now(UTC) - timedelta(minutes=20)
    db.add(_row("a@example.com", "s", "confirm", send_after=old))
    db.commit()
    assert check_backlog(db) is True
    assert "email backlog" in caplog.text
