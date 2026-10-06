"""Session behaviour against the fake APE server."""

from __future__ import annotations

import asyncio
import logging
from itertools import pairwise

import pytest

from custom_components.entia.api import (
    AuthError,
    Command,
    ConnectError,
    CoverDirection,
    CoverState,
    NotConnected,
    Session,
    SessionStatus,
    SwitchState,
)
from custom_components.entia.api.session import (
    BACKOFF_MAX,
    PACE_LIMIT,
    PACE_MAX_INTERVAL,
    PACE_STEP,
    PACE_WINDOW,
)

from ..conftest import PASSWORD, USERNAME, until
from ..fake_ape import HEARTBEAT, FakeApe
from ..test_client import ack, cover_event, light_event, startup_responses
from .conftest import FakeTime, Recorder, make_session, start

STARTUP = ["CONNECT", "FLAT_CONNECT", "FLAT_GET_TEMPLATE", "FLAT_GET"]
LOGGER = "custom_components.entia.api"


def device_set(ape: FakeApe, index: int = 0) -> tuple[str, list[dict]]:
    request = ape.sent("DEVICE_SET")[index]
    return request["params"]["deviceid"], request["params"]["data"]["attribute"]


def fail_connect(ape: FakeApe, times: int) -> None:
    for _ in range(times):
        ape.reply("CONNECT", "Forbidden", status=403)


# Connecting


async def test_startup(session: Session, ape: FakeApe, recorder: Recorder) -> None:
    await start(session, ape)

    assert ape.commands() == STARTUP
    connect, flat_connect, _, flat_get = (ape.sent(c)[0] for c in STARTUP)
    assert "sessid" not in connect
    assert flat_connect["sessid"] == "SESSION"
    assert flat_connect["params"]["pipe"] == "PIPE"
    assert (flat_connect["params"]["username"], flat_connect["params"]["password"]) == (USERNAME, PASSWORD)
    assert (flat_get["params"]["list"], flat_get["params"]["extended"]) == ("all", 1)
    assert [r["chl"] for r in ape.requests[:4]] == [1, 2, 3, 4]

    assert session.status is SessionStatus.CONNECTED
    assert recorder.statuses == [SessionStatus.CONNECTING, SessionStatus.CONNECTED]
    assert session.flat_id == "100"
    assert [d.id for d in recorder.templates[0].devices] == [1, 2, 3]
    assert session.states == {1: SwitchState(True), 2: SwitchState(False), 3: CoverState(70, CoverDirection.STOPPED)}
    assert sorted(recorder.states) == sorted(session.states.items())


async def test_template_on_later_poll(session: Session, ape: FakeApe) -> None:
    responses = startup_responses()
    ape.reply("CONNECT", responses["CONNECT"])
    ape.reply("FLAT_CONNECT", responses["FLAT_CONNECT"])
    ape.reply("FLAT_GET_TEMPLATE", HEARTBEAT)
    ape.reply("CHECK", responses["FLAT_GET_TEMPLATE"])
    ape.reply("FLAT_GET", responses["FLAT_GET"])
    session.start()
    await session.wait_ready(2)

    assert ape.commands(include_check=True)[:5] == ["CONNECT", "FLAT_CONNECT", "FLAT_GET_TEMPLATE", "CHECK", "FLAT_GET"]


async def test_auth_error(session: Session, ape: FakeApe, recorder: Recorder, fake_time: FakeTime) -> None:
    responses = startup_responses()
    ape.reply("CONNECT", responses["CONNECT"])
    ape.reply("FLAT_CONNECT", [{"raw": "FLAT_ERROR", "data": {"reason": "ERROR 401"}}])
    session.start()

    with pytest.raises(AuthError) as err:
        await session.wait_ready(2)

    assert session.status is SessionStatus.AUTH_FAILED
    assert recorder.statuses == [SessionStatus.CONNECTING, SessionStatus.AUTH_FAILED]
    assert ape.commands() == ["CONNECT", "FLAT_CONNECT"]
    assert fake_time.backoffs == []
    notes = "\n".join(err.value.__notes__)
    assert "FLAT_CONNECT" in notes
    assert PASSWORD not in notes
    with pytest.raises(AuthError):
        await session.wait_ready(1)


async def test_http_error_is_retried(session: Session, ape: FakeApe, recorder: Recorder, fake_time: FakeTime) -> None:
    fail_connect(ape, 2)
    await start(session, ape)

    assert ape.commands()[:3] == ["CONNECT", "CONNECT", "CONNECT"]
    assert fake_time.backoffs == [2, 4]
    assert recorder.statuses == [SessionStatus.CONNECTING, SessionStatus.RECONNECTING, SessionStatus.CONNECTED]


async def test_back_off_is_capped_and_reset(session: Session, ape: FakeApe, fake_time: FakeTime) -> None:
    fail_connect(ape, 12)
    await start(session, ape)
    assert fake_time.backoffs == [2, 4, 8, 16, 32, 64, 128, 256, BACKOFF_MAX, BACKOFF_MAX, BACKOFF_MAX, BACKOFF_MAX]

    fake_time.sleeps.clear()
    ape.push({"raw": "ERR", "data": {"code": "004", "value": "BAD_SESSID"}})
    ape.load("synthetic_startup.json")
    await until(lambda: ape.idle and session.status is SessionStatus.CONNECTED)
    assert fake_time.backoffs == [2]


async def test_failures_logged_once_and_recovery(
    session: Session, ape: FakeApe, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)
    fail_connect(ape, 3)
    await start(session, ape)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "HTTP 403" in warnings[0].getMessage()
    assert "Entia exchanges" in warnings[0].getMessage()
    assert "Reconnected to Entia" in caplog.text


async def test_server_error_reconnects(session: Session, ape: FakeApe, recorder: Recorder) -> None:
    await start(session, ape)

    ape.push({"raw": "ERR", "data": {"code": "004", "value": "BAD_SESSID"}})
    ape.load("synthetic_startup.json")
    await until(lambda: ape.idle and session.status is SessionStatus.CONNECTED)

    assert ape.commands() == STARTUP * 2
    assert recorder.statuses == [
        SessionStatus.CONNECTING,
        SessionStatus.CONNECTED,
        SessionStatus.RECONNECTING,
        SessionStatus.CONNECTED,
    ]


async def test_wait_ready_timeout(session: Session, ape: FakeApe) -> None:
    fail_connect(ape, 10_000)
    session.start()

    with pytest.raises(TimeoutError):
        await session.wait_ready(0.05)

    await session.close()
    ape.reset()


async def test_wait_ready_fail_fast(session: Session, ape: FakeApe) -> None:
    fail_connect(ape, 1)
    ape.load("synthetic_startup.json")
    session.start()

    with pytest.raises(ConnectError, match="HTTP 403"):
        await session.wait_ready(2, fail_fast=True)

    # the session keeps retrying in the background
    await session.wait_ready(2)
    assert session.status is SessionStatus.CONNECTED


async def test_close_interrupts_long_poll(session: Session, ape: FakeApe, recorder: Recorder) -> None:
    await start(session, ape)
    await until(lambda: ape.idle)

    await asyncio.wait_for(session.close(), 0.5)

    assert session.status is SessionStatus.STOPPED
    assert recorder.statuses[-1] is SessionStatus.STOPPED
    with pytest.raises(NotConnected):
        await session.send(Command.switch(1, True))


async def test_restart_after_close(session: Session, ape: FakeApe, recorder: Recorder) -> None:
    await start(session, ape)
    await session.close()
    ape.reset()

    await start(session, ape)

    assert recorder.statuses == [
        SessionStatus.CONNECTING,
        SessionStatus.CONNECTED,
        SessionStatus.STOPPED,
        SessionStatus.CONNECTING,
        SessionStatus.CONNECTED,
    ]
    assert ape.commands() == STARTUP * 2


async def test_sessions_are_independent(session: Session, ape: FakeApe, fake_time: FakeTime) -> None:
    other_ape = FakeApe()
    other = make_session(other_ape, fake_time, Recorder())
    await start(session, ape)
    other_ape.reply("CONNECT", startup_responses()["CONNECT"])
    other_ape.reply("FLAT_CONNECT", [{"raw": "FLAT_ERROR", "data": {"reason": "ERROR 401"}}])
    other.start()
    with pytest.raises(AuthError):
        await other.wait_ready(2)

    assert other.states == {}
    assert session.status is SessionStatus.CONNECTED
    assert len(session.states) == 3
    await other.close()


# Messages


async def test_state_callbacks_only_on_change(session: Session, ape: FakeApe, recorder: Recorder) -> None:
    await start(session, ape)
    recorder.states.clear()

    ape.push(light_event(1, on=True))
    ape.push(light_event(1, on=False))
    ape.push(light_event(1, on=False))
    await until(lambda: ape.idle)

    assert recorder.states == [(1, SwitchState(False))]


async def test_ignores_unsupported_devices(session: Session, ape: FakeApe, caplog: pytest.LogCaptureFixture) -> None:
    await start(session, ape)

    assert sorted(session.states) == [1, 2, 3]
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


async def test_unknown_message_warns_once(session: Session, ape: FakeApe, caplog: pytest.LogCaptureFixture) -> None:
    await start(session, ape)

    ape.push({"raw": "SOMETHING_NEW", "data": {}})
    ape.push({"raw": "SOMETHING_NEW", "data": {}})
    await until(lambda: ape.idle)

    assert len([r for r in caplog.records if r.levelno == logging.WARNING and "SOMETHING_NEW" in r.getMessage()]) == 1
    assert session.status is SessionStatus.CONNECTED


async def test_ack_on_later_poll(session: Session, ape: FakeApe) -> None:
    await start(session, ape)

    ape.reply("DEVICE_SET", HEARTBEAT)
    ape.push(*ack(1), light_event(1, on=False))
    await session.send(Command.switch(1, False))
    await until(lambda: ape.idle)

    assert session.states[1] == SwitchState(False)


async def test_events_in_command_response(session: Session, ape: FakeApe) -> None:
    await start(session, ape)

    ape.reply("DEVICE_SET", [*ack(3), cover_event(3, 100, direction=2)])
    await session.send(Command.cover_position(3, 0))
    await until(lambda: ape.idle)

    assert session.states[3] == CoverState(0, CoverDirection.CLOSING)


# Commands


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (Command.switch(1, False), ("1", [{"id": 1, "val": 0}])),
        (Command.switch(2, True), ("2", [{"id": 1, "val": 1}])),
        (Command.cover_position(3, 25), ("3", [{"id": 1, "val": 75}])),
        (Command.cover_stop(3), ("3", [{"id": 3, "val": 0}])),
    ],
)
async def test_send(session: Session, ape: FakeApe, command: Command, expected: tuple) -> None:
    await start(session, ape)
    ape.reply("DEVICE_SET", ack(command.device_id))

    await asyncio.wait_for(session.send(command), 2)

    assert device_set(ape) == expected


async def test_send_before_ready(session: Session) -> None:
    with pytest.raises(NotConnected):
        await session.send(Command.switch(1, True))


async def test_unsent_command_is_replaced(session: Session, ape: FakeApe) -> None:
    await start(session, ape)
    ape.reply("DEVICE_SET", ack(3))

    await asyncio.gather(session.send(Command.cover_position(3, 10)), session.send(Command.cover_position(3, 20)))
    await until(lambda: ape.idle)

    assert [r["params"]["data"]["attribute"] for r in ape.sent("DEVICE_SET")] == [[{"id": 1, "val": 80}]]


async def test_failed_command_raises_and_reconnects(session: Session, ape: FakeApe) -> None:
    await start(session, ape)
    ape.reply("DEVICE_SET", ConnectionError("connection reset"))
    ape.load("synthetic_startup.json")

    with pytest.raises(ConnectError, match="connection reset"):
        await session.send(Command.switch(1, False))

    await until(lambda: ape.idle and session.status is SessionStatus.CONNECTED)
    assert ape.commands()[4:] == ["DEVICE_SET", *STARTUP]


# Polling and refresh


def gaps(ape: FakeApe) -> list[float]:
    return [b - a for a, b in pairwise(ape.times)]


def required_gap(times: list[float], index: int) -> float:
    """Pacing rule, computed independently: PACE_STEP per request started in the window before."""
    recent = [t for t in times[:index] if times[index - 1] - t < PACE_WINDOW]
    return min(len(recent), PACE_LIMIT) * PACE_STEP


def idle_for(fake_time: FakeTime, seconds: float) -> None:
    """Let the window empty while the long poll is pending."""
    fake_time.advance(seconds)


async def queue_burst(session: Session, ape: FakeApe, count: int) -> None:
    """Send commands to ``count`` different devices at once."""
    for device_id in range(101, 101 + count):
        ape.reply("DEVICE_SET", ack(device_id))
    await asyncio.gather(*(session.send(Command.switch(d, True)) for d in range(101, 101 + count)))
    await until(lambda: ape.idle)


async def test_requests_follow_pacing_rule_and_never_overlap(session: Session, ape: FakeApe) -> None:
    await start(session, ape)
    for on in (False, True, False):
        ape.push(light_event(1, on=on))
    await until(lambda: ape.idle)
    await queue_burst(session, ape, 25)

    assert len(ape.requests) >= 30
    for index in range(1, len(ape.times)):
        assert ape.times[index] - ape.times[index - 1] >= required_gap(ape.times, index) - 1e-9, index
    assert ape.max_in_flight == 1


async def test_burst_slows_down_linearly(session: Session, ape: FakeApe, fake_time: FakeTime) -> None:
    await start(session, ape)
    await until(lambda: ape.idle)
    idle_for(fake_time, PACE_WINDOW)

    await queue_burst(session, ape, 25)

    sets = [t for r, t in zip(ape.requests, ape.times, strict=True) if r["cmd"] == "DEVICE_SET"]
    assert [round(b - a, 6) for a, b in pairwise(sets)] == [round(k * PACE_STEP, 6) for k in range(1, 20)] + [
        PACE_MAX_INTERVAL
    ] * 5


async def test_quiet_window_resets_pacing(session: Session, ape: FakeApe, fake_time: FakeTime) -> None:
    await start(session, ape)
    await until(lambda: ape.idle)
    idle_for(fake_time, PACE_WINDOW)
    await queue_burst(session, ape, 25)

    idle_for(fake_time, PACE_WINDOW)
    asked = fake_time.now
    ape.reply("DEVICE_SET", ack(1))
    await session.send(Command.switch(1, False))

    [sent] = [t for r, t in zip(ape.requests, ape.times, strict=True) if r["cmd"] == "DEVICE_SET"][-1:]
    assert sent == asked  # sent without waiting


async def test_unsent_commands_dedupe_while_paced(session: Session, ape: FakeApe) -> None:
    await start(session, ape)
    await until(lambda: ape.idle)
    for device_id in (1, 2, 3):
        ape.reply("DEVICE_SET", ack(device_id))

    await asyncio.gather(
        session.send(Command.switch(1, False)),
        session.send(Command.switch(2, True)),
        session.send(Command.cover_position(3, 10)),
        session.send(Command.switch(1, True)),
    )
    await until(lambda: ape.idle)

    # one request per device; a replaced command moves to the back of the queue, as in the old client
    sets = [r for r in ape.requests if r["cmd"] == "DEVICE_SET"]
    assert [r["params"]["deviceid"] for r in sets] == ["2", "3", "1"]
    assert sets[-1]["params"]["data"]["attribute"] == [{"id": 1, "val": 1}]
    assert ape.max_in_flight == 1


async def test_command_waits_at_most_max_interval(session: Session, ape: FakeApe, fake_time: FakeTime) -> None:
    """A command interrupts the long poll and goes out as soon as the pacing allows."""
    await start(session, ape)
    await until(lambda: ape.idle)
    fake_time.advance(0.01)

    ape.reply("DEVICE_SET", ack(1))
    await session.send(Command.switch(1, False))

    assert [r["cmd"] for r in ape.requests[-2:]] == ["CHECK", "DEVICE_SET"]
    assert 0 < ape.times[-1] - ape.times[-2] <= PACE_MAX_INTERVAL
    assert ape.times[-1] - ape.times[-2] == required_gap(ape.times, len(ape.times) - 1)


async def test_interrupted_poll_finishes_before_command(session: Session, ape: FakeApe, fake_time: FakeTime) -> None:
    """With no pacing delay due, the abandoned long poll must still end before the command starts."""
    await start(session, ape)
    await until(lambda: ape.idle)
    idle_for(fake_time, PACE_WINDOW)

    ape.reply("DEVICE_SET", ack(1))
    await session.send(Command.switch(1, False))

    assert ape.max_in_flight == 1


async def test_login_is_fast_when_idle(session: Session, ape: FakeApe) -> None:
    await start(session, ape)

    assert [round(g, 6) for g in gaps(ape)[:3]] == [round(PACE_STEP * k, 6) for k in (1, 2, 3)]


async def test_keeps_polling_through_heartbeats(session: Session, ape: FakeApe) -> None:
    ape.heartbeat = 0.001
    await start(session, ape)

    await until(lambda: len(ape.sent("CHECK")) >= 60)

    assert session.status is SessionStatus.CONNECTED
    assert ape.commands() == STARTUP


async def test_hourly_refresh(session: Session, ape: FakeApe, fake_time: FakeTime) -> None:
    await start(session, ape)
    notify = startup_responses()["FLAT_GET"]
    notify[0]["data"]["msg"]["response"]["device"][0]["attribute"] = [{"id": 1, "val": 0}]

    fake_time.advance(3000)
    ape.push(*HEARTBEAT)
    await until(lambda: ape.idle)
    assert ape.commands() == STARTUP

    fake_time.advance(601)
    ape.push(*HEARTBEAT)
    ape.reply("FLAT_GET", notify)
    await until(lambda: ape.idle)

    assert ape.commands() == [*STARTUP, "FLAT_GET"]
    assert session.states[1] == SwitchState(False)


async def test_refresh_when_cover_reports_moving_too_long(session: Session, ape: FakeApe, fake_time: FakeTime) -> None:
    await start(session, ape)
    ape.push(cover_event(3, 50, direction=1))
    await until(lambda: ape.idle and session.states[3].direction is CoverDirection.OPENING)

    fake_time.advance(121)
    ape.push(*HEARTBEAT)
    ape.reply("FLAT_GET", startup_responses()["FLAT_GET"])
    await until(lambda: ape.idle)

    assert ape.commands()[-1] == "FLAT_GET"
    assert session.states[3] == CoverState(70, CoverDirection.STOPPED)
