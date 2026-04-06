"""HomGar Irrigation Timers — Home Assistant custom component.

configuration.yaml:
    homgar_timers:
      email: your@email.com
      password: "yourpassword"   # quotes needed if password contains $
      area_code: 34              # Spain=34, UK=44, US=1
"""
from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any

import voluptuous as vol
import homeassistant.helpers.config_validation as cv
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import discovery
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    DOMAIN,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_AREA_CODE,
    STATE_POLL_INTERVAL_SECONDS,
)
from .api import HomGarApi, HomGarApiError
from .mqtt import HomGarMQTTClient, decode_d01

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SWITCH, Platform.NUMBER]

CONFIG_SCHEMA = vol.Schema({
    DOMAIN: vol.Schema({
        vol.Required(CONF_EMAIL): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Optional(CONF_AREA_CODE, default="34"): cv.string,
    })
}, extra=vol.ALLOW_EXTRA)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    if DOMAIN not in config:
        return True
    conf = config[DOMAIN]
    return await _setup_runtime(
        hass,
        conf[CONF_EMAIL],
        conf[CONF_PASSWORD],
        str(conf.get(CONF_AREA_CODE, "34")),
        source="yaml",
        discovery_config=config,
    )

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await _setup_runtime(
        hass,
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        str(entry.data.get(CONF_AREA_CODE, "34")),
        source="config_entry",
        entry=entry,
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok and DOMAIN in hass.data:
        poll_unsub = hass.data[DOMAIN].get("poll_unsub")
        if poll_unsub:
            poll_unsub()
        mqtt_client = hass.data[DOMAIN].get("mqtt")
        if mqtt_client:
            await hass.async_add_executor_job(mqtt_client.disconnect)
        hass.data.pop(DOMAIN, None)
    return unload_ok


async def _setup_runtime(
    hass: HomeAssistant,
    email: str,
    password: str,
    area_code: str,
    *,
    source: str,
    discovery_config: dict[str, Any] | None = None,
    entry: ConfigEntry | None = None,
) -> bool:
    existing = hass.data.get(DOMAIN)
    if existing and existing.get("mqtt"):
        _LOGGER.error(
            "HomGar already configured via %s; remove duplicate %s setup",
            existing.get("setup_source", "unknown"),
            source,
        )
        return False

    api = HomGarApi(email, password, area_code)
    try:
        iot_creds = await hass.async_add_executor_job(api.login)
    except HomGarApiError as e:
        _LOGGER.error("HomGar login failed: %s", e)
        return False
    try:
        timers = await hass.async_add_executor_job(api.get_timer_devices)
    except HomGarApiError as e:
        _LOGGER.error("HomGar device discovery failed: %s", e)
        return False
    if not timers:
        _LOGGER.warning("HomGar: no HTV0540FRF timers found")
        return True
    for t in timers:
        _LOGGER.info("HomGar timer: %s (mid=%s) zones=%s",
                     t["name"], t["mid"], [z["name"] for z in t["zones"]])
    state_store: dict[str, dict] = {}

    def publish_state(
        store_key: str,
        decoded: dict | None = None,
        *,
        available: bool,
        source: str,
        error: str | None = None,
    ) -> None:
        state = dict(state_store.get(store_key, {}))
        if decoded:
            state.update(decoded)
        state["_available"] = available
        state["_last_source"] = source
        state["_last_error"] = error
        state["_observed_at"] = time.time()
        state_store[store_key] = state
        hass.bus.fire(f"{DOMAIN}_state_update", {"key": store_key, "state": state})

    def on_state_update(store_key: str, decoded: dict) -> None:
        publish_state(store_key, decoded, available=True, source="cloud")

    mqtt_client = HomGarMQTTClient(api, iot_creds, on_state_update)
    mqtt_client.set_rest_client(api)  # REST client for valve commands via controlWorkMode
    await hass.async_add_executor_job(_seed_payloads, api, mqtt_client, timers)
    await hass.async_add_executor_job(_sync_payload_states, api, mqtt_client, timers, publish_state)
    connected = await hass.async_add_executor_job(mqtt_client.connect)
    if not connected:
        _LOGGER.warning("HomGar MQTT connection failed — controls will optimistically update")

    async def poll_states(_now) -> None:
        await hass.async_add_executor_job(_sync_payload_states, api, mqtt_client, timers, publish_state)

    poll_unsub = async_track_time_interval(
        hass,
        poll_states,
        timedelta(seconds=STATE_POLL_INTERVAL_SECONDS),
    )
    hass.data[DOMAIN] = {
        "timers": timers,
        "mqtt": mqtt_client,
        "state_store": state_store,
        "switch_entities": [],
        "poll_unsub": poll_unsub,
        "setup_source": source,
    }
    if entry:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    else:
        await discovery.async_load_platform(hass, "switch", DOMAIN, {}, discovery_config or {})
        await discovery.async_load_platform(hass, "number", DOMAIN, {}, discovery_config or {})
    return True


def _seed_payloads(api: HomGarApi, mqtt_client: HomGarMQTTClient, timers: list[dict]) -> None:
    """Preload current Dxx payloads so command building starts from real hub state."""
    d_keys_by_mid: dict[int, set[str]] = {}
    for timer in timers:
        d_keys_by_mid.setdefault(int(timer["mid"]), set()).add(f"D{int(timer['addr']):02d}")

    for mid, expected_d_keys in d_keys_by_mid.items():
        try:
            payloads = api.get_current_payloads(mid)
        except HomGarApiError as err:
            _LOGGER.warning("HomGar payload seed failed for hub %s: %s", mid, err)
            continue
        except Exception as err:  # pragma: no cover - defensive network guard
            _LOGGER.warning("HomGar payload seed crashed for hub %s: %s", mid, err)
            continue

        seeded = 0
        for d_key in expected_d_keys:
            payload = payloads.get(d_key)
            if payload:
                mqtt_client.set_current_payload(mid, d_key, payload)
                seeded += 1
        _LOGGER.info("HomGar seeded %d/%d payloads for hub %s", seeded, len(expected_d_keys), mid)


def _sync_payload_states(
    api: HomGarApi,
    mqtt_client: HomGarMQTTClient,
    timers: list[dict],
    publish_state,
) -> None:
    """Poll current hub payloads and feed them into the shared state-update path."""
    d_keys_by_mid: dict[int, set[str]] = {}
    for timer in timers:
        d_keys_by_mid.setdefault(int(timer["mid"]), set()).add(f"D{int(timer['addr']):02d}")

    for mid, expected_d_keys in d_keys_by_mid.items():
        try:
            payloads = api.get_current_payloads(mid)
        except HomGarApiError as err:
            _LOGGER.warning("HomGar state poll failed for hub %s: %s", mid, err)
            for d_key in expected_d_keys:
                publish_state(
                    f"{mid}_{d_key}",
                    available=False,
                    source="poll_error",
                    error=str(err),
                )
            continue
        except Exception as err:  # pragma: no cover - defensive network guard
            _LOGGER.warning("HomGar state poll crashed for hub %s: %s", mid, err)
            for d_key in expected_d_keys:
                publish_state(
                    f"{mid}_{d_key}",
                    available=False,
                    source="poll_error",
                    error=str(err),
                )
            continue

        for d_key in expected_d_keys:
            payload = payloads.get(d_key)
            if not payload:
                publish_state(
                    f"{mid}_{d_key}",
                    available=False,
                    source="poll_missing",
                    error="No payload returned for timer state",
                )
                continue
            mqtt_client.set_current_payload(mid, d_key, payload)
            decoded = decode_d01(payload)
            publish_state(
                f"{mid}_{d_key}",
                decoded,
                available=bool(decoded),
                source="poll",
                error=None if decoded else "Could not decode payload",
            )
