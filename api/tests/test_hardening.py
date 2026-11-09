"""Sign-up flood limits, unconfirmed-account cleanup and the production cookie name."""

from datetime import UTC, datetime, timedelta

from helpers import PASSWORD, register
from sqlalchemy import select, update

from lumawen_api.auth import cookie_name, purge_unconfirmed_accounts
from lumawen_api.config import get_settings
from lumawen_api.models import EmailToken, User


def test_one_ip_can_only_sign_up_so_often(client):
    codes = [
        client.post(
            "/api/auth/register", json={"email": f"x{n}@example.com", "password": PASSWORD}
        ).status_code
        for n in range(12)
    ]
    assert codes[:9] == [202] * 9 and codes[-1] == 429


def test_unconfirmed_accounts_are_purged_after_30_days(client, db):
    register(client, "old@example.com")
    register(client, "new@example.com")
    long_ago = datetime.now(UTC) - timedelta(days=31)
    db.execute(update(User).where(User.email == "old@example.com").values(created_at=long_ago))
    db.execute(update(EmailToken).values(expires_at=long_ago))  # links long expired
    db.execute(update(User).where(User.email == "new@example.com").values(created_at=long_ago))
    db.commit()
    # new@ asked again: give it a live link
    register(client, "new@example.com")
    assert purge_unconfirmed_accounts(db) == 1
    assert sorted(db.scalars(select(User.email))) == ["new@example.com"]


def test_production_cookie_has_the_host_prefix(monkeypatch):
    monkeypatch.setenv("LUMAWEN_ENV", "production")
    get_settings.cache_clear()
    try:
        assert cookie_name() == "__Host-lumawen_session"
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
