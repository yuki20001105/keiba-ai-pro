import asyncio

import pytest
from fastapi import HTTPException

from deps import auth


@pytest.mark.parametrize("environment", ["local", "development", "test", "production"])
def test_current_admin_never_falls_back_to_stale_admin_claim(monkeypatch, environment):
    monkeypatch.setenv("APP_ENV", environment)
    monkeypatch.setattr(auth, "_get_profile_from_db", lambda _: None)
    with pytest.raises(HTTPException) as error:
        asyncio.run(auth.require_current_admin({"user_id": "owner", "role": "admin"}))
    assert error.value.status_code == 503


def test_current_admin_rejects_revoked_role(monkeypatch):
    monkeypatch.setattr(auth, "_get_profile_from_db", lambda _: {"role": "user"})
    with pytest.raises(HTTPException) as error:
        asyncio.run(auth.require_current_admin({"user_id": "owner", "role": "admin"}))
    assert error.value.status_code == 403


def test_current_admin_requires_authenticated_identity(monkeypatch):
    calls = []
    monkeypatch.setattr(auth, "_get_profile_from_db", lambda value: calls.append(value))
    with pytest.raises(HTTPException) as error:
        asyncio.run(auth.require_current_admin({"role": "admin"}))
    assert error.value.status_code == 401
    assert calls == []


def test_current_admin_preserves_verified_owner(monkeypatch):
    calls = []

    def profile(user_id):
        calls.append(user_id)
        return {"role": "admin"}

    monkeypatch.setattr(auth, "_get_profile_from_db", profile)
    actual = asyncio.run(auth.require_current_admin({"user_id": "owner", "role": "user"}))
    assert actual == {"user_id": "owner", "role": "admin"}
    assert calls == ["owner"]
