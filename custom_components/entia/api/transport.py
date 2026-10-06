"""HTTP transport to the Entia APE server, with exchange logging and history."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Protocol

from .errors import ConnectError
from .messages import decode_response

_LOGGER = logging.getLogger(__name__)

URL = "https://ape.entia.si/2/"
# Same headers as the dostop.entia.si web app; trimming them needs a capture to verify (docs/plan.md)
HEADERS = {
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Host": "ape.entia.si",
    "Pragma": "no-cache",
    "Referer": "https://dostop.entia.si/",
    "sec-ch-ua": '"Google Chrome";v="89", "Chromium";v="89", ";Not A Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "Sec-Fetch-Dest": "script",
    "Sec-Fetch-Mode": "no-cors",
    "Sec-Fetch-Site": "same-site",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_4) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/89.0.4389.82 Safari/537.36"
    ),
}
REQUEST_TIMEOUT = 30
POLL_TIMEOUT = 120

HISTORY_SIZE = 10
HISTORY_MAX_CHARS = 2000

_SECRETS = re.compile(r'"(username|password|login_hash|sessionkey|sessid|pubid|pipe)":\s*"[^"]*"')


class HttpClient(Protocol):
    """The part of ``httpx.AsyncClient`` the transport uses."""

    async def get(self, url: str, *, headers: dict[str, str], timeout: float) -> Any: ...


def redact(text: str) -> str:
    """Remove credentials and session identifiers from a logged message."""
    return _SECRETS.sub(r'"\1":"**REDACTED**"', text)


@dataclass
class Exchange:
    """One request/response, redacted, for logs and error reports."""

    time: float
    request: str
    status: int | None = None
    response: str | None = None
    error: str | None = None
    duration: float | None = None

    @property
    def cmd(self) -> str:
        match = re.search(r'"cmd":"(\w+)"', self.request)
        return match.group(1) if match else "?"


class Transport:
    """Sends APE requests and keeps the most recent exchanges."""

    def __init__(self, client: HttpClient, url: str = URL) -> None:
        self._client = client
        self._url = url
        self._history: deque[Exchange] = deque(maxlen=HISTORY_SIZE)

    async def request(self, message: str, timeout: float = REQUEST_TIMEOUT) -> list[dict[str, Any]]:
        """Send an encoded request and return the messages in the response.

        Raises ConnectError for network and HTTP errors and ProtocolError for bodies that are not
        APE responses. Cancelling the call abandons the request.
        """
        exchange = Exchange(time=time.time(), request=redact(message))
        self._history.append(exchange)
        started = time.monotonic()
        try:
            response = await self._client.get(f"{self._url}?{message}", headers=HEADERS, timeout=timeout)
        except asyncio.CancelledError:
            exchange.error = "cancelled"
            raise
        except Exception as err:
            exchange.error = repr(err)
            raise ConnectError(f"Request failed: {err!r}") from err
        finally:
            exchange.duration = time.monotonic() - started
            if exchange.error is not None:
                self._log(exchange)

        exchange.status = response.status_code
        exchange.response = redact(response.text)
        self._log(exchange)
        if response.status_code != 200:
            raise ConnectError(f"HTTP {response.status_code}: {response.text[:200]!r}")
        return decode_response(response.text)

    @property
    def history(self) -> list[Exchange]:
        """Most recent exchanges, oldest first."""
        return list(self._history)

    def format_history(self) -> str:
        """The most recent exchanges, for an error report."""

        def clip(text: str) -> str:
            if len(text) <= HISTORY_MAX_CHARS:
                return text
            return text[:HISTORY_MAX_CHARS] + f"... ({len(text) - HISTORY_MAX_CHARS} more chars)"

        if not self._history:
            return "No Entia exchanges recorded"
        lines = [f"Last {len(self._history)} Entia exchanges (oldest first):"]
        for i, exchange in enumerate(self._history, 1):
            when = time.strftime("%H:%M:%S", time.localtime(exchange.time))
            duration = f"{exchange.duration:.2f}s" if exchange.duration is not None else "pending"
            status = f" HTTP {exchange.status}" if exchange.status is not None else ""
            lines.append(f"#{i} {when} {exchange.cmd} ({duration}){status}")
            lines.append(f"  > {clip(exchange.request)}")
            if exchange.response is not None:
                lines.append(f"  < {clip(exchange.response)}")
            if exchange.error is not None:
                lines.append(f"  ! {exchange.error}")
        return "\n".join(lines)

    @staticmethod
    def _log(exchange: Exchange) -> None:
        """Log an exchange as one JSON line; tests replay these from debug logs."""
        if not _LOGGER.isEnabledFor(logging.DEBUG):
            return
        try:
            request: Any = json.loads(exchange.request)[0]
        except (ValueError, IndexError, KeyError):
            request = exchange.request
        response: Any = None
        if exchange.response is not None:
            try:
                response = decode_response(exchange.response)
            except Exception:  # keep the raw text of anything malformed
                response = exchange.response
        _LOGGER.debug(
            "Exchange %s",
            json.dumps(
                {
                    "request": request,
                    "response": response,
                    "status": exchange.status,
                    "duration": round(exchange.duration, 3) if exchange.duration is not None else None,
                    "error": exchange.error,
                },
                separators=(",", ":"),
                ensure_ascii=False,
            ),
        )
