"""Real traffic (tests/fixtures/traffic) replayed through the api session."""

from __future__ import annotations

from collections import Counter

from custom_components.entia.api import (
    Command,
    CoverDirection,
    CoverState,
    DeviceKind,
    Session,
    SessionStatus,
    SwitchState,
)

from ..conftest import until
from ..fake_ape import FakeApe
from ..test_recorded import RECORDING, recorded
from .conftest import start


def command(session: Session, device_set: dict) -> Command:
    """The Command that produces a recorded DEVICE_SET."""
    device_id = int(device_set["params"]["deviceid"])
    [attribute] = device_set["params"]["data"]["attribute"]
    kind = session.template.device(device_id).kind
    if kind is DeviceKind.COVER:
        if attribute["id"] == 3:
            return Command.cover_stop(device_id)
        return Command.cover_position(device_id, 100 - attribute["val"])
    return Command.switch(device_id, bool(attribute["val"]))


async def test_startup(session: Session, ape: FakeApe) -> None:
    await start(session, ape, RECORDING)

    assert session.flat_id == "100"
    assert Counter(d.kind for d in session.template.devices) == {
        DeviceKind.LIGHT: 17,
        DeviceKind.FAN: 2,
        DeviceKind.COVER: 4,
    }
    assert set(session.states) == {d.id for d in session.template.devices}
    covers = [s for s in session.states.values() if isinstance(s, CoverState)]
    assert sorted(c.position for c in covers) == [0, 0, 2, 2]


async def test_replay_commands(session: Session, ape: FakeApe) -> None:
    await start(session, ape, RECORDING)
    device_sets = [x["request"] for x in recorded(RECORDING) if x["request"]["cmd"] == "DEVICE_SET"]

    for i, device_set in enumerate(device_sets):
        await until(lambda i=i: len(ape.sent("DEVICE_SET")) == i and ape.pending[:1] == ["DEVICE_SET"])
        await session.send(command(session, device_set))
    await until(lambda: ape.idle)

    def sent(request: dict) -> tuple:
        return request["params"]["deviceid"], request["params"]["data"]

    assert [sent(r) for r in ape.sent("DEVICE_SET")] == [sent(d) for d in device_sets]
    assert session.status is SessionStatus.CONNECTED
    assert session.states[36919] == SwitchState(True)
    assert session.states[17241] == SwitchState(False)
    assert session.states[28106] == SwitchState(False)
    assert session.states[17232] == CoverState(1, CoverDirection.STOPPED)
