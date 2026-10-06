"""Encode requests for and decode responses from the Entia APE server."""

from __future__ import annotations

import json
import re
from typing import Any

from .errors import ProtocolError
from .models import (
    Command,
    CoverDirection,
    CoverState,
    DeclaredDevice,
    DeviceKind,
    DeviceState,
    SwitchState,
    Template,
    clamp_percent,
)

# Element (type, subtype) in the template -> device kind; covers have any subtype
_KINDS = {(4, 1): DeviceKind.LIGHT, (4, 4): DeviceKind.FAN}
_COVER_TYPE = 6

# Server messages that end the session
ERROR_MESSAGES = frozenset({"ERR", "FLAT_ERROR", "FLAT_DISCONNECT", "QUIT", "DEVICE_SET_EXT"})
# Server messages that carry nothing the client uses
IGNORED_MESSAGES = frozenset(
    {
        "FLAT_WEATHER",
        "JOIN",
        "LEFT",
        "FLAT_HEARTBEAT",
        "CHANNEL",
        "DEVICE_SET",
        "FLAT_SETTING_GET",
        "FLAT_SET",
        "CLOSE",
        "LOGIN",
        "IDENT",
        "FLAT_LOGIN",
    }
)

_RESPONSE = re.compile(r"^Ape\.transport\.read\('(.+)'\)", re.DOTALL)


def encode_request(cmd: str, chl: int, sessid: str | None, pipe: str | None, params: dict[str, Any], now: float) -> str:
    """One APE request, as sent in the URL query."""
    message: dict[str, Any] = {"cmd": cmd, "chl": chl}
    if sessid:
        message["sessid"] = sessid
    all_params: dict[str, Any] = {"time": str(int(now))}
    if pipe:
        all_params["pipe"] = pipe
    all_params.update(params)
    message["params"] = all_params
    return json.dumps([message], separators=(",", ":"), ensure_ascii=False)


def decode_response(text: str) -> list[dict[str, Any]]:
    """Messages in an APE response body."""
    match = _RESPONSE.search(text)
    if not match:
        raise ProtocolError(f"Not an APE response: {text[:200]!r}")
    try:
        messages = json.loads(match.group(1))
    except ValueError as err:
        raise ProtocolError(f"Malformed APE response: {text[:200]!r}") from err
    if not isinstance(messages, list) or not all(isinstance(m, dict) for m in messages):
        raise ProtocolError(f"Malformed APE response: {text[:200]!r}")
    return messages


def command_params(command: Command) -> dict[str, Any]:
    return {
        "deviceid": str(command.device_id),
        "data": {"attribute": [{"id": command.attribute, "val": command.value}]},
    }


def _as_list(value: Any) -> list[Any]:
    """The template uses a single object where a list has one item."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def parse_template(flat_id: str, msg: dict[str, Any]) -> Template:
    """Supported devices declared in a FLAT_TEMPLATE message."""
    try:
        floors = _as_list(msg["hause"]["floors"]["floor"])
    except (KeyError, TypeError) as err:
        raise ProtocolError("Template without floors") from err

    devices: list[DeclaredDevice] = []
    for floor in floors:
        for room in _as_list((floor.get("rooms") or {}).get("room")):
            for element in _as_list((room.get("elements") or {}).get("element")):
                if not isinstance(element, dict) or not {"id", "type", "label"} <= element.keys():
                    continue
                if element["type"] == _COVER_TYPE:
                    kind = DeviceKind.COVER
                else:
                    kind = _KINDS.get((element["type"], element.get("subtype")))
                if kind is None:
                    continue
                devices.append(DeclaredDevice(int(element["id"]), kind, str(element["label"])))
    return Template(flat_id, tuple(devices))


def _attribute(device: dict[str, Any], attribute_id: int) -> int | None:
    for attribute in _as_list(device.get("attribute")):
        if isinstance(attribute, dict) and attribute.get("id") == attribute_id:
            return attribute.get("val")
    return None


def parse_states(template: Template, msg: dict[str, Any]) -> dict[int, DeviceState]:
    """States of declared devices in a FLAT_NOTIFY or FLAT_EVENT message.

    Devices the template does not declare (dimmers, schedules, ...) and devices whose reported
    type does not match their declared kind are skipped.
    """
    response = msg.get("response") if isinstance(msg, dict) else None
    devices = response.get("device") if isinstance(response, dict) else None
    states: dict[int, DeviceState] = {}
    for device in _as_list(devices) if isinstance(devices, (list, dict)) else []:
        if not isinstance(device, dict) or "id" not in device:
            continue
        declared = template.device(int(device["id"]))
        if declared is None:
            continue
        if declared.kind is DeviceKind.COVER:
            closed = _attribute(device, 1)
            if device.get("type") != _COVER_TYPE or closed is None:
                continue
            direction = {1: CoverDirection.OPENING, 2: CoverDirection.CLOSING}.get(
                _attribute(device, 3), CoverDirection.STOPPED
            )
            states[declared.id] = CoverState(clamp_percent(100 - closed), direction)
        else:
            value = _attribute(device, 1)
            if device.get("type") != 4 or value is None:
                continue
            states[declared.id] = SwitchState(value == 1)
    return states
