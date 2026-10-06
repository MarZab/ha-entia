"""The hub: one Entia session per config entry, its cached template and entity updates."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.helpers.storage import Store

from .api import AuthError, Command, DeviceState, EntiaError, Session, SessionStatus, Template, Transport
from .const import DEVICE_MANUFACTURER, DEVICE_MODEL, DEVICE_NAME, DOMAIN, READY_TIMEOUT, STORAGE_VERSION

if TYPE_CHECKING:
    from . import EntiaConfigEntry

_LOGGER = logging.getLogger(__name__)

# Replaced in tests to skip pacing and back-off delays
_sleep = asyncio.sleep

# Device entities are unavailable in these states
UNAVAILABLE = frozenset({SessionStatus.STOPPED, SessionStatus.RECONNECTING, SessionStatus.AUTH_FAILED})


def create_session(hass: HomeAssistant, username: str, password: str, **callbacks: Any) -> Session:
    """A session using Home Assistant's shared HTTP client."""
    return Session(Transport(get_async_client(hass)), username, password, sleep=_sleep, **callbacks)


class EntiaHub:
    """Keeps a flat connected and tells its entities about changes."""

    def __init__(self, hass: HomeAssistant, entry: EntiaConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.template: Template | None = None
        self.signal = f"{DOMAIN}_{entry.entry_id}_update"
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self.session = create_session(
            hass,
            entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD],
            on_status=self._on_status,
            on_template=self._on_template,
            on_state=self._on_state,
            create_task=lambda coro: entry.async_create_background_task(hass, coro, f"{DOMAIN} session"),
        )

    # Lifecycle

    async def async_setup(self) -> None:
        """Load the cached template; without one, connect now to fetch it.

        With a cached template the entities are created right away and the connection is made
        in the background, so a slow or unreachable Entia never blocks Home Assistant.
        """
        stored = await self._store.async_load() or {}
        if "template" in stored:
            self.template = Template.from_dict(stored["template"])
            return

        self.session.start()
        try:
            await self.session.wait_ready(READY_TIMEOUT, fail_fast=True)
        except AuthError as err:
            await self.session.close()
            raise ConfigEntryAuthFailed("Invalid Entia ID or PIN") from err
        except (TimeoutError, EntiaError) as err:
            await self.session.close()
            raise ConfigEntryNotReady(f"Could not connect to Entia: {err}") from err
        self.template = self.session.template
        await self._async_save()

    def async_start(self) -> None:
        self.session.start()

    async def async_stop(self) -> None:
        await self.session.close()

    async def async_remove(self) -> None:
        await self._store.async_remove()

    # State

    @property
    def device_info(self) -> DeviceInfo:
        assert self.template is not None
        return DeviceInfo(
            identifiers={(DOMAIN, self.template.flat_id)},
            name=DEVICE_NAME,
            model=DEVICE_MODEL,
            manufacturer=DEVICE_MANUFACTURER,
        )

    @property
    def available(self) -> bool:
        return self.session.status not in UNAVAILABLE

    def state(self, device_id: int) -> DeviceState | None:
        return self.session.states.get(device_id)

    async def async_send(self, command: Command) -> None:
        """Send a command, reporting failures to the caller."""
        if self.session.status is not SessionStatus.CONNECTED:
            raise HomeAssistantError(f"Entia is not connected ({self.session.status})")
        try:
            await self.session.send(command)
        except EntiaError as err:
            raise HomeAssistantError(f"Sending to Entia failed: {err}") from err

    # Session callbacks

    @callback
    def _on_status(self, status: SessionStatus) -> None:
        if status is SessionStatus.AUTH_FAILED:
            self.entry.async_start_reauth(self.hass)
        async_dispatcher_send(self.hass, self.signal, None)

    @callback
    def _on_template(self, template: Template) -> None:
        if template == self.template:
            return
        changed = self.template is not None
        self.template = template
        self.hass.async_create_task(self._async_save())
        if changed:
            _LOGGER.info("The Entia flat changed, reloading")
            self.hass.config_entries.async_schedule_reload(self.entry.entry_id)

    @callback
    def _on_state(self, device_id: int, state: DeviceState) -> None:
        async_dispatcher_send(self.hass, self.signal, device_id)

    async def _async_save(self) -> None:
        assert self.template is not None
        await self._store.async_save({"template": self.template.as_dict()})
