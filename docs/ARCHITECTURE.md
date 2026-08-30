# Advanced Automations v2 architecture

Advanced Automations v2 keeps the automation engine independent from the transport/framework layers.

## Integration API: ucapi-framework

`ucapi-framework` 1.9.6 owns the `IntegrationAPI` instance and the standard Remote connection lifecycle. Generated Remote and Sensor entities use the framework entity base classes.

The automation entity definitions are still rebuilt by `IntegrationController` because their commands and UI pages are dynamic and come from the local automation database rather than from a conventional external device.

## Remote Core API: Unfurled

`unfurled` 0.5.0 owns communication with the hosting Unfolded Circle Remote:

- authenticated REST requests via `CoreAPI`
- API-key creation and safe same-name replacement via `Remote.auth.generate_key()`
- reconnecting WebSocket transport via `RemoteWebSocketClient`
- event subscription and transport keepalive

`CoreClient` is intentionally retained as a narrow application adapter so the automation engine, trigger manager and web editor do not depend directly on Unfurled internals. REST-capable operations use `CoreAPI`; the small number of Core commands that are WebSocket-only use Unfurled's WebSocket transport with local request/response correlation.

## Domain layer

The following remain application-owned and transport-agnostic:

- `AutomationEngine`
- `TriggerManager`
- `AutomationDatabase`
- `ConfigStore`
- `IntegrationController`
- web editor/API

This boundary makes future Unfurled and ucapi-framework upgrades local to the adapter/framework integration instead of spreading protocol code through the automation engine.

## Authentication

The Web Configurator PIN is only used during setup. Unfurled creates or rotates the persistent `Advanced Automations` admin API key, the key is persisted in the existing private configuration, and the PIN is discarded.

Because Unfurled replaces an existing same-named key before issuing a new one, reconfiguration is idempotent and does not fail on the Remote Core API's duplicate-name HTTP 422 response.
