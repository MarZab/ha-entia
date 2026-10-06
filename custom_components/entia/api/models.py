"""Data models for the Entia API."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class DeviceKind(StrEnum):
    """Supported device kinds declared in the flat template."""

    LIGHT = "light"
    FAN = "fan"
    COVER = "cover"


class CoverDirection(StrEnum):
    STOPPED = "stopped"
    OPENING = "opening"
    CLOSING = "closing"


@dataclass(frozen=True)
class DeclaredDevice:
    """A device the template declares, identified by its element id."""

    id: int
    kind: DeviceKind
    label: str


@dataclass(frozen=True)
class Template:
    """The flat and the supported devices it declares."""

    flat_id: str
    devices: tuple[DeclaredDevice, ...]

    def device(self, device_id: int) -> DeclaredDevice | None:
        return next((d for d in self.devices if d.id == device_id), None)

    def as_dict(self) -> dict[str, Any]:
        return {
            "flat_id": self.flat_id,
            "devices": [{"id": d.id, "kind": d.kind.value, "label": d.label} for d in self.devices],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Template:
        return cls(
            flat_id=data["flat_id"],
            devices=tuple(DeclaredDevice(d["id"], DeviceKind(d["kind"]), d["label"]) for d in data["devices"]),
        )


@dataclass(frozen=True)
class SwitchState:
    """State of a light or fan."""

    on: bool


@dataclass(frozen=True)
class CoverState:
    """State of a cover; position is how far it is open (0 = closed, 100 = open)."""

    position: int
    direction: CoverDirection


type DeviceState = SwitchState | CoverState


@dataclass(frozen=True)
class Command:
    """Set one attribute of one device."""

    device_id: int
    attribute: int
    value: int

    @classmethod
    def switch(cls, device_id: int, on: bool) -> Command:
        return cls(device_id, 1, 1 if on else 0)

    @classmethod
    def cover_position(cls, device_id: int, position: int) -> Command:
        """Move a cover to a position (0 = closed, 100 = open)."""
        return cls(device_id, 1, 100 - clamp_percent(position))

    @classmethod
    def cover_stop(cls, device_id: int) -> Command:
        return cls(device_id, 3, 0)


def clamp_percent(value: int) -> int:
    return max(0, min(100, value))
