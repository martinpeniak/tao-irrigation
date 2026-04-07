"""Tests for HomGar switch optimistic state handling."""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "homgar_timers"
PACKAGE_NAME = "homgar_timers"


def _install_homeassistant_stubs() -> None:
    if "homeassistant" in sys.modules:
        return

    homeassistant = types.ModuleType("homeassistant")
    components = types.ModuleType("homeassistant.components")
    switch_module = types.ModuleType("homeassistant.components.switch")
    config_entries = types.ModuleType("homeassistant.config_entries")
    core = types.ModuleType("homeassistant.core")

    class _SwitchEntity:
        def schedule_update_ha_state(self):
            return None

        def async_write_ha_state(self):
            return None

        def async_on_remove(self, _listener):
            return None

    switch_module.SwitchEntity = _SwitchEntity
    config_entries.ConfigEntry = object
    core.callback = lambda func: func

    sys.modules["homeassistant"] = homeassistant
    sys.modules["homeassistant.components"] = components
    sys.modules["homeassistant.components.switch"] = switch_module
    sys.modules["homeassistant.config_entries"] = config_entries
    sys.modules["homeassistant.core"] = core


def _load_module(module_name: str, relative_path: str):
    _install_homeassistant_stubs()

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


switch_mod = _load_module("switch", "switch.py")


class _DummyMqtt:
    def send_open(self, *args, **kwargs):
        return True

    def send_close(self, *args, **kwargs):
        return True


def _build_switch():
    return switch_mod.HomGarZoneSwitch(
        hass=None,
        hub_mid=41212,
        sid=77343,
        timer_addr=1,
        zone_addr=3,
        timer_name="House Garden - 3",
        zone_name="Papaya 40",
        hub_product_key="pk",
        hub_device_name="dn",
        mqtt_client=_DummyMqtt(),
        state_store={},
    )


def test_confirmed_on_state_survives_null_poll_within_run_window():
    entity = _build_switch()

    with patch.object(switch_mod.time, "time", return_value=1000.0):
        entity.turn_on()

    with patch.object(switch_mod.time, "time", return_value=1005.0):
        entity._apply_state_record({"active_zone": 3})
        assert entity.is_on is True
        assert entity.assumed_state is True

    with patch.object(switch_mod.time, "time", return_value=1015.0):
        entity._apply_state_record({"active_zone": None})
        assert entity.is_on is True
        assert entity.assumed_state is True


def test_conflicting_zone_still_clears_pending_on_state():
    entity = _build_switch()

    with patch.object(switch_mod.time, "time", return_value=1000.0):
        entity.turn_on()

    with patch.object(switch_mod.time, "time", return_value=1010.0):
        entity._apply_state_record({"active_zone": 1})
        assert entity.is_on is False
        assert entity.assumed_state is False
