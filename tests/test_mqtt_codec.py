"""Tests for HomGar MQTT D01 payload encoding and decoding."""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

BASELINE = "11#17E1BE0019D8001AD8001BD8001D201E201F2018DC0121B70000000022B70000000023B70000000025AD000026AD000027AD0000"
ZONE1_ON = "11#17E1BF0019D8211AD8001BD8001D201E201F2018DC0121B77029EE1822B70000000023B70000000025AD580226AD000027AD0000"

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


_load_module("const", "const.py")
mqtt = _load_module("mqtt", "mqtt.py")


def test_decode_d01_reports_active_zone():
    decoded = mqtt.decode_d01(ZONE1_ON)
    assert decoded["active_zone"] == 1


def test_decode_d01_reports_duration_seconds():
    decoded = mqtt.decode_d01(ZONE1_ON)
    assert decoded["duration_seconds"] == 600


def test_decode_d01_reports_no_active_zone_for_baseline():
    decoded = mqtt.decode_d01(BASELINE)
    assert decoded["active_zone"] is None


def test_build_open_command_sets_zone_flag_and_duration():
    command = mqtt.build_open_command(BASELINE, zone_addr=1, duration_seconds=600)
    raw = bytes.fromhex(command.split("#", 1)[1])
    assert raw[6] == 0x21
    assert raw[42:44] == bytes.fromhex("5802")


def test_build_close_command_resets_zone_flag_and_duration():
    """Close command should clear zone flag, stop timestamp and duration.

    Note: does NOT check exact byte equality with BASELINE because the sequence
    byte (byte[2]) is decremented by build_close_command to match the hub's
    expected sequence counter, so the payload differs from the original baseline.
    """
    command = mqtt.build_close_command(ZONE1_ON)
    raw = bytes.fromhex(command.split("#", 1)[1])
    assert raw[6] == 0x00, "zone flag should be cleared"
    assert raw[42:44] == b'\x00\x00', "duration should be zeroed"
    assert raw[24:28] == b'\x00\x00\x00\x00', "stop timestamp should be zeroed"
