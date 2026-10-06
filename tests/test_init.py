"""Home Assistant tests: setup, entities, services and unload."""

from __future__ import annotations

import pytest
from homeassistant.components.logger.helpers import get_integration_loggers
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, STATE_OFF, STATE_ON, STATE_OPEN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.entia.api import transport as transport_module
from custom_components.entia.const import DOMAIN

from .conftest import PASSWORD, USERNAME, entity_id, until
from .fake_ape import FakeApe
from .test_client import ack, light_event


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Allow Home Assistant to load custom_components/entia."""


async def test_setup_creates_entities(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert entry.state is ConfigEntryState.LOADED

    assert hass.states.get(entity_id(hass, "light", 1)).state == STATE_ON
    assert hass.states.get(entity_id(hass, "fan", 2)).state == STATE_OFF
    cover = hass.states.get(entity_id(hass, "cover", 3))
    assert cover.state == STATE_OPEN
    assert cover.attributes["current_position"] == 70
    assert len(er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)) == 3


async def test_entities_belong_to_flat_device(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    registry = er.async_get(hass)
    devices = {e.device_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)}

    assert len(devices) == 1


async def test_pushed_event_updates_state(hass: HomeAssistant, entry: MockConfigEntry, ape: FakeApe) -> None:
    light = entity_id(hass, "light", 1)

    ape.push(light_event(1, on=False))
    await until(lambda: hass.states.get(light).state == STATE_OFF)


@pytest.mark.parametrize(
    ("platform", "service", "data", "device_id", "attribute"),
    [
        ("light", "turn_off", {}, 1, {"id": 1, "val": 0}),
        ("light", "turn_on", {}, 1, {"id": 1, "val": 1}),
        ("fan", "turn_on", {}, 2, {"id": 1, "val": 1}),
        ("fan", "turn_off", {}, 2, {"id": 1, "val": 0}),
        ("cover", "open_cover", {}, 3, {"id": 1, "val": 0}),
        ("cover", "close_cover", {}, 3, {"id": 1, "val": 100}),
        ("cover", "set_cover_position", {"position": 40}, 3, {"id": 1, "val": 60}),
        ("cover", "stop_cover", {}, 3, {"id": 3, "val": 0}),
    ],
)
async def test_services_send_device_set(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    ape: FakeApe,
    platform: str,
    service: str,
    data: dict,
    device_id: int,
    attribute: dict,
) -> None:
    ape.reply("DEVICE_SET", ack(device_id))

    await hass.services.async_call(
        platform, service, {"entity_id": entity_id(hass, platform, device_id), **data}, blocking=True
    )
    await until(lambda: not ape.pending)

    [request] = ape.sent("DEVICE_SET")
    assert request["params"]["deviceid"] == str(device_id)
    assert request["params"]["data"]["attribute"] == [attribute]


async def test_unload(hass: HomeAssistant, entry: MockConfigEntry, ape: FakeApe) -> None:
    ape.close()
    assert await hass.config_entries.async_unload(entry.entry_id)

    assert entry.state is ConfigEntryState.NOT_LOADED
    assert DOMAIN not in hass.data or entry.entry_id not in hass.data[DOMAIN]


async def test_debug_logging_toggle_covers_client(hass: HomeAssistant) -> None:
    """The integration's "Enable debug logging" option must include the client's exchange log."""
    loggers = await get_integration_loggers(hass, DOMAIN)
    name = transport_module._LOGGER.name

    assert any(name == logger or name.startswith(f"{logger}.") for logger in loggers), loggers


async def test_setup_from_recorded_traffic(hass: HomeAssistant, ape: FakeApe) -> None:
    ape.load("lights_cover_fan.log")
    entry = MockConfigEntry(domain=DOMAIN, unique_id=USERNAME, data={CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert sorted(e.domain for e in entities) == ["cover"] * 4 + ["fan"] * 2 + ["light"] * 17

    ape.close()
    assert await hass.config_entries.async_unload(entry.entry_id)
