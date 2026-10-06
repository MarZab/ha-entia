"""The Entia integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .hub import EntiaHub

PLATFORMS = [Platform.COVER, Platform.FAN, Platform.LIGHT, Platform.SENSOR, Platform.SWITCH]

type EntiaConfigEntry = ConfigEntry[EntiaHub]


async def async_setup_entry(hass: HomeAssistant, entry: EntiaConfigEntry) -> bool:
    """Set up Entia from a config entry."""
    hub = EntiaHub(hass, entry)
    await hub.async_setup()
    entry.runtime_data = hub
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    hub.async_start()
    return True


async def _async_options_updated(hass: HomeAssistant, entry: EntiaConfigEntry) -> None:
    """A changed timeout applies to the running connection, no reload needed."""
    entry.runtime_data.async_options_updated()


async def async_unload_entry(hass: HomeAssistant, entry: EntiaConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    await entry.runtime_data.async_stop()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: EntiaConfigEntry) -> None:
    """Remove the cached template of a deleted entry."""
    await EntiaHub(hass, entry).async_remove()
