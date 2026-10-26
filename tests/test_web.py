import re
import time

import pytest
from fastapi.testclient import TestClient

from hevy.config import Settings
from hevy.db import set_meta
from hevy.web import create_app

BASE = "/hevy"
CSRF = "t" * 43
HEADERS = {"x-site-user": "owner", "x-site-csrf": CSRF, "x-site-expires": str(int(time.time()) + 3600)}


def client_for(settings, monkeypatch, peer="127.0.0.1"):
    monkeypatch.setenv("SITE_AUTH", "1")
    app = create_app(settings)

    async def transport(scope, receive, send):
        if scope["type"] == "http":
            scope = {**scope, "client": (peer, 50000)}
        await app(scope, receive, send)
    return TestClient(transport, follow_redirects=False)


def test_pages_and_status_with_central_login(settings, monkeypatch):
    with client_for(settings, monkeypatch) as c:
        r = c.get(BASE + "/", headers=HEADERS)
        assert r.status_code == 200 and f'name="csrf" content="{CSRF}"' in r.text
        assert "script-src 'self'" in r.headers["content-security-policy"] and r.headers["cache-control"] == "no-store"
        assert c.get(BASE + "/system", headers=HEADERS).status_code == 200
        s = c.get(BASE + "/api/status", headers=HEADERS).json()
        assert s["sync"]["state"] == "never" and s["counts"]["workouts"] == 0 and s["sync"]["worker_alive"] is False
        assert c.get(BASE, headers=HEADERS).headers["location"] == BASE + "/"
        assert c.get(BASE + "/static/app.css", headers=HEADERS).status_code == 200
        assert c.get(BASE + "/nope", headers=HEADERS).status_code == 404


@pytest.mark.parametrize("change", [{"x-site-user": "other"}, {"x-site-csrf": "short"}, {"x-site-expires": "1"}])
def test_invalid_or_missing_login_fails_closed(settings, monkeypatch, change):
    with client_for(settings, monkeypatch) as c:
        for path in ("/", "/api/status", "/static/app.js", "/nope"):
            assert c.get(BASE + path, headers={**HEADERS, **change}).status_code == 401
            assert c.get(BASE + path).status_code == 401
        assert c.post(BASE + "/api/sync", headers={**HEADERS, **change, "x-csrf-token": CSRF}).status_code == 401


def test_headers_from_elsewhere_are_ignored(settings, monkeypatch):
    with client_for(settings, monkeypatch, "198.51.100.23") as c:
        assert c.get(BASE + "/api/status", headers={**HEADERS, "x-forwarded-for": "127.0.0.1"}).status_code == 401
    with client_for(settings, monkeypatch) as c:
        assert c.get(BASE + "/api/status", headers=list(HEADERS.items()) + [("x-site-user", "owner")]).status_code == 401


def test_proxy_key_is_required_once_configured(settings, monkeypatch, tmp_path):
    key = "k" * 64
    (tmp_path / "proxy-key").write_text(key + "\n")
    monkeypatch.setenv("SITE_PROXY_KEY_FILE", str(tmp_path / "proxy-key"))
    with client_for(settings, monkeypatch) as c:
        assert c.get(BASE + "/api/status", headers={**HEADERS, "x-site-proxy-key": key}).status_code == 200
        assert c.get(BASE + "/api/status", headers=HEADERS).status_code == 401             # a local process without the key
        assert c.get(BASE + "/api/status", headers={**HEADERS, "x-site-proxy-key": "k" * 63 + "x"}).status_code == 401
        two = list(HEADERS.items()) + [("x-site-proxy-key", key), ("x-site-proxy-key", key)]
        assert c.get(BASE + "/api/status", headers=two).status_code == 401
    with client_for(settings, monkeypatch, "198.51.100.23") as c:                         # the key alone is not enough
        assert c.get(BASE + "/api/status", headers={**HEADERS, "x-site-proxy-key": key}).status_code == 401


def test_production_refuses_without_proxy_key(settings, monkeypatch):
    monkeypatch.setenv("SITE_AUTH", "1")
    monkeypatch.delenv("SITE_PROXY_KEY_FILE", raising=False)
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    settings.env = "production"
    with pytest.raises(SystemExit, match="proxy key"):
        create_app(settings)


def test_sync_request_needs_csrf_and_same_origin(settings, monkeypatch, db):
    with client_for(settings, monkeypatch) as c:
        assert c.post(BASE + "/api/sync", headers=HEADERS).status_code == 403
        assert c.post(BASE + "/api/sync", headers={**HEADERS, "x-csrf-token": "x" * 43}).status_code == 403
        assert c.post(BASE + "/api/sync", headers={**HEADERS, "x-csrf-token": CSRF, "origin": "https://evil.example"}).status_code == 403
        ok = {**HEADERS, "x-csrf-token": CSRF, "origin": "https://example.com"}
        r1 = c.post(BASE + "/api/sync", headers=ok)
        r2 = c.post(BASE + "/api/sync", headers=ok)
        assert r1.status_code == 202 and r1.json()["queued"] is True
        assert r2.json() == {"request_id": r1.json()["request_id"], "queued": False}      # no pile-up
        assert c.get(BASE + "/api/status", headers=HEADERS).json()["sync"]["pending_request"]["id"] == r1.json()["request_id"]
        assert c.put(BASE + "/api/sync", headers=ok).status_code == 405


def test_production_refuses_without_central_login(settings, monkeypatch):
    monkeypatch.delenv("SITE_AUTH", raising=False)
    settings.env = "production"
    with pytest.raises(SystemExit):
        create_app(settings)


def test_production_refuses_development_data(settings, monkeypatch, db):
    with db.tx() as c:
        set_meta(c, "data_origin", "hevy-dump")
    monkeypatch.setenv("SITE_AUTH", "1")
    (settings.db_path.parent / "proxy-key").write_text("k" * 64)
    monkeypatch.setenv("SITE_PROXY_KEY_FILE", str(settings.db_path.parent / "proxy-key"))
    settings.env = "production"
    with pytest.raises(SystemExit, match="hevy-dump"):
        create_app(settings)


def test_status_after_a_sync(settings, monkeypatch, engine, fake):
    from datetime import datetime, timezone
    fake.add_workout(datetime(2026, 9, 1, 17, 0, tzinfo=timezone.utc))
    engine.run("backfill", "test")
    with client_for(settings, monkeypatch) as c:
        s = c.get(BASE + "/api/status", headers=HEADERS).json()
    assert s["sync"]["state"] == "ok" and s["counts"]["workouts"] == 1 and s["runs"][0]["kind"] == "backfill"
    assert "api-key" not in str(s) and "test-key" not in str(s)


def test_generic_proxy_login(settings, monkeypatch):
    """HEVY_AUTH=proxy: trust X-Remote-User from a proxy on this machine; production refuses to start with no login."""
    monkeypatch.delenv("SITE_AUTH", raising=False)
    monkeypatch.delenv("HEVY_AUTH", raising=False)
    prod = Settings(env="production", data_dir=settings.data_dir, public_origins=("https://example.com",))
    with pytest.raises(SystemExit, match="login in front"):
        create_app(prod)
    monkeypatch.setenv("HEVY_AUTH", "proxy")
    app = create_app(prod)

    def client(peer):
        async def transport(scope, receive, send):
            if scope["type"] == "http":
                scope = {**scope, "client": (peer, 50000)}
            await app(scope, receive, send)
        return TestClient(transport, follow_redirects=False)
    with client("127.0.0.1") as c:
        assert c.get(BASE + "/api/status").status_code == 401                                  # no user: refused
        page = c.get(BASE + "/", headers={"x-remote-user": "sam"})
        assert page.status_code == 200
        token = re.search(r'name="csrf" content="([^"]+)"', page.text).group(1)
        ok = {"x-remote-user": "sam", "x-csrf-token": token, "origin": "https://example.com"}
        assert c.post(BASE + "/api/sync", headers=ok).status_code == 202
        assert c.post(BASE + "/api/sync", headers={**ok, "x-csrf-token": "x" * 43}).status_code == 403
        assert c.get(BASE + "/api/status", headers={"x-remote-user": "bad user!"}).status_code == 401
    with client("10.0.0.5") as c:
        assert c.get(BASE + "/api/status", headers={"x-remote-user": "sam"}).status_code == 401  # not via the local proxy
