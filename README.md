# Advanced Automations

<p align="center">
  <img src="advanced-automations.png" alt="Advanced Automations icon" width="128">
</p>

Advanced Automations is a local visual automation engine for **Unfolded Circle Remote Two** and **Remote 3**. It combines entity triggers, conditions, commands, schedules, waits, HTTP requests, recovery logic, persistent run history and revision management in one browser-based editor.

It can run in either of two forms:

* As an **external integration service** on a server, NAS or Docker host
* Directly on a supported Remote as an **ARM64 custom integration package**

All automation processing happens locally. Cloud services are only contacted when an automation explicitly contains an HTTP request to an external endpoint.

## Features

### Visual automation editor

Automations are created through a guided four-step flow:

1. **Automation details** — name, description, enabled state, run mode and runtime controls
2. **Choose entities** — select the entities available to the automation
3. **Define triggers** — configure when the automation starts
4. **Define sequence** — build the actions, branches, waits and recovery behavior

Trigger and sequence cards are collapsible and can be reordered with drag and drop.

### Supported trigger types

* Entity state transition
* Entity remains in a state for a duration
* Numeric threshold crossing with optional hysteresis
* Any attribute change
* Selected attribute change
* Scheduled local time with weekday selection
* Periodic interval
* Initial Remote connection
* Remote reconnect
* Local webhook
* Another automation succeeds
* Another automation fails
* Another automation completes with any result
* Manual virtual button

Trigger behavior can be configured to either:

* Start when **any enabled trigger** matches
* Start when the changed trigger matches **and every configured target state is currently true**

### Sequence steps

* **Entity** — send a supported command to a controllable entity
* **Delay** — pause for a fixed duration
* **If / else** — evaluate entity or time conditions and run a branch
* **Wait for condition** — wait for a condition with explicit match and timeout outcomes
* **Parallel group** — execute multiple branches concurrently
* **HTTP request** — call an HTTP or HTTPS endpoint
* **Log message** — add a diagnostic event to the run history

Sensors remain available for triggers and conditions, but they cannot be selected as command targets.

### Run modes

* **Single** — ignore a new start while the automation is already running
* **Replace** — cancel the active run and restart from the beginning
* **Parallel** — allow multiple simultaneous runs

### Execution policies

Advanced Automations supports structured runtime control at both step and automation level:

* Per-step timeout
* Retry count
* Fixed retry delay
* Exponential retry backoff
* Failure branch
* Continue after final failure
* Fail after final failure
* Request automation rollback
* Maximum automation runtime
* Parallel branches with wait-for-all or wait-for-any behavior
* Cancellation cleanup sequence
* Rollback sequence

### Persistent run history

Run history is stored in SQLite and survives service restarts. Each automation overview can display:

* Last run
* Last successful run
* Last failure
* Average duration
* Recent run history
* Currently active step

Runs that were still active when the service stopped unexpectedly are marked as cancelled on the next start.

### Revisions and recovery

Before an automation is updated, deleted, restored, imported or replaced through the raw editor, the previous configuration is stored as a revision.

* Up to 50 revisions are retained per automation
* Revisions record their timestamp, action and edit source
* Revisions can be compared as formatted JSON
* Any retained revision can be restored
* Deleted automations remain in a recovery archive
* Deleted automations can be restored with their original identifier
* The visual editor and raw JSON editor both provide undo and redo before saving

### Automation blueprints

Blueprints are portable automation templates.

* The export contains only the automation itself
* Source entity records and installation metadata are not included
* Every entity reference is replaced with an independent placeholder
* During import, the user must choose a local entity for every placeholder
* Sensor entities are excluded from command-target placeholders
* API keys and Remote connection settings are never exported

### Raw JSON editor

Automations can also be edited directly as JSON. JSON is used instead of YAML because it is the integration's native persisted format and avoids implicit YAML type conversion.

The raw editor includes:

* Formatting
* Syntax validation
* Undo and redo
* Server-side schema validation
* Automatic revision creation when saved

### Remote entities

The integration exposes:

* **Advanced Automations** — a Remote entity containing enabled automation commands and optional touchscreen pages
* **Last automation triggered** — a read-only sensor showing the most recently started automation

The last-triggered value is restored from persistent history after a restart.

## Requirements

* Unfolded Circle Remote Two or Remote 3
* Remote Core API compatible with `min_core_api` **0.35.0** or newer
* A current Web Configurator PIN for first-time API authentication
* For external deployment:

  * Docker with host networking, or
  * Python 3.11 or newer
* For direct Remote deployment:

  * An ARM64 custom-integration archive built for `aarch64`

## Installation options

### Option 1: External integration installer

This is the recommended deployment method when using an external integration installer.

The service expects:

* Host networking so it can communicate directly with the Remote
* A persistent configuration directory mounted at `/config`
* An Integration API port in the installer-managed range
* A web editor port starting at **9201**

Install the supplied driver metadata and wheel through the external integration installer, then complete the integration setup flow on the Remote.

The installer normally provides these values automatically:

```text
UC_EXTERNAL=true
UC_RUNTIME_MODE=external
UC_CONFIG_HOME=/config
UC_INTEGRATION_INTERFACE=0.0.0.0
UC_INTEGRATION_HTTP_PORT=<installer-assigned port>
UC_AUTOMATIONS_WEB_HOST=0.0.0.0
UC_AUTOMATIONS_WEB_PORT=9201
```

If port 9201 is occupied, the editor scans upward until it finds a free port outside the Integration API reserved range.

The selected port is written to:

```text
/config/web-port.txt
```

### Option 2: Docker Compose

Clone the repository and start the service:

```bash
git clone https://github.com/jstnjx/uc-advanced-automations.git
cd uc-advanced-automations
docker compose up -d --build
```

The included Compose configuration uses:

* Host networking
* Integration API port `9090`
* Web editor port `9201`
* Persistent data in `./data`

Open the editor at:

```text
http://SERVER-IP:9201
```

Useful commands:

```bash
# Show service status
docker compose ps

# Follow logs
docker compose logs -f

# Restart the service
docker compose restart

# Stop the service
docker compose down
```

### Option 3: Python service

Create a Python 3.11 virtual environment and install the project:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install .
```

Create a persistent data directory and start the service:

```bash
export UC_EXTERNAL=true
export UC_RUNTIME_MODE=external
export UC_AUTOMATIONS_DATA_DIR="$PWD/data"
export UC_INTEGRATION_INTERFACE=0.0.0.0
export UC_INTEGRATION_HTTP_PORT=9090
export UC_AUTOMATIONS_WEB_HOST=0.0.0.0
export UC_AUTOMATIONS_WEB_PORT=9201

uc-advanced-automations
```

A hardened example systemd unit is included at:

```text
systemd/uc-advanced-automations.service
```

### Option 4: Direct Remote custom integration

The direct Remote package must be built in an ARM64 environment. Use the included GitHub Actions workflow or an ARM64 build host.

Build the archive:

```bash
bash ./tools/build_remote.sh aarch64
```

The generated package follows this naming pattern:

```text
uc-intg-advanced-automations-v1.0.3-aarch64.tar.gz
```

Verify it before installation:

```bash
bash ./tools/verify_remote_archive.sh \
  ./uc-intg-advanced-automations-v1.0.3-aarch64.tar.gz
```

> [!NOTE]
> The Python wheel is intended for external or server deployment. It is not the custom-integration archive installed directly on a Remote.

## First-time setup

### 1. Generate a Web Configurator PIN

On the Remote, open the profile or Web Configurator access settings and generate a current Web Configurator PIN.

The PIN is used only once to create a persistent API key.

### 2. Start the integration setup flow

Open the Advanced Automations integration on the Remote and begin setup.

Enter:

* The Remote IP address or hostname
* The current Web Configurator PIN

### 3. Persistent API-key creation

During setup, the integration:

1. Authenticates against the Remote Core REST API as `web-configurator`
2. Uses the entered PIN as the temporary password
3. Creates an `admin`-scoped persistent API key
4. Stores the returned one-time API key in the private configuration file
5. Immediately discards the submitted PIN

The PIN is not written to configuration and is not included in normal logs.

When reconfiguring the same Remote:

* Leave the PIN empty to retain the existing API key
* Enter a new PIN to create a replacement API key

### 4. Add the integration entities

After setup, add the integration's entities to the Remote configuration:

* **Advanced Automations**
* **Last automation triggered**

The main Remote entity is refreshed when automation commands or touchscreen pages change. A manual **Refresh entities** action is also available in the web editor.

### 5. Open the editor

For external installations, open:

```text
http://SERVER-IP:9201
```

If the page is unavailable, check the actual selected port in:

```text
/config/web-port.txt
```

or, for standalone Docker deployments:

```text
./data/web-port.txt
```

## Creating an automation

### Step 1: Automation details

Configure:

* Name
* Description
* Enabled or disabled state
* Optional Remote command
* Optional touchscreen exposure
* Run mode
* Optional maximum runtime
* Cancellation cleanup sequence
* Rollback sequence

### Step 2: Choose entities

Open the entity dropdown and select the entities used by the automation.

The picker supports:

* Search
* Entity-type filters
* Source-integration filters
* Select shown
* Clear unused

Entities already referenced by a trigger, condition or sequence step cannot be removed accidentally.

### Step 3: Define triggers

Add one or more trigger cards, configure their values, then select the trigger-combination behavior.

Common examples:

* Start when a media player changes from `OFF` to `ON`
* Start when a sensor remains unavailable for 30 seconds
* Start when a numeric value rises above a threshold
* Run every weekday at a defined local time
* Run when another automation fails
* Expose a manual virtual trigger

### Step 4: Define sequence

Add sequence steps and arrange them with drag and drop.

Steps may contain nested branches, execution policies and explicit timeout behavior. Use the automation overview after saving to review the complete trigger and execution timeline.

## Wait for condition

A wait step defines:

* One or more conditions
* Whether every condition or any condition must match
* Timeout
* Polling interval
* Time reference
* Match outcome
* Timeout outcome

Time reference options:

* **From automation trigger**
* **From when this step begins**

When the condition matches:

* Continue the sequence
* Stop successfully
* Run a match branch

When the timeout expires:

* Continue the sequence
* Stop successfully
* Fail the automation
* Run a timeout branch

Example:

> Wait up to 10 seconds from the trigger for playback to resume. If playback resumes, stop successfully. If the timeout expires, continue with the remaining sequence.

## Webhooks and manual triggers

Webhook triggers are available locally at:

```text
POST /api/webhooks/{webhook_id}
```

Manual virtual triggers can be started through:

```text
POST /api/triggers/{trigger_id}/run
```

Keep webhook identifiers private if the editor/API is reachable by other devices on the network.

## Data and persistence

The data directory contains the integration's persistent state.

| File                      | Purpose                                                                          |
| ------------------------- | -------------------------------------------------------------------------------- |
| `config.json`             | Remote settings and automation definitions                                       |
| `automation-data.sqlite3` | Run history, active-step data, events, revisions and deleted-automation recovery |
| `web-port.txt`            | Actual web editor port selected at startup                                       |
| `config.invalid-*.json`   | Backup created when invalid configuration recovery is required                   |

For installer-managed deployments, the data directory is normally `/config`.

For the included Docker Compose deployment, it is `./data` on the host and `/data` inside the container.

### Backup

Stop or pause writes before taking a consistent manual backup, then copy the complete data directory:

```bash
cp -a ./data ./data-backup
```

For Docker Compose:

```bash
docker compose stop
cp -a ./data ./data-backup
docker compose start
```

### Restore

Stop the service, replace the data directory with the backup, then start the service again.

Keep `config.json` and `automation-data.sqlite3` together so automation definitions, revisions and run history remain synchronized.

## Configuration

Default settings:

```json
{
  "settings": {
    "core_url": "ws://remote.local/ws",
    "api_key": "",
    "web_host": "0.0.0.0",
    "web_port": 9201,
    "timezone": "Europe/Berlin",
    "request_timeout_seconds": 10
  },
  "automations": []
}
```

The normal setup flow and web editor should be used instead of editing `config.json` manually.

### Environment variables

| Variable                   |                      Default | Purpose                                               |
| -------------------------- | ---------------------------: | ----------------------------------------------------- |
| `UC_EXTERNAL`              |      `true` in the container | Marks the service as externally hosted                |
| `UC_RUNTIME_MODE`          |                   `external` | Selects the runtime profile                           |
| `UC_AUTOMATIONS_DATA_DIR`  |  `/data` or `UC_CONFIG_HOME` | Persistent data directory                             |
| `UC_CONFIG_HOME`           |                        unset | Installer-provided persistent configuration directory |
| `UC_INTEGRATION_INTERFACE` |                    `0.0.0.0` | Integration API bind address                          |
| `UC_INTEGRATION_HTTP_PORT` |                       `9090` | Integration API port                                  |
| `UC_AUTOMATIONS_WEB_HOST`  |                    `0.0.0.0` | Web editor bind address                               |
| `UC_AUTOMATIONS_WEB_PORT`  |                       `9201` | Preferred web editor port                             |
| `UC_DISABLE_MDNS_PUBLISH`  | `true` in managed containers | Disables duplicate integration discovery publication  |

## Web API

| Method   | Endpoint                                             | Purpose                                           |
| -------- | ---------------------------------------------------- | ------------------------------------------------- |
| `GET`    | `/api/health`                                        | Liveness and startup diagnostics                  |
| `GET`    | `/api/status`                                        | Connection, trigger and execution status          |
| `GET`    | `/api/settings`                                      | Read non-PIN configuration settings               |
| `PUT`    | `/api/settings`                                      | Update settings                                   |
| `POST`   | `/api/settings/test`                                 | Test the Remote connection                        |
| `GET`    | `/api/entities`                                      | List available entities and current attributes    |
| `GET`    | `/api/entities/{id}/commands`                        | List supported commands for an entity             |
| `POST`   | `/api/integration/refresh`                           | Refresh integration entities on the Remote        |
| `GET`    | `/api/automations`                                   | List automations and history summaries            |
| `POST`   | `/api/automations`                                   | Create an automation                              |
| `PUT`    | `/api/automations/{id}`                              | Update an automation                              |
| `DELETE` | `/api/automations/{id}`                              | Delete and archive an automation                  |
| `POST`   | `/api/automations/{id}/run`                          | Start an automation                               |
| `GET`    | `/api/automations/{id}/history`                      | Read persistent run history and active-step state |
| `GET`    | `/api/automations/{id}/revisions`                    | List retained revisions                           |
| `POST`   | `/api/automations/{id}/revisions/{revision}/restore` | Restore a revision                                |
| `GET`    | `/api/revisions/deleted`                             | List deleted-automation revisions                 |
| `GET`    | `/api/revisions/{revision}`                          | Read one revision                                 |
| `POST`   | `/api/revisions/{revision}/restore-deleted`          | Restore a deleted automation                      |
| `POST`   | `/api/triggers/{trigger}/run`                        | Run a manual virtual trigger                      |
| `POST`   | `/api/webhooks/{webhook}`                            | Run a webhook trigger                             |
| `GET`    | `/api/logs`                                          | Read diagnostic run events                        |

Invalid automation payloads return structured HTTP `400` responses suitable for display in the editor. They should not produce an HTTP `500` error.

## Troubleshooting

### The editor returns `404: Not Found`

The Integration API and web editor use different ports. A plain HTTP request to the Integration API port may return `404` normally.

Open the editor on port 9201 or read the actual port from `web-port.txt`.

### Port 9201 is already in use

The editor scans upward automatically:

```text
9201 → 9202 → 9203 → ...
```

Check service logs or `web-port.txt` for the selected port.

### The Remote cannot connect to the integration

Check:

* The Integration API port is reachable from the Remote
* Host networking is enabled for Docker
* A firewall is not blocking the assigned Integration API port
* The integration is configured with the correct address
* `/api/health` reports `integration_api_ready: true`

### Remote API authentication fails

* Generate a new Web Configurator PIN on the Remote
* Confirm Web Configurator access is enabled
* Enter the Remote address without an unrelated proxy path
* Retry the setup flow with the new PIN

The PIN is only valid for creating the persistent API key and is not retained by the integration.

### Entities or commands are missing

* Select **Refresh entities** in the editor
* Confirm the integration entities have been added to the Remote
* Reconnect or reload the integration on the Remote
* Check `/api/status` and the service logs for Core API errors

### The browser still shows an older interface

Perform a hard refresh:

```text
Ctrl+F5
```

The frontend assets are versioned, but an older browser cache or reverse proxy may still need to be cleared after an upgrade.

### An automation was deleted or changed accidentally

Open the revision history or deleted-automation archive. v1.0.3 retains up to 50 revisions per automation and supports restoration of deleted automations with their original identifier.

## Development

Install development dependencies and run the test suite:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install pytest pytest-asyncio
pytest -q
```

Build a wheel:

```bash
python -m pip wheel . --no-deps --no-build-isolation --wheel-dir dist
```

Validate Python syntax:

```bash
python -m compileall -q src tests
```

## Project structure

```text
src/uc_advanced_automations/
├── api/static frontend modules
├── config_store.py        # persistent configuration and migration
├── core_client.py         # Remote Core API client
├── database.py            # SQLite history and revision storage
├── engine.py              # automation execution engine
├── integration.py         # entities exposed to the Remote
├── models.py              # validated automation schema
├── remote_auth.py         # persistent API-key creation
├── setup_flow.py          # Remote integration setup
├── triggers.py            # trigger scheduling and evaluation
└── web.py                 # local editor and API routes
```

## License

Released under the [MIT License](LICENSE).

Material Symbols assets are provided under the terms documented in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).