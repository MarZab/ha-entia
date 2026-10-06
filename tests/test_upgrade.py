"""Upgrade guardrails: an existing install must keep its entity ids, device and history.

Home Assistant maps entities to entity ids (and the recorder's history) through the entity registry,
keyed by platform + unique_id. These tests pre-populate the registries the way the integration left
them before the refactor and require setup to reuse them exactly. See docs/architecture.md, "Keeping
entity IDs".
"""

from __future__ import annotations

import json
import re

import pytest
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.entia.const import DOMAIN

from .conftest import PASSWORD, USERNAME
from .fake_ape import EXCHANGE_LINE, FIXTURES, FakeApe

RECORDING = "lights_cover_fan.log"
FLAT_ID = "100"  # flat id in the anonymized recording
DOMAINS = {(4, 1): "light", (4, 4): "fan", (6, 1): "cover"}
ELEMENT_UNIQUE_ID = re.compile(rf"^{FLAT_ID}-\d+$")


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Allow Home Assistant to load custom_components/entia."""


def declared_devices() -> list[tuple[str, int]]:
    """(entity domain, element id) for every supported device in the recorded template."""
    for match in EXCHANGE_LINE.finditer((FIXTURES / RECORDING).read_text()):
        for message in json.loads(match.group(1))["response"] or []:
            if message.get("raw") == "FLAT_TEMPLATE":
                rooms = message["data"]["msg"]["hause"]["floors"]["floor"]["rooms"]["room"]
                elements = [e for room in rooms for e in room["elements"]["element"]]
                return [
                    (DOMAINS[(e["type"], e.get("subtype"))], e["id"])
                    for e in elements
                    if (e.get("type"), e.get("subtype")) in DOMAINS
                ]
    raise AssertionError("no template in recording")


def config_entry() -> MockConfigEntry:
    """The config entry as the integration creates it today."""
    return MockConfigEntry(
        domain=DOMAIN,
        version=1,
        unique_id=USERNAME.lower(),
        title=USERNAME,
        data={CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
    )


async def setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def unload(hass: HomeAssistant, entry: MockConfigEntry, ape: FakeApe) -> None:
    ape.close()
    assert await hass.config_entries.async_unload(entry.entry_id)


def test_recording_declares_expected_devices() -> None:
    assert sorted(d for d, _ in declared_devices()) == ["cover"] * 4 + ["fan"] * 2 + ["light"] * 17


async def test_existing_install_keeps_entity_ids_and_device(hass: HomeAssistant, ape: FakeApe) -> None:
    entry = config_entry()
    entry.add_to_hass(hass)
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    area = ar.async_get(hass).async_create("Living room")

    # device as created before the refactor, customised by the user
    device = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, FLAT_ID)},
        name="Entia",
        model="Entia",
        manufacturer="Entia Living",
    )
    devices.async_update_device(device.id, name_by_user="Flat", area_id=area.id)

    # entity ids from older labels, so they differ from what a fresh setup would generate
    declared = declared_devices()
    expected: dict[str, str] = {}
    for domain, element_id in declared:
        registered = entities.async_get_or_create(
            domain,
            DOMAIN,
            f"{FLAT_ID}-{element_id}",
            suggested_object_id=f"entia_old_label_{element_id}",
            config_entry=entry,
            device_id=device.id,
            has_entity_name=True,
            original_name=f"Old label {element_id}",
        )
        expected[registered.unique_id] = registered.entity_id
    renamed_light = next(f"{FLAT_ID}-{i}" for d, i in declared if d == "light")
    disabled_fan = next(f"{FLAT_ID}-{i}" for d, i in declared if d == "fan")
    entities.async_update_entity(expected[renamed_light], new_entity_id="light.kitchen_ceiling")
    expected[renamed_light] = "light.kitchen_ceiling"
    entities.async_update_entity(expected[disabled_fan], disabled_by=er.RegistryEntryDisabler.USER)

    ape.load(RECORDING)
    await setup(hass, entry)

    registered = er.async_entries_for_config_entry(entities, entry.entry_id)
    device_entities = {e.unique_id: e for e in registered if ELEMENT_UNIQUE_ID.match(e.unique_id)}
    assert {uid: e.entity_id for uid, e in device_entities.items()} == expected
    assert not [e.entity_id for e in registered if re.search(r"_\d+$", e.entity_id) and "old_label" not in e.entity_id]
    assert device_entities[disabled_fan].disabled_by is er.RegistryEntryDisabler.USER
    # the connection entities are new; Home Assistant names them after the user's device name and area
    new = {e.unique_id: e.entity_id for e in registered if not ELEMENT_UNIQUE_ID.match(e.unique_id)}
    assert new == {
        f"{FLAT_ID}-status": "sensor.living_room_flat_status",
        f"{FLAT_ID}-connection": "switch.living_room_flat_connection",
    }
    assert not set(new.values()) & set(expected.values())

    for unique_id, entity_id in expected.items():
        state = hass.states.get(entity_id)
        if unique_id == disabled_fan:
            assert state is None
        else:
            assert state is not None and state.state != STATE_UNAVAILABLE, entity_id

    [flat] = dr.async_entries_for_config_entry(devices, entry.entry_id)
    assert flat.id == device.id
    assert flat.identifiers == {(DOMAIN, FLAT_ID)}
    assert (flat.name_by_user, flat.area_id) == ("Flat", area.id)
    assert {e.device_id for e in device_entities.values()} == {device.id}

    await unload(hass, entry, ape)


async def test_fresh_install_entity_ids(hass: HomeAssistant, ape: FakeApe) -> None:
    """New devices keep getting ``<domain>.entia_<label>`` ids, named ``Entia <label>``."""
    entry = config_entry()
    entry.add_to_hass(hass)
    ape.load(RECORDING)
    await setup(hass, entry)

    entities = er.async_get(hass)
    for domain, element_id in declared_devices():
        entity_id = entities.async_get_entity_id(domain, DOMAIN, f"{FLAT_ID}-{element_id}")
        assert entity_id == f"{domain}.entia_device_{element_id}"
        assert hass.states.get(entity_id).name == f"Entia Device {element_id}"

    assert entities.async_get_entity_id("sensor", DOMAIN, f"{FLAT_ID}-status") == "sensor.entia_status"
    assert entities.async_get_entity_id("switch", DOMAIN, f"{FLAT_ID}-connection") == "switch.entia_connection"

    [flat] = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert (flat.identifiers, flat.name, flat.model, flat.manufacturer) == (
        {(DOMAIN, FLAT_ID)},
        "Entia",
        "Entia",
        "Entia Living",
    )

    await unload(hass, entry, ape)


async def test_reload_keeps_entity_ids(hass: HomeAssistant, ape: FakeApe) -> None:
    entry = config_entry()
    entry.add_to_hass(hass)
    ape.load(RECORDING)
    await setup(hass, entry)
    entities = er.async_get(hass)
    before = {e.unique_id: e.entity_id for e in er.async_entries_for_config_entry(entities, entry.entry_id)}

    await unload(hass, entry, ape)
    ape.reset()
    ape.load(RECORDING)
    await setup(hass, entry)

    after = {e.unique_id: e.entity_id for e in er.async_entries_for_config_entry(entities, entry.entry_id)}
    assert after == before

    await unload(hass, entry, ape)
