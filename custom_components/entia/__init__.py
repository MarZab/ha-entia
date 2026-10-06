from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import Platform
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

from .const import DOMAIN
from .client import EntiaClient

# List of platforms to support. There should be a matching .py file for each,
# eg <cover.py> and <sensor.py>
PLATFORMS = [Platform.COVER, Platform.LIGHT, Platform.FAN]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Entia Client from a config entry"""

    c = hass.data.setdefault(DOMAIN, {})[entry.entry_id] = EntiaClient(entry.data[CONF_USERNAME],
                                                                       entry.data[CONF_PASSWORD])
    # Start the client loop
    await c.start()

    # Wait for devices to be found
    await c.wait('ready')

    # This creates each HA object for each platform your device requires.
    # It's done by calling the `async_setup_entry` function in each platform module.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # This is called when an entry/configured device is to be removed. The class
    # needs to unload itself, and remove callbacks. See the classes for further
    # details
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    await hass.data[DOMAIN][entry.entry_id].stop()

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
