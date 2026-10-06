"""Entia lights."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import EntiaConfigEntry
from .api import Command, DeviceKind, SwitchState
from .entity import EntiaEntity, declared


async def async_setup_entry(
    hass: HomeAssistant, entry: EntiaConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    hub = entry.runtime_data
    async_add_entities(EntiaLight(hub, device) for device in declared(hub, DeviceKind.LIGHT))


class EntiaLight(EntiaEntity, LightEntity):
    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = {ColorMode.ONOFF}  # noqa: RUF012 - HA entity attribute

    @property
    def is_on(self) -> bool | None:
        state = self.device_state
        return state.on if isinstance(state, SwitchState) else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._hub.async_send(Command.switch(self._device.id, True))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._hub.async_send(Command.switch(self._device.id, False))
