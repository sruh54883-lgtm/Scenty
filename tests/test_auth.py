"""Unit tests for auth utilities — no database required."""
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import auth
from config import settings


# ---------- passwords ----------
def test_password_hash_roundtrip():
    h = auth.hash_password("s3cret-pass")
    assert h != "s3cret-pass"          # never store plaintext
    assert auth.verify_password("s3cret-pass", h)
    assert not auth.verify_password("wrong", h)


def test_verify_password_handles_bad_hash():
    assert auth.verify_password("x", "not-a-valid-bcrypt-hash") is False


# ---------- JWT ----------
def test_jwt_roundtrip():
    token = auth.create_token(42, "admin")
    data = auth.decode_token(token)
    assert data is not None
    assert data["sub"] == "42"
    assert data["role"] == "admin"


def test_jwt_rejects_garbage():
    assert auth.decode_token("not.a.jwt") is None


def test_jwt_rejects_wrong_signature():
    from jose import jwt
    forged = jwt.encode({"sub": "1", "role": "admin"}, "attacker-secret", algorithm="HS256")
    assert auth.decode_token(forged) is None


# ---------- Telegram initData HMAC validation ----------
def _make_init_data(bot_token: str, user: dict, auth_date: int | None = None) -> str:
    auth_date = str(auth_date if auth_date is not None else int(time.time()))
    fields = {"auth_date": auth_date, "user": json.dumps(user)}
    dcs = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_telegram_init_data_valid():
    init = _make_init_data(settings.BOT_TOKEN, {"id": 123, "first_name": "Test"})
    parsed = auth.parse_telegram_init_data(init)
    assert parsed is not None and parsed["id"] == 123


def test_telegram_init_data_tampered_hash():
    init = _make_init_data(settings.BOT_TOKEN, {"id": 123}) + "deadbeef"
    assert auth.parse_telegram_init_data(init) is None


def test_telegram_init_data_wrong_token():
    init = _make_init_data("999:some-other-bot-token", {"id": 123})
    assert auth.parse_telegram_init_data(init) is None


def test_telegram_init_data_expired():
    old = int(time.time()) - 90000  # >24h ago
    init = _make_init_data(settings.BOT_TOKEN, {"id": 123}, auth_date=old)
    assert auth.parse_telegram_init_data(init) is None


def test_telegram_init_data_empty():
    assert auth.parse_telegram_init_data("") is None
