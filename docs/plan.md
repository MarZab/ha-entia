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
- [ ] Remove unneeded `dependencies: ["recorder"]` and empty `zeroconf`
- [ ] Confirm `codeowners`, `iot_class` (`cloud_push`), `integration_type`
- [ ] Pin or drop `httpx` requirement (HA already ships it)

## 3. Bugs

- [ ] `EntiaClient`: move class-level mutable state (`data`, `_signals`, `_callbacks`, `_queue`) into `__init__` so multiple config entries don't share devices
- [ ] `async_setup_entry`: wrap `wait('ready')` in a timeout and raise `ConfigEntryNotReady`; raise `ConfigEntryAuthFailed` on `BadCredentials`
- [ ] Light: use `_attr_color_mode = ColorMode.ONOFF` and `_attr_supported_color_modes = {ColorMode.ONOFF}`
- [ ] Config flow: implement `_validate_input` (attempt login, map to `invalid_auth` / `cannot_connect` / `unknown`)
- [ ] Move `available` to `EntiaEntity` so lights and fans also report offline state
- [ ] Propagate availability changes to entities when the client disconnects/reconnects (currently only per-device updates trigger a state write)

## 4. Client cleanup

- [ ] Reuse one HTTP client (`homeassistant.helpers.httpx_client.get_async_client`) instead of creating an `AsyncClient` per request (avoids blocking SSL context load in the event loop)
- [ ] Remove forced `_LOGGER.setLevel(logging.DEBUG)`
- [ ] Build request payloads with `json.dumps` instead of string concatenation (escape username/password)
- [ ] Review the spoofed browser headers; keep only what the server requires
- [ ] Decide on cover tilt: drop the ±1 position "tilt" hack or document it
- [ ] Remove misleading copy-pasted comments ("Add cover…" in light/fan)

## 5. Modernise to current HA APIs

- [ ] `FlowResult` → `ConfigFlowResult`
- [ ] `hass.data[DOMAIN]` → `entry.runtime_data` with a typed `EntiaConfigEntry`
- [ ] Add `strings.json` alongside `translations/en.json`
- [ ] Add reauth flow (`async_step_reauth`) to pair with `ConfigEntryAuthFailed`
- [ ] Optional: diagnostics platform with credentials redacted

## 6. Docker development environment

- [ ] Add `docker-compose.yml` running the official `ghcr.io/home-assistant/home-assistant` image (pinned to the `hacs.json` minimum version)
- [ ] Bind-mount `custom_components/entia` into `/config/custom_components/entia` for live editing
- [ ] Keep HA config in a git-ignored `dev/config/` with a minimal `configuration.yaml` (`logger: entia: debug`, `debugpy` optional)
- [ ] Optionally install HACS in the dev instance to test the custom repository install path
- [ ] Add a test container/service (or `Makefile` targets) for running `pytest` and linting (`ruff`) with `pytest-homeassistant-custom-component`
- [ ] Document usage in README (`docker compose up`, restart HA after code changes, port 8123)

## 7. Testing

- [ ] Add a standalone dev script for the client that reads credentials from env vars (old `test_client.py` had hardcoded credentials and was removed)
- [ ] Add `pytest-homeassistant-custom-component` tests for config flow and setup
- [ ] Manually verify on a real HA instance: install via HACS custom repo, add entry, control light/fan/cover, restart, reload, remove

## 8. Store listing (optional, later)

- [ ] Submit brand icons to `home-assistant/brands`
- [ ] Submit to the HACS default repository list
