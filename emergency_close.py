"""Emergency close script for all TAO HomGar irrigation timers."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
PACKAGE_ROOT = REPO_ROOT / "homgar_timers"
PACKAGE_NAME = "homgar_timers"
DEFAULT_HA_HOST = os.getenv("TAO_HA_HOST", "tao-ha.tail03c0af.ts.net")
TIMERS = [
    {"label": "Oliver's Orchard", "mid": 33679, "addr": 1, "sid": 63474},
    {"label": "House Garden - 3", "mid": 41212, "addr": 1, "sid": 77343},
    {"label": "House Garden - 2", "mid": 41212, "addr": 2, "sid": 77344},
    {"label": "Oasis", "mid": 50612, "addr": 1, "sid": 94930},
]


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
mqtt_mod = _load_module("mqtt", "mqtt.py")
HomGarApi = api_mod.HomGarApi
build_close_command = mqtt_mod.build_close_command
decode_d01 = mqtt_mod.decode_d01


def _parse_homgar_config(raw_config: str) -> dict[str, str]:
    config: dict[str, str] = {}
    inside_block = False
    for line in raw_config.splitlines():
        if not inside_block:
            if line.strip() == "homgar_timers:":
                inside_block = True
            continue

        if line and not line[0].isspace():
            break
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        config[key.strip()] = value.strip().strip("\"'")
    return config


def _load_credentials() -> tuple[str, str, str]:
    email = os.getenv("HOMGAR_EMAIL")
    password = os.getenv("HOMGAR_PASSWORD")
    area_code = os.getenv("HOMGAR_AREA_CODE", "34")
    if email and password:
        return email, password, area_code

    try:
        result = subprocess.run(
            [
                "ssh",
                "-o",
                "StrictHostKeyChecking=no",
                f"root@{DEFAULT_HA_HOST}",
                "cat /homeassistant/configuration.yaml",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception as exc:  # pragma: no cover - operator fallback
        raise SystemExit(
            "Could not load HomGar credentials from HA. Set HOMGAR_EMAIL, HOMGAR_PASSWORD, "
            f"and HOMGAR_AREA_CODE, or make sure SSH to {DEFAULT_HA_HOST} works. ({exc})"
        ) from exc

    config = _parse_homgar_config(result.stdout)
    if config.get("email") and config.get("password"):
        return config["email"], config["password"], config.get("area_code", "34")

    raise SystemExit(
        "No homgar_timers credentials found in /homeassistant/configuration.yaml. "
        "Set HOMGAR_EMAIL, HOMGAR_PASSWORD, and HOMGAR_AREA_CODE first."
    )


def main() -> int:
    email, password, area_code = _load_credentials()
    api = HomGarApi(email, password, area_code)
    api.login()
    discovered = {
        (int(timer["mid"]), int(timer["addr"])): timer
        for timer in api.get_timer_devices()
    }

    overall_success = True
    for timer in TIMERS:
        discovered_timer = discovered.get((timer["mid"], timer["addr"]))
        if not discovered_timer:
            print(
                f"[FAILED] {timer['label']}: could not discover hub metadata for "
                f"mid={timer['mid']} addr={timer['addr']}"
            )
            overall_success = False
            continue
        d_key = f"D{timer['addr']:02d}"
        try:
            current_payload = api.get_current_payloads(timer["mid"]).get(d_key, "")
        except Exception as exc:
            print(
                f"[WARN] {timer['label']}: could not fetch current payload for "
                f"mid={timer['mid']} addr={timer['addr']} ({exc})"
            )
            current_payload = ""
        active_zone = decode_d01(current_payload).get("active_zone") if current_payload else None
        if active_zone not in (1, 2, 3):
            active_zone = None
        if timer["addr"] > 1:
            state = build_close_command(current_payload) if current_payload else ""
            ok = api.set_sub_device_param(timer["sid"], timer["mid"], state) if state else False
        else:
            state = api.control_work_mode(
                mid=timer["mid"],
                product_key=discovered_timer["hub_product_key"],
                device_name=discovered_timer["hub_device_name"],
                mode=0,
                addr=timer["addr"],
                port=active_zone or 1,
                param="",
                duration=0,
            )
            ok = state is not None or active_zone is None
        status = "OK" if ok else "FAILED"
        print(
            f"[{status}] {timer['label']}: mid={timer['mid']} sid={timer['sid']} "
            f"addr={timer['addr']} active_zone={active_zone or 0} state={state or '<none>'}"
        )
        overall_success = overall_success and ok

    return 0 if overall_success else 1


if __name__ == "__main__":
    raise SystemExit(main())
