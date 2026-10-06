"""Debug logging: every exchange is logged so a debug log can be replayed as a fixture."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

import pytest

from custom_components.entia import client as client_module
from custom_components.entia.client import EntiaClient

from .conftest import PASSWORD, USERNAME, until
from .fake_ape import FakeApe
from .test_client import light_event, start

LOGGER = "custom_components.entia"


def exchanges(caplog: pytest.LogCaptureFixture) -> list[dict]:
    return [json.loads(r.getMessage().removeprefix("Exchange ")) for r in caplog.records if r.msg == "Exchange %s"]


def ha_log(caplog: pytest.LogCaptureFixture) -> str:
    """Render captured records the way Home Assistant writes home-assistant.log."""
    return "\n".join(
        f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(r.created))}.{int(r.msecs):03d} "
        f"{r.levelname} (MainThread) [{r.name}] {r.getMessage()}"
        for r in caplog.records
    )


async def test_exchanges_are_logged(client: EntiaClient, ape: FakeApe, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    await start(client, ape)

    logged = exchanges(caplog)
    assert [x["request"]["cmd"] for x in logged] == ["CONNECT", "FLAT_CONNECT", "FLAT_GET_TEMPLATE", "FLAT_GET"]
    assert logged[1]["request"]["params"]["password"] == "**REDACTED**"
    assert logged[1]["request"]["params"]["username"] == "**REDACTED**"
    assert logged[1]["response"] == [
        {
            "raw": "FLAT_LOGIN",
            "time": "1700000000",
            "data": {"flatid": "100", "sessionkey": "**REDACTED**", "login_hash": "**REDACTED**"},
        }
    ]
    assert logged[0]["response"][0]["data"]["sessid"] == "**REDACTED**"
    assert logged[1]["request"]["params"]["pipe"] == "**REDACTED**"
    assert logged[1]["status"] == 200
    assert PASSWORD not in caplog.text
    assert USERNAME not in caplog.text


async def test_idle_long_polls_are_logged(client: EntiaClient, ape: FakeApe, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    await start(client, ape)

    heartbeat = {"raw": "FLAT_HEARTBEAT", "time": "1700000001", "data": 0}
    empty_event = {"raw": "FLAT_EVENT", "time": "1700000002", "data": {"msg": {"response": {"device": 0}}}}
    ape.push(heartbeat)
    ape.push(empty_event)
    ape.push(light_event(1, on=False))
    await until(lambda: ape.idle)

    checks = [x for x in exchanges(caplog) if x["request"]["cmd"] == "CHECK"]
    assert [x["response"] for x in checks] == [[heartbeat], [empty_event], [light_event(1, on=False)]]


async def test_cancelled_long_polls_are_logged(
    client: EntiaClient, ape: FakeApe, caplog: pytest.LogCaptureFixture
) -> None:
    """Commands interrupt the pending long poll; the log shows it as a CHECK with an error."""
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    await start(client, ape)
    await until(lambda: ape.idle)

    ape.reply("DEVICE_SET", [{"raw": "DEVICE_SET", "data": {"msg": {"id": "1"}}}])
    await client.request("100-1", {"state": "off"})
    await until(lambda: ape.idle)

    logged = exchanges(caplog)[4:]
    assert [x["request"]["cmd"] for x in logged] == ["CHECK", "DEVICE_SET"]
    assert logged[0]["response"] is None
    assert "closed by client" in logged[0]["error"]


async def test_failed_requests_are_logged(client: EntiaClient, ape: FakeApe, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    await start(client, ape)

    ape.reply("DEVICE_SET", ConnectionError("connection reset"))
    ape.load("synthetic_startup.json")
    await client.request("100-1", {"state": "off"})
    await until(lambda: not ape.pending and client.online)

    [failed] = [x for x in exchanges(caplog) if x["request"]["cmd"] == "DEVICE_SET"]
    assert failed["response"] is None
    assert "connection reset" in failed["error"]


async def test_nothing_logged_without_debug(
    client: EntiaClient, ape: FakeApe, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)
    await start(client, ape)

    assert exchanges(caplog) == []


async def test_debug_log_replays(
    client: EntiaClient,
    ape: FakeApe,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    await start(client, ape)
    ape.push({"raw": "FLAT_EVENT", "data": {"msg": {"response": {"device": [
        {"id": 3, "type": 6, "attribute": [{"id": 1, "val": 55}, {"id": 2, "val": 0}, {"id": 3, "val": 2}]},
    ]}}}})  # fmt: skip
    await until(lambda: client.data["100-3"]["position"] == 45)
    log = tmp_path / "home-assistant.log"
    log.write_text(ha_log(caplog) + "\n")

    replay = FakeApe()
    monkeypatch.setattr(client_module, "httpx", replay.httpx())
    monkeypatch.setattr(EntiaClient, "data", {})
    replayed = EntiaClient(USERNAME, PASSWORD)
    replay.load(str(log))
    await replayed.start()
    try:
        await asyncio.wait_for(replayed.wait("ready"), 2)
        await until(lambda: not replay.pending)

        assert replay.commands(include_check=True)[:5] == [
            "CONNECT", "FLAT_CONNECT", "FLAT_GET_TEMPLATE", "FLAT_GET", "CHECK",
        ]  # fmt: skip
        assert replayed.data["100-3"]["position"] == 45
        assert replayed.data["100-3"]["state"] == "closing"
    finally:
        replay.close()
        await asyncio.wait_for(replayed.stop(), 5)
        assert replay.unexpected == []
