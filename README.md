# Unfolded Circle Advanced Automations

A dual-target integration for **Unfolded Circle Remote Two and Remote 3**. The same codebase can run:

- **directly on the Remote** as an ARM64 custom integration package; or
- **externally** on a server, VM, NAS, Raspberry Pi or Docker host.

It exposes one virtual **Advanced Automations** remote entity and provides a simple web interface for building conditional sequences.

## Features

- Sequential commands across configured Unfolded Circle entities
- Delays
- Nested **if / else** branches
- **All / any** condition groups
- Entity attribute comparisons
- Time-window conditions, including windows crossing midnight
- **Wait until** with timeout and polling interval
- HTTP requests for local webhooks and APIs
- Per-step **continue on error**
- Single-run or parallel execution mode
- Run history
- Atomic JSON persistence with file mode `0600`
- Generated commands and touchscreen buttons on the Remote
- One portable configuration format for embedded and external operation

No arbitrary Python, JavaScript or template expressions are evaluated. Conditions use a fixed set of operators.

## Runtime modes

Runtime mode is detected automatically:

| Target | Detection | Core API default | Configuration directory |
|---|---|---|---|
| Remote Two/3 | `UC_CONFIG_HOME` is supplied by the Remote | `ws://127.0.0.1/ws` | Remote-managed `UC_CONFIG_HOME` |
| External | `UC_EXTERNAL=true`, or no Remote environment is present | `ws://remote.local/ws` | `~/.config/uc-advanced-automations` |

`UC_RUNTIME_MODE=remote` or `UC_RUNTIME_MODE=external` can explicitly override detection.

The integration uses two listeners:

| Service | Default port | Purpose |
|---|---:|---|
| Integration API | `9090` | Connection from Remote Two/3 |
| Web interface | `8099` | Automation editor |

## Installation on Remote Two/3

Use the ARM64 release archive named similar to:

```text
uc-intg-advanced-automations-v0.2.0-aarch64.tar.gz
```

Do not extract it.

1. Open the Remote Web Configurator.
2. Go to **Integrations**.
3. Select **Add new** → **Install custom**.
4. Upload the `.tar.gz` archive.
5. Open **Advanced Automations** and start setup.
6. Open `http://REMOTE-IP:8099` in a browser.
7. Configure a Core API key and create automations.

In embedded mode, the integration connects back to the local Remote Core API through `ws://127.0.0.1/ws`. Configuration remains on the Remote in its integration-managed data directory.

### Build the Remote package

The embedded package must be built for ARM64. The included GitHub Actions workflow uses the official Unfolded Circle ARM64 PyInstaller image and produces the correct custom-integration archive layout:

```text
bin/
  driver
  ... bundled libraries
driver.json
advanced-automations.png
version.txt
```

On an ARM64 Linux system:

```bash
python -m pip install -r requirements-build.txt
make build-remote
```

On x86-64, use the included GitHub Actions workflow or run the official ARM64 builder through QEMU/Docker.

## External installation with Docker Compose

```bash
unzip uc-advanced-automations.zip
cd uc-advanced-automations
docker compose up -d --build
```

Open:

```text
http://SERVER-IP:8099
```

Then:

1. Open **Settings**.
2. Enter the Remote Core URL, normally `ws://REMOTE-IP/ws`.
3. Enter a Core API key.
4. Select **Test connection**.
5. Create an automation and save it.
6. Add the discovered **Advanced Automations** integration in the Web Configurator.
7. Add its remote entity to a profile or activity.

Docker host networking is used so mDNS and LAN access work without additional forwarding.

## External native installation

Requires Python 3.11 or newer.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install .
export UC_EXTERNAL=true
export UC_AUTOMATIONS_DATA_DIR="$PWD/data"
uc-advanced-automations
```

The included systemd unit assumes:

- application: `/opt/uc-advanced-automations`
- virtual environment: `/opt/uc-advanced-automations/.venv`
- service account: `uc-automations`
- data directory: `/var/lib/uc-advanced-automations`

## Core API authentication

Both targets use the Remote Core API to read entity state and execute commands. Create an API key through the Remote's API access configuration or the Core REST API `auth/api_keys` endpoint, then enter it in **Settings**.

The key is stored in `config.json` with owner-only file permissions. It is not encrypted. Do not expose port `8099` directly to the internet.

## Step types

### Device command

```json
{
  "type": "command",
  "entity_id": "media_player.living_room_tv",
  "cmd_id": "select_source",
  "params": {
    "source": "HDMI 1"
  }
}
```

Command IDs and parameters depend on the selected entity and integration.

### Condition

Conditions read the latest entity state from the Remote Core API. Attribute paths use dot notation, for example `state`, `volume`, or `media.title`.

Supported operators:

```text
eq, ne, gt, gte, lt, lte,
contains, not_contains, in, not_in,
exists, not_exists, truthy, falsy
```

A condition step contains nested `then` and `else` sequences.

### Wait until

Repeatedly evaluates a condition group until it matches or its timeout expires.

### HTTP request

Useful for Home Assistant webhooks, Tasmota commands, Node-RED endpoints and other local services. Accepted HTTP status codes are configurable.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `UC_RUNTIME_MODE` | auto | Force `remote` or `external` mode |
| `UC_EXTERNAL` | unset | Set to `true` for external deployments |
| `UC_CONFIG_HOME` | supplied by Remote | Remote-managed configuration directory |
| `UC_AUTOMATIONS_DATA_DIR` | target-specific | Override persistent configuration directory |
| `UC_CORE_URL` | target-specific | Override the initial Core WebSocket URL |
| `UC_AUTOMATIONS_WEB_HOST` | `0.0.0.0` | Initial web interface bind address |
| `UC_AUTOMATIONS_WEB_PORT` | `8099` | Initial web interface port |
| `UC_INTEGRATION_INTERFACE` | all interfaces | Integration API bind address |
| `UC_INTEGRATION_HTTP_PORT` | `9090` | Integration API port |
| `UC_DISABLE_MDNS_PUBLISH` | `false` | Disable integration mDNS advertisement |

The web host and port are persisted after first start. Changing them in the web interface requires a restart.

## Development

```bash
python -m pip install -r requirements.txt
make test
```

Build external Python distributions:

```bash
python -m pip install build
python -m build
```

## Limitations

- Automations are command-triggered. Background state-change triggers are not included yet.
- Command metadata is not queried yet; command IDs and parameters are entered manually.
- Changing generated commands or touchscreen pages may require an entity refresh or integration reload.
- A Remote custom-install archive must be built on ARM64 or through ARM64 emulation; an x86-64 wheel is not installable on the Remote.

## License

MIT
