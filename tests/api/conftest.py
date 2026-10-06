"""Fixtures for the Home Assistant free api package."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest

from custom_components.entia.api import DeviceState, Session, SessionStatus, Template, Transport
from custom_components.entia.api.session import PACE_MAX_INTERVAL

from ..conftest import PASSWORD, USERNAME
from ..fake_ape import FakeApe


class FakeTime:
    """Monotonic clock and sleep: sleeping advances the clock instantly and is recorded."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    @property
    def backoffs(self) -> list[float]:
        """Reconnect back-off sleeps, without the pacing between requests."""
        return [s for s in self.sleeps if s > PACE_MAX_INTERVAL]

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.now += delay
        await asyncio.sleep(0)


@dataclass
class Recorder:
    """Everything a session reported through its callbacks."""

    statuses: list[SessionStatus] = field(default_factory=list)
    templates: list[Template] = field(default_factory=list)
    states: list[tuple[int, DeviceState]] = field(default_factory=list)


@pytest.fixture
def fake_time() -> FakeTime:
    return FakeTime()


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


def make_session(ape: FakeApe, fake_time: FakeTime, recorder: Recorder, password: str = PASSWORD) -> Session:
    ape.clock = fake_time.clock
    return Session(
        Transport(ape.http_client()),
        USERNAME,
        password,
        on_status=recorder.statuses.append,
        on_template=recorder.templates.append,
        on_state=lambda device_id, state: recorder.states.append((device_id, state)),
        sleep=fake_time.sleep,
        clock=fake_time.clock,
    )


@pytest.fixture
async def session(ape: FakeApe, fake_time: FakeTime, recorder: Recorder) -> AsyncIterator[Session]:
    """A session against the fake server, closed after the test."""
    s = make_session(ape, fake_time, recorder)
    yield s
    await asyncio.wait_for(s.close(), 2)


async def start(session: Session, ape: FakeApe, fixture: str = "synthetic_startup.json") -> None:
    ape.load(fixture)
    session.start()
    await session.wait_ready(2)
