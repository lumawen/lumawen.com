"""Newsletters: drafts, test sends, scheduling, and queueing one copy per subscriber."""

from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

from helpers import outbox
from sqlalchemy import select
from test_lists import as_admin, subscribe, subscribed

from lumawen_api.mail import send_due
from lumawen_api.models import Newsletter, OutboxEmail
from lumawen_api.newsletters import queue_due_newsletters

ORIGIN = {"Origin": "http://localhost:3040"}


def draft(client, subject="Pickup prototype is alive", body="It hums. Video soon."):
    r = client.post(
        "/api/admin/newsletters", json={"subject": subject, "body_text": body}, headers=ORIGIN
    )
    assert r.status_code == 201, r.text
    return r.json()


def post(client, path, **body):
    return client.post(f"/api/admin/newsletters{path}", json=body, headers=ORIGIN)


def test_newsletters_need_admin(client):
    assert client.get("/api/admin/newsletters").status_code == 401
    assert client.post("/api/admin/newsletters", json={}).status_code == 401


def test_cookie_requests_from_another_site_are_refused(client):
    as_admin(client)
    r = client.post(
        "/api/admin/newsletters",
        json={"subject": "x", "body_text": "y"},
        headers={"Origin": "https://evil.example"},
    )
    assert r.status_code == 403


def test_draft_edit_list_delete(client):
    as_admin(client)
    n = draft(client)
    assert n["status"] == "draft" and n["recipients"] is None
    r = client.patch(
        f"/api/admin/newsletters/{n['id']}", json={"subject": "New subject"}, headers=ORIGIN
    )
    assert r.json()["subject"] == "New subject"
    assert r.json()["body_text"] == "It hums. Video soon."
    page = client.get("/api/admin/newsletters").json()
    assert page["total"] == 1 and page["newsletters"][0]["id"] == n["id"]
    assert client.delete(f"/api/admin/newsletters/{n['id']}", headers=ORIGIN).status_code == 204
    assert client.get(f"/api/admin/newsletters/{n['id']}").status_code == 404


def test_test_send_goes_only_to_the_admin(client):
    subscribed(client, "fan@example.com")
    as_admin(client)
    n = draft(client)
    assert post(client, f"/{n['id']}/test").status_code == 200
    [test] = outbox(kind="newsletter_test")
    assert test.to_address == "admin@lumawen.com"
    assert test.subject == "[Test] Pickup prototype is alive"
    assert "Unsubscribe" in test.body_text
    assert outbox("fan@example.com", "newsletter") == []
    assert post(client, f"/{n['id']}/test", to="Friend@Example.com").status_code == 200
    assert outbox("friend@example.com", "newsletter_test")


def test_test_sends_are_capped(client, monkeypatch):
    monkeypatch.setattr("lumawen_api.newsletters.TESTS_PER_HOUR", 2)
    as_admin(client)
    n = draft(client)
    assert post(client, f"/{n['id']}/test").status_code == 200
    assert post(client, f"/{n['id']}/test").status_code == 200
    assert post(client, f"/{n['id']}/test").status_code == 429


def test_send_now_queues_one_copy_per_subscriber(client, db):
    a = subscribed(client, "a@example.com")
    subscribed(client, "b@example.com")
    subscribe(client, "pending@example.com")
    gone = subscribed(client, "gone@example.com")
    client.post(f"/api/list/unsubscribe?token={gone.unsubscribe_token}")
    as_admin(client)
    n = draft(client)
    r = post(client, f"/{n['id']}/schedule")
    assert r.json()["status"] == "scheduled"

    assert queue_due_newsletters(db) == 2
    assert queue_due_newsletters(db) == 0  # never twice
    rows = outbox(kind="newsletter")
    assert sorted(r.to_address for r in rows) == ["a@example.com", "b@example.com"]
    row_a = next(r for r in rows if r.to_address == "a@example.com")
    link = f"http://localhost:3040/api/list/unsubscribe?token={a.unsubscribe_token}"
    assert row_a.body_text == "It hums. Video soon." + (
        "\n\n--\nYou're getting this because you signed up for Lumawen updates at "
        "lumawen.com.\nUnsubscribe: " + link
    )
    assert row_a.list_unsubscribe == link

    got = client.get(f"/api/admin/newsletters/{n['id']}").json()
    assert got["status"] == "sent" and got["recipients"] == 2
    assert got["delivery"] == {"pending": 2, "sent": 0, "failed": 0}

    captured: list[EmailMessage] = []
    send_due(db, captured.append)
    msg = next(m for m in captured if m["To"] == "a@example.com" and m["Subject"] == n["subject"])
    assert msg["List-Unsubscribe"] == f"<{link}>"
    assert msg["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert msg["Auto-Submitted"] is None
    got = client.get(f"/api/admin/newsletters/{n['id']}").json()
    assert got["delivery"]["sent"] == 2

    # Sent newsletters can't be changed.
    assert post(client, f"/{n['id']}/schedule").status_code == 409
    assert post(client, f"/{n['id']}/cancel").status_code == 409
    nid = n["id"]
    assert (
        client.patch(
            f"/api/admin/newsletters/{nid}", json={"subject": "x"}, headers=ORIGIN
        ).status_code
        == 409
    )
    assert client.delete(f"/api/admin/newsletters/{nid}", headers=ORIGIN).status_code == 409


def test_the_one_click_link_in_a_newsletter_unsubscribes(client, db):
    subscribed(client, "click@example.com")
    as_admin(client)
    n = draft(client)
    post(client, f"/{n['id']}/schedule")
    queue_due_newsletters(db)
    [row] = outbox("click@example.com", "newsletter")
    path = row.list_unsubscribe.removeprefix("http://localhost:3040")
    assert client.post(path, content="List-Unsubscribe=One-Click").status_code == 200
    from test_lists import subscriber

    assert subscriber("click@example.com").status == "unsubscribed"


def test_scheduled_for_later_waits_and_can_be_cancelled(client, db):
    subscribed(client, "later@example.com")
    as_admin(client)
    n = draft(client)
    when = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    r = post(client, f"/{n['id']}/schedule", send_at=when)
    assert r.json()["status"] == "scheduled"
    assert queue_due_newsletters(db) == 0
    r = post(client, f"/{n['id']}/cancel")
    assert r.json()["status"] == "draft" and r.json()["send_at"] is None

    post(client, f"/{n['id']}/schedule", send_at=when)
    row = db.scalar(select(Newsletter))
    row.send_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()
    assert queue_due_newsletters(db) == 1


def test_schedule_needs_a_time_zone_and_a_sane_date(client):
    as_admin(client)
    n = draft(client)
    assert post(client, f"/{n['id']}/schedule", send_at="2026-12-01T18:00:00").status_code == 422
    far = (datetime.now(UTC) + timedelta(days=400)).isoformat()
    assert post(client, f"/{n['id']}/schedule", send_at=far).status_code == 422


def test_empty_subject_or_body_is_refused(client):
    as_admin(client)
    r = client.post(
        "/api/admin/newsletters", json={"subject": "", "body_text": "x"}, headers=ORIGIN
    )
    assert r.status_code == 422


def test_newsletter_with_no_subscribers_is_sent_to_nobody(client, db):
    as_admin(client)
    n = draft(client)
    post(client, f"/{n['id']}/schedule")
    assert queue_due_newsletters(db) == 0
    assert client.get(f"/api/admin/newsletters/{n['id']}").json()["recipients"] == 0
    assert db.scalar(select(OutboxEmail).where(OutboxEmail.kind == "newsletter")) is None


def test_worker_stops_cleanly_on_sigint(client, monkeypatch):
    import os
    import signal
    import threading

    from lumawen_api.db import _session_factory
    from lumawen_api.mail import run_worker

    timer = threading.Timer(0.5, lambda: os.kill(os.getpid(), signal.SIGINT))
    timer.start()
    old_int, old_term = signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)
    try:
        run_worker(_session_factory(), loop=True, interval=0.1)  # returns instead of raising
    finally:
        timer.cancel()
        signal.signal(signal.SIGINT, old_int)
        signal.signal(signal.SIGTERM, old_term)


def queued_newsletter(client, db, *emails):
    subs = [subscribed(client, e) for e in emails]
    as_admin(client)
    n = draft(client)
    post(client, f"/{n['id']}/schedule")
    queue_due_newsletters(db)
    return n, subs


def test_unsubscribing_after_queueing_cancels_their_copy(client, db):
    n, (stay, leave) = queued_newsletter(client, db, "stay@example.com", "leave@example.com")
    client.post(f"/api/list/unsubscribe?token={leave.unsubscribe_token}")
    assert outbox("leave@example.com", "newsletter") == []
    captured: list[EmailMessage] = []
    send_due(db, captured.append)
    assert [m["To"] for m in captured if m["Subject"] == n["subject"]] == ["stay@example.com"]


def test_worker_skips_a_newsletter_for_someone_no_longer_subscribed(client, db):
    from lumawen_api.models import Subscriber

    n, (gone,) = queued_newsletter(client, db, "skip@example.com")
    sub = db.get(Subscriber, gone.id)
    sub.status = "unsubscribed"  # e.g. changed straight in the database
    db.commit()
    captured: list[EmailMessage] = []
    send_due(db, captured.append)
    assert [m for m in captured if m["Subject"] == n["subject"]] == []
    assert outbox("skip@example.com", "newsletter") == []


def test_erasing_a_subscriber_removes_every_email_to_them(client, db):
    n, (sub,) = queued_newsletter(client, db, "erase-all@example.com")
    assert outbox("erase-all@example.com")
    r = client.delete(f"/api/admin/subscribers/{sub.id}", headers=ORIGIN)
    assert r.status_code == 204
    assert outbox("erase-all@example.com") == []


def test_subject_must_be_one_line(client):
    as_admin(client)
    r = client.post(
        "/api/admin/newsletters",
        json={"subject": "Hi\r\nBcc: everyone@example.com", "body_text": "x"},
        headers=ORIGIN,
    )
    assert r.status_code == 422
    n = draft(client)
    r = client.patch(f"/api/admin/newsletters/{n['id']}", json={"subject": "a\nb"}, headers=ORIGIN)
    assert r.status_code == 422


def test_old_sent_emails_are_purged(client, db):
    from lumawen_api.mail import purge_outbox

    subscribe(client, "old-mail@example.com")
    send_due(db, lambda m: None)
    row = db.scalar(select(OutboxEmail))
    assert row.status == "sent"
    assert purge_outbox(db) == 0
    row.created_at = datetime.now(UTC) - timedelta(days=31)
    db.commit()
    assert purge_outbox(db) == 1


def test_send_due_stops_between_emails_when_asked(client, db):
    import threading

    for i in range(3):
        subscribe(client, f"stop{i}@example.com")
    stop = threading.Event()
    sent: list[EmailMessage] = []

    def sender(msg: EmailMessage) -> None:
        sent.append(msg)
        stop.set()

    assert send_due(db, sender, stop=stop) == 1
    assert len(outbox(kind="list_confirm")) == 3
    assert sum(r.status == "pending" for r in outbox(kind="list_confirm")) == 2
