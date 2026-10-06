"""Config flow for Entia."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.selector import NumberSelector, NumberSelectorConfig, NumberSelectorMode

from .api import AuthError, EntiaError
from .const import CONF_TIMEOUT, DEFAULT_TIMEOUT, DOMAIN, MAX_TIMEOUT, READY_TIMEOUT
from .hub import create_session

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema({vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str})
REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): str})


class EntiaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add an Entia flat with its Entia ID and PIN."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> EntiaOptionsFlow:
        return EntiaOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_USERNAME].lower())
            self._abort_if_unique_id_configured()
            errors = await self._async_validate(user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
            if not errors:
                return self.async_create_entry(title=user_input[CONF_USERNAME], data=user_input)
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await self._async_validate(entry.data[CONF_USERNAME], user_input[CONF_PASSWORD])
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            description_placeholders={"username": entry.data[CONF_USERNAME]},
            errors=errors,
        )

    async def _async_validate(self, username: str, password: str) -> dict[str, str]:
        """Log in once; errors keyed for the form."""
        session = create_session(self.hass, username, password)
        session.start()
        try:
            await session.wait_ready(READY_TIMEOUT, fail_fast=True)
        except AuthError:
            return {"base": "invalid_auth"}
        except (TimeoutError, EntiaError):
            return {"base": "cannot_connect"}
        except Exception:
            _LOGGER.exception("Unexpected error validating the Entia login")
            return {"base": "unknown"}
        finally:
            await session.close()
        return {}


class EntiaOptionsFlow(OptionsFlow):
    """Inactivity timeout."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data={CONF_TIMEOUT: int(user_input[CONF_TIMEOUT])})
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_TIMEOUT, default=self.config_entry.options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=MAX_TIMEOUT, step=1, unit_of_measurement="min", mode=NumberSelectorMode.BOX
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
