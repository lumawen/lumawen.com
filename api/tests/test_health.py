from sqlalchemy import create_engine, text


def test_health_reports_ok_with_database(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["version"]


def test_health_includes_build_info(client, tmp_path, monkeypatch):
    info = tmp_path / "build-info.json"
    info.write_text('{"commit": "abc1234", "built_at": "2026-11-08T12:00:00Z"}')
    from lumawen_api import config

    monkeypatch.setattr(config.get_settings(), "build_info_path", str(info))
    body = client.get("/api/health").json()
    assert body["commit"] == "abc1234"
    assert body["built_at"] == "2026-11-08T12:00:00Z"


def test_health_returns_503_when_database_is_down(client, monkeypatch):
    from fastapi.testclient import TestClient

    from lumawen_api import db
    from lumawen_api.main import create_app

    broken = create_engine("postgresql+psycopg://nobody@127.0.0.1:1/none", pool_pre_ping=False)
    monkeypatch.setattr(db, "_session_factory", lambda: db.sessionmaker(bind=broken))
    with TestClient(create_app()) as c:
        r = c.get("/api/health")
    assert r.status_code == 503
    assert r.json()["database"] == "unreachable"


def test_migrations_seed_community_settings(database_url):
    engine = create_engine(database_url)
    with engine.connect() as conn:
        rows = dict(conn.execute(text("SELECT key, value FROM app_settings")).all())
    engine.dispose()
    assert rows["community.mode"] == "both"
    assert "reddit.com/r/lumawen" in rows["community.subreddit_url"]


def test_docs_hidden_in_production(monkeypatch, database_url):
    from fastapi.testclient import TestClient

    from lumawen_api import config
    from lumawen_api.main import create_app

    monkeypatch.setattr(config.get_settings(), "env", "production")
    with TestClient(create_app()) as c:
        assert c.get("/api/docs").status_code == 404
        assert c.get("/api/openapi.json").status_code == 404


def test_public_config_gives_the_web_google_client_id(client, monkeypatch):
    from lumawen_api import config

    assert client.get("/api/config").json() == {"google_client_id": None}
    monkeypatch.setenv("LUMAWEN_GOOGLE_CLIENT_IDS", "web-id.apps.googleusercontent.com, android-id")
    config.get_settings.cache_clear()
    try:
        assert client.get("/api/config").json() == {
            "google_client_id": "web-id.apps.googleusercontent.com"
        }
    finally:
        monkeypatch.delenv("LUMAWEN_GOOGLE_CLIENT_IDS")
        config.get_settings.cache_clear()
