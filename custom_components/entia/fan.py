"""Entia fans."""

from __future__ import annotations

from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import EntiaConfigEntry
from .api import Command, DeviceKind, SwitchState
from .entity import EntiaEntity, declared


async def async_setup_entry(
    hass: HomeAssistant, entry: EntiaConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    hub = entry.runtime_data
    async_add_entities(EntiaFan(hub, device) for device in declared(hub, DeviceKind.FAN))


class EntiaFan(EntiaEntity, FanEntity):
    _attr_supported_features = FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF

    @property
    def is_on(self) -> bool | None:
        state = self.device_state
        return state.on if isinstance(state, SwitchState) else None

    async def async_turn_on(self, percentage: int | None = None, preset_mode: str | None = None, **kwargs: Any) -> None:
        await self._hub.async_send(Command.switch(self._device.id, True))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._hub.async_send(Command.switch(self._device.id, False))
