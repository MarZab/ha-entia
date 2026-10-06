# Architecture (refactor)

The integration is rebuilt around a Home Assistant–free protocol library (`api/`) and a hub that
owns the connection policy. This replaces `client/__init__.py` and the per-platform glue.

## Requirements

| # | Requirement |
|---|---|
| R1 | Light, fan and cover entities for the devices **declared in the Entia template**, nothing else. Devices that only appear in state dumps (dimmers, the extra switch) get no entities. Behaviour of existing entities stays the same. |
| R2 | **Entity IDs and history survive the upgrade.** |
| R3 | A **status** entity showing the connection state. |
| R4 | A **connection** switch, acting as a master switch: off = never connect. |
| R5 | An **inactivity timeout** option (minutes): close the connection when no entity changed for that long. |

Decisions (from review):

- After an inactivity close, device entities **stay available with their last known state**; using one reconnects, refreshes states and sends the command.
- **Activity** = any real state change: a command from Home Assistant, or a changed value pushed by Entia (wall switch, official app). Heartbeats, acks and refreshes that change nothing don't count.
- Connection switch off: no connection, commands fail with an error, device entities unavailable.
- Cover tilt buttons keep nudging the position by ±1 % (unchanged behaviour).
- A failed on-demand reconnect goes to `reconnecting` (back-off retries), not back to `idle`.

## Keeping entity IDs (R2)

Home Assistant maps entities to their `entity_id` (and the recorder's history) through the entity
registry, keyed by platform + `unique_id`. The refactor must keep, byte for byte:

| What | Value today | Rule |
|---|---|---|
| Platform domain | `entia` | unchanged |
| Entity domains | `light` (type 4/1), `fan` (type 4/4), `cover` (type 6) | a device never changes domain |
| Entity `unique_id` | `"{flat_id}-{element_id}"`, e.g. `"297-36919"` | element id from the template, as int formatted in base 10 |
| Device identifier | `("entia", "{flat_id}")`, name `Entia`, model `Entia`, manufacturer `Entia Living` | unchanged, so existing device and area assignments stay |
| Entity naming | `has_entity_name=True`, name = template label | only affects entities created after the upgrade, which then get the same `entia_<label>` pattern |
| Config entry | `unique_id` = lowercased Entia ID, data `username`/`password`, `VERSION = 1` | no data migration needed |

New entities use ids that cannot collide with element ids: `"{flat_id}-status"`, `"{flat_id}-connection"`.

A golden test pre-populates the registry with entities in today's format (taken from the recorded
traffic) and asserts that setup after the refactor reuses every `entity_id` and creates no duplicates.

## Layout

```
custom_components/entia/
  __init__.py        setup / unload, EntiaConfigEntry = ConfigEntry[EntiaHub]
  const.py
  manifest.json
  config_flow.py     user step (logs in, fetches template), reauth, options (timeout)
  hub.py             EntiaHub: session, cached template, connection policy, status, listeners
  entity.py          EntiaEntity: device info, availability and updates from the hub
  light.py fan.py cover.py
  sensor.py          status
  switch.py          connection
  strings.json  translations/en.json
  api/               no Home Assistant imports
    __init__.py      public names
    models.py        Template, DeclaredDevice, LightState, CoverState, enums
    messages.py      parse template / states / events; build commands
    transport.py     APE over HTTP
    session.py       login, message pump, commands, reconnect, refresh
    errors.py        AuthError, ConnectError, ProtocolError, Stopped
```

### `api/transport.py`

- One `httpx.AsyncClient`, injected (Home Assistant passes `get_async_client(hass)`), so no SSL setup in the event loop.
- Requests built with `json.dumps`. Browser headers trimmed to what the server needs (verify with a capture).
- HTTP status checked before decoding; non-200 raises `ConnectError` with the status.
- The long poll runs in a task that `cancel()` interrupts immediately (commands, close, unload).
- Every exchange is logged at DEBUG as `Exchange {json}` and kept in the 10-entry history, redacted
  (username, password, login_hash, sessionkey, sessid, pubid, pipe). Logger: `custom_components.entia.api`.

### `api/session.py`

Treats APE as a **message pipe**: every response is fed to one dispatcher, whatever request
returned it (the template can come on a later poll, acks arrive late, events ride on command
responses).

- `start()` / `close()`; `wait_ready(timeout)`; `send(command)`.
- Login: `CONNECT` → `FLAT_CONNECT` → `FLAT_GET_TEMPLATE` → `FLAT_GET`. `ERROR 401` → `AuthError`, no retry.
- Reconnect after network or server errors with back-off 2 s doubling to a 5 min cap, reset after a successful login.
- **One request at a time, paced**: before each request (login, polls, refreshes, commands) the session counts the requests started in the last 20 s (`n`) and waits until `min(n, 20) × 50 ms` has passed since the previous one. Idle → instant; a burst slows down linearly (50 ms, 100 ms, … 950 ms) to 1 request/s from the 21st request; after 20 s without requests it is instant again. A command interrupts the long poll, which is abandoned before the command starts, so a command waits at most 1 s.
- Commands queue per device: a newer command replaces one not sent yet (it moves to the back of the queue, as before).
- Hourly full refresh; early refresh when a cover reports moving for more than 2 minutes.
- Callbacks: `on_status(SessionStatus)`, `on_template(Template)`, `on_state(device_id, state)`.
- No class-level mutable state.

### `hub.py`

Owns everything Home Assistant–specific about the connection.

- **Template cache** in a `Store` (`entia.<entry_id>`): flat id and declared devices. Entities are created from it at
  startup without waiting for Entia, so setup never blocks. First setup after the upgrade has no cache: it connects
  with a timeout and raises `ConfigEntryNotReady` on failure. A changed template (devices added or removed) reloads the entry.
- **Connection switch** state, persisted in the same `Store` (default on).
- **Inactivity timer** (`async_call_later`), restarted on every activity; option `0` disables it.
- **On-demand connect**: a command while idle starts the session, waits for ready (30 s), then sends.
- **States**: last known state per device, kept across idle closes.
- Notifies entities through one dispatcher signal per entry.

## Connection policy

| Status (sensor) | Connection | Device entities | Command |
|---|---|---|---|
| `off` | none (switch off) | unavailable | error "connection is turned off" |
| `connecting` | logging in | last known state | waits for ready, then sends |
| `connected` | open | live | sent within ≤1 s (paced), restarts timer |
| `idle` | closed by inactivity timeout | last known state, available | reconnects, waits for ready, sends |
| `reconnecting` | lost, retrying with back-off | unavailable | error "not connected" |
| `auth_failed` | stopped, reauth flow started | unavailable | error |

Transitions:

- Switch on → `connecting` → `connected`; timer starts.
- Activity while `connected` → timer restarts.
- Timer expires → close session → `idle`.
- Command while `idle` → `connecting` → `connected` → command sent; timer restarts.
- Command while `idle` and the reconnect fails → command fails with an error → `reconnecting`.
- Network/server error → `reconnecting` → `connected` (no command lost silently: queued commands fail with an error).
- `ERROR 401` → `auth_failed` → reauth; success reloads the entry.
- Switch off (any state) → close session → `off`.
- HA start with switch on → `connecting` (fresh states), then the normal timeout applies.

## Options

- `timeout` — minutes without activity before closing the connection; `0` = stay connected (default).

## Testing

- `tests/api/`: transport, messages and session against `FakeApe` and the recordings (ports today's client tests; today's
  xfails become passing tests).
- `tests/`: Home Assistant level — golden entity-id test, config/reauth/options flows, connection switch, status
  sensor, inactivity timeout with time travel (`freezer` / `async_fire_time_changed`), on-demand reconnect, unload.
- Fixture loader accepts both `custom_components.entia.client` (old recordings) and `custom_components.entia.api` loggers.

## Out of scope

Anything not declared in the template (dimmers, the extra switch) and features not listed above (scenes,
schedules, diagnostics, brightness).
