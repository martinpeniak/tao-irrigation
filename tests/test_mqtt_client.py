"""Tests for HomGar MQTT client command pacing."""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch

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


mqtt_mod = _load_module("mqtt", "mqtt.py")


class _DummyApi:
    def re_login(self):
        raise AssertionError("re_login should not be called in pacing test")


def test_wait_for_command_slot_spaces_commands_per_timer():
    client = mqtt_mod.HomGarMQTTClient(
        _DummyApi(),
        {
            "product_key": "pk",
            "device_name": "dn",
            "device_secret": "secret",
            "mqtt_host": "example.com",
            "mqtt_port": 1883,
        },
        lambda *_: None,
    )

    monotonic_values = iter([10.0, 12.0])
    with patch.object(mqtt_mod.time, "monotonic", side_effect=lambda: next(monotonic_values)):
        with patch.object(mqtt_mod.time, "sleep") as sleep_mock:
            client._wait_for_command_slot(41212, 1)
            client._wait_for_command_slot(41212, 1)

    sleep_mock.assert_called_once()
    assert round(sleep_mock.call_args.args[0], 1) == 28.0
