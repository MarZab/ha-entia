"""Protocol tests for EntiaClient against the fake APE server."""

from __future__ import annotations

import asyncio
import json
import logging
import time

import pytest

from custom_components.entia import client as client_module
from custom_components.entia.client import HISTORY_SIZE, BadCredentials, EntiaClient

from .conftest import PASSWORD, USERNAME, until
from .fake_ape import FIXTURES, HEARTBEAT, FakeApe


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


def attributes(request: dict) -> tuple[str, list[dict]]:
    """Decode the device id and attributes of a DEVICE_SET request."""
    params = request["params"]
    return params["deviceid"], params["data"]["attribute"]


async def start(client: EntiaClient, ape: FakeApe, fixture: str = "synthetic_startup.json") -> None:
    ape.load(fixture)
    await client.start()
    await asyncio.wait_for(client.wait("ready"), 2)


# Connection


async def test_startup_handshake(client: EntiaClient, ape: FakeApe) -> None:
    await start(client, ape)

    assert ape.commands() == ["CONNECT", "FLAT_CONNECT", "FLAT_GET_TEMPLATE", "FLAT_GET"]
    assert client.online
    assert client.serial == "100"

    connect, flat_connect, _template, flat_get = (ape.sent(c)[0] for c in ape.commands())
    assert "sessid" not in connect
    assert "pipe" not in connect["params"]
    assert flat_connect["sessid"] == "SESSION"
    assert flat_connect["params"]["pipe"] == "PIPE"
    assert flat_connect["params"]["username"] == USERNAME
    assert flat_connect["params"]["password"] == PASSWORD
    assert flat_get["params"]["list"] == "all"
    assert flat_get["params"]["extended"] == 1
    assert [r["chl"] for r in ape.requests[:4]] == [1, 2, 3, 4]


async def test_bad_credentials(client: EntiaClient, ape: FakeApe) -> None:
    ape.reply(
        "CONNECT",
        [
            {"raw": "LOGIN", "data": {"sessid": "SESSION"}},
            {"raw": "IDENT", "data": {"user": {"pubid": "PIPE"}}},
        ],
    )
    ape.reply("FLAT_CONNECT", [{"raw": "FLAT_ERROR", "data": {"reason": "ERROR 401"}}])
    await client.start()

    with pytest.raises(BadCredentials) as err:
        await asyncio.wait_for(client.wait("ready"), 2)

    assert not client.online
    notes = "\n".join(err.value.__notes__)
    assert "FLAT_CONNECT" in notes
    assert PASSWORD not in notes
    assert USERNAME not in notes


async def test_reconnects_after_server_error(
    client: EntiaClient, ape: FakeApe, caplog: pytest.LogCaptureFixture
) -> None:
    await start(client, ape)

    ape.push({"raw": "ERR", "data": {"code": "004", "value": "BAD_SESSID"}})
    ape.load("synthetic_startup.json")
    await until(lambda: not ape.pending and client.online)

    assert ape.commands() == ["CONNECT", "FLAT_CONNECT", "FLAT_GET_TEMPLATE", "FLAT_GET"] * 2
    assert "Server Error" in caplog.text
    assert "Entia exchanges" in caplog.text


async def test_reconnects_after_network_error(
    client: EntiaClient, ape: FakeApe, caplog: pytest.LogCaptureFixture
) -> None:
    await start(client, ape)

    ape.reply("DEVICE_SET", ConnectionError("connection reset"))
    ape.load("synthetic_startup.json")
    await client.request("100-1", {"state": "off"})
    await until(lambda: not ape.pending and client.online)

    assert ape.commands()[4:] == ["DEVICE_SET", "CONNECT", "FLAT_CONNECT", "FLAT_GET_TEMPLATE", "FLAT_GET"]
    assert "Network Error" in caplog.text
    assert "connection reset" in caplog.text


# Parsing


async def test_template_creates_supported_devices(client: EntiaClient, ape: FakeApe) -> None:
    await start(client, ape)

    assert {k: (v["type"], v["name"]) for k, v in client.data.items()} == {
        "100-1": ("light", "Kitchen light"),
        "100-2": ("fan", "Bathroom fan"),
        "100-3": ("cover", "Living room blind"),
    }


async def test_initial_states(client: EntiaClient, ape: FakeApe) -> None:
    await start(client, ape)

    assert client.data["100-1"]["state"] == "on"
    assert client.data["100-2"]["state"] == "off"
    # Entia reports how far the cover is closed, HA expects how far it is open
    assert client.data["100-3"]["position"] == 70
    assert client.data["100-3"]["state"] == "stopped"


@pytest.mark.parametrize(
    ("closed_percent", "direction", "position", "state"),
    [
        (0, 0, 100, "stopped"),
        (100, 0, 0, "stopped"),
        (40, 1, 60, "opening"),
        (40, 2, 60, "closing"),
        (-5, 0, 100, "stopped"),
        (150, 0, 0, "stopped"),
    ],
)
async def test_cover_events(
    client: EntiaClient, ape: FakeApe, closed_percent: int, direction: int, position: int, state: str
) -> None:
    await start(client, ape)

    ape.push(cover_event(3, closed_percent, direction))
    await until(lambda: not ape.pending and client.data["100-3"]["position"] == position)

    assert client.data["100-3"]["state"] == state


async def test_event_triggers_callback(client: EntiaClient, ape: FakeApe) -> None:
    await start(client, ape)
    calls = []
    client.on("100-1", lambda: calls.append(client.data["100-1"]["state"]))

    ape.push(light_event(1, on=False))
    await until(lambda: calls)

    assert calls == ["off"]


async def test_events_for_unknown_devices_are_ignored(client: EntiaClient, ape: FakeApe) -> None:
    await start(client, ape)

    ape.push(light_event(99, on=True), light_event(1, on=False))
    await until(lambda: not ape.pending and client.data["100-1"]["state"] == "off")

    assert "100-99" not in client.data


# Commands


async def test_light_commands(client: EntiaClient, ape: FakeApe) -> None:
    await start(client, ape)

    ape.reply("DEVICE_SET", ack(1))
    await client.request("100-1", {"state": "off"})
    await until(lambda: not ape.pending)

    assert attributes(ape.sent("DEVICE_SET")[0]) == ("1", [{"id": 1, "val": 0}])


@pytest.mark.parametrize(
    ("state", "attribute"),
    [
        ({"position": 25}, {"id": 1, "val": 75}),
        ({"position": 100}, {"id": 1, "val": 0}),
        ({"position": 120}, {"id": 1, "val": 0}),
        ({"position": -10}, {"id": 1, "val": 100}),
        ({"state": "stopped"}, {"id": 3, "val": 0}),
    ],
)
async def test_cover_commands(client: EntiaClient, ape: FakeApe, state: dict, attribute: dict) -> None:
    await start(client, ape)

    ape.reply("DEVICE_SET", ack(3))
    await client.request("100-3", state)
    await until(lambda: not ape.pending)

    assert attributes(ape.sent("DEVICE_SET")[0]) == ("3", [attribute])


async def test_commands_for_same_device_are_deduplicated(client: EntiaClient, ape: FakeApe) -> None:
    await start(client, ape)

    ape.reply("DEVICE_SET", ack(3))
    await client.request("100-3", {"position": 10})
    await client.request("100-3", {"position": 20})
    await until(lambda: not ape.pending)
    await asyncio.sleep(0.01)

    assert [attributes(r) for r in ape.sent("DEVICE_SET")] == [("3", [{"id": 1, "val": 80}])]


async def test_commands_ignored_until_ready(client: EntiaClient, ape: FakeApe) -> None:
    await client.request("100-1", {"state": "on"})

    assert ape.requests == []


# Exchange history


async def test_history_is_redacted(client: EntiaClient, ape: FakeApe) -> None:
    await start(client, ape)

    flat_connect = next(x for x in client.history if '"FLAT_CONNECT"' in x["request"])
    assert PASSWORD not in flat_connect["request"]
    assert USERNAME not in flat_connect["request"]
    assert "**REDACTED**" in flat_connect["request"]
    assert flat_connect["status"] == 200
    assert "FLAT_LOGIN" in flat_connect["response"]


async def test_history_keeps_latest_exchanges(client: EntiaClient, ape: FakeApe) -> None:
    await start(client, ape)

    for i in range(HISTORY_SIZE + 5):
        ape.push(light_event(1, on=i % 2 == 0))
    await until(lambda: not ape.pending)

    assert len(client.history) == HISTORY_SIZE
    assert all('"CHECK"' in x["request"] for x in client.history)


async def test_format_history(client: EntiaClient, ape: FakeApe) -> None:
    await start(client, ape)

    report = client.format_history()

    assert report.startswith(f"Last {len(client.history)} Entia exchanges")
    for cmd in ["CONNECT", "FLAT_CONNECT", "FLAT_GET_TEMPLATE", "FLAT_GET"]:
        assert f" {cmd} (" in report


def test_format_history_empty() -> None:
    assert EntiaClient(USERNAME, PASSWORD).format_history() == "No Entia exchanges recorded"


async def test_crash_logs_history(client: EntiaClient, ape: FakeApe, caplog: pytest.LogCaptureFixture) -> None:
    ape.reply("CONNECT", [{"raw": "SOMETHING_NEW", "data": {}}])
    await client.start()

    with pytest.raises(RuntimeError, match="Unexpected response"):
        await asyncio.wait_for(client.wait("ready"), 2)

    record = next(r for r in caplog.records if r.levelno == logging.ERROR)
    assert record.getMessage() == "Client stopped on error"
    assert "SOMETHING_NEW" in "\n".join(record.exc_info[1].__notes__)


# Server behaviour seen in recorded traffic


async def test_template_delivered_on_next_poll(client: EntiaClient, ape: FakeApe) -> None:
    """APE is a message pipe: FLAT_GET_TEMPLATE may get a heartbeat and the template the next CHECK."""
    responses = startup_responses()
    ape.reply("CONNECT", responses["CONNECT"])
    ape.reply("FLAT_CONNECT", responses["FLAT_CONNECT"])
    ape.reply("FLAT_GET_TEMPLATE", HEARTBEAT)
    ape.reply("CHECK", responses["FLAT_GET_TEMPLATE"])
    ape.reply("FLAT_GET", responses["FLAT_GET"])
    await client.start()
    await asyncio.wait_for(client.wait("ready"), 2)

    assert ape.commands(include_check=True) == ["CONNECT", "FLAT_CONNECT", "FLAT_GET_TEMPLATE", "CHECK", "FLAT_GET"]
    assert client.data["100-3"]["position"] == 70


async def test_command_ack_on_later_poll(client: EntiaClient, ape: FakeApe, caplog: pytest.LogCaptureFixture) -> None:
    await start(client, ape)

    ape.reply("DEVICE_SET", HEARTBEAT)
    ape.push(*ack(1), light_event(1, on=False))
    await client.request("100-1", {"state": "off"})
    await until(lambda: ape.idle)

    assert client.data["100-1"]["state"] == "off"
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


async def test_events_in_command_response_are_applied(client: EntiaClient, ape: FakeApe) -> None:
    await start(client, ape)

    ape.reply("DEVICE_SET", [*ack(3), cover_event(3, 100, direction=2)])
    await client.request("100-3", {"position": 0})
    await until(lambda: ape.idle)

    assert client.data["100-3"]["position"] == 0
    assert client.data["100-3"]["state"] == "closing"


async def test_unsupported_devices_in_states_are_ignored(
    client: EntiaClient, ape: FakeApe, caplog: pytest.LogCaptureFixture
) -> None:
    """FLAT_NOTIFY includes dimmers missing from the template and schedules with ``"attribute": 0``."""
    await start(client, ape)

    assert sorted(client.data) == ["100-1", "100-2", "100-3"]
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


async def test_stays_connected_through_heartbeats(client: EntiaClient, ape: FakeApe) -> None:
    """Idle long polls return a heartbeat every few seconds; the client keeps polling."""
    ape.heartbeat = 0.001
    await start(client, ape)

    await until(lambda: len(ape.sent("CHECK")) >= 50)
    assert client.online
    assert ape.commands() == ["CONNECT", "FLAT_CONNECT", "FLAT_GET_TEMPLATE", "FLAT_GET"]

    ape.reply("DEVICE_SET", ack(1))
    await client.request("100-1", {"state": "off"})
    await until(lambda: not ape.pending)
    assert attributes(ape.sent("DEVICE_SET")[0]) == ("1", [{"id": 1, "val": 0}])


async def test_hourly_refresh(client: EntiaClient, ape: FakeApe) -> None:
    await start(client, ape)
    notify = startup_responses()["FLAT_GET"]
    notify[0]["data"]["msg"]["response"]["device"][0]["attribute"] = [{"id": 1, "val": 0}]

    client._last_refresh = time.time() - 3601
    ape.push(*HEARTBEAT)
    ape.reply("FLAT_GET", notify)
    await until(lambda: ape.idle)

    assert ape.commands() == ["CONNECT", "FLAT_CONNECT", "FLAT_GET_TEMPLATE", "FLAT_GET", "FLAT_GET"]
    assert client.data["100-1"]["state"] == "off"


async def test_refresh_when_cover_reports_moving_too_long(client: EntiaClient, ape: FakeApe) -> None:
    await start(client, ape)
    ape.push(cover_event(3, 50, direction=1))
    await until(lambda: ape.idle and client.data["100-3"]["state"] == "opening")

    client._last_refresh = time.time() - 121
    client.data["100-3"]["last_update"] = time.time() - 121
    ape.push(*HEARTBEAT)
    ape.reply("FLAT_GET", startup_responses()["FLAT_GET"])
    await until(lambda: ape.idle)

    assert ape.commands()[-1] == "FLAT_GET"
    assert client.data["100-3"]["state"] == "stopped"


async def test_retries_connect_after_http_error(client: EntiaClient, ape: FakeApe, sleeps: list[float]) -> None:
    """A proxy or server error page instead of an APE response is retried with back-off."""
    ape.reply("CONNECT", "Forbidden", status=403)
    ape.reply("CONNECT", "Forbidden", status=403)
    await start(client, ape)

    assert ape.commands()[:3] == ["CONNECT", "CONNECT", "CONNECT"]
    assert sleeps[:2] == [2, 4]


# Known bugs, see docs/plan.md


@pytest.mark.xfail(strict=True, reason="device data is stored on the class and shared between clients")
async def test_clients_do_not_share_devices(client: EntiaClient, ape: FakeApe) -> None:
    await start(client, ape)

    assert EntiaClient("other", "other").data == {}


@pytest.mark.xfail(strict=True, reason="stop() waits for the in-flight long poll (up to 120s)")
async def test_stop_interrupts_long_poll(client: EntiaClient, ape: FakeApe) -> None:
    await start(client, ape)

    await asyncio.wait_for(asyncio.shield(client.stop()), 0.5)


@pytest.mark.xfail(strict=True, reason="connect back-off doubles without a cap")
async def test_connect_back_off_is_capped(client: EntiaClient, ape: FakeApe, sleeps: list[float]) -> None:
    for _ in range(12):
        ape.reply("CONNECT", "Forbidden", status=403)
    await start(client, ape)

    assert max(sleeps) <= 300


@pytest.mark.xfail(strict=True, reason="connect failures are only logged at DEBUG")
async def test_connect_failure_is_logged_as_warning(
    client: EntiaClient, ape: FakeApe, caplog: pytest.LogCaptureFixture
) -> None:
    ape.reply("CONNECT", "Forbidden", status=403)
    await start(client, ape)

    assert [r for r in caplog.records if r.levelno == logging.WARNING and r.name.startswith("custom_components.entia")]


@pytest.mark.xfail(strict=True, reason="HTTP status is not checked, a 403 is reported as 'Response malformed'")
async def test_http_error_reports_status(client: EntiaClient, ape: FakeApe, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="custom_components.entia")
    ape.reply("CONNECT", "Forbidden", status=403)
    await start(client, ape)

    [failure] = [r for r in caplog.records if r.exc_info and r.getMessage().startswith("Error connecting")]
    assert "403" in str(failure.exc_info[1])


@pytest.mark.xfail(strict=True, reason="the rate limit also delays user commands")
async def test_commands_are_not_rate_limited(
    client: EntiaClient, ape: FakeApe, monkeypatch: pytest.MonkeyPatch
) -> None:
    await start(client, ape)
    await until(lambda: ape.idle)
    throttled: list[str] = []

    async def sleep(delay: float, result=None):
        throttled.append(ape.requests[-1]["cmd"] if delay else "")
        await asyncio.sleep(0)

    # the long poll in flight is the 21st message of a busy 20 s window
    monkeypatch.setattr(client_module.asyncio, "sleep", sleep)
    client._send_raw_window = time.time()
    client._send_raw_messages = 20
    ape.reply("DEVICE_SET", ack(1))
    await client.request("100-1", {"state": "off"})
    await until(lambda: ape.sent("DEVICE_SET"))

    # a sleep while the cancelled CHECK is the last request delays the DEVICE_SET
    assert "CHECK" not in throttled


def test_redact_only_touches_credential_fields() -> None:
    message = '[{"cmd":"FLAT_CONNECT","params":{"time":"1700012345","username":"abc","password": "secret-pin"}}]'

    assert EntiaClient._redact(message) == (
        '[{"cmd":"FLAT_CONNECT","params":{"time":"1700012345","username":"**REDACTED**","password":"**REDACTED**"}}]'
    )


def test_redact_login_response() -> None:
    response = (
        'Ape.transport.read(\'[{"raw":"FLAT_LOGIN","data":{"flatid":"100","sessionkey":"k","login_hash":"id"}},'
        '{"raw":"LOGIN","data":{"sessid":"s"}},{"raw":"IDENT","data":{"user":{"casttype":"uni","pubid":"p"}}}]\')'
    )

    assert EntiaClient._redact(response) == (
        'Ape.transport.read(\'[{"raw":"FLAT_LOGIN","data":{"flatid":"100","sessionkey":"**REDACTED**",'
        '"login_hash":"**REDACTED**"}},{"raw":"LOGIN","data":{"sessid":"**REDACTED**"}},'
        '{"raw":"IDENT","data":{"user":{"casttype":"uni","pubid":"**REDACTED**"}}}]\')'
    )
