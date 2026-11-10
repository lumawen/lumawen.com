"""The mailing list: double opt-in sign-up, confirm, unsubscribe, and the admin endpoints."""

import csv
import io

from helpers import link_token, outbox, web_login
from sqlalchemy import select

from lumawen_api.cli import create_admin
from lumawen_api.db import _session_factory
from lumawen_api.models import Subscriber

ADMIN_PASSWORD = "admin password 123"


def subscribe(client, email: str, **extra):
    return client.post("/api/list/subscribe", json={"email": email, **extra})


def subscriber(email: str) -> Subscriber | None:
    with _session_factory()() as db:
        return db.scalar(select(Subscriber).where(Subscriber.email == email))


def confirm_token(email: str) -> str:
    return link_token(outbox(email, "list_confirm")[-1])


def subscribed(client, email: str) -> Subscriber:
    assert subscribe(client, email).status_code == 202
    assert client.post(f"/api/list/confirm?token={confirm_token(email)}").status_code == 200
    return subscriber(email)


def as_admin(client) -> None:
    create_admin("admin@lumawen.com", ADMIN_PASSWORD)
    assert web_login(client, "admin@lumawen.com", ADMIN_PASSWORD).status_code == 200


def test_signup_is_pending_until_confirmed(client):
    r = subscribe(client, " Player@Example.com ")
    assert r.status_code == 202
    assert r.json() == {"status": "check_email"}
    sub = subscriber("player@example.com")
    assert sub.status == "pending"
    mail = outbox("player@example.com", "list_confirm")
    assert len(mail) == 1
    assert "/api/list/confirm?token=" in mail[0].body_text
    assert sub.confirm_token_hash and confirm_token("player@example.com") not in str(
        sub.confirm_token_hash
    )


def test_confirm_link_opens_a_page_and_only_the_button_confirms(client):
    subscribe(client, "a@example.com")
    token = confirm_token("a@example.com")
    page = client.get(f"/api/list/confirm?token={token}")
    assert page.status_code == 200
    assert 'method="post"' in page.text
    assert page.headers["cache-control"] == "no-store"
    assert subscriber("a@example.com").status == "pending"  # opening the link changes nothing

    r = client.post(f"/api/list/confirm?token={token}")
    assert r.status_code == 200
    assert "on the list" in r.text
    sub = subscriber("a@example.com")
    assert sub.status == "subscribed"
    assert sub.confirmed_at is not None
    # The link is single use.
    assert client.post(f"/api/list/confirm?token={token}").status_code == 400


def test_bad_or_expired_confirm_link(client, db):
    assert client.get("/api/list/confirm?token=not-a-real-token").status_code == 400
    assert client.post("/api/list/confirm?token=not-a-real-token").status_code == 400
    subscribe(client, "late@example.com")
    token = confirm_token("late@example.com")
    sub = db.scalar(select(Subscriber).where(Subscriber.email == "late@example.com"))
    from datetime import UTC, datetime, timedelta

    sub.confirm_expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()
    assert client.post(f"/api/list/confirm?token={token}").status_code == 400
    assert subscriber("late@example.com").status == "pending"


def test_signing_up_again_when_subscribed_sends_nothing(client):
    subscribed(client, "twice@example.com")
    assert subscribe(client, "twice@example.com").status_code == 202
    assert len(outbox("twice@example.com", "list_confirm")) == 1
    assert subscriber("twice@example.com").status == "subscribed"


def test_new_confirm_email_replaces_the_old_link(client):
    subscribe(client, "again@example.com")
    first = confirm_token("again@example.com")
    subscribe(client, "again@example.com")
    second = confirm_token("again@example.com")
    assert first != second
    assert client.post(f"/api/list/confirm?token={first}").status_code == 400
    assert client.post(f"/api/list/confirm?token={second}").status_code == 200


def test_confirm_emails_are_rate_limited_per_address(client):
    for _ in range(5):
        assert subscribe(client, "flood@example.com").status_code == 202
    assert len(outbox("flood@example.com", "list_confirm")) == 3


def test_signups_are_limited_per_ip(client):
    for i in range(12):
        assert subscribe(client, f"stranger{i}@example.com").status_code == 202
    assert len(outbox(kind="list_confirm")) == 10
    assert subscriber("stranger11@example.com") is None


def test_honeypot_field_drops_bots(client):
    r = subscribe(client, "bot@example.com", website="http://spam.example")
    assert r.status_code == 202
    assert subscriber("bot@example.com") is None
    assert outbox(kind="list_confirm") == []


def test_invalid_email_is_refused(client):
    assert subscribe(client, "not-an-email").status_code == 422
    assert subscribe(client, "pässe@example.com").status_code == 422


def test_unsubscribe_page_then_button(client):
    sub = subscribed(client, "bye@example.com")
    url = f"/api/list/unsubscribe?token={sub.unsubscribe_token}"
    page = client.get(url)
    assert page.status_code == 200 and 'method="post"' in page.text
    assert subscriber("bye@example.com").status == "subscribed"
    assert client.post(url).status_code == 200
    sub = subscriber("bye@example.com")
    assert sub.status == "unsubscribed" and sub.unsubscribed_at is not None
    # Again is fine, and the page now says so.
    assert client.post(url).status_code == 200
    assert "unsubscribed" in client.get(url).text
    assert client.post("/api/list/unsubscribe?token=nope-nope-nope").status_code == 400


def test_one_click_unsubscribe_from_mail_apps(client):
    sub = subscribed(client, "oneclick@example.com")
    r = client.post(
        f"/api/list/unsubscribe?token={sub.unsubscribe_token}",
        content="List-Unsubscribe=One-Click",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 200
    assert subscriber("oneclick@example.com").status == "unsubscribed"


def test_resubscribing_after_unsubscribe_needs_a_new_confirm(client):
    sub = subscribed(client, "back@example.com")
    client.post(f"/api/list/unsubscribe?token={sub.unsubscribe_token}")
    subscribe(client, "back@example.com")
    assert subscriber("back@example.com").status == "unsubscribed"
    assert (
        client.post(f"/api/list/confirm?token={confirm_token('back@example.com')}").status_code
        == 200
    )
    assert subscriber("back@example.com").status == "subscribed"


def test_admin_subscriber_endpoints_need_admin(client):
    assert client.get("/api/admin/subscribers").status_code == 401
    assert client.get("/api/admin/subscribers.csv").status_code == 401


def test_admin_lists_filters_and_searches_subscribers(client):
    subscribed(client, "one@example.com")
    subscribed(client, "two@example.org")
    subscribe(client, "pending@example.com")
    as_admin(client)
    page = client.get("/api/admin/subscribers").json()
    assert page["total"] == 3
    assert page["counts"] == {"pending": 1, "subscribed": 2, "unsubscribed": 0}
    assert "unsubscribe_token" not in page["subscribers"][0]
    only = client.get("/api/admin/subscribers?status=subscribed").json()
    assert {s["email"] for s in only["subscribers"]} == {"one@example.com", "two@example.org"}
    found = client.get("/api/admin/subscribers?q=Example.ORG").json()
    assert [s["email"] for s in found["subscribers"]] == ["two@example.org"]
    assert client.get("/api/admin/subscribers?q=%25").json()["total"] == 0  # % is literal


def test_admin_csv_export_is_subscribed_only_and_formula_safe(client, db):
    subscribed(client, "csv@example.com")
    subscribe(client, "waiting@example.com")
    sub = db.scalar(select(Subscriber).where(Subscriber.email == "csv@example.com"))
    sub.source = "=HYPERLINK(1)"
    db.commit()
    as_admin(client)
    r = client.get("/api/admin/subscribers.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0][0] == "email"
    assert [row[0] for row in rows[1:]] == ["csv@example.com"]
    assert rows[1][2] == "'=HYPERLINK(1)"


def test_admin_can_erase_a_subscriber(client):
    sub = subscribed(client, "erase@example.com")
    as_admin(client)
    origin = {"Origin": "http://localhost:3040"}
    assert client.delete(f"/api/admin/subscribers/{sub.id}", headers=origin).status_code == 204
    assert subscriber("erase@example.com") is None
    [entry] = client.get("/api/admin/activity").json()["entries"]
    assert entry["action"] == "subscriber.erase" and entry["target"] == "erase@example.com"
    assert entry["detail"]["status"] == "subscribed" and entry["detail"]["signed_up"]
    assert client.delete(f"/api/admin/subscribers/{sub.id}", headers=origin).status_code == 404


def test_a_flood_of_signups_cannot_switch_off_real_ones(client, monkeypatch):
    """No global cap: many sign-ups from many IPs must not silently drop the next person's."""
    monkeypatch.setattr("lumawen_api.lists.SIGNUPS_PER_IP_PER_HOUR", 1000)
    for i in range(350):
        assert subscribe(client, f"flood{i}@example.com").status_code == 202
    assert subscribe(client, "real@example.com").status_code == 202
    assert outbox("real@example.com", "list_confirm")
    assert subscriber("real@example.com").confirm_token_hash is not None


def test_unconfirmed_signups_are_purged(client, db):
    from datetime import UTC, datetime, timedelta

    from lumawen_api.lists import purge_unconfirmed

    subscribe(client, "old@example.com")
    subscribe(client, "new@example.com")
    subscribed(client, "kept@example.com")
    for email in ("old@example.com", "kept@example.com"):
        sub = db.scalar(select(Subscriber).where(Subscriber.email == email))
        sub.created_at = datetime.now(UTC) - timedelta(days=31)
        if sub.confirm_expires_at:
            sub.confirm_expires_at = datetime.now(UTC) - timedelta(days=24)
    db.commit()
    assert purge_unconfirmed(db) == 1
    assert subscriber("old@example.com") is None
    assert subscriber("new@example.com") is not None
    assert subscriber("kept@example.com").status == "subscribed"


def test_admin_csv_can_export_everyone(client):
    subscribed(client, "in@example.com")
    subscribe(client, "pending2@example.com")
    as_admin(client)
    rows = list(csv.reader(io.StringIO(client.get("/api/admin/subscribers.csv?status=all").text)))
    assert [row[0] for row in rows[1:]] == ["in@example.com", "pending2@example.com"]


def test_purge_keeps_an_old_signup_that_asked_again(client, db):
    from datetime import UTC, datetime, timedelta

    from lumawen_api.lists import purge_unconfirmed

    subscribe(client, "slow@example.com")
    sub = db.scalar(select(Subscriber).where(Subscriber.email == "slow@example.com"))
    sub.created_at = datetime.now(UTC) - timedelta(days=31)
    db.commit()
    assert purge_unconfirmed(db) == 0  # their link is still live
    assert (
        client.post(f"/api/list/confirm?token={confirm_token('slow@example.com')}").status_code
        == 200
    )
