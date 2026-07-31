# UC Advanced Automations

A dual-target integration for **UC Remote Two and UC Remote 3**. The same codebase can run:

- **directly on the UC Remote** as an ARM64 custom integration package; or
- **externally** on a server, VM, NAS, Raspberry Pi or Docker host.

It exposes one virtual **UC Advanced Automations** entity and provides a simple web interface for building conditional sequences.

## Features

- Sequential commands across configured UC entities
- Background entity state-change triggers with selectable attributes and from/to filters
- OR / AND trigger logic
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
- Single, replace, or parallel execution mode
- Drag-and-drop trigger and sequence ordering
- Modal validation prompts and run history
- Atomic JSON persistence with file mode `0600`
- Generated commands and touchscreen buttons on the UC Remote
- Automatic entity definition refresh after command or page changes
- One portable configuration format for embedded and external operation

No arbitrary Python, JavaScript or template expressions are evaluated. Conditions use a fixed set of operators.

## Runtime modes

Runtime mode is detected automatically:

| Target | Detection | Core API default | Configuration directory |
|---|---|---|---|
| UC Remote Two/3 | `UC_CONFIG_HOME` is supplied by the UC Remote | `ws://127.0.0.1/ws` | Remote-managed `UC_CONFIG_HOME` |
| External | `UC_EXTERNAL=true`, or no Remote environment is present | `ws://remote.local/ws` | `~/.config/uc-advanced-automations` |

`UC_RUNTIME_MODE=remote` or `UC_RUNTIME_MODE=external` can explicitly override detection.

The integration uses two listeners:

| Service | Default port | Purpose |
|---|---:|---|
| Integration API | `9090` | Connection from UC Remote Two/3 |
| Web interface | `9201` | Automation editor |

## Installation on UC Remote Two/3

Use the ARM64 release archive named similar to:

```text
uc-intg-advanced-automations-v0.4.0-aarch64.tar.gz
```

Do not extract it.

1. Open the UC Remote Web Configurator.
2. Go to **Integrations**.
3. Select **Add new** → **Install custom**.
4. Upload the `.tar.gz` archive.
5. Open **Advanced Automations** and start setup.
6. Enter the UC Remote address and the current **Web Configurator PIN**.
7. The integration creates and stores a persistent `admin` API key, then discards the PIN.
8. Open `http://REMOTE-IP:9201` in a browser and create automations.

In embedded mode, the integration connects back to the local UC Core API through `ws://127.0.0.1/ws`. Configuration remains on the UC Remote in its integration-managed data directory.

### Build the UC Remote package

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

The Python wheel produced by the external build is for server/native deployment. It is **not** a UC Remote custom-install package and cannot be uploaded through **Install custom**.

## Prebuilt GHCR image

The workflow publishes a multi-architecture image for `linux/amd64` and `linux/arm64`:

```text
ghcr.io/jstnjx/uc-advanced-automations
```

Release tags publish version aliases and `latest`. Pushes to `main` publish the `main` and commit-SHA tags.

```bash
docker pull ghcr.io/jstnjx/uc-advanced-automations:latest
docker run -d \
  --name uc-advanced-automations \
  --restart unless-stopped \
  --network host \
  -v "$PWD/data:/data" \
  ghcr.io/jstnjx/uc-advanced-automations:latest
```

## External installation with Docker Compose

```bash
unzip uc-advanced-automations.zip
cd uc-advanced-automations
docker compose up -d --build
```

Open:

```text
http://SERVER-IP:9201
```

Then:

1. Add or register the **Advanced Automations** integration in the Web Configurator.
2. Start the integration setup flow.
3. Enter the UC Remote IP address or hostname and the current **Web Configurator PIN**.
4. The integration authenticates as `web-configurator`, creates a persistent `admin` API key, stores it in its private configuration, and discards the PIN.
5. Open the automation editor and select **Test connection**.
6. Create an automation and save it.
7. Add its UC entity to a profile or activity.

Docker host networking is used for direct LAN access. Integration mDNS publishing is disabled by default in external mode because managed integrations are registered explicitly with the UC Remote.

### UC External Integration Installer

When installed through `uc-external-integration-installer`, the container follows the installer's runtime contract:

- persistent configuration uses the installer's `/config` mount;
- the Integration API listens on the port assigned by the installer in the UC-reserved `8000`–`9200` range;
- the automation editor starts at `9201`, outside that reserved range;
- `UC_AUTOMATIONS_WEB_PORT` can select another port from `9201` through `65535`;
- if the selected editor port is occupied, the integration scans upward (`9202`, `9203`, …) until a free port is found.

The selected editor port is printed as `AUTOMATION EDITOR URL` in the container log, written to `/config/web-port.txt`, and returned by `/api/health` and `/api/status`.

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

Both targets use the UC Core API to read entity state and execute commands. During the Integration-API setup flow, Advanced Automations asks for the UC Remote address and Web Configurator PIN. It sends a single authenticated request as `web-configurator` to `POST /api/auth/api_keys` with the key name `Advanced Automations` and the `admin` scope. The one-time `api_key` returned by the UC Remote is stored immediately; the PIN is never written to configuration or logs.

Reconfiguring the same UC Remote can keep the existing key by leaving the PIN empty. Entering a PIN creates a replacement key. The Settings dialog retains a manual API-key field for recovery and advanced deployments.

The API key is stored in `config.json` with owner-only file permissions. It is not encrypted. Do not expose port `9201` directly to the internet.

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

The web editor queries the UC Core command metadata for the selected entity. It presents only advertised commands and creates typed controls for number, boolean, enum, regex and entity-backed selection parameters. Entities that do not advertise commands are presented as read-only. Sensor entities are never offered as command targets.


### Background state trigger

An automation can be triggered by a UC command, an entity transition, or both. A state trigger watches one attribute and optionally filters its previous and new values:

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

A blank `from_value` or `to_value` matches any value. `debounce_ms` requires the new value to remain stable before execution; `cooldown_ms` prevents repeated runs. The integration subscribes to UC Core entity-change events and reconnects automatically while enabled triggers exist.

Trigger logic can be set per automation:

- **OR** starts the automation when any enabled trigger matches its transition filters.
- **AND** requires the triggering transition to match and every enabled trigger's current target state to satisfy its configured `to_value`. A blank `to_value` requires that attribute to exist.

Run modes determine what happens when an automation is started while already active:

- **Single** ignores the new run.
- **Replace** cancels the active run and starts from the beginning.
- **Parallel** allows multiple simultaneous runs.

### Condition

Conditions read the latest entity state from the UC Core API. Attribute paths use dot notation, for example `state`, `volume`, or `media.title`.

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
| `UC_CONFIG_HOME` | supplied by UC Remote | UC Remote-managed configuration directory |
| `UC_AUTOMATIONS_DATA_DIR` | target-specific | Override persistent configuration directory; installer-managed containers default to `/config` |
| `UC_CORE_URL` | target-specific | Override the initial Core WebSocket URL |
| `UC_AUTOMATIONS_WEB_HOST` | `0.0.0.0` | Initial web interface bind address |
| `UC_AUTOMATIONS_WEB_PORT` | `9201` | Preferred web interface port; must be 9201 or higher and scans upward if occupied |
| `UC_INTEGRATION_INTERFACE` | all interfaces | Integration API bind address |
| `UC_INTEGRATION_HTTP_PORT` | `9090` | Integration API port |
| `UC_DISABLE_MDNS_PUBLISH` | external: `true`; UC Remote: `false` | Disable integration mDNS advertisement |

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
- GitHub Actions builds both targets independently, validates the UC Remote archive architecture, and publishes SHA-256 checksums.

## License

MIT
