"""Tests for HomGar API re-login throttling."""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "homgar_timers"
PACKAGE_NAME = "homgar_timers"


def _load_module(module_name: str, relative_path: str):
    package = sys.modules.setdefault(PACKAGE_NAME, types.ModuleType(PACKAGE_NAME))
    package.__path__ = [str(PACKAGE_ROOT)]

    full_name = f"{PACKAGE_NAME}.{module_name}"
    if full_name in sys.modules:
        return sys.modules[full_name]

    spec = importlib.util.spec_from_file_location(full_name, PACKAGE_ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


api_mod = _load_module("api", "api.py")
HomGarApi = api_mod.HomGarApi
HomGarApiError = api_mod.HomGarApiError


def test_re_login_reuses_fresh_token_from_other_thread():
    api = HomGarApi("user@example.com", "secret", "34")
    api._token = "new-token"
    api._iot_credentials = {"mqtt_host": "example.com"}

    with patch.object(api, "_login_locked") as login_locked:
        result = api.re_login(previous_token="old-token")

    assert result == api._iot_credentials
    login_locked.assert_not_called()


def test_re_login_backs_off_after_homgar_rate_limit():
    api = HomGarApi("user@example.com", "secret", "34")

    with patch.object(
        api,
        "_login_locked",
        side_effect=HomGarApiError("Login failed: {'code': 9993, 'msg': 'operate too frequently'}"),
    ):
        with pytest.raises(HomGarApiError, match="operate too frequently"):
            api.re_login(previous_token="expired-token")

    with patch.object(api, "_login_locked") as login_locked:
        with pytest.raises(HomGarApiError, match="Login backoff active"):
            api.re_login(previous_token="expired-token")

    login_locked.assert_not_called()
