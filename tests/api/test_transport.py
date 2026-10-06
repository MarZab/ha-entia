"""HTTP transport: errors, redaction, exchange log and history."""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

from custom_components.entia.api import ConnectError, ProtocolError, Transport
from custom_components.entia.api.messages import encode_request
from custom_components.entia.api.transport import HISTORY_SIZE, redact

from ..conftest import until
from ..fake_ape import EXCHANGE_LINE, HEARTBEAT, FakeApe

LOGGER = "custom_components.entia.api"


def request(cmd: str, **params) -> str:
    return encode_request(cmd, 1, "real-session", "real-pipe", params, 1700000000)


def logged(caplog: pytest.LogCaptureFixture) -> list[dict]:
    lines = "\n".join(f"[{r.name}] {r.getMessage()}" for r in caplog.records)
    return [json.loads(m.group(1)) for m in EXCHANGE_LINE.finditer(lines)]


@pytest.fixture
def transport(ape: FakeApe) -> Transport:
    return Transport(ape.http_client())


async def test_returns_messages(transport: Transport, ape: FakeApe) -> None:
    ape.reply("CHECK", HEARTBEAT)

    assert await transport.request(request("CHECK")) == HEARTBEAT


async def test_http_error(transport: Transport, ape: FakeApe) -> None:
    ape.reply("CONNECT", "Approval required", status=403)

    with pytest.raises(ConnectError, match="HTTP 403"):
        await transport.request(request("CONNECT"))


async def test_network_error(transport: Transport, ape: FakeApe) -> None:
    ape.reply("CONNECT", ConnectionError("connection reset"))

    with pytest.raises(ConnectError, match="connection reset"):
        await transport.request(request("CONNECT"))


async def test_malformed_response(transport: Transport, ape: FakeApe) -> None:
    ape.reply("CONNECT", "<html>maintenance</html>")

    with pytest.raises(ProtocolError):
        await transport.request(request("CONNECT"))


def test_redact() -> None:
    text = (
        '{"username":"id","password": "1111","time":"1700012345","sessid":"s","pipe":"p",'
        '"data":{"flatid":"100","sessionkey":"k","login_hash":"id","user":{"pubid":"p"}}}'
    )

    assert redact(text) == (
        '{"username":"**REDACTED**","password":"**REDACTED**","time":"1700012345","sessid":"**REDACTED**",'
        '"pipe":"**REDACTED**","data":{"flatid":"100","sessionkey":"**REDACTED**","login_hash":"**REDACTED**",'
        '"user":{"pubid":"**REDACTED**"}}}'
    )


async def test_history_is_redacted_and_bounded(transport: Transport, ape: FakeApe) -> None:
    ape.reply("FLAT_CONNECT", [{"raw": "FLAT_LOGIN", "data": {"flatid": "100", "login_hash": "real-id"}}])
    await transport.request(request("FLAT_CONNECT", username="real-id", password="real-pin"))

    [exchange] = transport.history
    assert "real-" not in exchange.request + exchange.response
    assert (exchange.cmd, exchange.status) == ("FLAT_CONNECT", 200)

    for _ in range(HISTORY_SIZE + 3):
        ape.reply("CHECK", HEARTBEAT)
        await transport.request(request("CHECK"))
    assert len(transport.history) == HISTORY_SIZE
    assert {e.cmd for e in transport.history} == {"CHECK"}


async def test_format_history(transport: Transport, ape: FakeApe) -> None:
    assert transport.format_history() == "No Entia exchanges recorded"
    ape.reply("CONNECT", "Approval required", status=403)
    with pytest.raises(ConnectError):
        await transport.request(request("CONNECT"))

    report = transport.format_history()
    assert report.startswith("Last 1 Entia exchanges")
    assert " CONNECT (" in report
    assert "HTTP 403" in report
    assert "< Approval required" in report


async def test_every_exchange_is_logged(transport: Transport, ape: FakeApe, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    ape.reply("CHECK", HEARTBEAT)
    ape.reply("CONNECT", "Approval required", status=403)
    ape.reply("FLAT_GET", ConnectionError("reset"))

    await transport.request(request("CHECK"))
    with pytest.raises(ConnectError):
        await transport.request(request("CONNECT"))
    with pytest.raises(ConnectError):
        await transport.request(request("FLAT_GET"))
    poll = asyncio.ensure_future(transport.request(request("CHECK")))
    await until(lambda: ape.idle)
    poll.cancel()
    with pytest.raises(asyncio.CancelledError):
        await poll

    entries = logged(caplog)
    assert [(e["request"]["cmd"], e["status"], e["error"]) for e in entries] == [
        ("CHECK", 200, None),
        ("CONNECT", 403, None),
        ("FLAT_GET", None, "ConnectionError('reset')"),
        ("CHECK", None, "cancelled"),
    ]
    assert entries[0]["response"] == HEARTBEAT
    assert entries[1]["response"] == "Approval required"
    assert entries[0]["request"]["sessid"] == "**REDACTED**"
    assert "real-" not in caplog.text


async def test_nothing_logged_without_debug(
    transport: Transport, ape: FakeApe, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)
    ape.reply("CHECK", HEARTBEAT)

    await transport.request(request("CHECK"))

    assert logged(caplog) == []
