"""Connection status sensor and connection switch."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.entia.api import Command
from custom_components.entia.const import DOMAIN

from .conftest import entity_id, until
from .fake_ape import HEARTBEAT, FakeApe
from .messages import startup_responses

STATUS = "sensor.entia_status"
CONNECTION = "switch.entia_connection"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Allow Home Assistant to load custom_components/entia."""


async def switch(hass: HomeAssistant, service: str) -> None:
    await hass.services.async_call("switch", service, {"entity_id": CONNECTION}, blocking=True)
    await hass.async_block_till_done()


async def test_entities(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    registry = er.async_get(hass)

    status = registry.async_get(STATUS)
    assert (status.unique_id, status.entity_category) == ("100-status", "diagnostic")
    state = hass.states.get(STATUS)
    assert state.state == "connected"
    assert state.name == "Entia Status"
    assert state.attributes["options"] == ["off", "connecting", "connected", "idle", "reconnecting", "auth_failed"]

    connection = registry.async_get(CONNECTION)
    assert (connection.unique_id, connection.entity_category) == ("100-connection", "config")
    assert hass.states.get(CONNECTION).state == STATE_ON
    assert hass.states.get(CONNECTION).name == "Entia Connection"


async def test_status_follows_connection(hass: HomeAssistant, entry: MockConfigEntry, ape: FakeApe) -> None:
    ape.down = True
    ape.push({"raw": "ERR", "data": {"code": "004", "value": "BAD_SESSID"}})
    await until(lambda: hass.states.get(STATUS).state == "reconnecting")

    ape.down = False
    ape.reply("CONNECT", startup_responses()["CONNECT"])
    ape.reply("FLAT_CONNECT", [{"raw": "FLAT_ERROR", "data": {"reason": "ERROR 401"}}])
    await until(lambda: hass.states.get(STATUS).state == "auth_failed")
    assert hass.states.get(CONNECTION).state == STATE_ON


async def test_turning_off_disconnects(hass: HomeAssistant, entry: MockConfigEntry, ape: FakeApe) -> None:
    light = entity_id(hass, "light", 1)
    ape.heartbeat = 0.001  # a running session keeps polling
    ape.push(*HEARTBEAT)  # wake the poll that is already waiting
    await until(lambda: len(ape.sent("CHECK")) > 3)

    await switch(hass, "turn_off")
    requests = len(ape.requests)
    assert ape.in_flight == 0

    assert hass.states.get(CONNECTION).state == STATE_OFF
    assert hass.states.get(STATUS).state == "off"
    assert hass.states.get(light).state == STATE_UNAVAILABLE
    with pytest.raises(HomeAssistantError, match="turned off"):
        await entry.runtime_data.async_send(Command.switch(1, True))
    for _ in range(20):
        await asyncio.sleep(0.001)
    assert len(ape.requests) == requests


async def test_turning_on_reconnects(hass: HomeAssistant, entry: MockConfigEntry, ape: FakeApe) -> None:
    light = entity_id(hass, "light", 1)
    await switch(hass, "turn_off")
    ape.reset()
    ape.load("synthetic_startup.json")

    await switch(hass, "turn_on")

    await until(lambda: hass.states.get(STATUS).state == "connected")
    assert hass.states.get(light).state == STATE_ON


async def test_off_is_remembered(
    hass: HomeAssistant, entry: MockConfigEntry, ape: FakeApe, hass_storage: dict[str, Any]
) -> None:
    light = entity_id(hass, "light", 1)
    await switch(hass, "turn_off")
    assert hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"]["enabled"] is False
    requests = len(ape.requests)

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get(CONNECTION).state == STATE_OFF
    assert hass.states.get(STATUS).state == "off"
    assert hass.states.get(light).state == STATE_UNAVAILABLE
    assert len(ape.requests) == requests
