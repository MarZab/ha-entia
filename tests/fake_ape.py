"""In-memory stand-in for the Entia APE long-polling server.

The client talks to ``https://ape.entia.si/2/?<json>`` with plain GET requests and
gets back ``Ape.transport.read('<json>')``. ``FakeApe`` replaces ``httpx`` inside the
client module and answers from a script of exchanges:

* ``reply(cmd, response)`` queues a response for the next request with that ``cmd``
* ``push(*events)`` queues events delivered to the next ``CHECK`` long poll

The real server is a per-session message pipe: a request returns whatever is queued, which
is not necessarily the reply to that request (see docs/plan.md, protocol notes). Scripts
model that by queuing the recorded response for whichever command received it.

Scripts can also be loaded from traffic fixtures in ``tests/fixtures/traffic``: hand-written
JSON, or a Home Assistant debug log of the integration (the client logs every exchange).
A ``CHECK`` with nothing scripted blocks until something is pushed, the client cancels it
(``aclose``) or the fake is closed. With ``heartbeat`` set it instead answers with a
``FLAT_HEARTBEAT`` after that many seconds, like the real server does every ~3-8 s.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections import deque
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Self
from urllib.parse import unquote

FIXTURES = Path(__file__).parent / "fixtures" / "traffic"

HEARTBEAT = [{"raw": "FLAT_HEARTBEAT", "data": 0}]

# Debug log line of an exchange, from the old client or the api transport, e.g.
# "... DEBUG (MainThread) [custom_components.entia.api.transport] Exchange {...}"
EXCHANGE_LINE = re.compile(r"\[custom_components\.entia\.(?:client|api\.transport)\] Exchange (\{.*\})$", re.MULTILINE)


def wrap(response: list[dict]) -> str:
    """Encode a response the way the APE server does."""
    return "Ape.transport.read('" + json.dumps(response, separators=(",", ":")) + "')"


def parse_request(url: str) -> dict[str, Any]:
    """Decode the single command carried in a request URL."""
    query = unquote(url.split("?", 1)[1])
    [message] = json.loads(query)
    return message


class FakeResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code


class FakeApe:
    def __init__(self, heartbeat: float | None = None) -> None:
        self.heartbeat = heartbeat
        self.requests: list[dict[str, Any]] = []
        self.unexpected: list[dict[str, Any]] = []
        self._script: deque[tuple[str, Any, int]] = deque()
        self._wake = asyncio.Event()
        self._closed = False
        self._polling = 0
        # request start times on ``clock`` (set it to the session's test clock) and overlap tracking
        self.clock: Callable[[], float] = lambda: 0.0
        self.times: list[float] = []
        self.in_flight = 0
        self.max_in_flight = 0

    # Scripting

    def reply(self, cmd: str, response: list[dict] | str | Exception, status: int = 200) -> FakeApe:
        """Answer the next ``cmd`` request with a response list, raw text or an exception."""
        self._script.append((cmd, response, status))
        self._wake.set()
        return self

    def push(self, *events: dict) -> FakeApe:
        """Deliver events to the next CHECK long poll."""
        return self.reply("CHECK", list(events))

    def load(self, name: str) -> FakeApe:
        """Queue every exchange from a traffic fixture.

        ``.json`` files hold ``{"exchanges": [...]}``. Anything else is read as a Home
        Assistant debug log, taking the ``Exchange {...}`` lines the client writes.
        """
        path = FIXTURES / name
        if path.suffix == ".json":
            exchanges = json.loads(path.read_text())["exchanges"]
        else:
            exchanges = [json.loads(m.group(1)) for m in EXCHANGE_LINE.finditer(path.read_text())]
        for exchange in exchanges:
            if exchange["response"] is None:
                continue  # request was cancelled or failed while recording
            self.reply(exchange["request"]["cmd"], exchange["response"])
        return self

    @property
    def pending(self) -> list[str]:
        """Commands still waiting in the script."""
        return [cmd for cmd, _, _ in self._script]

    @property
    def idle(self) -> bool:
        """Script consumed and the client is back in a long poll, so every response was processed."""
        return not self._script and self._polling > 0

    def commands(self, include_check: bool = False) -> list[str]:
        """Commands received so far, in order."""
        return [r["cmd"] for r in self.requests if include_check or r["cmd"] != "CHECK"]

    def sent(self, cmd: str) -> list[dict[str, Any]]:
        """Received requests with the given command."""
        return [r for r in self.requests if r["cmd"] == cmd]

    def reset(self) -> None:
        """Drop what is left of the script and reopen, for a new session after a close."""
        self._script.clear()
        self._closed = False

    def close(self) -> None:
        """Release blocked long polls so a stopping client can exit."""
        self._closed = True
        self._wake.set()

    # Server

    async def handle(self, url: str) -> FakeResponse:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            return await self._handle(url)
        finally:
            self.in_flight -= 1

    async def _handle(self, url: str) -> FakeResponse:
        message = parse_request(url)
        self.requests.append(message)
        self.times.append(self.clock())
        cmd = message["cmd"]

        while True:
            self._wake.clear()
            if self._script and self._script[0][0] == cmd:
                _, response, status = self._script.popleft()
                if isinstance(response, Exception):
                    raise response
                if isinstance(response, str):
                    return FakeResponse(response, status)
                return FakeResponse(wrap(response), status)
            if cmd != "CHECK":
                self.unexpected.append(message)
                return FakeResponse(wrap(HEARTBEAT))
            if self._closed:
                return FakeResponse(wrap(HEARTBEAT))
            self._polling += 1
            try:
                if self.heartbeat is None:
                    await self._wake.wait()
                else:
                    try:
                        async with asyncio.timeout(self.heartbeat):
                            await self._wake.wait()
                    except TimeoutError:
                        return FakeResponse(wrap(HEARTBEAT))
            finally:
                self._polling -= 1

    def http_client(self) -> FakeHttpClient:
        """HTTP client to inject into ``api.Transport``; cancelling a request abandons it."""
        return FakeHttpClient(self)

    def httpx(self) -> SimpleNamespace:
        """Replacement for the ``httpx`` module used by the client."""
        ape = self

        class AsyncClient:
            def __init__(self, *args, **kwargs) -> None:
                self._cancelled = asyncio.Event()

            async def __aenter__(self) -> Self:
                return self

            async def __aexit__(self, *exc) -> None:
                self._cancelled.set()

            async def aclose(self) -> None:
                self._cancelled.set()

            async def get(self, url: str, **kwargs) -> FakeResponse:
                request = asyncio.ensure_future(ape.handle(url))
                cancelled = asyncio.ensure_future(self._cancelled.wait())
                done, _ = await asyncio.wait({request, cancelled}, return_when=asyncio.FIRST_COMPLETED)
                cancelled.cancel()
                if request in done:
                    return request.result()
                request.cancel()
                raise ConnectionError("request closed by client")

        return SimpleNamespace(AsyncClient=AsyncClient)


class FakeHttpClient:
    def __init__(self, ape: FakeApe) -> None:
        self._ape = ape

    async def get(self, url: str, *, headers: dict[str, str], timeout: float) -> FakeResponse:
        return await self._ape.handle(url)
