"""Sign-up flood limits, unconfirmed-account cleanup and the production cookie name."""

from datetime import UTC, datetime, timedelta

from helpers import PASSWORD, register
from sqlalchemy import select, update

from lumawen_api.auth import SIGNUPS_PER_IP_PER_HOUR, cookie_name, purge_unconfirmed_accounts
from lumawen_api.config import get_settings
from lumawen_api.models import EmailToken, User


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
