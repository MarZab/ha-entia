"""Hub and setup: template cache, non-blocking setup, auth, flat changes, availability."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.entia.api import Command, DeclaredDevice, DeviceKind, Template
from custom_components.entia.const import DOMAIN

from .conftest import PASSWORD, USERNAME, entity_id, until
from .fake_ape import FakeApe
from .test_client import startup_responses

TEMPLATE = Template(
    "100",
    (
        DeclaredDevice(1, DeviceKind.LIGHT, "Kitchen light"),
        DeclaredDevice(2, DeviceKind.FAN, "Bathroom fan"),
        DeclaredDevice(3, DeviceKind.COVER, "Living room blind"),
    ),
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Allow Home Assistant to load custom_components/entia."""


def new_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=USERNAME, data={CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD})
    entry.add_to_hass(hass)
    return entry


def cache(hass_storage: dict[str, Any], entry: MockConfigEntry, template: Template) -> None:
    hass_storage[f"{DOMAIN}.{entry.entry_id}"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"{DOMAIN}.{entry.entry_id}",
        "data": {"template": template.as_dict()},
    }


async def setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def unload(hass: HomeAssistant, entry: MockConfigEntry, ape: FakeApe) -> None:
    ape.close()
    if entry.state is ConfigEntryState.LOADED:
        assert await hass.config_entries.async_unload(entry.entry_id)
    ape.reset()


def reauth_flows(hass: HomeAssistant) -> list[dict]:
    return [
        f for f in hass.config_entries.flow.async_progress_by_handler(DOMAIN) if f["context"]["source"] == SOURCE_REAUTH
    ]


# Setup


async def test_first_setup_caches_template(hass: HomeAssistant, ape: FakeApe, hass_storage: dict[str, Any]) -> None:
    entry = new_entry(hass)
    ape.load("synthetic_startup.json")
    await setup(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    await until(lambda: f"{DOMAIN}.{entry.entry_id}" in hass_storage)
    assert hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"] == {"template": TEMPLATE.as_dict()}
    await unload(hass, entry, ape)


async def test_setup_with_cache_does_not_wait_for_entia(
    hass: HomeAssistant, ape: FakeApe, hass_storage: dict[str, Any]
) -> None:
    entry = new_entry(hass)
    cache(hass_storage, entry, TEMPLATE)
    ape.down = True

    await setup(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    light = entity_id(hass, "light", 1)
    assert hass.states.get(light).state == STATE_UNAVAILABLE

    ape.down = False
    ape.load("synthetic_startup.json")
    await until(lambda: hass.states.get(light).state == STATE_ON)
    await unload(hass, entry, ape)


async def test_first_setup_retries_when_unreachable(hass: HomeAssistant, ape: FakeApe) -> None:
    entry = new_entry(hass)
    ape.down = True

    await setup(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_RETRY
    ape.reset()


async def test_first_setup_with_bad_credentials_starts_reauth(hass: HomeAssistant, ape: FakeApe) -> None:
    entry = new_entry(hass)
    ape.reply("CONNECT", startup_responses()["CONNECT"])
    ape.reply("FLAT_CONNECT", [{"raw": "FLAT_ERROR", "data": {"reason": "ERROR 401"}}])

    await setup(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert len(reauth_flows(hass)) == 1


async def test_rejected_login_while_running_starts_reauth(
    hass: HomeAssistant, entry: MockConfigEntry, ape: FakeApe
) -> None:
    light = entity_id(hass, "light", 1)
    ape.push({"raw": "ERR", "data": {"code": "004", "value": "BAD_SESSID"}})
    ape.reply("CONNECT", startup_responses()["CONNECT"])
    ape.reply("FLAT_CONNECT", [{"raw": "FLAT_ERROR", "data": {"reason": "ERROR 401"}}])

    await until(lambda: hass.states.get(light).state == STATE_UNAVAILABLE)
    await hass.async_block_till_done()

    assert len(reauth_flows(hass)) == 1
    with pytest.raises(HomeAssistantError, match="not connected"):
        await entry.runtime_data.async_send(Command.switch(1, True))


async def test_flat_change_reloads_with_new_devices(
    hass: HomeAssistant, ape: FakeApe, hass_storage: dict[str, Any]
) -> None:
    entry = new_entry(hass)
    cache(hass_storage, entry, Template("100", TEMPLATE.devices[:2]))
    # the first session reloads the entry as soon as it sees the new template
    responses = startup_responses()
    for cmd in ("CONNECT", "FLAT_CONNECT", "FLAT_GET_TEMPLATE"):
        ape.reply(cmd, responses[cmd])
    ape.load("synthetic_startup.json")  # the session after the reload

    await setup(hass, entry)
    registry = er.async_get(hass)
    await until(lambda: registry.async_get_entity_id("cover", DOMAIN, "100-3") is not None)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"] == {"template": TEMPLATE.as_dict()}
    await unload(hass, entry, ape)


async def test_removing_entry_deletes_cache(
    hass: HomeAssistant, entry: MockConfigEntry, ape: FakeApe, hass_storage: dict[str, Any]
) -> None:
    await until(lambda: f"{DOMAIN}.{entry.entry_id}" in hass_storage)
    ape.close()

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert f"{DOMAIN}.{entry.entry_id}" not in hass_storage


# Availability and commands


async def test_unavailable_while_reconnecting(hass: HomeAssistant, entry: MockConfigEntry, ape: FakeApe) -> None:
    light = entity_id(hass, "light", 1)
    cover = entity_id(hass, "cover", 3)
    assert hass.states.get(light).state == STATE_ON

    ape.down = True
    ape.push({"raw": "ERR", "data": {"code": "004", "value": "BAD_SESSID"}})
    await until(lambda: hass.states.get(cover).state == STATE_UNAVAILABLE)
    assert hass.states.get(light).state == STATE_UNAVAILABLE

    ape.down = False
    ape.load("synthetic_startup.json")
    await until(lambda: hass.states.get(light).state == STATE_ON)
    assert hass.states.get(cover).attributes["current_position"] == 70


async def test_failed_command_raises(hass: HomeAssistant, entry: MockConfigEntry, ape: FakeApe) -> None:
    ape.reply("DEVICE_SET", ConnectionError("connection reset"))
    ape.load("synthetic_startup.json")

    with pytest.raises(HomeAssistantError, match="connection reset"):
        await hass.services.async_call("light", "turn_off", {"entity_id": entity_id(hass, "light", 1)}, blocking=True)

    await until(lambda: ape.idle)
