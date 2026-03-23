"""HomGar Irrigation Timers — Home Assistant custom component.

configuration.yaml:
    homgar_timers:
      email: your@email.com
      password: "yourpassword"   # quotes needed if password contains $
      area_code: 34              # Spain=34, UK=44, US=1
"""
import logging
from typing import Any

import voluptuous as vol
import homeassistant.helpers.config_validation as cv
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import discovery

from .const import DOMAIN, CONF_EMAIL, CONF_PASSWORD, CONF_AREA_CODE
from .api import HomGarApi, HomGarApiError
from .mqtt import HomGarMQTTClient

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

    def on_state_update(store_key: str, decoded: dict) -> None:
        state_store[store_key] = decoded
        hass.bus.fire(f"{DOMAIN}_state_update", {"key": store_key, "state": decoded})

    mqtt_client = HomGarMQTTClient(api, iot_creds, on_state_update)
    mqtt_client.set_rest_client(api)  # REST client for valve commands via /app/device/sub/update
    connected = await hass.async_add_executor_job(mqtt_client.connect)
    if not connected:
        _LOGGER.warning("HomGar MQTT connection failed — controls will optimistically update")
    hass.data[DOMAIN] = {
        "timers": timers,
        "mqtt": mqtt_client,
        "state_store": state_store,
        "switch_entities": [],
        "setup_source": source,
    }
    if entry:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    else:
        await discovery.async_load_platform(hass, "switch", DOMAIN, {}, discovery_config or {})
        await discovery.async_load_platform(hass, "number", DOMAIN, {}, discovery_config or {})
    return True
