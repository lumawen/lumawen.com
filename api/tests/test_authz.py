"""Who can call what: every staff route, tried as every role, by cookie and by app token.

If you add a route under /api/admin, add it to ROUTES below or test_every_admin_route_is_listed
fails. That's on purpose: a new route has to say who may use it.
"""

import uuid

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from helpers import app_login
from sqlalchemy import select, update
from test_lists import as_admin, subscribe
from test_staff import ORIGIN, as_, make

from lumawen_api.db import _session_factory
from lumawen_api.models import Newsletter, User

ROLES = ("user", "contributor", "moderator", "security", "admin")
WRITERS = {"contributor", "admin"}
STAFF = {"moderator", "security", "admin"}
SECURITY = {"security", "admin"}
ADMIN = {"admin"}

# (method, path template, json body, roles allowed). {nid} is a draft newsletter, {uid} a plain
# user, {sid} a subscriber. Allowed roles may still get 4xx for business reasons (409 etc.),
# but never 401/403; everyone else must get 403.
ROUTES = [
    ("GET", "/api/admin/newsletters", None, WRITERS),
    ("POST", "/api/admin/newsletters", {"subject": "Hi", "body_text": "Body"}, WRITERS),
    ("GET", "/api/admin/newsletters/{nid}", None, WRITERS),
    ("PATCH", "/api/admin/newsletters/{nid}", {"subject": "Edited"}, WRITERS),
    ("POST", "/api/admin/newsletters/{nid}/test", {}, WRITERS),
    ("POST", "/api/admin/newsletters/{nid}/restore", None, ADMIN),
    ("POST", "/api/admin/newsletters/{nid}/schedule", {}, ADMIN),
    ("POST", "/api/admin/newsletters/{nid}/cancel", None, ADMIN),
    ("DELETE", "/api/admin/newsletters/{nid}", None, WRITERS),
    ("GET", "/api/admin/subscribers", None, ADMIN),
    ("GET", "/api/admin/subscribers.csv", None, ADMIN),
    ("DELETE", "/api/admin/subscribers/{sid}", None, ADMIN),
    ("GET", "/api/admin/users", None, STAFF),
    ("PUT", "/api/admin/users/{uid}/role", {"role": "admin"}, ADMIN),
    ("POST", "/api/admin/users/{uid}/suspend", {"reason": "x"}, STAFF),
    ("POST", "/api/admin/users/{uid}/unsuspend", None, STAFF),
    ("POST", "/api/admin/users/{uid}/sign-out", None, SECURITY),
    ("DELETE", "/api/admin/users/{uid}", None, ADMIN),
    ("GET", "/api/admin/activity", None, SECURITY),
]


def test_every_admin_route_is_listed(app):
    listed = {(m, p) for m, p, _, _ in ROUTES}
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path.startswith("/api/admin"):
            path = route.path.replace("{newsletter_id}", "{nid}")
            path = path.replace("{user_id}", "{uid}").replace("{subscriber_id}", "{sid}")
            for method in route.methods - {"HEAD"}:
                assert (method, path) in listed, f"{method} {route.path} has no authz test"


def _fixtures(client) -> dict:
    """A fresh draft, plain user and subscriber for one call (the call may delete them)."""
    r = client.post("/api/admin/newsletters", json={"subject": "Draft", "body_text": "Body"})
    assert r.status_code == 201, r.text
    uid = uuid.uuid4()
    with _session_factory()() as db:  # straight into the table: sign-ups are rate limited
        db.add(User(id=uid, email=f"target-{uid.hex[:8]}@example.com", role="user"))
        db.commit()
    subscribe(client, f"sub-{uuid.uuid4().hex[:8]}@example.com")
    from lumawen_api.models import Subscriber

    with _session_factory()() as db:
        sid = db.scalar(select(Subscriber.id).order_by(Subscriber.created_at.desc()).limit(1))
    return {"nid": r.json()["id"], "uid": str(uid), "sid": str(sid)}


def _call(c: TestClient, method: str, path: str, body, headers=None):
    return c.request(method, path, json=body, headers={**ORIGIN, **(headers or {})})


@pytest.mark.parametrize("transport", ["cookie", "bearer"])
def test_role_matrix(app, client, transport):
    as_admin(client)
    for role in ROLES[:-1]:
        make(f"{role}@example.com", role)
    for role in ROLES:
        email = "admin@lumawen.com" if role == "admin" else f"{role}@example.com"
        if transport == "cookie":
            if role == "admin":
                c, headers = client, None
            else:
                c, headers = as_(app, email), None
        else:
            from test_lists import ADMIN_PASSWORD

            password = ADMIN_PASSWORD if role == "admin" else None
            tokens = (
                app_login(TestClient(app), email, password)
                if password
                else app_login(TestClient(app), email)
            )
            c, headers = TestClient(app), {"Authorization": f"Bearer {tokens['access_token']}"}
        for method, template, body, allowed in ROUTES:
            path = template.format(**_fixtures(client))
            got = _call(c, method, path, body, headers).status_code
            if role in allowed:
                assert got not in (401, 403), f"{role} {method} {template}: {got}"
            else:
                assert got == 403, f"{role} {method} {template}: {got} (want 403)"


def test_anonymous_gets_401_everywhere(app, client):
    as_admin(client)
    anon = TestClient(app)
    for method, template, body, _ in ROUTES:
        path = template.format(**_fixtures(client))
        assert _call(anon, method, path, body).status_code == 401, f"{method} {template}"


# ---- contributor edge cases --------------------------------------------------------------------


def _contributor(app, client) -> TestClient:
    as_admin(client)
    make("writer@example.com", "contributor")
    return as_(app, "writer@example.com")


def _draft(c) -> str:
    r = c.post(
        "/api/admin/newsletters", json={"subject": "Hi", "body_text": "Body"}, headers=ORIGIN
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_contributor_cant_set_status_or_send_at_through_edit(app, client):
    writer = _contributor(app, client)
    nid = _draft(writer)
    r = writer.patch(
        f"/api/admin/newsletters/{nid}",
        json={"status": "scheduled", "send_at": "2026-01-01T00:00:00Z", "recipients": 1},
        headers=ORIGIN,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "draft" and r.json()["send_at"] is None
    r = writer.post(
        "/api/admin/newsletters",
        json={
            "subject": "x",
            "body_text": "y",
            "status": "scheduled",
            "send_at": "2026-01-01T00:00:00Z",
        },
        headers=ORIGIN,
    )
    assert r.json()["status"] == "draft"


def test_contributor_cant_touch_a_scheduled_newsletter(app, client):
    writer = _contributor(app, client)
    nid = _draft(writer)
    r = client.post(
        f"/api/admin/newsletters/{nid}/schedule",
        json={"send_at": "2099-01-01T00:00:00Z"},
        headers=ORIGIN,
    )
    assert r.status_code in (200, 422)
    with _session_factory()() as db:
        db.execute(update(Newsletter).where(Newsletter.id == nid).values(status="scheduled"))
        db.commit()
    assert (
        writer.patch(
            f"/api/admin/newsletters/{nid}", json={"body_text": "evil"}, headers=ORIGIN
        ).status_code
        == 403
    )
    assert writer.delete(f"/api/admin/newsletters/{nid}", headers=ORIGIN).status_code == 403
    assert writer.post(f"/api/admin/newsletters/{nid}/cancel", headers=ORIGIN).status_code == 403


@pytest.mark.parametrize(
    "to", ["admin@lumawen.com", "WRITER@example.com ", "writer+x@example.com", "someone@else.com"]
)
def test_contributor_tests_only_go_to_themselves(app, client, to):
    writer = _contributor(app, client)
    nid = _draft(writer)
    r = writer.post(f"/api/admin/newsletters/{nid}/test", json={"to": to}, headers=ORIGIN)
    if to.strip().lower() == "writer@example.com":
        assert r.status_code == 200
    else:
        assert r.status_code == 403, r.text


def test_admin_send_refuses_words_it_hasnt_seen(app, client):
    """A contributor edits after the admin opened it: the admin's send is refused."""
    writer = _contributor(app, client)
    nid = _draft(writer)
    seen = client.get(f"/api/admin/newsletters/{nid}").json()["updated_at"]
    assert (
        writer.patch(
            f"/api/admin/newsletters/{nid}", json={"body_text": "swapped"}, headers=ORIGIN
        ).status_code
        == 200
    )
    r = client.post(
        f"/api/admin/newsletters/{nid}/schedule", json={"seen_version": seen}, headers=ORIGIN
    )
    assert r.status_code == 409


def test_staff_cant_change_their_own_role(app, client):
    as_admin(client)
    for role in ("contributor", "moderator", "security"):
        uid = make(f"{role}@example.com", role)
        c = as_(app, f"{role}@example.com")
        assert (
            c.put(
                f"/api/admin/users/{uid}/role", json={"role": "admin"}, headers=ORIGIN
            ).status_code
            == 403
        )
        with _session_factory()() as db:
            assert db.scalar(select(User.role).where(User.id == uid)) == role


def test_demoted_contributor_loses_access_at_once(app, client):
    as_admin(client)
    uid = make("writer@example.com", "contributor")
    writer = as_(app, "writer@example.com")
    tokens = app_login(TestClient(app), "writer@example.com")
    bearer = {"Authorization": f"Bearer {tokens['access_token']}"}
    assert writer.get("/api/admin/newsletters").status_code == 200
    assert (
        client.put(
            f"/api/admin/users/{uid}/role", json={"role": "user"}, headers=ORIGIN
        ).status_code
        == 200
    )
    assert writer.get("/api/admin/newsletters").status_code == 403
    assert TestClient(app).get("/api/admin/newsletters", headers=bearer).status_code == 403
