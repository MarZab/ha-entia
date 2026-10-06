"""Entia messages for tests: pushes and replies shaped like recorded traffic, and recordings."""

from __future__ import annotations

import json

from .fake_ape import EXCHANGE_LINE, FIXTURES

# Real traffic captured with the development Home Assistant (docs/development.md): login, two lights
# toggled, a cover opened/stopped/closed/stopped and a fan toggled
RECORDING = "lights_cover_fan.log"


def recorded(name: str) -> list[dict]:
    return [json.loads(m.group(1)) for m in EXCHANGE_LINE.finditer((FIXTURES / name).read_text())]


def light_event(device_id: int, on: bool) -> dict:
    """Push for a light/fan, shaped like the recorded FLAT_EVENT."""
    device = {
        "id": device_id,
        "type": 4,
        "attribute": [{"id": 1, "val": 1 if on else 0}],
        "setting": [{"id": 2, "val": 0}],
    }
    return {"raw": "FLAT_EVENT", "time": "1700000100", "data": {"msg": {"response": {"device": [device]}}}}


def cover_event(device_id: int, closed_percent: int, direction: int = 0) -> dict:
    """Push for a cover, shaped like the recorded FLAT_EVENT (attribute 4 is counter-like, 5 is 0)."""
    attributes = [
        {"id": 1, "val": closed_percent},
        {"id": 2, "val": closed_percent},
        {"id": 3, "val": direction},
        {"id": 4, "val": 21715},
        {"id": 5, "val": 0},
    ]
    device = {"id": device_id, "type": 6, "attribute": attributes}
    return {"raw": "FLAT_EVENT", "time": "1700000100", "data": {"msg": {"response": {"device": [device]}}}}


def ack(device_id: int) -> list[dict]:
    """DEVICE_SET acknowledgement as the server sends it (possibly on a later poll)."""
    return [{"raw": "DEVICE_SET", "time": "1700000100", "data": {"msg": {"id": str(device_id)}}}]


def startup_responses() -> dict[str, list[dict]]:
    """Responses of the synthetic startup, by the command that asked for them."""
    fixture = json.loads((FIXTURES / "synthetic_startup.json").read_text())
    return {x["request"]["cmd"]: x["response"] for x in fixture["exchanges"]}
