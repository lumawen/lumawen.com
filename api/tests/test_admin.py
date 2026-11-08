"""Roles, the admin API and the create-admin command."""

import pytest
from helpers import signed_up, web_login

from lumawen_api.cli import create_admin, main


def test_admin_api_needs_admin_role(client):
    assert client.get("/api/admin/users").status_code == 401
    signed_up(client, "user@example.com")
    web_login(client, "user@example.com")
    assert client.get("/api/admin/users").status_code == 403


def test_create_admin_then_list_users(client):
    signed_up(client, "member@example.com")
    assert create_admin("Admin@Lumawen.com", "admin password 123") == "created"
    r = web_login(client, "admin@lumawen.com", "admin password 123")
    assert r.json()["user"]["role"] == "admin"
    page = client.get("/api/admin/users").json()
    assert page["total"] == 2
    assert {u["email"] for u in page["users"]} == {"admin@lumawen.com", "member@example.com"}


def test_create_admin_promotes_existing_account_and_keeps_password(client):
    signed_up(client, "promote@example.com")
    assert create_admin("promote@example.com", None) == "updated"
    r = web_login(client, "promote@example.com")
    assert r.json()["user"]["role"] == "admin"


def test_create_admin_command_reads_password_from_stdin(client, monkeypatch, capsys):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("stdin password 42\n"))
    main(["create-admin", "cli@lumawen.com", "--password-stdin"])
    assert "created admin cli@lumawen.com" in capsys.readouterr().out
    assert web_login(client, "cli@lumawen.com", "stdin password 42").status_code == 200


def test_create_admin_command_rejects_short_password(client, monkeypatch):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("short\n"))
    with pytest.raises(SystemExit):
        main(["create-admin", "short@lumawen.com", "--password-stdin"])
