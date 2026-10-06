"""Base entity for Entia devices."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .api import DeclaredDevice, DeviceKind, DeviceState
from .hub import EntiaHub


class EntiaEntity(Entity):
    """A device declared in the flat template; Entia pushes its changes."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, hub: EntiaHub, device: DeclaredDevice) -> None:
        assert hub.template is not None
        self._hub = hub
        self._device = device
        # unique id, name and device info are unchanged from earlier versions so entity ids and history stay
        self._attr_unique_id = f"{hub.template.flat_id}-{device.id}"
        self._attr_name = device.label
        self._attr_device_info = hub.device_info

    @property
    def available(self) -> bool:
        return self._hub.available

    @property
    def device_state(self) -> DeviceState | None:
        return self._hub.state(self._device.id)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, self._hub.signal, self._handle_update))

    @callback
    def _handle_update(self, device_id: int | None) -> None:
        """Write the state for changes of this device and of the connection (``None``)."""
        if device_id is None or device_id == self._device.id:
            self.async_write_ha_state()


def declared(hub: EntiaHub, kind: DeviceKind) -> list[DeclaredDevice]:
    assert hub.template is not None
    return [d for d in hub.template.devices if d.kind is kind]
