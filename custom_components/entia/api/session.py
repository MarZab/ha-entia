"""A connection to one Entia flat: login, message pump, commands and reconnects.

The APE server is a per-session message pipe: every request returns whatever is queued for the
session, not necessarily the answer to that request (the template can arrive on a later poll, acks
arrive late, events ride along on command responses). All responses therefore go through one
dispatcher regardless of the request that returned them.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable, Coroutine
from enum import StrEnum
from typing import Any

from .errors import AuthError, ConnectError, NotConnected, ProtocolError, ServerError, Stopped
from .messages import (
    ERROR_MESSAGES,
    IGNORED_MESSAGES,
    command_params,
    encode_request,
    parse_states,
    parse_template,
)
from .models import Command, CoverDirection, CoverState, DeviceState, Template
from .transport import POLL_TIMEOUT, REQUEST_TIMEOUT, Transport

_LOGGER = logging.getLogger(__name__)

BACKOFF_INITIAL = 2
BACKOFF_MAX = 300
# Pacing, for every request: one at a time, and the gap since the previous request grows
# linearly with the requests started in the last PACE_WINDOW seconds, PACE_STEP each, up to
# PACE_MAX_INTERVAL (reached after PACE_LIMIT requests). Idle -> instant; busy -> 1 request/s.
PACE_WINDOW = 20.0
PACE_LIMIT = 20
PACE_MAX_INTERVAL = 1.0
PACE_STEP = PACE_MAX_INTERVAL / PACE_LIMIT
FULL_REFRESH_INTERVAL = 3600
STUCK_COVER_AFTER = 120


class SessionStatus(StrEnum):
    STOPPED = "stopped"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    AUTH_FAILED = "auth_failed"


class Session:
    """Keeps one flat connected and its device states current."""

    def __init__(
        self,
        transport: Transport,
        username: str,
        password: str,
        *,
        on_status: Callable[[SessionStatus], None] | None = None,
        on_template: Callable[[Template], None] | None = None,
        on_state: Callable[[int, DeviceState], None] | None = None,
        create_task: Callable[[Coroutine[Any, Any, None]], asyncio.Task[None]] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._transport = transport
        self._username = username
        self._password = password
        self._on_status = on_status
        self._on_template = on_template
        self._on_state = on_state
        self._create_task = create_task
        self._sleep = sleep
        self._clock = clock
        self._wall_clock = wall_clock

        self.status = SessionStatus.STOPPED
        self.template: Template | None = None
        self.flat_id: str | None = None
        self._states: dict[int, DeviceState] = {}
        self._updated: dict[int, float] = {}

        self._task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self._ended = asyncio.Event()
        self._error: Exception | None = None
        self._failure_waiters: set[asyncio.Future[None]] = set()
        self._wake = asyncio.Event()
        self._commands: dict[int, tuple[Command, asyncio.Future[None]]] = {}
        self._requests: dict[str, dict[str, Any]] = {}
        self._recent: deque[float] = deque()
        self._last_refresh: float | None = None
        self._template_received = False
        self._failures = 0
        self._backoff = BACKOFF_INITIAL
        self._unknown: set[str] = set()

        self._chl = 0
        self._sessid: str | None = None
        self._pipe: str | None = None

    # Public API

    @property
    def transport(self) -> Transport:
        return self._transport

    @property
    def states(self) -> dict[int, DeviceState]:
        """Last known state of each declared device."""
        return dict(self._states)

    def start(self) -> None:
        """Connect in the background; reconnects until closed or the login is rejected."""
        if self._task is not None and not self._task.done():
            return
        self._ended.clear()
        self._error = None
        self._failures = 0
        self._backoff = BACKOFF_INITIAL
        create_task = self._create_task or asyncio.get_running_loop().create_task
        self._task = create_task(self._run())

    async def close(self) -> None:
        """Disconnect now, abandoning any request in flight."""
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._fail_commands(Stopped("Session closed"))
        self._ready.clear()
        self._ended.set()
        self._set_status(SessionStatus.STOPPED)

    async def wait_ready(self, timeout: float, *, fail_fast: bool = False) -> None:
        """Wait until connected with fresh states.

        Raises AuthError if the login is rejected, Stopped if the session ends and TimeoutError.
        With ``fail_fast`` the first connection failure is raised instead of waiting for a retry
        (the session keeps retrying until closed).
        """
        if self.status is SessionStatus.CONNECTED:
            return
        if self._ended.is_set():
            raise self._error or Stopped("Session is not running")
        failure: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        if fail_fast:
            self._failure_waiters.add(failure)
        waiters = {asyncio.ensure_future(self._ready.wait()), asyncio.ensure_future(self._ended.wait()), failure}
        try:
            await asyncio.wait(waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        finally:
            self._failure_waiters.discard(failure)
            for waiter in waiters:
                waiter.cancel()
        if self._ready.is_set():
            return
        if self._ended.is_set():
            raise self._error or Stopped("Session closed")
        if failure.done() and not failure.cancelled() and failure.exception() is not None:
            raise failure.exception()
        raise TimeoutError("Entia did not become ready in time")

    async def send(self, command: Command) -> None:
        """Send a command; returns once the server accepted the request.

        A newer command for the same device replaces one that was not sent yet.
        """
        if self.status is not SessionStatus.CONNECTED:
            raise NotConnected(f"Not connected (status: {self.status})")
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        replaced = self._commands.pop(command.device_id, None)
        if replaced is not None and not replaced[1].done():
            replaced[1].set_result(None)
        self._commands[command.device_id] = (command, future)
        self._wake.set()
        await future

    # Connection

    async def _run(self) -> None:
        try:
            while True:
                self._set_status(SessionStatus.RECONNECTING if self._failures else SessionStatus.CONNECTING)
                try:
                    await self._login()
                    await self._pump()
                except AuthError as err:
                    err.add_note(self._transport.format_history())
                    _LOGGER.error("Entia rejected the login for this Entia ID and PIN")
                    self._error = err
                    self._fail_commands(err)
                    self._set_status(SessionStatus.AUTH_FAILED)
                    return
                except (ConnectError, ProtocolError, ServerError) as err:
                    self._ready.clear()
                    self._fail_commands(err)
                    for waiter in self._failure_waiters:
                        if not waiter.done():
                            waiter.set_exception(err)
                    self._failures += 1
                    if self._failures == 1:
                        _LOGGER.warning(
                            "Connection to Entia failed (%s), retrying in %ss\n%s",
                            err,
                            self._backoff,
                            self._transport.format_history(),
                        )
                    else:
                        _LOGGER.debug("Connection to Entia failed again (%s), retrying in %ss", err, self._backoff)
                    self._set_status(SessionStatus.RECONNECTING)
                    await self._sleep(self._backoff)
                    self._backoff = min(self._backoff * 2, BACKOFF_MAX)
        finally:
            self._ready.clear()
            self._ended.set()

    async def _login(self) -> None:
        self._chl = 0
        self._sessid = None
        self._pipe = None
        self._template_received = False
        self._requests.clear()

        for message in await self._request("CONNECT", {"name": None}):
            raw = message.get("raw")
            data = message.get("data") or {}
            if raw == "LOGIN" and self._sessid is None:
                self._sessid = data.get("sessid")
            elif raw == "IDENT" and self._pipe is None:
                self._pipe = (data.get("user") or {}).get("pubid")
            elif raw in ERROR_MESSAGES:
                raise ServerError(f"{raw} during connect: {data}")
        if not self._sessid or not self._pipe:
            raise ProtocolError("Connect did not return a session")

        flat_id = None
        for message in await self._request("FLAT_CONNECT", {"username": self._username, "password": self._password}):
            raw = message.get("raw")
            data = message.get("data") or {}
            if raw == "FLAT_LOGIN":
                flat_id = str(data.get("flatid"))
            elif raw == "FLAT_ERROR" and data.get("reason") == "ERROR 401":
                raise AuthError("Invalid Entia ID or PIN")
            elif raw in ERROR_MESSAGES:
                raise ServerError(f"{raw} during login: {data}")
        if not flat_id:
            raise ProtocolError("Login did not return a flat")
        self.flat_id = flat_id
        self._enqueue("FLAT_GET_TEMPLATE", {})

    async def _pump(self) -> None:
        while True:
            self._schedule_refresh()
            # pace before choosing, so commands arriving meanwhile go first and can still be replaced
            await self._pace()
            if self._commands:
                device_id = next(iter(self._commands))
                command, future = self._commands.pop(device_id)
                try:
                    messages = await self._request("DEVICE_SET", command_params(command))
                except Exception as err:
                    if not future.done():
                        future.set_exception(err)
                    raise
                if not future.done():
                    future.set_result(None)
            elif self._requests:
                cmd = next(iter(self._requests))
                messages = await self._request(cmd, self._requests.pop(cmd))
            else:
                polled = await self._poll()
                if polled is None:
                    continue
                messages = polled
            self._dispatch(messages)

    async def _poll(self) -> list[dict[str, Any]] | None:
        """One long poll; None if interrupted because there is something to send.

        The interrupted request is abandoned before returning, so requests never overlap.
        """
        self._wake.clear()
        if self._commands or self._requests:
            return None
        poll = asyncio.ensure_future(self._request("CHECK", {}, POLL_TIMEOUT))
        woken = asyncio.ensure_future(self._wake.wait())
        try:
            done, _ = await asyncio.wait({poll, woken}, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            poll.cancel()
            raise
        finally:
            woken.cancel()
        if poll in done:
            return poll.result()
        poll.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await poll
        return None

    def _pace_delay(self) -> float:
        """Seconds until the next request may start."""
        now = self._clock()
        while self._recent and now - self._recent[0] >= PACE_WINDOW:
            self._recent.popleft()
        if not self._recent:
            return 0.0
        gap = min(len(self._recent), PACE_LIMIT) * PACE_STEP
        return max(0.0, self._recent[-1] + gap - now)

    async def _pace(self) -> None:
        """Wait until the next request may start."""
        delay = self._pace_delay()
        if delay > 0:
            await self._sleep(delay)

    async def _request(
        self, cmd: str, params: dict[str, Any], timeout: float = REQUEST_TIMEOUT
    ) -> list[dict[str, Any]]:
        await self._pace()
        self._recent.append(self._clock())
        self._chl += 1
        message = encode_request(cmd, self._chl, self._sessid, self._pipe, params, self._wall_clock())
        return await self._transport.request(message, timeout)

    def _enqueue(self, cmd: str, params: dict[str, Any]) -> None:
        self._requests[cmd] = params
        self._wake.set()

    def _schedule_refresh(self) -> None:
        if self.status is not SessionStatus.CONNECTED or self._last_refresh is None:
            return
        now = self._clock()
        age = now - self._last_refresh
        stuck = age > STUCK_COVER_AFTER and any(
            isinstance(state, CoverState)
            and state.direction is not CoverDirection.STOPPED
            and now - self._updated.get(device_id, now) > STUCK_COVER_AFTER
            for device_id, state in self._states.items()
        )
        if age > FULL_REFRESH_INTERVAL or stuck:
            self._refresh()

    def _refresh(self) -> None:
        self._last_refresh = self._clock()
        self._enqueue("FLAT_GET", {"list": "all", "extended": 1})

    # Messages

    def _dispatch(self, messages: list[dict[str, Any]]) -> None:
        for message in messages:
            raw = message.get("raw")
            if raw in ERROR_MESSAGES:
                raise ServerError(f"{raw}: {message.get('data')}")
            if raw == "FLAT_TEMPLATE":
                self._apply_template(message)
            elif raw in ("FLAT_NOTIFY", "FLAT_EVENT"):
                self._apply_states(message)
                if raw == "FLAT_NOTIFY" and self._template_received and self.status is not SessionStatus.CONNECTED:
                    self._connected()
            elif raw not in IGNORED_MESSAGES and raw not in self._unknown:
                self._unknown.add(str(raw))
                _LOGGER.warning("Unexpected message from Entia: %s", message)

    def _apply_template(self, message: dict[str, Any]) -> None:
        try:
            template = parse_template(self.flat_id or "", message["data"]["msg"])
        except (KeyError, TypeError) as err:
            raise ProtocolError("Malformed template") from err
        self.template = template
        self._template_received = True
        self._callback(self._on_template, template)
        self._refresh()

    def _apply_states(self, message: dict[str, Any]) -> None:
        if self.template is None or not self._template_received:
            return
        msg = (message.get("data") or {}).get("msg")
        now = self._clock()
        for device_id, state in parse_states(self.template, msg).items():
            self._updated[device_id] = now
            if self._states.get(device_id) != state:
                self._states[device_id] = state
                self._callback(self._on_state, device_id, state)

    def _connected(self) -> None:
        if self._failures:
            _LOGGER.info("Reconnected to Entia")
        else:
            _LOGGER.info("Connected to Entia")
        self._failures = 0
        self._backoff = BACKOFF_INITIAL
        self._ready.set()
        self._set_status(SessionStatus.CONNECTED)

    # Helpers

    def _set_status(self, status: SessionStatus) -> None:
        if status is self.status:
            return
        self.status = status
        self._callback(self._on_status, status)

    def _fail_commands(self, err: Exception) -> None:
        commands, self._commands = self._commands, {}
        for _, future in commands.values():
            if not future.done():
                future.set_exception(err)

    @staticmethod
    def _callback(callback: Callable[..., None] | None, *args: Any) -> None:
        if callback is None:
            return
        try:
            callback(*args)
        except Exception:
            _LOGGER.exception("Error in Entia callback")
