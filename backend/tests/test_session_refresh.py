import datetime as dt

import pytest

from app.session_refresh import _jwt_expiry, _replace_cookie, refresh_provider_sessions


def test_replace_cookie_replaces_token():
    cookie = "app-uuid=a; token=old; userinfo=b"
    assert _replace_cookie(cookie, "token", "new") == "app-uuid=a; token=new; userinfo=b"


def test_jwt_expiry_reads_token_payload():
    header = "eyJhbGciOiJIUzI1NiJ9"
    payload = "eyJleHAiOjE3ODc4ODg0NDl9"
    cookie = f"token={header}.{payload}.sig"
    assert _jwt_expiry(cookie, "token") == dt.datetime.fromtimestamp(1787888449, tz=dt.timezone.utc)


@pytest.mark.asyncio
async def test_refresh_updates_riskbird_when_near_expiry(monkeypatch):
    near_expiry = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=2)
    new_expiry = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=30)

    class FakeRepo:
        def __init__(self) -> None:
            self.updated: list[tuple[str, str, object]] = []

        async def get_runtime_config(self):
            return {
                "sessions": {
                    "riskbird": {
                        "provider": "riskbird",
                        "cookie": "token=old",
                        "expires_at": near_expiry,
                    }
                }
            }

        async def upsert_provider_session(self, provider: str, cookie: str, expires_at: object):
            self.updated.append((provider, cookie, expires_at))

    async def fake_refresh(cookie: str):
        assert cookie == "token=old"
        return "token=new", new_expiry

    monkeypatch.setattr("app.session_refresh._refresh_riskbird", fake_refresh)
    repo = FakeRepo()
    await refresh_provider_sessions(repo)
    assert repo.updated == [("riskbird", "token=new", new_expiry)]


@pytest.mark.asyncio
async def test_refresh_skips_when_not_near_expiry(monkeypatch):
    far_expiry = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)

    class FakeRepo:
        async def get_runtime_config(self):
            return {
                "sessions": {
                    "riskbird": {
                        "provider": "riskbird",
                        "cookie": "token=ok",
                        "expires_at": far_expiry,
                    }
                }
            }

        async def upsert_provider_session(self, provider: str, cookie: str, expires_at: object):
            raise AssertionError("should not refresh")

    async def unexpected(cookie: str):
        raise AssertionError("should not refresh")

    monkeypatch.setattr("app.session_refresh._refresh_riskbird", unexpected)
    await refresh_provider_sessions(FakeRepo())
