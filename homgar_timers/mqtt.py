"""Alibaba IoT MQTT client for HomGar hub control.

MQTT Protocol (reverse-engineered from live traffic capture):
  Broker:   {productKey}.iot-as-mqtt.us-west-1.aliyuncs.com:1883
  client_id = "{deviceName}|securemode=3,signmethod=hmacsha1|"
  content   = "clientId{dn}deviceName{dn}productKey{pk}"
  sign      = HMAC-SHA1(deviceSecret, content).hexdigest()
  username  = "{deviceName}&{productKey}"
  password  = sign

State topic: /sys/{productKey}/{deviceName}/thing/service/property/set
Payload param format: "#P{ts}{hub_mid}|{D_updates_json}|{ts}|{propver}#"

D01 hex payload (after "11#" prefix):
  byte[6]:    0x20|zone_addr if running, 0x00 if off
  byte[24:28]: LE uint32 stop timestamp
  byte[42:44]: LE uint16 duration seconds
"""
import hashlib, hmac, json, logging, struct, threading, time
from typing import Callable

try:
    import paho.mqtt.client as mqtt
    PAHO_AVAILABLE = True
except ImportError:
    PAHO_AVAILABLE = False

from .const import (
    DURATION_BYTE_OFFSET,
    PAYLOAD_SEQUENCE_BYTE_OFFSET,
    STOP_TS_BYTE_OFFSET,
    ZONE_FLAG_BYTE_OFFSET,
    ZONE_RUNNING_FLAG,
)

_LOGGER = logging.getLogger(__name__)
PROP_SET_TOPIC = "/sys/{product_key}/{device_name}/thing/service/property/set"


def _build_aliyun_auth(product_key, device_name, device_secret):
    client_id = f"{device_name}|securemode=3,signmethod=hmacsha1|"
    content = f"clientId{device_name}deviceName{device_name}productKey{product_key}"
    sign = hmac.new(device_secret.encode(), content.encode(), hashlib.sha1).hexdigest()
    return client_id, f"{device_name}&{product_key}", sign


def decode_d01(hex_payload: str) -> dict:
    if "#" not in hex_payload:
        return {}
    try:
        b = bytes.fromhex(hex_payload.split("#", 1)[1])
    except ValueError:
        return {}
    result = {"raw": hex_payload}
    if len(b) > ZONE_FLAG_BYTE_OFFSET:
        flag = b[ZONE_FLAG_BYTE_OFFSET]
        result["active_zone"] = (flag & 0x0F) if (flag & ZONE_RUNNING_FLAG) else None
    if len(b) >= DURATION_BYTE_OFFSET + 2:
        result["duration_seconds"] = struct.unpack_from("<H", b, DURATION_BYTE_OFFSET)[0]
    if len(b) >= STOP_TS_BYTE_OFFSET + 4:
        result["stop_timestamp"] = struct.unpack_from("<I", b, STOP_TS_BYTE_OFFSET)[0]
    return result


def build_open_command(current_payload: str, zone_addr: int, duration_seconds: int) -> str:
    if "#" not in current_payload:
        return current_payload
    prefix, hex_str = current_payload.split("#", 1)
    try:
        b = bytearray(bytes.fromhex(hex_str))
    except ValueError:
        return current_payload
    while len(b) <= DURATION_BYTE_OFFSET + 1:
        b.append(0x00)
    if len(b) > PAYLOAD_SEQUENCE_BYTE_OFFSET:
        b[PAYLOAD_SEQUENCE_BYTE_OFFSET] = (b[PAYLOAD_SEQUENCE_BYTE_OFFSET] + 1) & 0xFF
    b[ZONE_FLAG_BYTE_OFFSET] = ZONE_RUNNING_FLAG | (zone_addr & 0x0F)
    struct.pack_into("<H", b, DURATION_BYTE_OFFSET, duration_seconds)
    if len(b) >= STOP_TS_BYTE_OFFSET + 4:
        struct.pack_into("<I", b, STOP_TS_BYTE_OFFSET, int(time.time()) + duration_seconds)
    return f"{prefix}#{b.hex().upper()}"


def build_close_command(current_payload: str) -> str:
    if "#" not in current_payload:
        return current_payload
    prefix, hex_str = current_payload.split("#", 1)
    try:
        b = bytearray(bytes.fromhex(hex_str))
    except ValueError:
        return current_payload
    if len(b) > PAYLOAD_SEQUENCE_BYTE_OFFSET and len(b) > ZONE_FLAG_BYTE_OFFSET:
        if b[ZONE_FLAG_BYTE_OFFSET] & ZONE_RUNNING_FLAG:
            b[PAYLOAD_SEQUENCE_BYTE_OFFSET] = (b[PAYLOAD_SEQUENCE_BYTE_OFFSET] - 1) & 0xFF
    if len(b) > ZONE_FLAG_BYTE_OFFSET:
        b[ZONE_FLAG_BYTE_OFFSET] = 0x00
    if len(b) >= STOP_TS_BYTE_OFFSET + 4:
        struct.pack_into("<I", b, STOP_TS_BYTE_OFFSET, 0)
    if len(b) >= DURATION_BYTE_OFFSET + 2:
        struct.pack_into("<H", b, DURATION_BYTE_OFFSET, 0)
    return f"{prefix}#{b.hex().upper()}"


class HomGarMQTTClient:
    def __init__(self, api, iot_credentials: dict, on_state_update: Callable[[str, dict], None]):
        if not PAHO_AVAILABLE:
            raise RuntimeError("paho-mqtt required: pip install paho-mqtt>=1.6.0")
        self._api = api
        self._on_state_update = on_state_update
        self._client = None
        self._connected = False
        self._lock = threading.Lock()
        self._client_lock = threading.Lock()
        self._current_payloads: dict[str, str] = {}
        self._reconnect_thread = None
        self._shutdown_requested = False
        self._apply_credentials(iot_credentials)

    def connect(self) -> bool:
        try:
            self._shutdown_requested = False
            self._connect_client()
            return self._wait_for_connection()
        except Exception as e:
            _LOGGER.error("HomGar MQTT connect error: %s", e)
            return False

    def disconnect(self):
        self._shutdown_requested = True
        with self._client_lock:
            if self._client:
                try:
                    self._client.loop_stop()
                except Exception:
                    _LOGGER.debug("HomGar MQTT loop_stop failed during disconnect", exc_info=True)
                try:
                    self._client.disconnect()
                except Exception:
                    _LOGGER.debug("HomGar MQTT disconnect failed during shutdown", exc_info=True)

    def _apply_credentials(self, iot_credentials: dict) -> None:
        self._creds = iot_credentials
        pk = iot_credentials["product_key"]
        dn = iot_credentials["device_name"]
        ds = iot_credentials["device_secret"]
        self._client_id, self._username, self._password = _build_aliyun_auth(pk, dn, ds)
        self._mqtt_host = iot_credentials["mqtt_host"]
        self._mqtt_port = iot_credentials.get("mqtt_port", 1883)
        self._topic = PROP_SET_TOPIC.format(product_key=pk, device_name=dn)

    def _build_client(self):
        client = mqtt.Client(client_id=self._client_id, protocol=mqtt.MQTTv311)
        client.username_pw_set(self._username, self._password)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.on_disconnect = self._on_disconnect
        return client

    def _connect_client(self) -> None:
        self._connected = False
        with self._client_lock:
            if self._client:
                try:
                    self._client.loop_stop()
                except Exception:
                    _LOGGER.debug("HomGar MQTT loop_stop failed before reconnect", exc_info=True)
                try:
                    self._client.disconnect()
                except Exception:
                    _LOGGER.debug("HomGar MQTT disconnect failed before reconnect", exc_info=True)
            self._client = self._build_client()
            self._client.connect(self._mqtt_host, self._mqtt_port, 60)
            self._client.loop_start()

    def _wait_for_connection(self) -> bool:
        for _ in range(20):
            if self._connected:
                return True
            time.sleep(0.5)
        return False

    def _start_reconnect_thread(self) -> None:
        if self._shutdown_requested:
            return
        if self._reconnect_thread and self._reconnect_thread.is_alive():
            return
        self._reconnect_thread = threading.Thread(target=self._reconnect_loop, daemon=True)
        self._reconnect_thread.start()

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self._connected = True
            client.subscribe(self._topic, qos=0)
            _LOGGER.info("HomGar MQTT connected, sub: %s", self._topic)
        else:
            _LOGGER.error("HomGar MQTT connect failed rc=%s", rc)

    def _on_disconnect(self, client, userdata, rc):
        self._connected = False
        if self._shutdown_requested:
            _LOGGER.info("HomGar MQTT disconnected cleanly")
            return
        _LOGGER.warning("HomGar MQTT disconnected rc=%s — will reconnect", rc)
        if rc != 0:
            self._start_reconnect_thread()

    def _reconnect_loop(self):
        """Re-login and reconnect after unexpected disconnect."""
        for attempt in range(1, 6):
            if self._shutdown_requested:
                return
            time.sleep(min(30 * attempt, 300))
            try:
                fresh_credentials = self._api.re_login()
                self._apply_credentials(fresh_credentials)
                self._connect_client()
                if self._wait_for_connection():
                    _LOGGER.info("HomGar MQTT reconnected on attempt %d", attempt)
                    return
                raise RuntimeError("MQTT reconnect timed out")
            except Exception as e:
                _LOGGER.error("Reconnect attempt %d failed: %s", attempt, e)
        _LOGGER.error("HomGar MQTT reconnect exhausted after 5 attempts")

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8", errors="replace"))
            param_str = payload.get("params", {}).get("param", "")
            if not param_str or not param_str.startswith("#P"):
                return
            inner = param_str.strip("#")
            parts = inner.split("|", 1)
            if len(parts) < 2:
                return
            hub_mid = parts[0][-5:].lstrip("0") or parts[0][-5:]
            rest = parts[1]
            d_updates_raw = rest.rsplit("|", 2)[0] if rest.count("|") >= 2 else rest
            try:
                d_updates = json.loads(d_updates_raw)
            except json.JSONDecodeError:
                return
            for key, val in d_updates.items():
                if not key.startswith("D"):
                    continue
                raw_val = val.get("value", "") if isinstance(val, dict) else val
                if not raw_val or "#" not in str(raw_val):
                    continue
                store_key = f"{hub_mid}_{key}"
                with self._lock:
                    self._current_payloads[store_key] = str(raw_val)
                decoded = decode_d01(str(raw_val))
                if decoded:
                    self._on_state_update(store_key, decoded)
        except Exception as e:
            _LOGGER.error("HomGar MQTT message error: %s", e)

    def get_current_payload(self, hub_mid, d_key="D01") -> str:
        with self._lock:
            return self._current_payloads.get(f"{hub_mid}_{d_key}", f"11#{'00' * 52}")

    def send_open(self, hub_mid, timer_addr: int, zone_addr: int, duration_seconds: int, sid: int = 0) -> bool:
        d_key = f"D{str(timer_addr).zfill(2)}"
        new_payload = build_open_command(self.get_current_payload(hub_mid, d_key), zone_addr, duration_seconds)
        return self._publish(hub_mid, d_key, new_payload, sid=sid)

    def send_close(self, hub_mid, timer_addr: int, sid: int = 0) -> bool:
        d_key = f"D{str(timer_addr).zfill(2)}"
        new_payload = build_close_command(self.get_current_payload(hub_mid, d_key))
        return self._publish(hub_mid, d_key, new_payload, sid=sid)

    def set_rest_client(self, rest_client) -> None:
        """Set the REST client for sending commands via /app/device/sub/update."""
        self._rest_client = rest_client

    def _publish(self, hub_mid, d_key: str, payload_hex: str, sid: int = 0) -> bool:
        """Send command via REST API (/app/device/sub/update) — the correct control path.

        IMPORTANT: Publishing to the user-level Alibaba IoT MQTT topic does NOT reach
        the physical hub. The hub listens on its own device topic which requires the
        hub's device secret (unavailable via API). The HomGar REST endpoint
        /app/device/sub/update relays commands server-side to the hub.
        """
        if not hasattr(self, '_rest_client') or not self._rest_client:
            _LOGGER.error("HomGar: REST client not set, cannot send command")
            return False
        try:
            result = self._rest_client.set_sub_device_param(sid, hub_mid, payload_hex)
            _LOGGER.info("HomGar REST command hub=%s sid=%s d_key=%s result=%s",
                         hub_mid, sid, d_key, result)
            return result
        except Exception as e:
            _LOGGER.error("HomGar REST command failed: %s", e)
            return False
