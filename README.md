# Unfolded Circle Advanced Automations

A dual-target integration for **Unfolded Circle Remote Two and Remote 3**. The same codebase can run:

- **directly on the Remote** as an ARM64 custom integration package; or
- **externally** on a server, VM, NAS, Raspberry Pi or Docker host.

It exposes one virtual **Advanced Automations** remote entity and provides a simple web interface for building conditional sequences.

## Features

- Sequential commands across configured Unfolded Circle entities
- Background entity state-change triggers with from/to filters
- Trigger stabilization (debounce) and cooldown controls
- Command IDs and typed parameters discovered from Core command metadata
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
- Automatic entity definition refresh after command or page changes
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
uc-intg-advanced-automations-v0.3.1-aarch64.tar.gz
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

On x86-64, use the included GitHub Actions workflow. The build script refuses to create a Remote archive from an x86-64 runtime, and CI verifies that `bin/driver` is an ARM64 ELF executable before publishing it.

The Python wheel produced by the external build is for server/native deployment. It is **not** a Remote custom-install package and cannot be uploaded through **Install custom**.

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

Docker host networking is used for direct LAN access. Integration mDNS publishing is disabled by default in external mode because managed integrations are registered explicitly with the Remote.

### UC External Integration Installer

When installed through `uc-external-integration-installer`, the container follows the installer's runtime contract:

- persistent configuration uses the installer's `/config` mount;
- the Integration API listens on the port assigned by the installer;
- the web interface defaults to the assigned Integration API port plus `10000` to avoid colliding with other host-network containers;
- `UC_AUTOMATIONS_WEB_PORT` can override that companion port.

For example, an installer-assigned Integration API port of `8000` gives a default web-interface port of `18000`. The selected port is printed in the container startup log and returned by `/api/health` and `/api/status`. If that port is unavailable, the integration automatically selects another free port instead of exiting.

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

The web editor queries the Remote Core command metadata for the selected entity. It presents only advertised commands and creates typed controls for number, boolean, enum, regex and entity-backed selection parameters. Manual JSON entry remains available as a fallback when an integration does not provide metadata.


### Background state trigger

An automation can be triggered by a Remote command, an entity transition, or both. A state trigger watches one attribute and optionally filters its previous and new values:

```json
{
  "type": "entity_state",
  "entity_id": "switch.living_room_power",
  "attribute": "state",
  "from_value": "OFF",
  "to_value": "ON",
  "debounce_ms": 500,
  "cooldown_ms": 5000
}
```

A blank `from_value` or `to_value` matches any value. `debounce_ms` requires the new value to remain stable before execution; `cooldown_ms` prevents repeated runs. The integration subscribes to Core entity-change events and reconnects automatically while enabled triggers exist.

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
| `UC_AUTOMATIONS_DATA_DIR` | target-specific | Override persistent configuration directory; installer-managed containers default to `/config` |
| `UC_CORE_URL` | target-specific | Override the initial Core WebSocket URL |
| `UC_AUTOMATIONS_WEB_HOST` | `0.0.0.0` | Initial web interface bind address |
| `UC_AUTOMATIONS_WEB_PORT` | target-specific | Initial web interface port; installer-managed containers default to Integration API port + `10000` |
| `UC_INTEGRATION_INTERFACE` | all interfaces | Integration API bind address |
| `UC_INTEGRATION_HTTP_PORT` | `9090` | Integration API port |
| `UC_DISABLE_MDNS_PUBLISH` | external: `true`; Remote: `false` | Disable integration mDNS advertisement |

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

## Target-specific artifacts

- Upload `uc-intg-advanced-automations-vX.Y.Z-aarch64.tar.gz` to Remote Two/3. It contains an ARM64 self-contained executable and the required custom-integration archive layout.
- Use the wheel, source distribution, Docker image or systemd deployment only for external installations.
- GitHub Actions builds both targets independently, validates the Remote archive architecture, and publishes SHA-256 checksums.

## License

MIT
