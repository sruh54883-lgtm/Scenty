"""Integration tests — exercise the live ASGI app against a real Postgres."""
import os


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_root_redirects_to_webapp(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/webapp"


def test_security_headers_present(client):
    r = client.get("/health")
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "referrer-policy" in r.headers


def test_admin_login_success(client):
    r = client.post("/auth/admin/login",
                    json={"username": "admin", "password": os.environ["ADMIN_SECRET"]})
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "admin"
    assert body["token"]


def test_admin_login_wrong_password(client):
    r = client.post("/auth/admin/login",
                    json={"username": "admin", "password": "definitely-wrong"})
    assert r.status_code == 401


def test_admin_login_unknown_user(client):
    r = client.post("/auth/admin/login",
                    json={"username": "ghost", "password": "x"})
    assert r.status_code == 401
