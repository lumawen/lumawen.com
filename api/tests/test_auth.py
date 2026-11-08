"""Email accounts: register, confirm, sign in/out, refresh, reset, lockout."""

from datetime import UTC, datetime, timedelta

from helpers import PASSWORD, app_login, link_token, outbox, register, signed_up, web_login
from sqlalchemy import select, update

from lumawen_api.models import AuthSession, EmailToken, User


def test_register_confirm_login_me_logout(client):
    token = register(client, "Player@Example.com")
    [email] = outbox("player@example.com")
    assert email.subject == "Confirm your Lumawen account"
    assert "/dashboard/confirm?token=" in email.body_text

    r = client.post("/api/auth/confirm", json={"token": token})
    assert r.json() == {"status": "confirmed"}

    r = web_login(client, "player@example.com")
    assert r.status_code == 200
    assert r.json()["user"]["email"] == "player@example.com"
    assert r.json()["access_token"] is None
    cookie = r.headers["set-cookie"].lower()
    assert "lumawen_session=" in cookie and "httponly" in cookie and "samesite=lax" in cookie

    me = client.get("/api/me").json()
    assert me["role"] == "user" and me["email_verified"] is True

    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/me").status_code == 401


def test_cookie_is_secure_in_production(client, monkeypatch):
    from lumawen_api import config

    signed_up(client, "secure@example.com")
    monkeypatch.setattr(config.get_settings(), "env", "production")
    assert "secure" in web_login(client, "secure@example.com").headers["set-cookie"].lower()


def test_cannot_sign_in_before_confirming(client):
    register(client, "new@example.com")
    r = web_login(client, "new@example.com")
    assert r.status_code == 403
    assert "Confirm your email" in r.json()["detail"]


def test_register_answers_the_same_for_existing_accounts(client):
    signed_up(client, "taken@example.com")
    r = client.post(
        "/api/auth/register", json={"email": "taken@example.com", "password": "another password"}
    )
    assert r.status_code == 202 and r.json() == {"status": "check_email"}
    assert outbox("taken@example.com")[-1].kind == "already_registered"
    # The password did not change.
    assert web_login(client, "taken@example.com").status_code == 200
    assert web_login(client, "taken@example.com", "another password").status_code == 401


def test_someone_else_cannot_preset_the_password(client):
    """An attacker signs up with the victim's email first; the victim's own link wins."""
    register(client, "victim@example.com", "attacker password")
    victim_token = register(client, "victim@example.com", "victim password")
    assert client.post("/api/auth/confirm", json={"token": victim_token}).status_code == 200
    assert web_login(client, "victim@example.com", "attacker password").status_code == 401
    assert web_login(client, "victim@example.com", "victim password").status_code == 200


def test_older_confirm_link_stops_working(client):
    first = register(client, "twice@example.com", "first password")
    register(client, "twice@example.com", "second password")
    assert client.post("/api/auth/confirm", json={"token": first}).status_code == 400


def test_confirm_link_is_single_use_and_expires(client, db):
    token = register(client, "once@example.com")
    assert client.post("/api/auth/confirm", json={"token": token}).status_code == 200
    assert client.post("/api/auth/confirm", json={"token": token}).status_code == 400

    token = register(client, "late@example.com")
    db.execute(update(EmailToken).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    db.commit()
    assert client.post("/api/auth/confirm", json={"token": token}).status_code == 400
    assert client.post("/api/auth/confirm", json={"token": "nonsense"}).status_code == 400


def test_resend_confirmation(client):
    register(client, "resend@example.com")
    r = client.post("/api/auth/resend-confirmation", json={"email": "resend@example.com"})
    assert r.status_code == 202
    emails = outbox("resend@example.com", "confirm")
    assert len(emails) == 2
    assert (
        client.post("/api/auth/confirm", json={"token": link_token(emails[-1])}).status_code == 200
    )
    assert web_login(client, "resend@example.com").status_code == 200
    # Unknown addresses get the same answer and no email.
    r = client.post("/api/auth/resend-confirmation", json={"email": "nobody@example.com"})
    assert r.status_code == 202 and outbox("nobody@example.com") == []


def test_wrong_password_and_unknown_email_look_the_same(client):
    signed_up(client, "real@example.com")
    wrong = web_login(client, "real@example.com", "not the password")
    unknown = web_login(client, "ghost@example.com")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_lockout_after_repeated_wrong_passwords(client):
    signed_up(client, "locked@example.com")
    for _ in range(5):
        assert web_login(client, "locked@example.com", "wrong password").status_code == 401
    r = web_login(client, "locked@example.com")
    assert r.status_code == 429


def test_password_rules(client):
    r = client.post("/api/auth/register", json={"email": "short@example.com", "password": "short"})
    assert r.status_code == 422
    r = client.post("/api/auth/register", json={"email": "not-an-email", "password": PASSWORD})
    assert r.status_code == 422


def test_app_tokens_and_refresh_rotation(client):
    signed_up(client, "app@example.com")
    tokens = app_login(client, "app@example.com")
    assert tokens["token_type"] == "bearer" and tokens["expires_in"] == 15 * 60
    client.cookies.clear()
    auth = {"Authorization": f"Bearer {tokens['access_token']}"}
    assert client.get("/api/me", headers=auth).json()["email"] == "app@example.com"

    r = client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200
    new = r.json()
    assert new["refresh_token"] != tokens["refresh_token"]
    # The old access token is replaced; the new one works.
    assert client.get("/api/me", headers=auth).status_code == 401
    new_auth = {"Authorization": f"Bearer {new['access_token']}"}
    assert client.get("/api/me", headers=new_auth).status_code == 200

    # Re-using the old refresh token looks like theft: the whole session ends.
    r = client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401
    assert client.get("/api/me", headers=new_auth).status_code == 401
    r = client.post("/api/auth/refresh", json={"refresh_token": new["refresh_token"]})
    assert r.status_code == 401


def test_expired_access_token(client, db):
    signed_up(client, "expired@example.com")
    tokens = app_login(client, "expired@example.com")
    client.cookies.clear()
    db.execute(
        update(AuthSession).values(access_expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    db.commit()
    auth = {"Authorization": f"Bearer {tokens['access_token']}"}
    assert client.get("/api/me", headers=auth).status_code == 401


def test_web_cookie_cannot_be_used_as_bearer_or_app_refresh(client):
    signed_up(client, "web@example.com")
    raw = web_login(client, "web@example.com").cookies["lumawen_session"]
    client.cookies.clear()
    assert client.get("/api/me", headers={"Authorization": f"Bearer {raw}"}).status_code == 401
    assert client.post("/api/auth/refresh", json={"refresh_token": raw}).status_code == 401


def test_cross_site_post_with_cookie_is_blocked(client):
    signed_up(client, "csrf@example.com")
    web_login(client, "csrf@example.com")
    r = client.post("/api/auth/logout", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert client.get("/api/me").status_code == 200
    r = client.post("/api/auth/logout", headers={"Origin": "http://localhost:3040"})
    assert r.status_code == 200


def test_logout_all_ends_every_device(client):
    signed_up(client, "multi@example.com")
    tokens = app_login(client, "multi@example.com")
    web_login(client, "multi@example.com")
    assert client.post("/api/auth/logout-all").status_code == 200
    client.cookies.clear()
    auth = {"Authorization": f"Bearer {tokens['access_token']}"}
    assert client.get("/api/me", headers=auth).status_code == 401


def test_forgot_and_reset_password(client, db):
    signed_up(client, "forgot@example.com")
    tokens = app_login(client, "forgot@example.com")
    for _ in range(5):
        web_login(client, "forgot@example.com", "wrong password")  # now locked

    r = client.post("/api/auth/forgot-password", json={"email": "Forgot@Example.com"})
    assert r.status_code == 202
    [email] = outbox("forgot@example.com", "reset")
    token = link_token(email)
    r = client.post(
        "/api/auth/reset-password", json={"token": token, "password": "a brand new password"}
    )
    assert r.json() == {"status": "password_changed"}

    assert web_login(client, "forgot@example.com").status_code == 401
    assert web_login(client, "forgot@example.com", "a brand new password").status_code == 200
    # Other devices were signed out, and the link only works once.
    auth = {"Authorization": f"Bearer {tokens['access_token']}"}
    client.cookies.clear()
    assert client.get("/api/me", headers=auth).status_code == 401
    r = client.post(
        "/api/auth/reset-password", json={"token": token, "password": "yet another one"}
    )
    assert r.status_code == 400


def test_reset_also_confirms_an_unconfirmed_account(client):
    register(client, "unconfirmed@example.com")
    client.post("/api/auth/forgot-password", json={"email": "unconfirmed@example.com"})
    token = link_token(outbox("unconfirmed@example.com", "reset")[-1])
    client.post("/api/auth/reset-password", json={"token": token, "password": "chosen at reset"})
    assert web_login(client, "unconfirmed@example.com", "chosen at reset").status_code == 200


def test_forgot_password_unknown_email_sends_nothing(client):
    r = client.post("/api/auth/forgot-password", json={"email": "nobody@example.com"})
    assert r.status_code == 202 and r.json() == {"status": "check_email"}
    assert outbox("nobody@example.com") == []


def test_emails_are_rate_limited_per_address(client):
    signed_up(client, "spam@example.com")
    for _ in range(5):
        client.post("/api/auth/forgot-password", json={"email": "spam@example.com"})
    assert len(outbox("spam@example.com", "reset")) == 3


def test_tokens_are_stored_hashed(client, db):
    token = register(client, "hashed@example.com")
    stored = db.scalars(select(EmailToken.token_hash)).all()
    assert token not in stored
    client.post("/api/auth/confirm", json={"token": token})
    raw = web_login(client, "hashed@example.com").cookies["lumawen_session"]
    assert raw not in db.scalars(select(AuthSession.token_hash)).all()
    user = db.scalar(select(User).where(User.email == "hashed@example.com"))
    assert user.password_hash.startswith("$argon2id$")
