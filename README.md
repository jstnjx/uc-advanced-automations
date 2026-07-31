# Advanced Automations

Advanced Automations is a visual automation engine for **Unfolded Circle Remote Two** and **Remote 3**. It can run as an ARM64 custom integration on the Remote or as an external container managed by an external integration installer.

The integration combines entity-state triggers, conditions, commands, delays, HTTP requests and log actions in one local workflow editor.

## Features

- Automation overview with a read-only trigger and sequence timeline
- Four-step automation editor:
  1. Automation details
  2. Entity selection
  3. Trigger definition
  4. Sequence definition
- Drag-and-drop trigger and sequence ordering
- Nested if/else sequences, wait-until steps and post-trigger timeframes
- Any-trigger and all-target-states trigger behavior with plain-language descriptions
- Single, Replace and Parallel run modes
- Read-only sensor support for triggers and conditions
- Command metadata and typed parameter controls for controllable entities
- Raw JSON editor for complete automation access
- Blueprint export, import and entity mapping for sharing automations
- Structured validation dialogs instead of browser alerts
- Saving overlay with a blurred backdrop
- Manual run-log refresh with optional continuous polling
- A **Last automation triggered** sensor entity
- Persistent Remote API-key creation during integration setup

## Why the raw editor uses JSON

JSON is the integration's native persisted format. Using JSON keeps the raw editor and the stored automation schema identical, avoids YAML implicit type conversions, and does not require a second parser or conversion layer. The visual editor remains the recommended interface; the raw editor is intended for precise review and advanced changes.

## Automation editor

Open the web interface and create an automation through the guided flow.

### 1. Automation details

Configure:

- Name and description
- Optional Remote command
- Enabled state
- Whether the automation appears as a Remote command and touchscreen button
- Run mode:
  - **Single:** ignore a new start while the automation is active
  - **Replace:** cancel the active run and restart from the beginning
  - **Parallel:** allow simultaneous runs

### 2. Choose entities

Select the entities available to the automation from a dropdown containing all entities reported by the Remote. The dropdown can be searched and filtered with checkboxes by entity type and source integration. Existing configurations created before v0.6.0 are migrated automatically by collecting their referenced entities.

Sensors remain selectable for triggers and conditions but are never offered as command targets.

### 3. Define triggers

An automation may start when:

- Any enabled trigger matches; or
- The changed trigger matches and every enabled trigger's current target state is true.

Each trigger can select an entity attribute, optional previous and target values, debounce time and cooldown time.

### 4. Define sequence

Available steps:

- **Entity:** send a supported command to a controllable entity
- **Delay:** pause execution
- **If / else:** branch using entity or time conditions
- **Wait until:** pause until conditions match, or monitor a recovery condition for a fixed timeframe measured from the trigger
- **HTTP request:** call an HTTP or HTTPS endpoint
- **Log message:** write a diagnostic run-log entry

Root steps, triggers and nested branch steps support drag-and-drop ordering.


## Automation overview

Selecting an existing automation opens a read-only overview instead of immediately entering the editor. The overview shows enabled state, run mode, Remote command exposure, selected entities, triggers and a timeline of the complete sequence. Select **Edit** to open the four-step setup flow.

## Run log refresh

The web interface does not poll logs continuously by default. Select **Refresh** to query new entries on demand, or enable **Continuous refresh** to poll every two seconds until the switch is disabled or the page is closed.

## Automation blueprints

The **Blueprint** dialog exports the current automation as a portable JSON file. Entity identifiers are replaced with mapping slots. When importing the blueprint on another installation, each slot is mapped to a local entity before the automation is created.

Blueprints contain:

- Format and schema version
- Name, description and export timestamp
- Required entity slots and command-target requirements
- The complete automation template

API keys, connection settings and other installation credentials are never included.

## Last automation triggered sensor

The integration exposes a sensor named **Last automation triggered**. Its value is updated whenever an automation run is accepted, regardless of whether the run was started from a trigger, the Remote or the web interface.

## Remote authentication setup

During the integration setup flow:

1. Enter the Remote address.
2. Enter the current Web Configurator PIN.
3. The integration authenticates as `web-configurator` and creates an `admin`-scoped persistent API key through the official Core REST API.
4. The returned one-time API key is stored in the private configuration file.
5. The submitted PIN is discarded and is never persisted.

When reconfiguring the same Remote, an empty PIN keeps the existing API key. Entering a PIN creates a replacement key.

## External service installation

The project includes a Dockerfile compatible with external integration installers.

Runtime behavior:

- Host networking for direct access to the Remote
- Persistent configuration mounted at `/config` by the installer
- Integration API port assigned by the installer in its reserved range
- Web editor starting at port **9201** and scanning upward if occupied
- Integration discovery publishing disabled by default for managed external containers

After installation, open:

```text
http://SERVER-IP:9201
```

The selected editor port is also written to:

```text
/config/web-port.txt
```

### Docker Compose

```bash
docker compose up -d --build
```

The included Compose configuration uses host networking and stores data in a named volume.

## Custom integration package

Build the ARM64 package in an ARM64 environment or through GitHub Actions:

```bash
bash ./tools/build_remote.sh aarch64
```

The generated archive uses this pattern:

```text
uc-intg-advanced-automations-v0.6.0-aarch64.tar.gz
```

Verify an archive before installation:

```bash
bash ./tools/verify_remote_archive.sh ./uc-intg-advanced-automations-v0.6.0-aarch64.tar.gz
```

The Python wheel is for external/server deployment and is not a custom integration archive.

## Web API

Important endpoints:

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/health` | Container liveness and service diagnostics |
| `GET` | `/api/status` | Connection, trigger and run status |
| `GET` | `/api/entities` | Available entities and current attributes |
| `GET` | `/api/entities/{id}/commands` | Command metadata for an entity |
| `GET` | `/api/automations` | List automations |
| `POST` | `/api/automations` | Create an automation |
| `PUT` | `/api/automations/{id}` | Update an automation |
| `DELETE` | `/api/automations/{id}` | Delete an automation |
| `POST` | `/api/automations/{id}/run` | Start an automation |
| `POST` | `/api/integration/refresh` | Refresh generated entities and commands |

Invalid automation payloads return HTTP `400` with JSON-safe field details. They do not produce an internal-server-error response.

## Configuration storage

The configuration file contains connection settings and automation definitions. It is written atomically and, where supported by the filesystem, uses mode `0600`.

Corrupt or incompatible configurations are backed up and recovered. Valid automations are salvaged individually when possible.

## Development

Install dependencies and run tests:

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Validate the frontend:

```bash
node --check src/uc_advanced_automations/static/app.js
```

Build the external wheel:

```bash
python -m pip wheel . --no-deps --no-build-isolation -w dist
```

## License

Released under the [MIT License](LICENSE).
