from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN


class EntiaEntity(Entity):
    # Do not pool since the device will push updates to HA

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, client, unique_id) -> None:
        # Usual setup is done here. Callbacks are added in async_added_to_hass.
        self._client = client

        # https://developers.home-assistant.io/docs/entity_registry_index/#unique-id-requirements
        # Note: This is NOT used to generate the user visible Entity ID used in automations.
        self._attr_unique_id = unique_id

        # This is the name for this *entity*, the "name" attribute from "device_info"
        # is used as the device name for device screens in the UI. This name is used on
        # entity screens, and used to build the Entity ID that's used is automations etc.
        self._attr_name = self._client.data[unique_id]['name']
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, client.serial)},
            name=client.name,
            model=client.model,
            manufacturer=client.manufacturer
        )

    async def async_added_to_hass(self) -> None:
        """Run when this Entity has been added to HA."""
        self._client.on(self._attr_unique_id, self.async_write_ha_state)

    async def async_will_remove_from_hass(self) -> None:
        """Entity being removed from hass."""
        # The opposite of async_added_to_hass. Remove any registered call backs here.
        self._client.off(self._attr_unique_id)

