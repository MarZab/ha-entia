"""Inactivity timeout: idle close, what counts as activity, reconnect on demand."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.entia.api import Command
from custom_components.entia.const import CONF_TIMEOUT, DOMAIN

from .conftest import PASSWORD, USERNAME, entity_id, settle
from .fake_ape import HEARTBEAT, FakeApe
from .messages import ack, light_event

STATUS = "sensor.entia_status"
CONNECTION = "switch.entia_connection"
TIMEOUT = 5  # minutes


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Allow Home Assistant to load custom_components/entia."""


@pytest.fixture
async def timed_entry(
    hass: HomeAssistant, ape: FakeApe, freezer: FrozenDateTimeFactory
) -> AsyncIterator[MockConfigEntry]:
    """A loaded entry with a 5 minute inactivity timeout, on a frozen clock from the start."""
    ape.load("synthetic_startup.json")
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=USERNAME,
        data={CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
        options={CONF_TIMEOUT: TIMEOUT},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await settle(lambda: hass.states.get(STATUS).state == "connected" and ape.idle)
    yield entry
    if entry.state is ConfigEntryState.LOADED:
        ape.close()
        assert await hass.config_entries.async_unload(entry.entry_id)


async def wait(hass: HomeAssistant, freezer: FrozenDateTimeFactory, minutes: float) -> None:
    """Let time pass and run whatever came due."""
    freezer.tick(timedelta(minutes=minutes))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def status(hass: HomeAssistant) -> str:
    return hass.states.get(STATUS).state


# Closing


async def test_no_timeout_by_default(
    hass: HomeAssistant, entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    await wait(hass, freezer, 24 * 60)

    assert status(hass) == "connected"


async def test_closes_after_inactivity(
    hass: HomeAssistant, timed_entry: MockConfigEntry, ape: FakeApe, freezer: FrozenDateTimeFactory
) -> None:
    light = entity_id(hass, "light", 1)
    cover = entity_id(hass, "cover", 3)

    await wait(hass, freezer, TIMEOUT - 0.1)
    assert status(hass) == "connected"

    await wait(hass, freezer, 0.2)
    assert status(hass) == "idle"
    assert ape.in_flight == 0
    # devices keep their last state and stay usable
    assert hass.states.get(light).state == STATE_ON
    assert hass.states.get(cover).attributes["current_position"] == 70
    assert hass.states.get(CONNECTION).state == STATE_ON


async def test_changes_from_entia_keep_it_open(
    hass: HomeAssistant, timed_entry: MockConfigEntry, ape: FakeApe, freezer: FrozenDateTimeFactory
) -> None:
    await wait(hass, freezer, 4)
    ape.push(light_event(1, on=False))
    await settle(lambda: ape.idle)

    await wait(hass, freezer, 4)
    assert status(hass) == "connected"

    await wait(hass, freezer, 1.1)
    assert status(hass) == "idle"


async def test_commands_keep_it_open(
    hass: HomeAssistant, timed_entry: MockConfigEntry, ape: FakeApe, freezer: FrozenDateTimeFactory
) -> None:
    await wait(hass, freezer, 4)
    ape.reply("DEVICE_SET", ack(1))
    await hass.services.async_call("light", "turn_off", {"entity_id": entity_id(hass, "light", 1)}, blocking=True)

    await wait(hass, freezer, 4)
    assert status(hass) == "connected"

    await wait(hass, freezer, 1.1)
    assert status(hass) == "idle"


@pytest.mark.parametrize(
    "push",
    [
        pytest.param(HEARTBEAT, id="heartbeat"),
        pytest.param([light_event(1, on=True)], id="unchanged-state"),
        pytest.param(ack(1), id="ack"),
    ],
)
async def test_traffic_without_changes_does_not_count(
    hass: HomeAssistant,
    timed_entry: MockConfigEntry,
    ape: FakeApe,
    freezer: FrozenDateTimeFactory,
    push: list[dict],
) -> None:
    await wait(hass, freezer, 4)
    ape.push(*push)
    await settle(lambda: ape.idle)

    await wait(hass, freezer, 1.1)
    assert status(hass) == "idle"


# Reconnecting on demand


async def test_command_while_idle_reconnects_and_sends(
    hass: HomeAssistant, timed_entry: MockConfigEntry, ape: FakeApe, freezer: FrozenDateTimeFactory
) -> None:
    await wait(hass, freezer, TIMEOUT + 0.1)
    assert status(hass) == "idle"
    requests = len(ape.requests)
    ape.load("synthetic_startup.json")
    ape.reply("DEVICE_SET", ack(1))

    await hass.services.async_call("light", "turn_off", {"entity_id": entity_id(hass, "light", 1)}, blocking=True)

    assert [r["cmd"] for r in ape.requests[requests:] if r["cmd"] != "CHECK"] == [
        "CONNECT",
        "FLAT_CONNECT",
        "FLAT_GET_TEMPLATE",
        "FLAT_GET",
        "DEVICE_SET",
    ]
    assert status(hass) == "connected"

    # and the timer runs again
    await wait(hass, freezer, TIMEOUT + 0.1)
    assert status(hass) == "idle"


async def test_failed_reconnect_fails_command_and_retries(
    hass: HomeAssistant, timed_entry: MockConfigEntry, ape: FakeApe, freezer: FrozenDateTimeFactory
) -> None:
    light = entity_id(hass, "light", 1)
    await wait(hass, freezer, TIMEOUT + 0.1)
    ape.down = True

    with pytest.raises(HomeAssistantError, match="Could not connect"):
        await hass.services.async_call("light", "turn_off", {"entity_id": light}, blocking=True)

    assert status(hass) == "reconnecting"
    assert hass.states.get(light).state == STATE_UNAVAILABLE

    ape.down = False
    ape.load("synthetic_startup.json")
    await settle(lambda: status(hass) == "connected")
    assert hass.states.get(light).state == STATE_ON


# Switch and options


async def test_switch_off_while_idle(
    hass: HomeAssistant, timed_entry: MockConfigEntry, ape: FakeApe, freezer: FrozenDateTimeFactory
) -> None:
    light = entity_id(hass, "light", 1)
    await wait(hass, freezer, TIMEOUT + 0.1)

    await hass.services.async_call("switch", "turn_off", {"entity_id": CONNECTION}, blocking=True)
    assert status(hass) == "off"
    assert hass.states.get(light).state == STATE_UNAVAILABLE
    # Home Assistant skips unavailable entities; the hub itself refuses commands while off
    requests = len(ape.requests)
    await hass.services.async_call("light", "turn_off", {"entity_id": light}, blocking=True)
    with pytest.raises(HomeAssistantError, match="turned off"):
        await timed_entry.runtime_data.async_send(Command.switch(1, False))
    assert len(ape.requests) == requests

    ape.load("synthetic_startup.json")
    await hass.services.async_call("switch", "turn_on", {"entity_id": CONNECTION}, blocking=True)
    await settle(lambda: status(hass) == "connected")
    assert hass.states.get(CONNECTION).state == STATE_ON
    await hass.services.async_call("switch", "turn_off", {"entity_id": CONNECTION}, blocking=True)
    assert hass.states.get(CONNECTION).state == STATE_OFF


async def test_options_flow_sets_timeout_without_reload(
    hass: HomeAssistant, entry: MockConfigEntry, ape: FakeApe, freezer: FrozenDateTimeFactory
) -> None:
    hub = entry.runtime_data
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_TIMEOUT: 10})
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_TIMEOUT: 10}
    assert entry.runtime_data is hub  # not reloaded
    await wait(hass, freezer, 9.9)
    assert status(hass) == "connected"
    await wait(hass, freezer, 0.2)
    assert status(hass) == "idle"


async def test_shorter_timeout_applies_from_now(
    hass: HomeAssistant, timed_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    await wait(hass, freezer, 3)
    hass.config_entries.async_update_entry(timed_entry, options={CONF_TIMEOUT: 10})
    await hass.async_block_till_done()

    await wait(hass, freezer, 9.9)
    assert status(hass) == "connected"
    await wait(hass, freezer, 0.2)
    assert status(hass) == "idle"


async def test_disabling_timeout_keeps_it_open(
    hass: HomeAssistant, timed_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    hass.config_entries.async_update_entry(timed_entry, options={CONF_TIMEOUT: 0})
    await hass.async_block_till_done()

    await wait(hass, freezer, 60)
    assert status(hass) == "connected"
