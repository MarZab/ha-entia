"""Entia connection switch."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import EntiaConfigEntry
from .entity import EntiaConnectionEntity
from .hub import EntiaHub


async def async_setup_entry(
    hass: HomeAssistant, entry: EntiaConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([EntiaConnectionSwitch(entry.runtime_data)])


class EntiaConnectionSwitch(EntiaConnectionEntity, SwitchEntity):
    """Off = never connect to Entia; devices are unavailable and refuse commands."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, hub: EntiaHub) -> None:
        super().__init__(hub, "connection")

    @property
    def is_on(self) -> bool:
        return self._hub.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._hub.async_set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._hub.async_set_enabled(False)
