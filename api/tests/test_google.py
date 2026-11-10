"""Sign in with Google (Google's token check is replaced by a fake)."""

import pytest
from helpers import PASSWORD, confirm, outbox, register, signed_up, web_login
from sqlalchemy import select

from lumawen_api import config, google
from lumawen_api.models import User, UserIdentity

WEB_ID = "web-client.apps.googleusercontent.com"
ANDROID_ID = "android-client.apps.googleusercontent.com"


@pytest.fixture(autouse=True)
def google_setup(monkeypatch):
    monkeypatch.setattr(config.get_settings(), "google_client_ids", f"{WEB_ID}, {ANDROID_ID}")
    tokens: dict[str, dict] = {}

    def fake_fetch(token: str, audiences: list[str]) -> dict:
        if token not in tokens:
            raise ValueError("Token used too late / bad signature")
        return tokens[token]

    monkeypatch.setattr(google, "fetch_claims", fake_fetch)
    return tokens


def claims(sub="g-123", email="player@gmail.com", aud=WEB_ID, verified=True, **extra):
    return {
        "sub": sub,
        "email": email,
        "email_verified": verified,
        "aud": aud,
        "iss": "https://accounts.google.com",
        "name": "Pat Player",
        **extra,
    }


def google_login(client, token, kind="web"):
    return client.post("/api/auth/google", json={"id_token": token, "client": kind})


def test_new_google_user_is_created_confirmed(client, google_setup, db):
    google_setup["t1"] = claims()
    r = google_login(client, "t1")
    assert r.status_code == 200, r.text
    user = r.json()["user"]
    assert user["email"] == "player@gmail.com" and user["email_verified"] is True
    assert user["display_name"] == "Pat Player"
    assert client.get("/api/me").json()["email"] == "player@gmail.com"
    stored = db.scalar(select(User).where(User.email == "player@gmail.com"))
    assert stored.password_hash is None
    # Signing in again finds the same account through the link.
    google_setup["t2"] = claims(email="renamed@gmail.com")
    assert google_login(client, "t2").json()["user"]["email"] == "player@gmail.com"


def test_android_app_gets_tokens(client, google_setup):
    # Android tokens carry the web client ID as aud and the Android client ID as azp.
    google_setup["a1"] = claims(aud=WEB_ID, azp=ANDROID_ID)
    body = google_login(client, "a1", kind="app").json()
    assert body["token_type"] == "bearer" and body["refresh_token"]


def test_links_to_existing_confirmed_gmail_account(client, google_setup, db):
    signed_up(client, "Player@Gmail.com")
    google_setup["t"] = claims()
    assert google_login(client, "t").status_code == 200
    assert db.scalar(select(UserIdentity.email)) == "player@gmail.com"
    assert len(db.scalars(select(User)).all()) == 1
    # The password still works too.
    assert web_login(client, "player@gmail.com").status_code == 200


def test_google_cancels_someone_elses_pending_signup(client, google_setup):
    attacker_link = register(client, "player@gmail.com", "attacker password")
    google_setup["t"] = claims()
    assert google_login(client, "t").status_code == 200
    assert confirm(client, attacker_link, "attacker password").status_code == 400
    assert web_login(client, "player@gmail.com", "attacker password").status_code == 401


@pytest.mark.parametrize(
    "bad",
    [
        claims(aud="someone-elses-app.apps.googleusercontent.com"),
        claims(iss="https://evil.example"),
        claims(sub=""),
    ],
)
def test_rejects_tokens_not_meant_for_us(client, google_setup, bad):
    google_setup["bad"] = bad
    assert google_login(client, "bad").status_code == 401


def test_rejects_unverified_google_email(client, google_setup):
    google_setup["u"] = claims(verified=False)
    assert google_login(client, "u").status_code == 403


def test_rejects_forged_or_expired_token(client):
    assert google_login(client, "not-a-real-token").status_code == 401


def test_off_when_no_client_ids(client, google_setup, monkeypatch):
    monkeypatch.setattr(config.get_settings(), "google_client_ids", "")
    google_setup["t"] = claims()
    assert google_login(client, "t").status_code == 503


def test_web_google_sign_in_from_another_site_is_blocked(client, google_setup):
    google_setup["t"] = claims()
    r = client.post(
        "/api/auth/google",
        json={"id_token": "t", "client": "web"},
        headers={"Origin": "https://evil.example"},
    )
    assert r.status_code == 403


def test_password_less_google_user_can_set_a_password(client, google_setup):
    from helpers import link_token, outbox

    google_setup["t"] = claims()
    google_login(client, "t")
    client.post("/api/auth/forgot-password", json={"email": "player@gmail.com"})
    token = link_token(outbox("player@gmail.com", "reset")[-1])
    client.post("/api/auth/reset-password", json={"token": token, "password": PASSWORD})
    assert web_login(client, "player@gmail.com").status_code == 200


def test_parallel_first_google_sign_ins(app, google_setup):
    from concurrent.futures import ThreadPoolExecutor

    from fastapi.testclient import TestClient

    google_setup["t"] = claims()

    def go(_):
        with TestClient(app) as c:
            return google_login(c, "t").status_code

    with ThreadPoolExecutor(6) as pool:
        codes = list(pool.map(go, range(6)))
    assert set(codes) <= {200, 409} and 200 in codes
    from lumawen_api.db import _session_factory

    with _session_factory()() as db:
        assert len(db.scalars(select(User)).all()) == 1
        assert len(db.scalars(select(UserIdentity)).all()) == 1


def test_workspace_account_links_when_google_hosts_the_domain(client, google_setup):
    signed_up(client, "rae@luthiers.example")
    google_setup["t"] = claims(email="rae@luthiers.example", hd="luthiers.example")
    assert google_login(client, "t").status_code == 200


def google_confirm(client, token, link, kind="web"):
    return client.post(
        "/api/auth/google/confirm", json={"id_token": token, "token": link, "client": kind}
    )


def google_link(email: str) -> str:
    from helpers import link_token, outbox

    return link_token(outbox(email, "google_confirm")[-1])


def test_other_addresses_get_the_same_answer_whether_or_not_an_account_exists(
    client, google_setup, db
):
    """email_verified on a non-Gmail, non-Workspace address may be years old, and the answer
    must not tell anyone whether the address has an account."""
    signed_up(client, "ops@smallco.example")
    google_setup["old"] = claims(sub="old-owner", email="ops@smallco.example")
    google_setup["new"] = claims(sub="new-person", email="nobody@smallco.example")
    taken, free = google_login(client, "old"), google_login(client, "new")
    assert taken.status_code == free.status_code == 202
    assert taken.json() == free.json() == {"status": "check_email"}
    assert db.scalars(select(UserIdentity)).all() == []
    assert outbox("ops@smallco.example", "google_confirm")


def test_old_google_account_cannot_take_over_without_the_inbox(client, google_setup, db):
    signed_up(client, "ops@smallco.example")
    google_setup["old"] = claims(sub="old-owner", email="ops@smallco.example")
    assert google_login(client, "old").status_code == 202
    link = google_link("ops@smallco.example")
    # The inbox's real owner opens the link but has a different Google account: no link.
    google_setup["mine"] = claims(sub="real-owner", email="ops@smallco.example")
    assert google_confirm(client, "mine", link).status_code == 400
    assert db.scalars(select(UserIdentity)).all() == []
    # A pending sign-up isn't cancelled by just asking.
    pending = register(client, "new@smallco.example", "real owner pw")
    google_setup["old2"] = claims(sub="old-owner-2", email="new@smallco.example")
    assert google_login(client, "old2").status_code == 202
    assert confirm(client, pending, "real owner pw").status_code == 200


def test_other_addresses_link_after_the_emailed_confirm(client, google_setup, db):
    google_setup["t"] = claims(email="someone@elsewhere.example")
    assert google_login(client, "t").status_code == 202
    assert client.get("/api/me").status_code == 401  # not signed in yet
    link = google_link("someone@elsewhere.example")
    r = google_confirm(client, "t", link)
    assert r.status_code == 200, r.text
    assert r.json()["user"]["email_verified"] is True
    assert client.get("/api/me").json()["email"] == "someone@elsewhere.example"
    assert google_confirm(client, "t", link).status_code == 400  # one use
    assert google_login(client, "t").status_code == 200  # linked: straight in from now on


def test_google_confirm_links_an_existing_password_account(client, google_setup, db):
    signed_up(client, "me@elsewhere.example")
    google_setup["t"] = claims(sub="mine", email="me@elsewhere.example")
    assert google_login(client, "t").status_code == 202
    assert google_confirm(client, "t", google_link("me@elsewhere.example")).status_code == 200
    assert web_login(client, "me@elsewhere.example").status_code == 200  # password still works
    assert len(db.scalars(select(UserIdentity)).all()) == 1


def test_expired_google_links_are_refused(client, google_setup, db):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from lumawen_api.models import EmailToken

    google_setup["t"] = claims(email="late@elsewhere.example")
    google_login(client, "t")
    link = google_link("late@elsewhere.example")
    db.execute(update(EmailToken).values(expires_at=datetime.now(UTC) - timedelta(minutes=1)))
    db.commit()
    assert google_confirm(client, "t", link).status_code == 400


def test_password_reset_removes_linked_google_accounts(client, google_setup, db):
    from helpers import link_token, outbox

    google_setup["t"] = claims(email="someone@elsewhere.example")
    google_login(client, "t")
    google_confirm(client, "t", google_link("someone@elsewhere.example"))
    assert len(db.scalars(select(UserIdentity)).all()) == 1
    client.post("/api/auth/forgot-password", json={"email": "someone@elsewhere.example"})
    token = link_token(outbox("someone@elsewhere.example", "reset")[-1])
    client.post("/api/auth/reset-password", json={"token": token, "password": PASSWORD})
    assert db.scalars(select(UserIdentity)).all() == []
    assert google_login(client, "t").status_code == 202  # has to prove the inbox again


def test_string_email_verified_is_refused(client, google_setup):
    google_setup["s"] = claims(verified="true")
    assert google_login(client, "s").status_code == 403


def test_parallel_first_sign_ins_with_other_address_all_answer_check_email(app, google_setup):
    from concurrent.futures import ThreadPoolExecutor

    from fastapi.testclient import TestClient

    google_setup["t"] = claims(email="me@elsewhere.example")

    def go(_):
        with TestClient(app) as c:
            return google_login(c, "t", kind="app").status_code

    with ThreadPoolExecutor(6) as pool:
        assert set(pool.map(go, range(6))) == {202}


def test_non_string_claims_are_refused(client, google_setup):
    google_setup["l"] = claims(email=["a@gmail.com"])
    assert google_login(client, "l").status_code == 401
