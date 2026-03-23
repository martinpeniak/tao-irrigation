"""Tests for the HomGar D01 payload codec."""
from __future__ import annotations

import importlib.util
import struct
import sys
import types
from pathlib import Path
from unittest.mock import patch

BASELINE = "11#17E1BE0019D8001AD8001BD8001D201E201F2018DC0121B70000000022B70000000023B70000000025AD000026AD000027AD0000"
APP_OPEN = "11#17E1BE0019D8211AD8001BD8001D201E201F2018DC0121B725BAEE1822B70000000023B70000000025AD3C0026AD000027AD0000"

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


const = _load_module("const", "const.py")
mqtt = _load_module("mqtt", "mqtt.py")


def test_decode_d01_reports_active_zone():
    decoded = mqtt.decode_d01(APP_OPEN)
    assert decoded["active_zone"] == 1


def test_decode_d01_reports_duration_seconds():
    decoded = mqtt.decode_d01(APP_OPEN)
    assert decoded["duration_seconds"] == 60


def test_decode_d01_reports_no_active_zone_for_baseline():
    decoded = mqtt.decode_d01(BASELINE)
    assert decoded["active_zone"] is None


def test_build_open_command_uses_homgar_epoch_stop_timestamp():
    expected_stop = struct.unpack_from("<I", bytes.fromhex(APP_OPEN.split("#", 1)[1]), 24)[0]
    capture_unix_time = const.HOMGAR_EPOCH_OFFSET + expected_stop - 60

    with patch.object(mqtt.time, "time", return_value=capture_unix_time):
        command = mqtt.build_open_command(BASELINE, zone_addr=1, duration_seconds=60)

    raw = bytes.fromhex(command.split("#", 1)[1])
    assert raw[6] == 0x21
    assert raw[42] == 0x3C
    assert struct.unpack_from("<I", raw, 24)[0] == expected_stop


def test_build_close_command_zeros_runtime_fields():
    command = mqtt.build_close_command(APP_OPEN)
    raw = bytes.fromhex(command.split("#", 1)[1])
    assert raw[6] == 0x00
    assert raw[24:28] == b"\x00\x00\x00\x00"
    assert raw[42:44] == b"\x00\x00"


def test_homgar_now_matches_capture_epoch():
    expected_stop = struct.unpack_from("<I", bytes.fromhex(APP_OPEN.split("#", 1)[1]), 24)[0]
    capture_unix_time = const.HOMGAR_EPOCH_OFFSET + expected_stop - 60
    assert mqtt.homgar_now(capture_unix_time) + 60 == expected_stop
