# HACS repackaging plan

Goal: ship `custom_components/entia` as an installable HACS custom repository,
fix known bugs in the integration, and bring it up to current Home Assistant
conventions.

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

## 3. Bugs

- [ ] `EntiaClient`: move class-level mutable state (`data`, `_signals`, `_callbacks`, `_queue`) into `__init__` so multiple config entries don't share devices (xfail: `test_clients_do_not_share_devices`; then drop the `isolate_client_state` test fixture)
- [ ] `async_setup_entry`: wrap `wait('ready')` in a timeout and raise `ConfigEntryNotReady`; raise `ConfigEntryAuthFailed` on `BadCredentials`
- [ ] Light: use `_attr_color_mode = ColorMode.ONOFF` and `_attr_supported_color_modes = {ColorMode.ONOFF}`
- [ ] Config flow: implement `_validate_input` (attempt login, map to `invalid_auth` / `cannot_connect` / `unknown`) (xfail: `test_user_flow_rejects_bad_credentials`)
- [ ] Move `available` to `EntiaEntity` so lights and fans also report offline state
- [ ] Propagate availability changes to entities when the client disconnects/reconnects (currently only per-device updates trigger a state write)
- [x] Debug log leaked the Entia ID: `FLAT_LOGIN` returns it as `login_hash`; also `sessionkey`, `sessid`, `pubid`/`pipe` were logged. All are now redacted
- [ ] Rate limiting (20 messages / 20 s) delays commands: each command costs a `DEVICE_SET` plus long polls, a quick on/off sequence held a fan command for ~6 s. Throttle only the polling loop, not user commands (xfail: `test_commands_are_not_rate_limited`)
- [ ] Connect retry back-off doubles without a cap (2, 4, 8 … s); after a long outage it waits for hours. Cap it (e.g. 5 min) and reset after a successful login (xfail: `test_connect_back_off_is_capped`)
- [ ] Connect failures are only logged at DEBUG, so a failing integration looks idle; log the first failure (with history) at WARNING and recovery at INFO (xfail: `test_connect_failure_is_logged_as_warning`)
- [ ] Check the HTTP status before parsing: a proxy/server 403 is reported as "Response malformed" (xfail: `test_http_error_reports_status`)
- [ ] `stop()` waits for the in-flight 120s long poll, so unloading/reloading the entry can hang for up to 2 minutes; close the pending request on stop (xfail: `test_stop_interrupts_long_poll`)

## 4. Client cleanup

Protocol notes from the first capture (`tests/fixtures/traffic/lights_cover_fan.log`):

- APE is a per-session message pipe: a request returns whatever is queued, not its own reply (`FLAT_GET_TEMPLATE` got a heartbeat, the template came on the next `CHECK`; `DEVICE_SET` acks arrive on later polls). Don't pair responses with requests
- Template element ids are ints; elements have `id`, `label`, `type`, `subtype`, `isflatdevice`; one floor, rooms as a list
- `FLAT_NOTIFY` lists 35 devices, the template only 29: 5 dimmers (type 5) and one type-4 switch are missing from the template
- Cover (type 6, subtype 1) attributes 1-5: 1 = closed %, 2 = same as 1 when stopped (target?), 3 = direction, 4 = counter-like, 5 = 0
- Lights/fans (type 4) carry `setting` id 2; type 11 (HMI) has scenes (Away, Cosy, Day, Evening, Morning, Night); type 18 are schedules

- [x] Keep the last 10 request/response exchanges (credentials redacted) and print them on every error path; attached to raised exceptions as a note (`EntiaClient.history`, `format_history()`)
- [ ] Reuse one HTTP client (`homeassistant.helpers.httpx_client.get_async_client`) instead of creating an `AsyncClient` per request (avoids blocking SSL context load in the event loop)
- [x] Remove forced `_LOGGER.setLevel(logging.DEBUG)` (debug output is now controlled by HA's "Enable debug logging")
- [ ] Build request payloads with `json.dumps` instead of string concatenation (escape username/password)
- [ ] Review the spoofed browser headers; keep only what the server requires
- [ ] Decide on cover tilt: drop the ±1 position "tilt" hack or document it
- [ ] Remove misleading copy-pasted comments ("Add cover…" in light/fan)

## 5. Modernise to current HA APIs

- [ ] `FlowResult` → `ConfigFlowResult`
- [ ] `hass.data[DOMAIN]` → `entry.runtime_data` with a typed `EntiaConfigEntry`
- [ ] Add `strings.json` alongside `translations/en.json`
- [ ] Add reauth flow (`async_step_reauth`) to pair with `ConfigEntryAuthFailed`
- [ ] Optional: diagnostics platform with credentials redacted (include `client.history`)

## 6. Docker development environment

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

## 7. Testing

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
- [ ] Find where the web app gets dimmers (type 5) that are missing from the template; add dimmer `light` support with brightness
- [ ] Consider scene entities for the HMI (type 11) scenes
- [ ] Look at the official web app's traffic in browser dev tools to find commands we don't use yet (dimmers type 5, tilt, scenes)
- [ ] Replace `synthetic_startup.json` with a reviewed real capture; verify response shapes (`DEVICE_SET` reply, `ERR` codes, id types int vs str)
- [x] Synthetic fixture and test helpers use recorded shapes (IDENT `casttype`, template `city`/`isflatdevice`, cover subtype 1 with 5 attributes, light `setting`, `DEVICE_SET` ack `{"msg":{"id"}}`, schedules with `"attribute": 0`, dimmers only in states)
- [x] Tests for observed server behaviour: template on a later poll, acks on a later poll, events inside a command response, heartbeat polling (`FakeApe(heartbeat=…)`), hourly and stuck-cover refresh, HTTP error retry
- [ ] Add tests per recorded scenario; turn surprises into xfail tests first
- [ ] Add coverage reporting (`pytest-cov`) and run tests + lint in GitHub Actions
- [ ] Lint the integration code (`ruff check custom_components`, 31 findings now) and add it to `mise run lint`
- [ ] Manually verify on a real HA instance: install via HACS custom repo, add entry, control light/fan/cover, restart, reload, remove
