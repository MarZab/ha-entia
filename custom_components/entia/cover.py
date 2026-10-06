"""Entia covers (blinds)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.cover import ATTR_POSITION, CoverDeviceClass, CoverEntity, CoverEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import EntiaConfigEntry
from .api import Command, CoverDirection, CoverState, DeviceKind
from .entity import EntiaEntity, declared


async def async_setup_entry(
    hass: HomeAssistant, entry: EntiaConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    hub = entry.runtime_data
    async_add_entities(EntiaCover(hub, device) for device in declared(hub, DeviceKind.COVER))


class EntiaCover(EntiaEntity, CoverEntity):
    _attr_device_class = CoverDeviceClass.BLIND
    _attr_supported_features = (
        CoverEntityFeature.SET_POSITION
        | CoverEntityFeature.STOP
        | CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.OPEN_TILT
        | CoverEntityFeature.CLOSE_TILT
    )
    _attr_current_cover_tilt_position = None

    @property
    def _cover(self) -> CoverState | None:
        state = self.device_state
        return state if isinstance(state, CoverState) else None

    @property
    def current_cover_position(self) -> int | None:
        return self._cover.position if self._cover else None

    @property
    def is_closed(self) -> bool | None:
        return self._cover.position == 0 if self._cover else None

    @property
    def is_opening(self) -> bool:
        return self._cover is not None and self._cover.direction is CoverDirection.OPENING

    @property
    def is_closing(self) -> bool:
        return self._cover is not None and self._cover.direction is CoverDirection.CLOSING

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self._hub.async_send(Command.cover_position(self._device.id, 100))

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self._hub.async_send(Command.cover_position(self._device.id, 0))

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        await self._hub.async_send(Command.cover_position(self._device.id, kwargs[ATTR_POSITION]))

    async def async_stop_cover(self, **kwargs: Any) -> None:
        await self._hub.async_send(Command.cover_stop(self._device.id))

    # Entia covers have no tilt; the tilt buttons nudge the position by 1 %, as in earlier versions

    async def async_open_cover_tilt(self, **kwargs: Any) -> None:
        await self._nudge(+1)

    async def async_close_cover_tilt(self, **kwargs: Any) -> None:
        await self._nudge(-1)

    async def _nudge(self, step: int) -> None:
        if self._cover is not None:
            await self._hub.async_send(Command.cover_position(self._device.id, self._cover.position + step))
