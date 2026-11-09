"""Email accounts: register, confirm, sign in/out, refresh, reset, lockout."""

from datetime import UTC, datetime, timedelta

from helpers import (
    PASSWORD,
    app_login,
    confirm,
    link_token,
    outbox,
    register,
    signed_up,
    web_login,
)
from sqlalchemy import select, update

from lumawen_api.models import AuthSession, EmailToken, User


def test_register_confirm_login_me_logout(client):
    token = register(client, "Player@Example.com")
    [email] = outbox("player@example.com")
    assert email.subject == "Confirm your Lumawen account"
    assert "/dashboard/#/confirm?token=" in email.body_text

    r = confirm(client, token)
    assert r.status_code == 200
    assert r.json()["user"]["email"] == "player@example.com"
    assert client.get("/api/me").json()["email"] == "player@example.com"  # signed in already
    client.post("/api/auth/logout")
    assert client.get("/api/me").status_code == 401

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
    """An attacker signs up with the victim's address, before or after the victim."""
    attacker_first = register(client, "victim@example.com", "attacker password")
    victim = register(client, "victim@example.com", "victim password")
    attacker_last = register(client, "victim@example.com", "attacker password")
    # The attacker's links (in the victim's inbox) don't work with the victim's password...
    assert confirm(client, attacker_first, "victim password").status_code == 400
    assert confirm(client, attacker_last, "victim password").status_code == 400
    # ...and the victim's own link still works, with the victim's password only.
    assert confirm(client, victim, "attacker password").status_code == 400
    assert confirm(client, victim, "victim password").status_code == 200
    assert web_login(client, "victim@example.com", "attacker password").status_code == 401
    assert web_login(client, "victim@example.com", "victim password").status_code == 200
    # Once confirmed, the attacker's links are dead.
    assert confirm(client, attacker_last, "attacker password").status_code == 400


def test_confirm_link_is_single_use_and_expires(client, db):
    token = register(client, "once@example.com")
    assert confirm(client, token).status_code == 200
    assert confirm(client, token).status_code == 400

    token = register(client, "late@example.com")
    db.execute(update(EmailToken).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    db.commit()
    assert confirm(client, token).status_code == 400
    assert confirm(client, "nonsense").status_code == 400


def test_signing_up_again_resends_the_link(client):
    register(client, "again@example.com")
    token = register(client, "again@example.com")
    assert len(outbox("again@example.com", "confirm")) == 2
    assert confirm(client, token).status_code == 200


def test_wrong_password_and_unknown_email_look_the_same(client):
    signed_up(client, "real@example.com")
    wrong = web_login(client, "real@example.com", "not the password")
    unknown = web_login(client, "ghost@example.com")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_throttle_looks_the_same_for_real_and_unknown_emails(client):
    signed_up(client, "locked@example.com")
    real = [web_login(client, "locked@example.com", "wrong password").status_code for _ in range(6)]
    fake = [web_login(client, "ghost@example.com", "wrong password").status_code for _ in range(6)]
    assert real == fake == [401] * 5 + [429]


def test_throttle_is_per_ip_so_others_can_still_sign_in(client, app):
    from fastapi.testclient import TestClient

    signed_up(client, "target@example.com")
    with TestClient(app, client=("203.0.113.9", 5000)) as attacker:
        for _ in range(6):
            web_login(attacker, "target@example.com", "wrong password")
        assert web_login(attacker, "target@example.com").status_code == 429
    assert web_login(client, "target@example.com").status_code == 200


def test_throttle_per_email_across_many_ips(client, app, monkeypatch):
    from fastapi.testclient import TestClient

    from lumawen_api import config

    monkeypatch.setattr(config.get_settings(), "max_failed_per_email", 4)
    signed_up(client, "spread@example.com")
    for n in range(4):
        with TestClient(app, client=(f"198.51.100.{n}", 5000)) as c:
            assert web_login(c, "spread@example.com", "wrong password").status_code == 401
    with TestClient(app, client=("192.0.2.77", 5000)) as elsewhere:
        assert web_login(elsewhere, "spread@example.com").status_code == 429
        # The owner's own network (where they signed in) isn't paused.
        assert web_login(client, "spread@example.com").status_code == 200
        # A password reset lifts the per-email pause everywhere.
        client.post("/api/auth/forgot-password", json={"email": "spread@example.com"})
        token = link_token(outbox("spread@example.com", "reset")[-1])
        client.post("/api/auth/reset-password", json={"token": token, "password": "new pass 1"})
        assert web_login(elsewhere, "spread@example.com", "new pass 1").status_code == 200


def test_unconfirmed_login_hint_needs_the_right_password(client):
    register(client, "pending@example.com")
    assert web_login(client, "pending@example.com").status_code == 403
    assert web_login(client, "pending@example.com", "wrong password").status_code == 401


def test_login_from_another_site_is_blocked(client):
    signed_up(client, "origin@example.com")
    r = client.post(
        "/api/auth/login",
        json={"email": "origin@example.com", "password": PASSWORD},
        headers={"Origin": "https://evil.example"},
    )
    assert r.status_code == 403


def test_non_ascii_email_is_refused(client):
    r = client.post("/api/auth/register", json={"email": "ÄBC@example.com", "password": PASSWORD})
    assert r.status_code == 422


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

    # Re-using it right away is a retry or race: refused, but the session lives on.
    r = client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 409
    assert client.get("/api/me", headers=new_auth).status_code == 200


def test_old_refresh_token_coming_back_later_ends_the_session(client, db):
    signed_up(client, "stolen@example.com")
    tokens = app_login(client, "stolen@example.com")
    client.cookies.clear()
    new = client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).json()
    db.execute(update(AuthSession).values(rotated_at=datetime.now(UTC) - timedelta(minutes=5)))
    db.commit()
    r = client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401
    new_auth = {"Authorization": f"Bearer {new['access_token']}"}
    assert client.get("/api/me", headers=new_auth).status_code == 401
    r = client.post("/api/auth/refresh", json={"refresh_token": new["refresh_token"]})
    assert r.status_code == 401


def test_parallel_refreshes_keep_the_session(client, app):
    from concurrent.futures import ThreadPoolExecutor

    from fastapi.testclient import TestClient

    signed_up(client, "race@example.com")
    tokens = app_login(client, "race@example.com")

    def go(_):
        with TestClient(app) as c:
            return c.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})

    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(go, range(4)))
    codes = sorted(r.status_code for r in results)
    assert codes == [200, 409, 409, 409]
    winner = next(r for r in results if r.status_code == 200).json()
    r = client.post("/api/auth/refresh", json={"refresh_token": winner["refresh_token"]})
    assert r.status_code == 200


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
    confirm(client, token)
    raw = web_login(client, "hashed@example.com").cookies["lumawen_session"]
    assert raw not in db.scalars(select(AuthSession.token_hash)).all()
    user = db.scalar(select(User).where(User.email == "hashed@example.com"))
    assert user.password_hash.startswith("$argon2id$")


def test_only_the_newest_reset_link_works(client):
    signed_up(client, "two-resets@example.com")
    client.post("/api/auth/forgot-password", json={"email": "two-resets@example.com"})
    client.post("/api/auth/forgot-password", json={"email": "two-resets@example.com"})
    first, second = (link_token(e) for e in outbox("two-resets@example.com", "reset"))
    body = {"password": "another new password"}
    assert client.post("/api/auth/reset-password", json={"token": first, **body}).status_code == 400
    assert (
        client.post("/api/auth/reset-password", json={"token": second, **body}).status_code == 200
    )


def test_parallel_signups_never_fail(app):
    from concurrent.futures import ThreadPoolExecutor

    from fastapi.testclient import TestClient

    def go(_):
        with TestClient(app) as c:
            return c.post(
                "/api/auth/register", json={"email": "same@example.com", "password": PASSWORD}
            ).status_code

    with ThreadPoolExecutor(8) as pool:
        assert set(pool.map(go, range(8))) == {202}


def test_confirm_racing_a_new_signup_does_not_deadlock(app, client):
    from concurrent.futures import ThreadPoolExecutor

    from fastapi.testclient import TestClient

    for n in range(5):
        email = f"deadlock{n}@example.com"
        token = register(client, email)

        def sign_up_again(_, email=email):
            with TestClient(app) as c:
                return c.post("/api/auth/register", json={"email": email, "password": PASSWORD})

        def click(_, token=token):
            with TestClient(app) as c:
                return confirm(c, token)

        with ThreadPoolExecutor(2) as pool:
            a = pool.submit(sign_up_again, None)
            b = pool.submit(click, None)
            assert a.result().status_code == 202
            assert b.result().status_code == 200


def test_parallel_guesses_cannot_beat_the_limit(client, app):
    from concurrent.futures import ThreadPoolExecutor

    from fastapi.testclient import TestClient

    signed_up(client, "parallel@example.com")

    def guess(_):
        with TestClient(app, client=("192.0.2.50", 5000)) as c:
            return web_login(c, "parallel@example.com", "wrong password").status_code

    with ThreadPoolExecutor(10) as pool:
        codes = list(pool.map(guess, range(20)))
    assert codes.count(401) == 5 and codes.count(429) == 15


def test_ipv6_addresses_in_one_64_share_a_limit(client, app):
    from fastapi.testclient import TestClient

    signed_up(client, "v6@example.com")
    for n in range(5):
        with TestClient(app, client=(f"2001:db8:1:2::{n + 1}", 5000)) as c:
            web_login(c, "v6@example.com", "wrong password")
    with TestClient(app, client=("2001:db8:1:2::99", 5000)) as c:
        assert web_login(c, "v6@example.com").status_code == 429


def test_reset_lifts_every_pause_for_that_email(client):
    signed_up(client, "typo@example.com")
    for _ in range(5):
        web_login(client, "typo@example.com", "wrong password")
    client.post("/api/auth/forgot-password", json={"email": "typo@example.com"})
    token = link_token(outbox("typo@example.com", "reset")[-1])
    client.post("/api/auth/reset-password", json={"token": token, "password": "new password 2"})
    assert web_login(client, "typo@example.com", "new password 2").status_code == 200


def test_old_throttle_rows_are_purged(client, db):
    from lumawen_api.auth import purge_throttle
    from lumawen_api.models import LoginThrottle

    web_login(client, "gone@example.com", "wrong password")
    assert purge_throttle(db) == 0
    db.execute(
        update(LoginThrottle).values(window_started_at=datetime.now(UTC) - timedelta(days=2))
    )
    db.commit()
    assert purge_throttle(db) == 2


def test_confirm_signs_in_the_app_and_blocks_cross_site_web(client):
    token = register(client, "phone@example.com")
    r = client.post(
        "/api/auth/confirm",
        json={"token": token, "password": PASSWORD},
        headers={"origin": "https://evil.example"},
    )
    assert r.status_code == 403
    r = client.post(
        "/api/auth/confirm",
        json={"token": token, "password": PASSWORD, "client": "app", "device_name": "Pixel"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["access_token"] and r.json()["refresh_token"]
    me = client.get("/api/me", headers={"authorization": f"Bearer {r.json()['access_token']}"})
    assert me.json()["email"] == "phone@example.com"


def test_confirm_password_guesses_are_limited(client):
    token = register(client, "guessme@example.com")
    codes = [confirm(client, token, f"wrong guess {n}").status_code for n in range(12)]
    assert codes[0] == 400
    assert 429 in codes
    assert confirm(client, token).status_code == 429  # paused even for the right password
