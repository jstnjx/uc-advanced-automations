<!-- Advanced Automations v2.0.0 -->
# Advanced Automations v2 sequence steps

Advanced Automations v2 adds orchestration and run-scoped values without introducing a scripting runtime. New steps use the same timeout, retry, failure-branch and rollback policies as existing sequence steps.

## Set variable

Stores a value for the current run. The source can be a literal value, another run variable, or an entity attribute. Variables are isolated per run and are not persisted after the run finishes.

Variable names must start with a letter or underscore and may contain letters, numbers and underscores.

## Template / Transform value

Renders a value into a run variable. Templates support:

- `{{ variable }}` or `{{ vars.variable }}`
- `{{ automation.id }}`, `{{ automation.name }}`, `{{ automation.command }}`
- `{{ run.id }}` and `{{ run.source }}`
- inline filters: `upper`, `lower`, `trim`, `length`, `json`, `int`, `float`, `bool`

An exact placeholder preserves the original value type. Mixed text templates render to a string. Output can additionally be coerced to string, number, boolean or JSON.

Templates are also resolved in entity-command parameters, HTTP URLs/headers/bodies, log messages, condition comparison values, command-sequence parameters and event filters.

## Choose / Switch

Evaluates one template expression and runs the first matching case. Supported operators include equality, numeric comparison, contains/in and truthy/falsy checks. If no case matches, the Default branch runs.

Each case and the Default branch can contain any sequence step, including another Choose / Switch.

## Wait for event

Waits for a named Remote Core WebSocket event instead of polling entity state. Optional payload filters use dotted paths and all configured filters must match.

The received payload can be stored in a run variable for later templates. Timeout behavior can fail the automation, continue the sequence or stop successfully. The Core listener is always removed after completion, timeout or cancellation.

## Run automation

Starts another configured automation. It can:

- fire and forget;
- wait for the child run to finish;
- propagate child failure/cancellation to the parent;
- optionally pass a copy of the current run variables to the child.

Recursive automation call chains are rejected at runtime.

## Stop automation

Stops the current automation successfully or cancels all active runs of another configured automation. When stopping another automation, the step can optionally fail if that automation is not running.

## Command sequence / Macro

Two modes are available:

- **Command sequence:** execute multiple entity commands in order with an optional delay after each command.
- **Remote macro:** execute a configured Remote macro using `macro.run`.

The step-level execution policy applies to the complete command sequence or macro invocation.

## Activity control

Starts, stops or toggles a selected Remote activity. Start/stop use the Core `activity.on` and `activity.off` entity commands. Toggle reads the current activity state first and then chooses the appropriate command.

## Blueprints and entity selection

Entity references used by Set variable, Command sequence / Macro and Activity control participate in the normal entity-selection validation and blueprint placeholder mapping. Nested Choose / Switch branches are traversed recursively just like If / Else, Wait and Parallel branches.
