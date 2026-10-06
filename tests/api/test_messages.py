"""Message encoding and parsing."""

from __future__ import annotations

import json

import pytest

from custom_components.entia.api import (
    Command,
    CoverDirection,
    CoverState,
    DeclaredDevice,
    DeviceKind,
    ProtocolError,
    SwitchState,
    Template,
)
from custom_components.entia.api.messages import (
    command_params,
    decode_response,
    encode_request,
    parse_states,
    parse_template,
)

from ..fake_ape import wrap
from ..messages import cover_event, light_event, startup_responses

TEMPLATE = Template(
    "100",
    (
        DeclaredDevice(1, DeviceKind.LIGHT, "Kitchen light"),
        DeclaredDevice(2, DeviceKind.FAN, "Bathroom fan"),
        DeclaredDevice(3, DeviceKind.COVER, "Living room blind"),
    ),
)


def template_msg() -> dict:
    return startup_responses()["FLAT_GET_TEMPLATE"][0]["data"]["msg"]


def event_msg(event: dict) -> dict:
    return event["data"]["msg"]


# Requests


def test_encode_request() -> None:
    encoded = encode_request("FLAT_GET", 4, "SESSION", "PIPE", {"list": "all", "extended": 1}, 1700000000.9)

    assert json.loads(encoded) == [
        {
            "cmd": "FLAT_GET",
            "chl": 4,
            "sessid": "SESSION",
            "params": {"time": "1700000000", "pipe": "PIPE", "list": "all", "extended": 1},
        }
    ]


def test_encode_request_before_session() -> None:
    assert json.loads(encode_request("CONNECT", 1, None, None, {"name": None}, 1700000000)) == [
        {"cmd": "CONNECT", "chl": 1, "params": {"time": "1700000000", "name": None}}
    ]


def test_encode_request_escapes_credentials() -> None:
    encoded = encode_request("FLAT_CONNECT", 2, "S", "P", {"username": 'a"b', "password": "c\\d"}, 0)

    assert json.loads(encoded)[0]["params"]["username"] == 'a"b'
    assert json.loads(encoded)[0]["params"]["password"] == "c\\d"


@pytest.mark.parametrize(
    ("command", "device_id", "attribute"),
    [
        (Command.switch(1, True), "1", {"id": 1, "val": 1}),
        (Command.switch(1, False), "1", {"id": 1, "val": 0}),
        (Command.cover_position(3, 25), "3", {"id": 1, "val": 75}),
        (Command.cover_position(3, 100), "3", {"id": 1, "val": 0}),
        (Command.cover_position(3, 120), "3", {"id": 1, "val": 0}),
        (Command.cover_position(3, -10), "3", {"id": 1, "val": 100}),
        (Command.cover_stop(3), "3", {"id": 3, "val": 0}),
    ],
)
def test_commands(command: Command, device_id: str, attribute: dict) -> None:
    assert command_params(command) == {"deviceid": device_id, "data": {"attribute": [attribute]}}


# Responses


def test_decode_response() -> None:
    assert decode_response(wrap([{"raw": "FLAT_HEARTBEAT", "data": 0}])) == [{"raw": "FLAT_HEARTBEAT", "data": 0}]


@pytest.mark.parametrize(
    "text",
    [
        "Approval required for ape.entia.si:443.",
        "Ape.transport.read('not json')",
        'Ape.transport.read(\'{"raw":"X"}\')',
        "Ape.transport.read('[1, 2]')",
    ],
)
def test_decode_malformed(text: str) -> None:
    with pytest.raises(ProtocolError):
        decode_response(text)


# Template


def test_parse_template_keeps_declared_supported_devices() -> None:
    assert parse_template("100", template_msg()) == TEMPLATE


def test_parse_template_accepts_single_items() -> None:
    msg = {
        "hause": {
            "floors": {
                "floor": {
                    "rooms": {"room": {"elements": {"element": {"id": "7", "type": 6, "subtype": 2, "label": "Blind"}}}}
                }
            }
        }
    }

    assert parse_template("100", msg) == Template("100", (DeclaredDevice(7, DeviceKind.COVER, "Blind"),))


def test_parse_template_without_floors() -> None:
    with pytest.raises(ProtocolError):
        parse_template("100", {"hause": {}})


def test_template_round_trip() -> None:
    assert Template.from_dict(TEMPLATE.as_dict()) == TEMPLATE


# States


def test_parse_initial_states() -> None:
    msg = startup_responses()["FLAT_GET"][0]["data"]["msg"]

    assert parse_states(TEMPLATE, msg) == {
        1: SwitchState(True),
        2: SwitchState(False),
        3: CoverState(70, CoverDirection.STOPPED),
    }


@pytest.mark.parametrize(
    ("closed", "direction", "expected"),
    [
        (0, 0, CoverState(100, CoverDirection.STOPPED)),
        (100, 0, CoverState(0, CoverDirection.STOPPED)),
        (40, 1, CoverState(60, CoverDirection.OPENING)),
        (40, 2, CoverState(60, CoverDirection.CLOSING)),
        (-5, 0, CoverState(100, CoverDirection.STOPPED)),
        (150, 0, CoverState(0, CoverDirection.STOPPED)),
    ],
)
def test_parse_cover(closed: int, direction: int, expected: CoverState) -> None:
    assert parse_states(TEMPLATE, event_msg(cover_event(3, closed, direction))) == {3: expected}


def test_parse_ignores_undeclared_and_mismatched_devices() -> None:
    msg = {
        "response": {
            "device": [
                {"id": 99, "type": 4, "attribute": [{"id": 1, "val": 1}]},
                {"id": 5, "type": 18, "attribute": 0},
                {"id": 1, "type": 6, "attribute": [{"id": 1, "val": 1}]},
                {"type": 4, "attribute": [{"id": 1, "val": 1}]},
            ]
        }
    }

    assert parse_states(TEMPLATE, msg) == {}


@pytest.mark.parametrize("msg", [{"response": {"device": 0}}, {"response": {}}, {}, None])
def test_parse_empty_states(msg: dict | None) -> None:
    assert parse_states(TEMPLATE, msg) == {}


def test_parse_switch_event() -> None:
    assert parse_states(TEMPLATE, event_msg(light_event(2, on=True))) == {2: SwitchState(True)}
