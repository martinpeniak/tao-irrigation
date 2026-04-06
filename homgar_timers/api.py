"""HomGar Cloud API client.

Authentication (reverse-engineered from homgar_api.py v0.2.9):
  - Endpoint:  /auth/basic/app/login  (NOT /app/user/login)
  - Password:  MD5(password.encode()).hexdigest()
  - DeviceId:  MD5((email + area_code).encode()).hexdigest()
  - Headers:   Content-Type: application/json, lang: en, appCode: 1
  - Auth token used in subsequent requests as "auth" header (not Authorization)
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import urllib.request
import urllib.error
from typing import Any

from .const import (
    HOMGAR_BASE_URL, HOMGAR_LOGIN_PATH,
    HOMGAR_HOMES_PATH, HOMGAR_DEVICES_PATH, HOMGAR_DEVICE_STATUS_PATH, TIMER_MODEL,
    LOGIN_RATE_LIMIT_BACKOFF_SECONDS,
)

_LOGGER = logging.getLogger(__name__)


def _extract_d_payloads(value: Any, found: dict[str, str] | None = None) -> dict[str, str]:
    if found is None:
        found = {}
    if isinstance(value, dict):
        item_id = value.get("id")
        item_value = value.get("value")
        if isinstance(item_id, str) and item_id.startswith("D") and "#" in str(item_value):
            found[item_id] = str(item_value)
        for key, item in value.items():
            if isinstance(key, str) and key.startswith("D"):
                raw_val = item.get("value", "") if isinstance(item, dict) else item
                if "#" in str(raw_val):
                    found[key] = str(raw_val)
            _extract_d_payloads(item, found)
    elif isinstance(value, list):
        for item in value:
            _extract_d_payloads(item, found)
    return found


class HomGarApiError(Exception):
    pass


class HomGarApi:
    def __init__(self, email: str, password: str, area_code: str):
        self._email = email
        self._password = password
        self._area_code = area_code
        self._token: str | None = None
        self._iot_credentials: dict | None = None
        self._auth_lock = threading.Lock()
        self._login_backoff_until = 0.0

    def _headers(self) -> dict:
        if not self._token:
            raise HomGarApiError("Not logged in")
        return {"auth": self._token, "lang": "en", "appCode": "1",
                "Content-Type": "application/json"}

    def _get(self, path: str, params: dict | None = None) -> Any:
        url = HOMGAR_BASE_URL + path
        if params:
            url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
        for attempt in range(2):
            previous_token = self._token
            req = urllib.request.Request(url, headers=self._headers())
            try:
                resp = urllib.request.urlopen(req, timeout=15)
                data = json.loads(resp.read().decode())
            except urllib.error.HTTPError as e:
                raise HomGarApiError(f"HTTP {e.code}: {e.read().decode()[:200]}")
            if data.get("code") == 0:
                return data.get("data")
            if data.get("code") == 1004 and attempt == 0:
                _LOGGER.warning("HomGar token expired during GET %s; refreshing login", path)
                self.re_login(previous_token=previous_token)
                continue
            raise HomGarApiError(f"API error: {data}")

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode()
        for attempt in range(2):
            previous_token = self._token
            req = urllib.request.Request(
                HOMGAR_BASE_URL + path,
                data=body,
                headers=self._headers(),
                method="POST",
            )
            try:
                resp = urllib.request.urlopen(req, timeout=15)
                data = json.loads(resp.read().decode())
            except urllib.error.HTTPError as e:
                raise HomGarApiError(f"HTTP {e.code}: {e.read().decode()[:200]}")
            if data.get("code") == 1004 and attempt == 0:
                _LOGGER.warning("HomGar token expired during POST %s; refreshing login", path)
                self.re_login(previous_token=previous_token)
                continue
            return data
        raise HomGarApiError(f"POST failed after retry: {path}")


    def login(self) -> dict:
        """Login and return Alibaba IoT credentials for MQTT."""
        with self._auth_lock:
            return self._login_locked()

    def _login_locked(self) -> dict:
        pwd_md5 = hashlib.md5(self._password.encode("utf-8")).hexdigest()
        device_id = hashlib.md5((self._email + self._area_code).encode("utf-8")).hexdigest()
        payload = json.dumps({
            "areaCode": self._area_code,
            "phoneOrEmail": self._email,
            "password": pwd_md5,
            "deviceId": device_id,
        }).encode()
        req = urllib.request.Request(
            HOMGAR_BASE_URL + HOMGAR_LOGIN_PATH, data=payload,
            headers={"Content-Type": "application/json", "lang": "en", "appCode": "1"},
            method="POST",
        )
        try:
            resp = urllib.request.urlopen(req, timeout=15)
            data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            raise HomGarApiError(f"Login HTTP {e.code}: {e.read().decode()[:200]}")
        if data.get("code") != 0:
            raise HomGarApiError(f"Login failed: {data}")
        d = data["data"]
        self._token = d["token"]
        host_port = d["mqttHostUrl"]
        self._iot_credentials = {
            "iot_id": d["user"]["iotId"],
            "product_key": d["user"]["productKey"],
            "device_name": d["user"]["deviceName"],
            "device_secret": d["user"]["deviceSecret"],
            "mqtt_host": host_port.split(":")[0],
            "mqtt_port": int(host_port.split(":")[1]) if ":" in host_port else 1883,
        }
        self._login_backoff_until = 0.0
        _LOGGER.info("HomGar login OK token=%s...", self._token[:10])
        return self._iot_credentials

    def re_login(self, *, previous_token: str | None = None) -> dict:
        """Refresh the auth token and MQTT credentials."""
        with self._auth_lock:
            if previous_token and self._token and self._token != previous_token and self._iot_credentials:
                return self._iot_credentials

            now = time.monotonic()
            if now < self._login_backoff_until:
                remaining = int(self._login_backoff_until - now)
                raise HomGarApiError(
                    f"Login backoff active for {remaining}s after recent HomGar rate limit"
                )

            try:
                return self._login_locked()
            except HomGarApiError as err:
                if "operate too frequently" in str(err) or "'code': 9993" in str(err):
                    self._login_backoff_until = time.monotonic() + LOGIN_RATE_LIMIT_BACKOFF_SECONDS
                raise

    @property
    def iot_credentials(self) -> dict | None:
        return self._iot_credentials

    def get_timer_devices(self) -> list[dict]:
        """Return list of all HTV0540FRF timer sub-devices across all homes."""
        homes = self._get(HOMGAR_HOMES_PATH) or []
        timers = []
        for home in homes:
            hid = home.get("hid") or home.get("id")
            if not hid:
                continue
            devices = self._get(HOMGAR_DEVICES_PATH, {"hid": hid}) or []
            for hub in devices:
                hub_mid = hub.get("mid")
                hub_name = hub.get("name", "Unknown Hub")
                for sub in hub.get("subDevices", []):
                    if sub.get("model") != TIMER_MODEL:
                        continue
                    port_describe = sub.get("portDescribe", "")
                    zone_names = [z.strip() for z in port_describe.split("|")] if port_describe else []
                    port_count = sub.get("portNumber", 3)
                    zones = []
                    for i in range(1, port_count + 1):
                        name = zone_names[i-1] if i-1 < len(zone_names) and zone_names[i-1] else f"Zone {i}"
                        zones.append({"addr": i, "name": name})
                    timers.append({
                        "sid": sub.get("sid"), "mid": hub_mid, "addr": sub.get("addr"),
                        "name": sub.get("name", "").strip(), "hub_name": hub_name,
                        "hub_product_key": hub.get("productKey", ""),
                        "hub_device_name": hub.get("deviceName", ""),
                        "zones": zones, "hid": hid,
                    })
        _LOGGER.info("HomGar: found %d timers", len(timers))
        return timers

    def get_device_status(self, mid: int) -> Any:
        """Return raw device status for a hub."""
        return self._get(HOMGAR_DEVICE_STATUS_PATH, {"mid": mid}) or {}

    def get_current_payloads(self, mid: int) -> dict[str, str]:
        """Extract current Dxx payloads from the hub status response."""
        return _extract_d_payloads(self.get_device_status(mid))

    def set_sub_device_param(self, sid: int, mid: int, param: str) -> bool:
        """Send valve command via REST. Discovered endpoint: POST /app/device/sub/update.

        Required fields: sid (sub-device ID), mid (hub device ID), param (D01 hex payload).
        Returns True on code=0 SUCCESS.

        Discovery note: MQTT publish to the user-level Alibaba IoT topic does NOT reach
        the physical hub. This REST endpoint is the correct control path — it relays
        commands server-side to the hub's device topic.
        """
        data = self._post("/app/device/sub/update", {"sid": sid, "mid": mid, "param": param})
        if data.get("code") == 0:
            _LOGGER.info("HomGar REST OK sid=%s mid=%s paramVer=%s",
                         sid, mid, data.get("data", {}).get("paramVersion"))
            return True
        _LOGGER.error("HomGar REST failed sid=%s mid=%s: %s", sid, mid, data)
        return False

    def control_work_mode(
        self,
        *,
        mid: int,
        product_key: str,
        device_name: str,
        mode: int,
        addr: int,
        port: int,
        param: str = "",
        duration: int = 0,
    ) -> str | None:
        """Control a gateway sub-device via /app/device/controlWorkMode.

        Live behavior on TAO:
          - Open: mode=1, addr=<timer_addr>, port=<zone_addr>, param=<Dxx payload>, duration=<seconds>
          - Close: mode=0, addr=<timer_addr>, port=<zone_addr>, param="", duration=0
          - Response code 0 = open accepted, code 4 = close accepted
        """
        data = self._post(
            "/app/device/controlWorkMode",
            {
                "mid": mid,
                "productKey": product_key,
                "deviceName": device_name,
                "mode": mode,
                "addr": addr,
                "port": port,
                "param": param,
                "duration": duration,
            },
        )
        code = data.get("code")
        if code in (0, 4):
            state = data.get("data", {}).get("state")
            _LOGGER.info(
                "HomGar controlWorkMode OK mid=%s mode=%s addr=%s port=%s code=%s",
                mid,
                mode,
                addr,
                port,
                code,
            )
            return state if isinstance(state, str) and "#" in state else None
        _LOGGER.error(
            "HomGar controlWorkMode failed mid=%s mode=%s addr=%s port=%s: %s",
            mid,
            mode,
            addr,
            port,
            data,
        )
        return None
