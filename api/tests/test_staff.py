"""Roles, user management (search, roles, suspend, sign out, delete) and the activity log."""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from helpers import signed_up, web_login
from sqlalchemy import func, select, update
from test_lists import ADMIN_PASSWORD, as_admin

from lumawen_api.db import _session_factory
from lumawen_api.models import Newsletter, User
from lumawen_api.newsletters import purge_archived

ORIGIN = {"Origin": "http://localhost:3040"}


def make(email: str, role: str = "user", name: str | None = None) -> str:
    """A confirmed account with a role. Returns its id. Uses its own client (own cookies)."""
    from lumawen_api.main import app

    with TestClient(app) as c:
        signed_up(c, email)
    with _session_factory()() as db:
        db.execute(update(User).where(User.email == email).values(role=role, display_name=name))
        db.commit()
        return str(db.scalar(select(User.id).where(User.email == email)))


def as_(app, email: str) -> TestClient:
    c = TestClient(app)
    assert web_login(c, email).status_code == 200
    return c


def admin_id() -> str:
    with _session_factory()() as db:
        return str(db.scalar(select(User.id).where(User.email == "admin@lumawen.com")))


def test_search_by_email_or_name_and_filter_by_role(client):
    as_admin(client)
    make("rae@example.com", "contributor", name="Rae Lindqvist")
    make("hank@example.com", "moderator")
    make("someone@example.com")

    def emails(query: str) -> list[str]:
        return [u["email"] for u in client.get(f"/api/admin/users?{query}").json()["users"]]

    assert emails("q=LINDQ") == ["rae@example.com"]
    assert emails("q=hank@") == ["hank@example.com"]
    assert emails("role=moderator") == ["hank@example.com"]
    assert emails("q=%25") == []  # a literal %, not a wildcard
    page = client.get("/api/admin/users").json()
    assert page["total"] == 4
    assert page["counts"]["admin"] == 1 and page["counts"]["contributor"] == 1
    rae = next(u for u in page["users"] if u["email"] == "rae@example.com")
    assert rae["has_password"] and not rae["has_google"] and rae["suspended_at"] is None


def test_who_can_see_what(app, client):
    as_admin(client)
    for role in ("user", "contributor", "moderator", "security"):
        make(f"{role}@example.com", role)
    expect = {
        # role: (users, activity, newsletters, mailing list)
        "user": (403, 403, 403, 403),
        "contributor": (403, 403, 200, 403),
        "moderator": (200, 403, 403, 403),
        "security": (200, 200, 403, 403),
    }
    for role, codes in expect.items():
        c = as_(app, f"{role}@example.com")
        got = tuple(
            c.get(path).status_code
            for path in (
                "/api/admin/users",
                "/api/admin/activity",
                "/api/admin/newsletters",
                "/api/admin/subscribers",
            )
        )
        assert got == codes, role


def test_only_admins_change_roles_and_never_their_own(app, client):
    as_admin(client)
    uid = make("rae@example.com")
    r = client.put(f"/api/admin/users/{uid}/role", json={"role": "contributor"}, headers=ORIGIN)
    assert r.status_code == 200 and r.json()["role"] == "contributor"
    assert (
        client.put(
            f"/api/admin/users/{admin_id()}/role", json={"role": "user"}, headers=ORIGIN
        ).status_code
        == 400
    )
    assert (
        client.put(
            f"/api/admin/users/{uid}/role", json={"role": "boss"}, headers=ORIGIN
        ).status_code
        == 422
    )
    make("sec@example.com", "security")
    sec = as_(app, "sec@example.com")
    assert (
        sec.put(f"/api/admin/users/{uid}/role", json={"role": "admin"}, headers=ORIGIN).status_code
        == 403
    )
    [entry] = client.get("/api/admin/activity").json()["entries"]
    assert entry["action"] == "user.role" and entry["target"] == "rae@example.com"
    assert entry["detail"] == {"before": "user", "after": "contributor"}
    assert entry["actor_email"] == "admin@lumawen.com"


def test_suspend_ends_sessions_and_blocks_sign_in(app, client):
    as_admin(client)
    uid = make("trouble@example.com")
    victim = as_(app, "trouble@example.com")
    assert victim.get("/api/me").status_code == 200

    r = client.post(f"/api/admin/users/{uid}/suspend", json={"reason": "spam"}, headers=ORIGIN)
    assert r.status_code == 200 and r.json()["suspended_reason"] == "spam"
    assert r.json()["sessions"] == 0
    assert victim.get("/api/me").status_code == 401
    r = web_login(TestClient(app), "trouble@example.com")
    assert r.status_code == 403 and "suspended" in r.json()["detail"]
    assert client.get("/api/admin/users?suspended=true").json()["total"] == 1

    assert client.post(f"/api/admin/users/{uid}/unsuspend", headers=ORIGIN).status_code == 200
    assert web_login(TestClient(app), "trouble@example.com").status_code == 200


def test_moderators_and_security_suspend_only_below_them(app, client):
    as_admin(client)
    user = make("u@example.com")
    mod_id = make("mod@example.com", "moderator")
    sec_id = make("sec@example.com", "security")
    mod = as_(app, "mod@example.com")
    sec = as_(app, "sec@example.com")

    def suspend(c, uid):
        return c.post(f"/api/admin/users/{uid}/suspend", json={}, headers=ORIGIN).status_code

    assert suspend(mod, sec_id) == 403
    assert suspend(mod, admin_id()) == 403
    assert suspend(mod, mod_id) == 400  # yourself
    assert suspend(sec, admin_id()) == 403
    assert mod.post(f"/api/admin/users/{user}/sign-out", headers=ORIGIN).status_code == 403
    assert suspend(sec, mod_id) == 200
    assert mod.get("/api/admin/users").status_code == 401  # suspended: signed out
    assert suspend(sec, user) == 200
    assert sec.post(f"/api/admin/users/{user}/sign-out", headers=ORIGIN).status_code == 200
    assert sec.post(f"/api/admin/users/{admin_id()}/sign-out", headers=ORIGIN).status_code == 403


def test_admin_deletes_an_account_but_not_their_own(app, client):
    as_admin(client)
    uid = make("gone@example.com")
    assert client.delete(f"/api/admin/users/{admin_id()}", headers=ORIGIN).status_code == 400
    assert client.delete(f"/api/admin/users/{uid}", headers=ORIGIN).status_code == 204
    assert client.get("/api/admin/users?q=gone").json()["total"] == 0
    assert web_login(TestClient(app), "gone@example.com").status_code == 401
    actions = [e["action"] for e in client.get("/api/admin/activity").json()["entries"]]
    assert actions == ["user.delete"]


def test_contributors_write_drafts_but_admins_send(app, client):
    as_admin(client)
    make("rae@example.com", "contributor")
    rae = as_(app, "rae@example.com")
    r = rae.post(
        "/api/admin/newsletters", json={"subject": "Pickups", "body_text": "Hi"}, headers=ORIGIN
    )
    assert r.status_code == 201
    nid = r.json()["id"]
    base = f"/api/admin/newsletters/{nid}"
    assert rae.post(f"{base}/test", json={}, headers=ORIGIN).status_code == 200
    assert rae.post(f"{base}/test", json={"to": "x@example.com"}, headers=ORIGIN).status_code == 403
    assert rae.post(f"{base}/schedule", json={}, headers=ORIGIN).status_code == 403
    later = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    assert (
        client.post(f"{base}/schedule", json={"send_at": later}, headers=ORIGIN).status_code == 200
    )
    # Scheduled: the contributor can no longer edit or delete it; the admin can.
    assert rae.patch(base, json={"subject": "x"}, headers=ORIGIN).status_code == 403
    assert rae.delete(base, headers=ORIGIN).status_code == 403
    assert client.delete(base, headers=ORIGIN).status_code == 204
    assert client.get(base).status_code == 404
    actions = [e["action"] for e in client.get("/api/admin/activity").json()["entries"]]
    assert actions == ["newsletter.delete_scheduled", "newsletter.schedule"]


def test_archived_newsletters_are_purged_after_30_days(db):
    old = Newsletter(subject="old", body_text="x", status="sent")
    old.archived_at = datetime.now(UTC) - timedelta(days=31)
    recent = Newsletter(subject="recent", body_text="x", status="sent")
    recent.archived_at = datetime.now(UTC) - timedelta(days=29)
    kept = Newsletter(subject="kept", body_text="x", status="sent")
    db.add_all([old, recent, kept])
    db.commit()
    assert purge_archived(db) == 1
    assert sorted(db.scalars(select(Newsletter.subject))) == ["kept", "recent"]


def test_two_admins_demoting_each_other_at_once_leave_one(app, client):
    from concurrent.futures import ThreadPoolExecutor

    as_admin(client)
    for _ in range(5):
        a = admin_id()
        b = make(f"b{_}@example.com", "admin")
        ca, cb = TestClient(app), as_(app, f"b{_}@example.com")
        assert web_login(ca, "admin@lumawen.com", ADMIN_PASSWORD).status_code == 200
        with ThreadPoolExecutor(2) as pool:
            ra = pool.submit(
                ca.put, f"/api/admin/users/{b}/role", json={"role": "user"}, headers=ORIGIN
            )
            rb = pool.submit(
                cb.put, f"/api/admin/users/{a}/role", json={"role": "user"}, headers=ORIGIN
            )
            codes = sorted([ra.result().status_code, rb.result().status_code])
        with _session_factory()() as db:
            admins = db.scalar(select(func.count()).select_from(User).where(User.role == "admin"))
        assert admins >= 1, codes
        # put things back for the next round
        with _session_factory()() as db:
            db.execute(update(User).where(User.email == "admin@lumawen.com").values(role="admin"))
            db.execute(update(User).where(User.id == b).values(role="user"))
            db.commit()


def test_lower_roles_cannot_lift_a_higher_suspension(app, client):
    as_admin(client)
    uid = make("u@example.com")
    make("mod@example.com", "moderator")
    mod = as_(app, "mod@example.com")
    assert (
        client.post(f"/api/admin/users/{uid}/suspend", json={}, headers=ORIGIN).status_code == 200
    )
    r = mod.post(f"/api/admin/users/{uid}/unsuspend", headers=ORIGIN)
    assert r.status_code == 403 and "admin" in r.json()["detail"]
    assert client.post(f"/api/admin/users/{uid}/unsuspend", headers=ORIGIN).status_code == 200


def test_create_admin_lifts_a_suspension(client):
    from lumawen_api.cli import create_admin

    as_admin(client)
    with _session_factory()() as db:
        db.execute(
            update(User)
            .where(User.email == "admin@lumawen.com")
            .values(suspended_at=datetime.now(UTC))
        )
        db.commit()
    create_admin("admin@lumawen.com", None)
    with _session_factory()() as db:
        assert db.scalar(select(User.suspended_at).where(User.email == "admin@lumawen.com")) is None


def test_send_refuses_a_version_the_admin_has_not_seen(app, client):
    as_admin(client)
    make("rae@example.com", "contributor")
    rae = as_(app, "rae@example.com")
    n = client.post(
        "/api/admin/newsletters", json={"subject": "Hi", "body_text": "v1"}, headers=ORIGIN
    ).json()
    seen = n["updated_at"]
    rae.patch(f"/api/admin/newsletters/{n['id']}", json={"body_text": "v2"}, headers=ORIGIN)
    r = client.post(
        f"/api/admin/newsletters/{n['id']}/schedule", json={"seen_version": seen}, headers=ORIGIN
    )
    assert r.status_code == 409
    fresh = client.get(f"/api/admin/newsletters/{n['id']}").json()["updated_at"]
    r = client.post(
        f"/api/admin/newsletters/{n['id']}/schedule", json={"seen_version": fresh}, headers=ORIGIN
    )
    assert r.status_code == 200, r.text


def test_erasing_scrubs_the_activity_log(client):
    as_admin(client)
    uid = make("forget-me@example.com")
    client.post(f"/api/admin/users/{uid}/suspend", json={}, headers=ORIGIN)
    client.delete(f"/api/admin/users/{uid}", headers=ORIGIN)
    entries = client.get("/api/admin/activity").json()["entries"]
    assert all("forget-me" not in e["target"] for e in entries)
    assert [e["target"] for e in entries] == ["(erased account)", "(erased)"]
