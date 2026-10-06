# HACS repackaging plan

Goal: ship `custom_components/entia` as an installable HACS custom repository, rebuilt per
[`architecture.md`](architecture.md): same entities and entity ids, plus a connection status,
a connection switch and an inactivity timeout.

## 1. Repository scaffolding

- [ ] Add `hacs.json` (`name`, minimum `homeassistant` version, `render_readme`)
- [ ] Add `README.md` (features, supported devices, install via HACS, configuration, limitations)
- [ ] Add `LICENSE`
- [ ] Add `.github/workflows/validate.yml` running `hacs/action` and `home-assistant/actions/hassfest`
- [ ] Set GitHub repo description and topics (`home-assistant`, `hacs`, `integration`)
- [ ] Tag a first release (version must match `manifest.json`)

## 2. Manifest

- [ ] Add `documentation` and `issue_tracker` URLs
- [x] Remove unneeded `dependencies: ["recorder"]` (it broke setup without a database)
- [ ] Remove empty `zeroconf`
- [x] Drop `loggers: ["entia"]`; the client now logs under `custom_components.entia.client`, which the debug toggle already covers
- [ ] Confirm `codeowners`, `iot_class` (`cloud_push`), `integration_type`
- [ ] Pin or drop `httpx` requirement (HA already ships it)

## 3. Refactor

Design: [`architecture.md`](architecture.md). Each phase leaves the suite green; known-bug xfails turn into
passing tests as their phase lands.

### Phase 0 — guardrails (before touching code)

- [ ] Golden entity-id test: pre-populate the entity and device registry in today's format (from `lights_cover_fan.log`), set up, assert every `entity_id` and the device are reused and nothing is duplicated
- [ ] Capture today's entity behaviour that must not change (light/fan on/off, cover position/open/close/stop/tilt nudge) as HA-level tests if not already covered

### Phase 1 — `api/` protocol library

- [ ] `api/models.py`, `api/messages.py`: parse template (declared devices only), states and events; build commands
- [ ] `api/transport.py`: injected `httpx.AsyncClient`, `json.dumps` payloads, HTTP status check, cancellable long poll, exchange log + history + redaction
- [ ] `api/session.py`: message-pipe dispatcher, login, `AuthError` on `ERROR 401`, capped back-off (2 s → 5 min, reset on login), poll-only rate limit, command dedup, hourly and stuck-cover refresh, `close()` interrupts the long poll, no class-level state
- [ ] Port client tests to `tests/api/`; resolves xfails `test_clients_do_not_share_devices`, `test_stop_interrupts_long_poll`, `test_connect_back_off_is_capped`, `test_http_error_reports_status`, `test_commands_are_not_rate_limited`
- [ ] Connect failures logged once at WARNING with history, recovery at INFO (xfail `test_connect_failure_is_logged_as_warning`)
- [ ] Trim the spoofed browser headers to what the server needs (verify with a capture)
- [ ] Fixture loader and `mise run traffic` accept the new `custom_components.entia.api` logger as well as the old one

### Phase 2 — hub and setup

- [ ] `hub.py`: `EntiaHub` as `entry.runtime_data` (`EntiaConfigEntry`), template cache and switch state in a `Store`
- [ ] Non-blocking setup from the cached template; first setup without cache connects with a timeout → `ConfigEntryNotReady`
- [ ] `AuthError` → `ConfigEntryAuthFailed` + reauth flow (`async_step_reauth`)
- [ ] Config flow validates the login and fetches the template (`invalid_auth` / `cannot_connect` / `unknown`; xfail `test_user_flow_rejects_bad_credentials`)
- [ ] Template change (devices added/removed) reloads the entry
- [ ] `ConfigFlowResult`, `strings.json` + `translations/en.json`

### Phase 3 — entities

- [ ] `EntiaEntity`: device info as today, availability and updates from the hub (all platforms, not just covers)
- [ ] Light: `_attr_color_mode = ColorMode.ONOFF`, `_attr_supported_color_modes = {ColorMode.ONOFF}`
- [ ] Fan, cover: same features as today, including the cover tilt buttons (±1 % position nudge)
- [ ] Status sensor (`SensorDeviceClass.ENUM`: `off`, `connecting`, `connected`, `idle`, `reconnecting`, `auth_failed`), unique id `{flat}-status`
- [ ] Connection switch (master), unique id `{flat}-connection`, persisted

### Phase 4 — connection policy

- [ ] Inactivity timer, option `timeout` (minutes, `0` = never) via options flow; reset on HA commands and on changed pushed values only
- [ ] Idle close keeps last known states available; a command reconnects, waits for ready (30 s), then sends; a failed reconnect fails the command and moves to `reconnecting`
- [ ] Switch off closes the session, entities unavailable, commands raise `HomeAssistantError`
- [ ] Tests with time travel for every transition in the architecture doc's table

### Phase 5 — cleanup

- [ ] Delete `client/`, the `isolate_client_state` fixture and obsolete tests
- [ ] `ruff check custom_components` clean and part of `mise run lint`
- [ ] Manual check in the dev instance: upgrade from the current version keeps entity ids and history

Protocol notes from the first capture (`tests/fixtures/traffic/lights_cover_fan.log`):

- APE is a per-session message pipe: a request returns whatever is queued, not its own reply (`FLAT_GET_TEMPLATE` got a heartbeat, the template came on the next `CHECK`; `DEVICE_SET` acks arrive on later polls). Don't pair responses with requests
- Idle `CHECK` returns a single `FLAT_HEARTBEAT` after ~3-8 s (median 6 s), so an idle connection makes ~10 requests/min
- Template element ids are ints; elements have `id`, `label`, `type`, `subtype`, `isflatdevice`; one floor, rooms as a list
- `FLAT_NOTIFY` lists 35 devices, the template only 29: 5 dimmers (type 5) and one type-4 switch are missing from the template (out of scope: not declared)
- Cover (type 6, subtype 1) attributes 1-5: 1 = closed %, 2 = same as 1 when stopped (target?), 3 = direction, 4 = counter-like, 5 = 0
- Lights/fans (type 4) carry `setting` id 2; type 11 (HMI) has scenes; type 18 are schedules

Done before the refactor:

- [x] Debug log leaked the Entia ID: `FLAT_LOGIN` returns it as `login_hash`; also `sessionkey`, `sessid`, `pubid`/`pipe` were logged. All are now redacted
- [x] Keep the last 10 request/response exchanges (credentials redacted) and print them on every error path
- [x] Remove forced `_LOGGER.setLevel(logging.DEBUG)`

## 4. Docker development environment

- [x] `compose.yaml` running `ghcr.io/home-assistant/home-assistant` (`HA_VERSION`, default 2026.9.4, same as the test harness)
- [x] Bind-mount `custom_components/entia` into `/config/custom_components/entia` for live editing
- [x] Committed `dev/configuration.yaml` (`default_config`, debug logging for `custom_components.entia`); state in git-ignored `dev/config/`
- [x] mise tasks `ha`, `ha-restart`, `ha-logs`, `ha-down`, and `traffic <name>` to copy logged exchanges into a fixture
- [x] Document usage in `docs/development.md`
- [x] Local tooling via mise (`mise.toml`: Python, uv, `.venv`; tasks `install`, `test`, `lint`, `format`)
- [ ] Verify `mise.toml` with a real mise install (mise download was blocked in the sandbox; the `traffic` task uses mise's `usage` args)
- [ ] Pin `HA_VERSION` to the `hacs.json` minimum once decided
- [ ] Optionally install HACS in the dev instance to test the custom repository install path
- [ ] Optional: `debugpy` for attaching a debugger to the dev instance
- [ ] Move development docs into the README when it is written

## 5. Testing

Test suite: `tests/` with a fake APE server (`tests/fake_ape.py`) that replays traffic fixtures from
`tests/fixtures/traffic/`: hand-written JSON, or a Home Assistant debug log of the integration.

Capturing traffic: enable debug logging on the Entia integration (Settings → Devices & services → Entia →
Enable debug logging), use the devices, then disable it to download the log. The client writes one
`Exchange {...}` JSON line per request/response (credentials redacted, including idle and cancelled long polls); drop the log
into `tests/fixtures/traffic/` and `ape.load("<file>.log")` replays it. Known bugs are `xfail(strict=True)` tests, so fixing one makes the suite tell you
to remove the marker.

- [x] Fake APE server: scripted replies, pushed `CHECK` events, long-poll cancel, request log
- [x] Client protocol tests: handshake, template/state parsing, cover inversion and clamping, commands, dedup, reconnects, exchange history
- [x] `pytest-homeassistant-custom-component` tests: config flow, setup, entities, services, pushed updates, unload
- [x] Debug log capture: client logs each exchange as an `Exchange {json}` line at DEBUG; `FakeApe.load()` replays `.log` files directly
- [x] First recording `lights_cover_fan.log` (login, lights, cover open/stop/close/stop, fan), anonymized; replayed at client and HA level in `tests/test_recorded.py`
- [ ] Heartbeat recording: leave the dev instance idle for >1 h to measure long poll duration/heartbeat interval and see the hourly refresh
- [ ] More recordings: full cover travel without stop, events triggered from the official app/wall switches, session expiry/reconnect, wrong PIN, network drop
- [ ] Make anonymizing captures part of `mise run traffic` (labels, city, flat id) so recordings can be committed without manual review
- [ ] Replace `synthetic_startup.json` with a reviewed real capture; verify response shapes (`DEVICE_SET` reply, `ERR` codes, id types int vs str)
- [x] Synthetic fixture and test helpers use recorded shapes (IDENT `casttype`, template `city`/`isflatdevice`, cover subtype 1 with 5 attributes, light `setting`, `DEVICE_SET` ack `{"msg":{"id"}}`, schedules with `"attribute": 0`, dimmers only in states)
- [x] Tests for observed server behaviour: template on a later poll, acks on a later poll, events inside a command response, heartbeat polling (`FakeApe(heartbeat=…)`), hourly and stuck-cover refresh, HTTP error retry
- [ ] Add tests per recorded scenario; turn surprises into xfail tests first
- [ ] Add coverage reporting (`pytest-cov`) and run tests + lint in GitHub Actions
- [ ] Manually verify on a real HA instance: install via HACS custom repo, add entry, control light/fan/cover, restart, reload, remove
