"""Shared fixtures."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.entia import hub as hub_module
from custom_components.entia.const import DOMAIN

from .fake_ape import FakeApe

USERNAME = "test-entia-id"
PASSWORD = "test-pin"


@pytest.fixture
def ape(monkeypatch: pytest.MonkeyPatch) -> FakeApe:
    """Fake Entia server wired into the hub, without pacing and back-off delays."""
    fake = FakeApe()

    async def no_delay(delay: float) -> None:
        await asyncio.sleep(0)

    monkeypatch.setattr(hub_module, "get_async_client", lambda hass: fake.http_client())
    monkeypatch.setattr(hub_module, "_sleep", no_delay)
    yield fake
    fake.close()
    assert fake.unexpected == [], f"unscripted requests: {fake.unexpected}"


async def until(condition: Callable[[], bool], timeout: float = 2) -> None:
    """Wait for the session to reach a condition."""
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.001)


@pytest.fixture
async def entry(hass: HomeAssistant, ape: FakeApe) -> AsyncIterator[MockConfigEntry]:
    """A loaded config entry backed by the synthetic startup traffic."""
    ape.load("synthetic_startup.json")
    entry = MockConfigEntry(domain=DOMAIN, unique_id=USERNAME, data={CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield entry
    if entry.state is ConfigEntryState.LOADED:
        ape.close()
        assert await hass.config_entries.async_unload(entry.entry_id)


def entity_id(hass: HomeAssistant, platform: str, device_id: int) -> str:
    registry = er.async_get(hass)
    found = registry.async_get_entity_id(platform, DOMAIN, f"100-{device_id}")
    assert found, f"no {platform} entity for device {device_id}"
    return found


async def settle(condition: Callable[[], bool], rounds: int = 2000) -> None:
    """Like ``until`` but without timers, for tests with a frozen clock (``freezer``)."""
    for _ in range(rounds):
        if condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition not reached")
