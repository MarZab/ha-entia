from __future__ import annotations

from typing import Any, Optional

from homeassistant.components.fan import FanEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.components.fan import FanEntityFeature
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import EntiaEntity
from .const import DOMAIN


# This function is called as part of the __init__.async_setup_entry (via the
# hass.config_entries.async_forward_entry_setup call)
async def async_setup_entry(
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        async_add_entities: AddEntitiesCallback,
) -> None:
    """Add cover for passed config_entry in HA."""
    # The hub is loaded from the associated hass.data entry that was created in the
    # __init__.async_setup_entry function
    client = hass.data[DOMAIN][config_entry.entry_id]

    # Add all Covers to HA
    async_add_entities(EntiaFan(client, x['unique_id']) for x in client.data.values() if x['type'] == "fan")


class EntiaFan(EntiaEntity, FanEntity):
    # todo 2024.8
    _attr_supported_features = FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF

    @property
    def is_on(self) -> bool | None:
        return self._client.data[self._attr_unique_id]['state'] == 'on'

    async def async_turn_off(self, **kwargs) -> None:
        await self._client.request(self._attr_unique_id, {'state': 'off'})

    async def async_turn_on(self, percentage: int | None = None, preset_mode: str | None = None, **kwargs: Any) -> None:
        await self._client.request(self._attr_unique_id, {'state': 'on'})
