"""HomGar irrigation zone switch entities."""
import logging
import time

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback

from .const import (
    COMMAND_CONFIRMATION_GRACE_SECONDS,
    DEFAULT_DURATION_SECONDS,
    DOMAIN,
    STATE_STALE_SECONDS,
)
from .mqtt import HomGarMQTTClient

_LOGGER = logging.getLogger(__name__)


def setup_platform(hass, config, add_entities, discovery_info=None):
    if DOMAIN not in hass.data:
        return
    data = hass.data[DOMAIN]
    entities = _build_entities(hass, data)
    data["switch_entities"] = entities
    add_entities(entities, True)
    _LOGGER.info("HomGar: added %d zone switches", len(entities))


async def async_setup_entry(hass, entry: ConfigEntry, async_add_entities):
    if DOMAIN not in hass.data:
        return
    data = hass.data[DOMAIN]
    entities = _build_entities(hass, data)
    data["switch_entities"] = entities
    async_add_entities(entities, True)
    _LOGGER.info("HomGar: added %d zone switches", len(entities))


def _build_entities(hass, data):
    entities = []
    for timer in data["timers"]:
        for zone in timer["zones"]:
            entities.append(
                HomGarZoneSwitch(
                    hass,
                    timer["mid"],
                    timer.get("sid") or 0,
                    timer["addr"],
                    zone["addr"],
                    timer["name"],
                    zone["name"],
                    timer.get("hub_product_key", ""),
                    timer.get("hub_device_name", ""),
                    data["mqtt"],
                    data["state_store"],
                )
            )
    return entities


class HomGarZoneSwitch(SwitchEntity):
    def __init__(
        self,
        hass,
        hub_mid,
        sid,
        timer_addr,
        zone_addr,
        timer_name,
        zone_name,
        hub_product_key,
        hub_device_name,
        mqtt_client: HomGarMQTTClient,
        state_store,
    ):
        self._hub_mid = hub_mid
        self._sid = sid
        self._timer_addr = timer_addr
        self._zone_addr = zone_addr
        self._timer_name = timer_name
        self._zone_name = zone_name
        self._hub_product_key = hub_product_key
        self._hub_device_name = hub_device_name
        self._mqtt = mqtt_client
        self._state_store = state_store
        self._is_on = False
        self._assumed_state = True
        self._last_command_at = 0.0
        self._pending_target_is_on: bool | None = None
        self._pending_state_until = 0.0
        self._duration_seconds = DEFAULT_DURATION_SECONDS
        d_key = f"D{str(timer_addr).zfill(2)}"
        self._state_key = f"{hub_mid}_{d_key}"
        self._attr_unique_id = f"homgar_{hub_mid}_{timer_addr}_{zone_addr}"
        self._attr_name = f"Irrigation {timer_name} {zone_name}"
        self._attr_icon = "mdi:sprinkler"
        self._apply_state_record(self._state_store.get(self._state_key, {}))

    @property
    def is_on(self) -> bool:
        if (
            self._assumed_state
            and self._pending_target_is_on is True
            and time.time() > self._pending_state_until
        ):
            self._is_on = False
            self._assumed_state = False
            self._pending_target_is_on = None
        return self._is_on

    @property
    def assumed_state(self) -> bool:
        return self._assumed_state

    @property
    def available(self) -> bool:
        state = self._state_store.get(self._state_key, {})
        if self._assumed_state and (time.time() - self._last_command_at) <= COMMAND_CONFIRMATION_GRACE_SECONDS:
            return True
        if not state or not state.get("_available", False):
            return False
        observed_at = state.get("_observed_at")
        if not observed_at:
            return False
        return (time.time() - observed_at) <= STATE_STALE_SECONDS

    @property
    def extra_state_attributes(self) -> dict:
        state = self._state_store.get(self._state_key, {})
        observed_at = state.get("_observed_at")
        return {"hub_mid": self._hub_mid, "sid": self._sid, "timer_addr": self._timer_addr,
                "zone_addr": self._zone_addr, "timer_name": self._timer_name,
                "zone_name": self._zone_name, "duration_seconds": self._duration_seconds,
                "active_zone": state.get("active_zone"), "stop_timestamp": state.get("stop_timestamp"),
                "state_available": state.get("_available", False),
                "state_source": state.get("_last_source"),
                "state_last_error": state.get("_last_error"),
                "state_last_observed": observed_at,
                "state_age_seconds": int(time.time() - observed_at) if observed_at else None,
                "state_assumed_until": int(self._pending_state_until) if self._assumed_state else None}

    def set_duration(self, seconds: int):
        self._duration_seconds = seconds

    def turn_on(self, **kwargs):
        _LOGGER.warning("HomGar OPEN %s zone %d for %ds", self._timer_name, self._zone_addr, self._duration_seconds)
        if self._mqtt.send_open(
            self._hub_mid,
            self._timer_addr,
            self._zone_addr,
            self._duration_seconds,
            product_key=self._hub_product_key,
            device_name=self._hub_device_name,
            sid=self._sid,
        ):
            self._is_on = True
            self._assumed_state = True
            self._last_command_at = time.time()
            self._pending_target_is_on = True
            self._pending_state_until = self._last_command_at + self._duration_seconds + COMMAND_CONFIRMATION_GRACE_SECONDS
            self.schedule_update_ha_state()

    def turn_off(self, **kwargs):
        _LOGGER.warning("HomGar CLOSE %s zone %d", self._timer_name, self._zone_addr)
        if self._mqtt.send_close(
            self._hub_mid,
            self._timer_addr,
            self._zone_addr,
            product_key=self._hub_product_key,
            device_name=self._hub_device_name,
            sid=self._sid,
        ):
            self._is_on = False
            self._assumed_state = True
            self._last_command_at = time.time()
            self._pending_target_is_on = False
            self._pending_state_until = self._last_command_at + COMMAND_CONFIRMATION_GRACE_SECONDS
            self.schedule_update_ha_state()

    async def async_added_to_hass(self):
        @callback
        def handle_update(event):
            if event.data.get("key") == self._state_key:
                decoded = event.data.get("state", {})
                self._apply_state_record(decoded)
                self.async_write_ha_state()
        self.async_on_remove(self.hass.bus.async_listen(f"{DOMAIN}_state_update", handle_update))

    def _apply_state_record(self, state: dict) -> None:
        if not state:
            return
        active_zone = state.get("active_zone")
        now = time.time()

        if self._assumed_state and self._pending_target_is_on is True and now < self._pending_state_until:
            if active_zone == self._zone_addr:
                self._is_on = True
                self._assumed_state = False
                self._pending_target_is_on = None
            elif active_zone not in (None, self._zone_addr):
                self._is_on = False
                self._assumed_state = False
                self._pending_target_is_on = None
            return

        if self._assumed_state and self._pending_target_is_on is False and now < self._pending_state_until:
            if active_zone in (None,):
                self._is_on = False
                self._assumed_state = False
                self._pending_target_is_on = None
            return

        self._is_on = active_zone == self._zone_addr
        self._assumed_state = False
        self._pending_target_is_on = None
