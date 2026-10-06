"""Shared fixtures."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from types import SimpleNamespace

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.entia import client as client_module
from custom_components.entia.client import EntiaClient
from custom_components.entia.const import DOMAIN

from .fake_ape import FakeApe

USERNAME = "test-entia-id"
PASSWORD = "test-pin"


@pytest.fixture(autouse=True)
def isolate_client_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give each test fresh copies of EntiaClient's class-level mutable state.

    EntiaClient keeps ``data``, ``_signals``, ``_callbacks`` and ``_queue`` on the class,
    so they leak between instances (see docs/plan.md, "Bugs"). Without this, state would
    also leak between tests.
    """
    monkeypatch.setattr(EntiaClient, "data", {})
    monkeypatch.setattr(EntiaClient, "_signals", {})
    monkeypatch.setattr(EntiaClient, "_callbacks", {})
    monkeypatch.setattr(EntiaClient, "_queue", [])


@pytest.fixture(autouse=True)
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Skip the client's rate limiting and reconnect back-off delays, recording them."""
    delays: list[float] = []

    async def sleep(delay: float, result=None):
        delays.append(delay)
        await asyncio.sleep(0)
        return result

    fake_asyncio = SimpleNamespace(**{k: getattr(asyncio, k) for k in dir(asyncio) if not k.startswith("__")})
    fake_asyncio.sleep = sleep
    monkeypatch.setattr(client_module, "asyncio", fake_asyncio)
    return delays


@pytest.fixture
def ape(monkeypatch: pytest.MonkeyPatch) -> FakeApe:
    """Fake Entia server wired into the client module."""
    fake = FakeApe()
    monkeypatch.setattr(client_module, "httpx", fake.httpx())
    yield fake
    fake.close()
    assert fake.unexpected == [], f"unscripted requests: {fake.unexpected}"


@pytest.fixture
async def client(ape: FakeApe) -> AsyncIterator[EntiaClient]:
    """A client that is stopped at the end of the test."""
    c = EntiaClient(USERNAME, PASSWORD)
    yield c
    ape.close()
    await asyncio.wait_for(c.stop(), 5)


async def until(condition: Callable[[], bool], timeout: float = 2) -> None:
    """Wait for the client task to reach a condition."""
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
