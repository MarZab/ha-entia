from __future__ import annotations

from typing import Any
import logging

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverEntityFeature,
    CoverEntity,
    CoverDeviceClass
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import EntiaEntity
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


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
    async_add_entities(EntiaCover(client, x['unique_id']) for x in client.data.values() if x['type'] == "cover")


class EntiaCover(EntiaEntity, CoverEntity):
    _attr_supported_features = (
            CoverEntityFeature.SET_POSITION |
            CoverEntityFeature.STOP |
            CoverEntityFeature.OPEN |
            CoverEntityFeature.CLOSE |
            CoverEntityFeature.OPEN_TILT |
            CoverEntityFeature.CLOSE_TILT
    )

    _attr_device_class = CoverDeviceClass.BLIND

    @property
    def available(self) -> bool:
        return self._client.online

    # details: https://developers.home-assistant.io/docs/core/entity/cover/
    @property
    def current_cover_position(self):
        """Return the current position of the cover."""
        return self._client.data[self._attr_unique_id]['position']

    @property
    def is_closed(self) -> bool:
        return self._client.data[self._attr_unique_id]['position'] == 0

    @property
    def is_closing(self) -> bool:
        return self._client.data[self._attr_unique_id]['state'] == 'closing'

    @property
    def is_opening(self) -> bool:
        return self._client.data[self._attr_unique_id]['state'] == 'opening'

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self._client.request(self._attr_unique_id, {'position': 100})

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self._client.request(self._attr_unique_id, {'position': 0})

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        await self._client.request(self._attr_unique_id, {'position': kwargs[ATTR_POSITION]})

    async def async_stop_cover(self, **kwargs: Any) -> None:
        await self._client.request(self._attr_unique_id, {'state': 'stopped'})

    @property
    def current_cover_tilt_position(self) -> int | None:
        return None

    async def async_open_cover_tilt(self, **kwargs: Any) -> None:
        await self._client.request(self._attr_unique_id, {'position': self.current_cover_position + 1})

    async def async_close_cover_tilt(self, **kwargs: Any) -> None:
        await self._client.request(self._attr_unique_id, {'position': self.current_cover_position - 1})
