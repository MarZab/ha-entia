"""Entia connection status."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import EntiaConfigEntry
from .entity import EntiaConnectionEntity
from .hub import ConnectionStatus, EntiaHub


async def async_setup_entry(
    hass: HomeAssistant, entry: EntiaConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([EntiaStatusSensor(entry.runtime_data)])


class EntiaStatusSensor(EntiaConnectionEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_options = [status.value for status in ConnectionStatus]  # noqa: RUF012 - HA entity attribute

    def __init__(self, hub: EntiaHub) -> None:
        super().__init__(hub, "status")

    @property
    def native_value(self) -> str:
        return self._hub.status.value
