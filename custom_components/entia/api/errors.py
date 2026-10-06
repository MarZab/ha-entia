"""Errors raised by the Entia API."""

from __future__ import annotations


class EntiaError(Exception):
    """Base class for Entia errors."""


class AuthError(EntiaError):
    """The Entia ID or PIN was rejected."""


class ConnectError(EntiaError):
    """The server could not be reached or answered with an HTTP error."""


class ProtocolError(EntiaError):
    """The server sent something the client does not understand."""


class ServerError(EntiaError):
    """The server reported an error or ended the session."""


class NotConnected(EntiaError):
    """A command was sent while the session is not connected."""


class Stopped(EntiaError):
    """The session was closed."""
