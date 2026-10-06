"""Replays of real traffic captured with the development Home Assistant (docs/development.md).

lights_cover_fan.log: login, then two lights toggled, a cover opened/stopped/closed/stopped
and a fan toggled from the Home Assistant UI.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter

from custom_components.entia.client import EntiaClient

from .conftest import until
from .fake_ape import EXCHANGE_LINE, FIXTURES, FakeApe

RECORDING = "lights_cover_fan.log"


def recorded(name: str) -> list[dict]:
    return [json.loads(m.group(1)) for m in EXCHANGE_LINE.finditer((FIXTURES / name).read_text())]


def client_request(client: EntiaClient, device_set: dict) -> tuple[str, dict]:
    """The EntiaClient.request call that produces a recorded DEVICE_SET."""
    unique_id = f"{client.serial}-{device_set['params']['deviceid']}"
    [attribute] = device_set["params"]["data"]["attribute"]
    if client.data[unique_id]["type"] == "cover":
        if attribute["id"] == 3:
            return unique_id, {"state": "stopped"}
        return unique_id, {"position": 100 - attribute["val"]}
    return unique_id, {"state": "on" if attribute["val"] else "off"}


async def replay(client: EntiaClient, ape: FakeApe, name: str) -> None:
    """Replay a recording, issuing its commands when the recorded traffic reaches them."""
    ape.load(name)
    await client.start()
    await asyncio.wait_for(client.wait("ready"), 2)

    device_sets = [x["request"] for x in recorded(name) if x["request"]["cmd"] == "DEVICE_SET"]
    for i, device_set in enumerate(device_sets):
        await until(lambda i=i: len(ape.sent("DEVICE_SET")) == i and ape.pending[:1] == ["DEVICE_SET"])
        await client.request(*client_request(client, device_set))
    await until(lambda: ape.idle)


async def test_startup_devices(client: EntiaClient, ape: FakeApe) -> None:
    ape.load(RECORDING)
    await client.start()
    await asyncio.wait_for(client.wait("ready"), 2)

    assert client.serial == "100"
    assert Counter(d["type"] for d in client.data.values()) == {"light": 17, "fan": 2, "cover": 4}
    # covers report how far they are closed
    assert sorted(d["position"] for d in client.data.values() if d["type"] == "cover") == [0, 0, 2, 2]
    assert all(d["state"] == "stopped" for d in client.data.values() if d["type"] == "cover")
    assert all(d["state"] in ("on", "off") for d in client.data.values() if d["type"] != "cover")


async def test_template_arrives_on_next_poll(client: EntiaClient, ape: FakeApe) -> None:
    """APE returns whatever is queued for the session, not a response to the request."""
    ape.load(RECORDING)
    await client.start()
    await asyncio.wait_for(client.wait("ready"), 2)

    assert ape.commands(include_check=True)[:5] == ["CONNECT", "FLAT_CONNECT", "FLAT_GET_TEMPLATE", "CHECK", "FLAT_GET"]


async def test_replay_commands(client: EntiaClient, ape: FakeApe) -> None:
    await replay(client, ape, RECORDING)

    sent = [(r["params"]["deviceid"], r["params"]["data"]["attribute"]) for r in ape.sent("DEVICE_SET")]
    assert sent == [
        (x["request"]["params"]["deviceid"], x["request"]["params"]["data"]["attribute"])
        for x in recorded(RECORDING)
        if x["request"]["cmd"] == "DEVICE_SET"
    ]
    assert client.data["100-36919"]["state"] == "on"
    assert client.data["100-17241"]["state"] == "off"
    assert client.data["100-28106"]["state"] == "off"
    assert client.data["100-17232"]["position"] == 1
    assert client.data["100-17232"]["state"] == "stopped"
