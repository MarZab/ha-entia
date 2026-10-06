"""Shared fixtures."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from types import SimpleNamespace

import pytest

from custom_components.entia import client as client_module
from custom_components.entia.client import EntiaClient

from .fake_ape import FakeApe

USERNAME = "test-entia-id"
PASSWORD = "test-pin"


@pytest.fixture(autouse=True)
def isolate_client_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give each test fresh copies of EntiaClient's class-level mutable state.

    EntiaClient keeps ``data``, ``_signals``, ``_callbacks`` and ``_queue`` on the class,
    so they leak between instances (see docs/plan.md, "Bugs"). Without this, state would
    also leak between tests.
    """
    monkeypatch.setattr(EntiaClient, "data", {})
    monkeypatch.setattr(EntiaClient, "_signals", {})
    monkeypatch.setattr(EntiaClient, "_callbacks", {})
    monkeypatch.setattr(EntiaClient, "_queue", [])


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the client's rate limiting and reconnect back-off delays."""

    async def sleep(delay: float, result=None):
        await asyncio.sleep(0)
        return result

    fake_asyncio = SimpleNamespace(**{k: getattr(asyncio, k) for k in dir(asyncio) if not k.startswith("__")})
    fake_asyncio.sleep = sleep
    monkeypatch.setattr(client_module, "asyncio", fake_asyncio)


@pytest.fixture
def ape(monkeypatch: pytest.MonkeyPatch) -> FakeApe:
    """Fake Entia server wired into the client module."""
    fake = FakeApe()
    monkeypatch.setattr(client_module, "httpx", fake.httpx())
    yield fake
    fake.close()
    assert fake.unexpected == [], f"unscripted requests: {fake.unexpected}"


@pytest.fixture
async def client(ape: FakeApe) -> AsyncIterator[EntiaClient]:
    """A client that is stopped at the end of the test."""
    c = EntiaClient(USERNAME, PASSWORD)
    yield c
    ape.close()
    await asyncio.wait_for(c.stop(), 5)


async def until(condition: Callable[[], bool], timeout: float = 2) -> None:
    """Wait for the client task to reach a condition."""
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.001)
