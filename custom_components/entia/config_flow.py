"""Config flow for Entia Smart Home integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str
    }
)


class EntiaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Entia Smart Home"""

    VERSION = 1

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=STEP_USER_DATA_SCHEMA
            )

        await self.async_set_unique_id(str.lower(user_input[CONF_USERNAME]))
        self._abort_if_unique_id_configured()

        errors = await self._validate_input(
            user_input[CONF_USERNAME], user_input[CONF_PASSWORD]
        )

        if errors != {}:
            return self.async_show_form(
                step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
            )

        return self.async_create_entry(title=user_input[CONF_USERNAME], data=user_input)

    async def _validate_input(self, username, password) -> dict[str, str]:
        """Validate the user input allows us to connect."""
        errors = {}

        # todo: validate the username and password

        return errors