"""HomGar Irrigation Timers — Home Assistant custom component.

configuration.yaml:
    homgar_timers:
      email: your@email.com
      password: "yourpassword"   # quotes needed if password contains $
      area_code: 34              # Spain=34, UK=44, US=1
"""
import logging
from homeassistant.core import HomeAssistant
from homeassistant.helpers import discovery
from .const import DOMAIN, CONF_EMAIL, CONF_PASSWORD, CONF_AREA_CODE
from .api import HomGarApi, HomGarApiError
from .mqtt import HomGarMQTTClient

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    if DOMAIN not in config:
        return True
    conf = config[DOMAIN]
    return await _setup(hass, conf[CONF_EMAIL], conf[CONF_PASSWORD],
                        str(conf.get(CONF_AREA_CODE, "34")), config)


async def _setup(hass, email, password, area_code, config) -> bool:
    hass.data.setdefault(DOMAIN, {})
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

    mqtt_client = HomGarMQTTClient(iot_creds, on_state_update)
    connected = await hass.async_add_executor_job(mqtt_client.connect)
    if not connected:
        _LOGGER.warning("HomGar MQTT connection failed — controls will optimistically update")
    hass.data[DOMAIN] = {"timers": timers, "mqtt": mqtt_client,
                         "state_store": state_store, "switch_entities": []}
    await discovery.async_load_platform(hass, "switch", DOMAIN, {}, config)
    await discovery.async_load_platform(hass, "number", DOMAIN, {}, config)
    return True
