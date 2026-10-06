"""Entity behaviour that must survive the refactor (docs/plan.md, phase 0).

Values here are what the integration produces before the refactor. Known bugs are strict xfails
describing the intended behaviour instead of pinning the bug.
"""

from __future__ import annotations

import pytest
from homeassistant.components.cover import CoverEntityFeature
from homeassistant.components.fan import FanEntityFeature
from homeassistant.const import STATE_CLOSED, STATE_CLOSING, STATE_OFF, STATE_ON, STATE_OPEN, STATE_OPENING
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import entity_id, until
from .fake_ape import FakeApe
from .test_client import ack, cover_event, light_event


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Allow Home Assistant to load custom_components/entia."""


async def call(hass: HomeAssistant, domain: str, service: str, target: str, **data) -> None:
    await hass.services.async_call(domain, service, {"entity_id": target, **data}, blocking=True)


async def sent_attribute(ape: FakeApe) -> dict:
    await until(lambda: not ape.pending)
    [request] = ape.sent("DEVICE_SET")
    [attribute] = request["params"]["data"]["attribute"]
    return attribute


# Names


async def test_friendly_names(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert hass.states.get(entity_id(hass, "light", 1)).name == "Entia Kitchen light"
    assert hass.states.get(entity_id(hass, "fan", 2)).name == "Entia Bathroom fan"
    assert hass.states.get(entity_id(hass, "cover", 3)).name == "Entia Living room blind"


# Light


async def test_light(hass: HomeAssistant, entry: MockConfigEntry, ape: FakeApe) -> None:
    light = entity_id(hass, "light", 1)
    state = hass.states.get(light)
    assert state.state == STATE_ON
    assert state.attributes["color_mode"] == "onoff"
    assert state.attributes["supported_features"] == 0

    ape.push(light_event(1, on=False))
    await until(lambda: hass.states.get(light).state == STATE_OFF)
    assert hass.states.get(light).attributes["color_mode"] is None


@pytest.mark.xfail(strict=True, reason="supported_color_modes is the string 'onoff', HA turns it into its characters")
async def test_light_supported_color_modes(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert hass.states.get(entity_id(hass, "light", 1)).attributes["supported_color_modes"] == ["onoff"]


# Fan


async def test_fan(hass: HomeAssistant, entry: MockConfigEntry, ape: FakeApe) -> None:
    fan = entity_id(hass, "fan", 2)
    state = hass.states.get(fan)
    assert state.state == STATE_OFF
    assert state.attributes["supported_features"] == FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF

    ape.push(light_event(2, on=True))
    await until(lambda: hass.states.get(fan).state == STATE_ON)


# Cover


async def test_cover_attributes(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    state = hass.states.get(entity_id(hass, "cover", 3))

    assert state.attributes["device_class"] == "blind"
    assert state.attributes["supported_features"] == (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.SET_POSITION
        | CoverEntityFeature.STOP
        | CoverEntityFeature.OPEN_TILT
        | CoverEntityFeature.CLOSE_TILT
    )
    assert "current_tilt_position" not in state.attributes


@pytest.mark.parametrize(
    ("closed_percent", "direction", "state", "position"),
    [
        (0, 0, STATE_OPEN, 100),
        (40, 0, STATE_OPEN, 60),
        (40, 1, STATE_OPENING, 60),
        (40, 2, STATE_CLOSING, 60),
        (100, 0, STATE_CLOSED, 0),
    ],
)
async def test_cover_states(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    ape: FakeApe,
    closed_percent: int,
    direction: int,
    state: str,
    position: int,
) -> None:
    cover = entity_id(hass, "cover", 3)

    ape.push(cover_event(3, closed_percent, direction))
    await until(lambda: ape.idle)

    assert hass.states.get(cover).state == state
    assert hass.states.get(cover).attributes["current_position"] == position


@pytest.mark.parametrize(
    ("closed_percent", "service", "sent"),
    [
        (30, "open_cover_tilt", 29),  # position 70 -> 71
        (30, "close_cover_tilt", 31),  # position 70 -> 69
        (0, "open_cover_tilt", 0),  # fully open, clamped
        (100, "close_cover_tilt", 100),  # fully closed, clamped
    ],
)
async def test_cover_tilt_nudges_position(
    hass: HomeAssistant, entry: MockConfigEntry, ape: FakeApe, closed_percent: int, service: str, sent: int
) -> None:
    cover = entity_id(hass, "cover", 3)
    ape.push(cover_event(3, closed_percent))
    await until(lambda: ape.idle and hass.states.get(cover).attributes["current_position"] == 100 - closed_percent)

    ape.reply("DEVICE_SET", ack(3))
    await call(hass, "cover", service, cover)

    assert await sent_attribute(ape) == {"id": 1, "val": sent}
