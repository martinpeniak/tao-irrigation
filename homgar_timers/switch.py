"""HomGar irrigation zone switch entities."""
import logging
from homeassistant.components.switch import SwitchEntity
from homeassistant.core import callback
from .const import DOMAIN, DEFAULT_DURATION_SECONDS
from .mqtt import HomGarMQTTClient

_LOGGER = logging.getLogger(__name__)


def setup_platform(hass, config, add_entities, discovery_info=None):
    if DOMAIN not in hass.data:
        return
    data = hass.data[DOMAIN]
    entities = []
    for timer in data["timers"]:
        for zone in timer["zones"]:
            e = HomGarZoneSwitch(hass, timer["mid"], timer["addr"], zone["addr"],
                                 timer["name"], zone["name"], data["mqtt"], data["state_store"])
            entities.append(e)
    data["switch_entities"] = entities
    add_entities(entities, True)
    _LOGGER.info("HomGar: added %d zone switches", len(entities))


class HomGarZoneSwitch(SwitchEntity):
    def __init__(self, hass, hub_mid, timer_addr, zone_addr,
                 timer_name, zone_name, mqtt_client: HomGarMQTTClient, state_store):
        self._hub_mid = hub_mid
        self._timer_addr = timer_addr
        self._zone_addr = zone_addr
        self._timer_name = timer_name
        self._zone_name = zone_name
        self._mqtt = mqtt_client
        self._state_store = state_store
        self._is_on = False
        self._duration_seconds = DEFAULT_DURATION_SECONDS
        d_key = f"D{str(timer_addr).zfill(2)}"
        self._state_key = f"{hub_mid}_{d_key}"
        self._attr_unique_id = f"homgar_{hub_mid}_{timer_addr}_{zone_addr}"
        self._attr_name = f"Irrigation {timer_name} {zone_name}"
        self._attr_icon = "mdi:sprinkler"

    @property
    def is_on(self) -> bool:
        return self._is_on

    @property
    def extra_state_attributes(self) -> dict:
        state = self._state_store.get(self._state_key, {})
        return {"hub_mid": self._hub_mid, "timer_addr": self._timer_addr,
                "zone_addr": self._zone_addr, "timer_name": self._timer_name,
                "zone_name": self._zone_name, "duration_seconds": self._duration_seconds,
                "active_zone": state.get("active_zone"), "stop_timestamp": state.get("stop_timestamp")}

    def set_duration(self, seconds: int):
        self._duration_seconds = seconds

    def turn_on(self, **kwargs):
        _LOGGER.info("Opening %s zone %d for %ds", self._timer_name, self._zone_addr, self._duration_seconds)
        if self._mqtt.send_open(self._hub_mid, self._timer_addr, self._zone_addr, self._duration_seconds):
            self._is_on = True
            self.schedule_update_ha_state()

    def turn_off(self, **kwargs):
        _LOGGER.info("Closing %s zone %d", self._timer_name, self._zone_addr)
        if self._mqtt.send_close(self._hub_mid, self._timer_addr):
            self._is_on = False
            self.schedule_update_ha_state()

    async def async_added_to_hass(self):
        @callback
        def handle_update(event):
            if event.data.get("key") == self._state_key:
                decoded = event.data.get("state", {})
                self._is_on = decoded.get("active_zone") == self._zone_addr
                self.async_write_ha_state()
        self.async_on_remove(self.hass.bus.async_listen(f"{DOMAIN}_state_update", handle_update))
