"""Sign-up flood limits, unconfirmed-account cleanup and the production cookie name."""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from helpers import PASSWORD, confirm, register, signed_up, web_login
from sqlalchemy import select, update

from lumawen_api import config
from lumawen_api.auth import (
    SIGNUPS_PER_IP_PER_HOUR,
    cookie_name,
    purge_throttle,
    purge_unconfirmed_accounts,
)
from lumawen_api.config import get_settings
from lumawen_api.models import EmailToken, KnownSignInPlace, User


def test_one_ip_can_only_sign_up_so_often(client):
    codes = [
        client.post(
            "/api/auth/register", json={"email": f"x{n}@example.com", "password": PASSWORD}
        ).status_code
        for n in range(SIGNUPS_PER_IP_PER_HOUR + 1)
    ]
    assert codes[:-1] == [202] * SIGNUPS_PER_IP_PER_HOUR and codes[-1] == 429


def test_unconfirmed_accounts_are_purged_after_30_days(client, db):
    emails = ["old@example.com", "new@example.com", "reset@example.com"]
    for email in emails:
        register(client, email)
    long_ago = datetime.now(UTC) - timedelta(days=31)
    db.execute(update(User).where(User.email.in_(emails)).values(created_at=long_ago))
    db.execute(update(EmailToken).values(expires_at=long_ago))  # links long expired
    db.commit()
    # new@ asked again and reset@ asked for a reset: both have a live link, so both stay
    register(client, "new@example.com")
    client.post("/api/auth/forgot-password", json={"email": "reset@example.com"})
    assert purge_unconfirmed_accounts(db) == 1
    assert sorted(db.scalars(select(User.email))) == ["new@example.com", "reset@example.com"]


def test_production_cookie_has_the_host_prefix(monkeypatch):
    monkeypatch.setenv("LUMAWEN_ENV", "production")
    get_settings.cache_clear()
    try:
        assert cookie_name() == "__Host-lumawen_session"
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def _flood(app, email, n):
    for i in range(n):
        with TestClient(app, client=(f"198.51.100.{i}", 5000)) as c:
            web_login(c, email, "wrong password")


def test_a_flood_of_wrong_passwords_does_not_lock_out_the_owner(client, app, monkeypatch):
    monkeypatch.setattr(config.get_settings(), "max_failed_per_email", 3)
    signed_up(client, "owner@example.com")  # confirming signs in: this network is known
    _flood(app, "owner@example.com", 3)
    assert web_login(client, "owner@example.com").status_code == 200
    # The known network still has its own limit: 5 wrong passwords pause it too.
    for _ in range(5):
        web_login(client, "owner@example.com", "wrong password")
    assert web_login(client, "owner@example.com").status_code == 429


def test_signing_out_everywhere_forgets_known_networks(client, app, monkeypatch, db):
    monkeypatch.setattr(config.get_settings(), "max_failed_per_email", 3)
    signed_up(client, "careful@example.com")
    assert client.post("/api/auth/logout-all").status_code in (200, 204)
    assert db.scalars(select(KnownSignInPlace)).all() == []
    _flood(app, "careful@example.com", 3)
    assert web_login(client, "careful@example.com").status_code == 429


def test_known_networks_expire(client, db):
    signed_up(client, "away@example.com")
    long_ago = datetime.now(UTC) - timedelta(days=91)
    db.execute(update(KnownSignInPlace).values(last_at=long_ago))
    db.commit()
    purge_throttle(db)
    assert db.scalars(select(KnownSignInPlace)).all() == []


def test_a_flood_does_not_block_the_confirm_link(client, app, monkeypatch):
    monkeypatch.setattr(config.get_settings(), "max_failed_per_email", 3)
    token = register(client, "fresh@example.com")
    _flood(app, "fresh@example.com", 3)
    assert confirm(client, token).status_code == 200


def test_busy_password_hashing_answers_503_instead_of_queueing_forever(client, monkeypatch):
    """When every Argon2 slot stays taken, sign-in gives up fast so other routes keep working."""
    from lumawen_api import security

    monkeypatch.setattr(security, "HASH_WAIT_SECONDS", 0.05)
    taken = 0
    while security._slots.acquire(blocking=False):
        taken += 1
    try:
        r = web_login(client, "busy@example.com")
        assert r.status_code == 503
        assert r.headers["retry-after"] == "2"
        assert client.get("/api/health").status_code == 200
    finally:
        for _ in range(taken):
            security._slots.release()
    assert web_login(client, "busy@example.com").status_code == 401
