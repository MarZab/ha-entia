# Entia for Home Assistant

Control an [Entia Living](https://entia.si) smart home from Home Assistant: lights, fans and blinds,
with changes from wall switches and the Entia app pushed to Home Assistant as they happen.

This is an unofficial integration. It uses the same cloud service as the Entia web app
(dostop.entia.si) and needs the Entia ID and PIN you use there.

## Installation

Requires Home Assistant 2026.9 or newer.

### HACS

1. In HACS, open the menu (⋮) → **Custom repositories**, add `https://github.com/marzab/ha-entia`
   with type **Integration**.
2. Install **Entia** and restart Home Assistant.
3. **Settings → Devices & services → Add integration → Entia**, then enter your Entia ID and PIN.

### Manual

Copy `custom_components/entia` into your Home Assistant `config/custom_components/` folder,
restart, and add the integration as above.

## What you get

One **Entia** device for your flat with:

| Entity | For |
|---|---|
| `light` | each light declared in your flat (on/off) |
| `fan` | each fan declared in your flat (on/off) |
| `cover` | each blind: open, close, stop, set position. The tilt buttons move the blind by 1 % |
| `sensor` *Status* | the connection: `off`, `connecting`, `connected`, `idle`, `reconnecting`, `auth_failed` |
| `switch` *Connection* | turns the connection to Entia on or off |

Only devices that your flat's Entia configuration declares get entities. Dimmers and other device
types are not supported.

### Connection switch

Turning **Connection** off disconnects from Entia: the devices become unavailable and commands are
refused until you turn it back on. The setting is kept across restarts.

### Closing the connection when idle

**Configure** on the integration sets **Close the connection after inactivity** (minutes, `0` =
always stay connected, the default). When nothing changes for that long (no command from Home
Assistant, no change from a wall switch or the Entia app) the connection is closed and the status
shows `idle`. The devices keep their last known state and stay usable: the next command reconnects,
refreshes the states and then runs. Changes made outside Home Assistant while idle show up when it
reconnects.

## Behaviour worth knowing

- **Requests are paced**: one at a time, and a burst slows down gradually to one request per second,
  so a scene that switches many lights takes a few seconds.
- **Wrong or changed PIN**: the integration is flagged under **Settings → Devices & services** and asks
  for the new PIN.
- **Entia unreachable**: the status shows `reconnecting` and the devices are unavailable; it retries
  with increasing delays up to 5 minutes.
- **Changed flat** (devices added, removed or renamed in Entia): the integration reloads with the new
  device list. Existing entities keep their entity ids.

## Upgrading from 0.1

Entity ids, the device and its area and the entities' history are kept. On the first start after
upgrading the integration connects once to read your flat's device list; if Entia is unreachable at
that moment, Home Assistant retries the setup until it is.

## Troubleshooting

Use **Enable debug logging** on the integration, reproduce the problem, then disable it to download
the log. Every request and response with the Entia server is logged on one `Exchange {...}` line;
your Entia ID, PIN and session identifiers are replaced with `**REDACTED**`. The log still contains
your device and room names, so check it before posting it in an issue.

Issues: <https://github.com/marzab/ha-entia/issues>

## Development

See [docs/development.md](docs/development.md) for the test suite, the development Home Assistant in
Docker and capturing traffic; [docs/architecture.md](docs/architecture.md) for the design.

## License

[MIT](LICENSE)
