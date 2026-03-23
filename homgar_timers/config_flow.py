"""Config flow for the HomGar irrigation timer integration."""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant import config_entries

from .api import HomGarApi, HomGarApiError
from .const import CONF_AREA_CODE, CONF_EMAIL, CONF_PASSWORD, DOMAIN

_LOGGER = logging.getLogger(__name__)


class HomGarConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for HomGar timers."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}

        if user_input is not None:
            unique_id = f"{user_input[CONF_EMAIL].strip().lower()}::{user_input[CONF_AREA_CODE].strip()}"
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()

            api = HomGarApi(
                user_input[CONF_EMAIL].strip(),
                user_input[CONF_PASSWORD],
                user_input[CONF_AREA_CODE].strip(),
            )
            try:
                await self.hass.async_add_executor_job(api.login)
                timers = await self.hass.async_add_executor_job(api.get_timer_devices)
            except HomGarApiError as err:
                _LOGGER.error("HomGar config flow validation failed: %s", err)
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected HomGar config flow error")
                errors["base"] = "unknown"
            else:
                if not timers:
                    errors["base"] = "no_timers_found"
                else:
                    return self.async_create_entry(
                        title=user_input[CONF_EMAIL].strip(),
                        data={
                            CONF_EMAIL: user_input[CONF_EMAIL].strip(),
                            CONF_PASSWORD: user_input[CONF_PASSWORD],
                            CONF_AREA_CODE: user_input[CONF_AREA_CODE].strip(),
                        },
                    )

        schema = vol.Schema(
            {
                vol.Required(CONF_EMAIL): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(CONF_AREA_CODE, default="34"): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
