"""Entia APE protocol client, independent of Home Assistant."""

from .errors import AuthError, ConnectError, EntiaError, NotConnected, ProtocolError, ServerError, Stopped
from .models import (
    Command,
    CoverDirection,
    CoverState,
    DeclaredDevice,
    DeviceKind,
    DeviceState,
    SwitchState,
    Template,
)
from .session import Session, SessionStatus
from .transport import Transport

__all__ = [
    "AuthError",
    "Command",
    "ConnectError",
    "CoverDirection",
    "CoverState",
    "DeclaredDevice",
    "DeviceKind",
    "DeviceState",
    "EntiaError",
    "NotConnected",
    "ProtocolError",
    "ServerError",
    "Session",
    "SessionStatus",
    "Stopped",
    "SwitchState",
    "Template",
    "Transport",
]
