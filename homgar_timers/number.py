"""HomGar irrigation zone duration number entities."""
import logging
from homeassistant.components.number import NumberEntity, NumberMode
from .const import DOMAIN, DEFAULT_DURATION_SECONDS

_LOGGER = logging.getLogger(__name__)


def setup_platform(hass, config, add_entities, discovery_info=None):
    if DOMAIN not in hass.data:
        return
    data = hass.data[DOMAIN]
    entities = []
    for timer in data["timers"]:
        for zone in timer["zones"]:
            entities.append(HomGarZoneDuration(hass, timer["mid"], timer["addr"],
                zone["addr"], timer["name"], zone["name"]))
    add_entities(entities, True)
    _LOGGER.info("HomGar: added %d zone duration numbers", len(entities))


class HomGarZoneDuration(NumberEntity):
    def __init__(self, hass, hub_mid, timer_addr, zone_addr, timer_name, zone_name):
        self._hub_mid = hub_mid
        self._timer_addr = timer_addr
        self._zone_addr = zone_addr
        self._timer_name = timer_name
        self._zone_name = zone_name
        self._duration_minutes = DEFAULT_DURATION_SECONDS // 60
        self._attr_unique_id = f"homgar_dur_{hub_mid}_{timer_addr}_{zone_addr}"
        self._attr_name = f"Irrigation {timer_name} {zone_name} Duration"
        self._attr_icon = "mdi:timer"
        self._attr_native_min_value = 1
        self._attr_native_max_value = 120
        self._attr_native_step = 1
        self._attr_mode = NumberMode.BOX
        self._attr_native_unit_of_measurement = "min"

    @property
    def native_value(self) -> float:
        return self._duration_minutes

    def set_native_value(self, value: float):
        self._duration_minutes = int(value)
        switch_uid = f"homgar_{self._hub_mid}_{self._timer_addr}_{self._zone_addr}"
        for e in self.hass.data.get(DOMAIN, {}).get("switch_entities", []):
            if getattr(e, "_attr_unique_id", None) == switch_uid:
                e.set_duration(self._duration_minutes * 60)
                break
        self.schedule_update_ha_state()

    @property
    def extra_state_attributes(self) -> dict:
        return {"duration_seconds": self._duration_minutes * 60,
                "zone_addr": self._zone_addr, "timer_name": self._timer_name,
                "zone_name": self._zone_name}
