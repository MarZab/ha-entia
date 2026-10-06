# Development

Tooling is managed with [mise](https://mise.jdx.dev): `mise install` sets up Python and uv and
creates `.venv`.

```sh
mise install
mise run install   # test and lint dependencies
mise run test
mise run lint
```

## Development Home Assistant

`compose.yaml` runs Home Assistant with `custom_components/entia` mounted from this checkout.
Its configuration is `dev/configuration.yaml`; everything HA writes (onboarding, entries, logs)
lives in the git-ignored `dev/config/`.

```sh
mise run ha          # start, then open http://localhost:8123 and onboard
mise run ha-restart  # load code changes
mise run ha-logs     # follow the log
mise run ha-down     # stop
```

Set `HA_VERSION` to run another Home Assistant release.

Machine-specific settings (proxy, extra mounts) go in `compose.override.yaml`, which compose
loads automatically and git ignores.

## Capturing traffic

`dev/configuration.yaml` enables debug logging for `custom_components.entia`, so the client
writes every exchange with the Entia server to `dev/config/home-assistant.log` as one
`Exchange {json}` line. Credentials are redacted and idle long polls are skipped.

1. `mise run ha`, add the Entia integration with your Entia ID and PIN.
2. Do the thing you want to capture: toggle lights, move/stop covers, use the official app at
   the same time to see pushed events, restart to capture a fresh login.
3. `mise run traffic <name>` copies the exchanges to `tests/fixtures/traffic/<name>.log`
   (it reads the current log and the one from before the last restart).
4. Review the file: it contains your device and room names. Then replay it in a test:

   ```python
   ape.load("<name>.log")
   ```

On a regular Home Assistant install the same lines appear when you use **Enable debug logging**
on the integration; the log downloaded when you disable it works as a fixture too.
