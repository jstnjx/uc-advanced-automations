/* Advanced Automations v1.0.2 */

function withExecutionPolicy(step) {
  return {
    ...step,
    execution_timeout_ms: 0,
    retry_count: 0,
    retry_delay_ms: 1000,
    retry_backoff: "fixed",
    failure_action: "fail",
    failure_steps: [],
  };
}

function makeStep(type) {
  let step;
  switch (type) {
    case "command": step = { type, entity_id: firstEntityId({ commandable: true }), cmd_id: "", params: {} }; break;
    case "delay": step = { type, milliseconds: 1000 }; break;
    case "condition": step = { type, mode: "all", conditions: [makeCondition("entity")], then: [], else: [] }; break;
    case "wait": step = {
      type,
      mode: "all",
      conditions: [makeCondition("entity")],
      timeout_ms: 30000,
      interval_ms: 500,
      time_reference: "step",
      on_match: "continue",
      on_timeout: "fail",
      match_steps: [],
      timeout_steps: [],
    }; break;
    case "parallel": step = {
      type,
      wait_for: "all",
      branches: [
        { name: "Branch 1", steps: [] },
        { name: "Branch 2", steps: [] },
      ],
    }; break;
    case "http": step = { type, method: "POST", url: "http://", headers: {}, body: {}, timeout_seconds: 10, status_min: 200, status_max: 299 }; break;
    case "log": step = { type, message: "Automation reached this step", level: "info" }; break;
    default: throw new Error(`Unknown step type: ${type}`);
  }
  return withExecutionPolicy({ ...step, _ui_id: createId() });
}

function ensureStepId(step) {
  step._ui_id ||= createId();
  return step._ui_id;
}

function makeCondition(kind) {
  if (kind === "time") return { kind: "time", operator: "between", start: "18:00", end: "23:59", weekdays: [0, 1, 2, 3, 4, 5, 6] };
  const entityId = firstEntityId();
  return { kind: "entity", entity_id: entityId, attribute: defaultAttribute(entityId), operator: "eq", value: "ON" };
}

function renderSteps(container, steps, branchName) {
  container.replaceChildren();
  container.classList.add("drop-zone");
  if (!steps.length) {
    const empty = document.createElement("div");
    empty.className = "steps-empty";
    empty.textContent = branchName === "root" ? "No steps. Add the first sequence step." : "No steps in this branch.";
    container.append(empty);
    return;
  }
  steps.forEach((step, index) => container.append(renderStep(step, index, steps)));
}

function renderStep(step, index, siblings) {
  const stepId = ensureStepId(step);
  const wrapper = document.createElement("article");
  wrapper.className = "step";
  const collapsed = state.collapsedSteps.has(stepId);
  wrapper.classList.toggle("collapsed", collapsed);
  const head = document.createElement("div");
  head.className = "step-head collapsible-head";
  const title = document.createElement("div");
  title.className = "step-title";
  const handle = dragHandle();
  const toggle = toolButton(collapsed ? "expand_more" : "expand_less", collapsed ? "Expand step" : "Collapse step", () => {
    if (state.collapsedSteps.has(stepId)) state.collapsedSteps.delete(stepId); else state.collapsedSteps.add(stepId);
    renderEditor();
  });
  const number = document.createElement("span");
  number.className = "step-number";
  number.textContent = String(index + 1).padStart(2, "0");
  const label = document.createElement("span");
  label.textContent = stepLabel(step.type);
  title.append(handle, toggle, number, label);
  const tools = document.createElement("div");
  tools.className = "step-tools";
  tools.append(toolButton("delete", "Delete step", () => {
    siblings.splice(index, 1);
    markDirty();
    renderEditor();
  }));
  head.append(title, tools);
  const body = document.createElement("div");
  body.className = "step-body";
  body.classList.toggle("hidden", collapsed);
  body.append(renderStepBody(step), executionPolicy(step));
  wrapper.append(head, body);
  attachSortable(handle, wrapper, siblings, index, "step");
  return wrapper;
}

function dragHandle() {
  const handle = materialIcon("drag_indicator", "drag-handle");
  handle.setAttribute("aria-label", "Drag to reorder");
  handle.draggable = true;
  return handle;
}

function attachSortable(handle, card, siblings, index, kind) {
  handle.addEventListener("dragstart", (event) => {
    state.drag = { siblings, index, kind };
    card.classList.add("dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", `${kind}:${index}`);
  });
  handle.addEventListener("dragend", () => {
    state.drag = null;
    document.querySelectorAll(".dragging,.drag-over").forEach((node) => node.classList.remove("dragging", "drag-over"));
  });
  card.addEventListener("dragover", (event) => {
    if (!state.drag || state.drag.kind !== kind || state.drag.siblings !== siblings) return;
    event.preventDefault();
    card.classList.add("drag-over");
  });
  card.addEventListener("dragleave", () => card.classList.remove("drag-over"));
  card.addEventListener("drop", (event) => {
    event.preventDefault();
    card.classList.remove("drag-over");
    if (!state.drag || state.drag.kind !== kind || state.drag.siblings !== siblings) return;
    const from = state.drag.index;
    const to = index;
    if (from === to) return;
    const [moved] = siblings.splice(from, 1);
    siblings.splice(to, 0, moved);
    markDirty();
    renderEditor();
  });
}

function stepLabel(type) {
  return {
    command: "Entity",
    delay: "Delay",
    condition: "If / else condition",
    wait: "Wait for condition",
    parallel: "Parallel group",
    http: "HTTP request",
    log: "Log message",
  }[type] || type;
}

function toolButton(iconName, label, handler) {
  const button = document.createElement("button");
  button.type = "button";
  button.append(materialIcon(iconName));
  button.setAttribute("aria-label", label);
  button.addEventListener("click", handler);
  return button;
}

function renderStepBody(step) {
  const grid = document.createElement("div");
  grid.className = "step-grid";
  if (step.type === "command") {
    grid.append(renderCommandStep(step));
  } else if (step.type === "delay") {
    grid.append(numberField("Milliseconds", step.milliseconds, (value) => { step.milliseconds = value; }, 0, 86400000));
  } else if (step.type === "condition") {
    const block = document.createElement("div");
    block.className = "wide";
    block.append(conditionGroup(step), branchEditor("Then", "then", step.then || (step.then = []), step), branchEditor("Else", "else", step.else || (step.else = []), step));
    grid.append(block);
  } else if (step.type === "wait") {
    step.time_reference ||= "step";
    step.on_match ||= "continue";
    step.on_timeout ||= "fail";
    step.match_steps ||= [];
    step.timeout_steps ||= [];
    grid.append(
      selectField("Time reference", step.time_reference, [
        { value: "trigger", label: "From automation trigger" },
        { value: "step", label: "From when this step begins" },
      ], (value) => { step.time_reference = value; }),
      numberField("Timeout (ms)", step.timeout_ms ?? 30000, (value) => { step.timeout_ms = value; }, 1, 86400000),
      numberField("Poll interval (ms)", step.interval_ms ?? 500, (value) => { step.interval_ms = value; }, 100, 60000),
      selectField("When condition matches", step.on_match, [
        { value: "continue", label: "Continue sequence" },
        { value: "stop", label: "Stop sequence successfully" },
        { value: "branch", label: "Run match branch" },
      ], (value) => { step.on_match = value; renderEditor(); }),
      selectField("When timeout expires", step.on_timeout, [
        { value: "continue", label: "Continue sequence" },
        { value: "stop", label: "Stop successfully" },
        { value: "fail", label: "Fail automation" },
        { value: "branch", label: "Run timeout branch" },
      ], (value) => { step.on_timeout = value; renderEditor(); }),
    );
    const conditions = document.createElement("div");
    conditions.className = "wide";
    conditions.append(conditionGroup(step));
    grid.append(conditions);
    if (step.on_match === "branch") grid.append(branchEditor("When condition matches", "match_steps", step.match_steps, step));
    if (step.on_timeout === "branch") grid.append(branchEditor("When timeout expires", "timeout_steps", step.timeout_steps, step));
  } else if (step.type === "parallel") {
    step.branches ||= [{ name: "Branch 1", steps: [] }, { name: "Branch 2", steps: [] }];
    grid.append(selectField("Complete when", step.wait_for || "all", [
      { value: "all", label: "All branches finish" },
      { value: "any", label: "The first branch finishes" },
    ], (value) => { step.wait_for = value; }));
    const block = document.createElement("div");
    block.className = "wide parallel-branches";
    step.branches.forEach((branch, index) => block.append(parallelBranchEditor(step, branch, index)));
    const addBranch = document.createElement("button");
    addBranch.type = "button";
    addBranch.className = "button ghost small button-with-icon";
    setButtonContent(addBranch, "add", "Add parallel branch");
    addBranch.addEventListener("click", () => {
      step.branches.push({ name: `Branch ${step.branches.length + 1}`, steps: [] });
      markDirty();
      renderEditor();
    });
    block.append(addBranch);
    grid.append(block);
  } else if (step.type === "http") {
    grid.append(
      selectField("Method", step.method || "POST", ["GET", "POST", "PUT", "PATCH", "DELETE"], (value) => { step.method = value; }),
      textField("URL", step.url || "", (value) => { step.url = value; }, "http://home-assistant.local:8123/api/…"),
      jsonField("Headers (JSON)", step.headers || {}, (value) => { step.headers = value; }, true),
      jsonField("Body (JSON or value)", step.body ?? {}, (value) => { step.body = value; }, true),
      numberField("HTTP timeout (seconds)", step.timeout_seconds ?? 10, (value) => { step.timeout_seconds = value; }, 1, 120),
      rangeFields(step),
    );
  } else if (step.type === "log") {
    grid.append(
      selectField("Level", step.level || "info", ["info", "warning", "error", "success"], (value) => { step.level = value; }),
      textField("Message", step.message || "", (value) => { step.message = value; }),
    );
  }
  return grid;
}

function parallelBranchEditor(parent, branch, index) {
  const section = document.createElement("section");
  section.className = "branch parallel-branch";
  const head = document.createElement("div");
  head.className = "branch-head";
  const name = document.createElement("input");
  name.value = branch.name || `Branch ${index + 1}`;
  name.addEventListener("input", () => { branch.name = name.value; markDirty(); });
  const actions = document.createElement("div");
  const add = document.createElement("button");
  add.type = "button";
  add.className = "button ghost small button-with-icon";
  setButtonContent(add, "add", "Add step");
  add.addEventListener("click", () => openStepPicker(branch.steps));
  const remove = toolButton("delete", "Delete branch", () => {
    if (parent.branches.length <= 2) return;
    parent.branches.splice(index, 1);
    markDirty();
    renderEditor();
  });
  remove.disabled = parent.branches.length <= 2;
  actions.append(add, remove);
  head.append(name, actions);
  const container = document.createElement("div");
  container.className = "steps";
  renderSteps(container, branch.steps || (branch.steps = []), "parallel");
  section.append(head, container);
  return section;
}

async function loadCommandDefinitions(entityId) {
  if (!entityId || isSensor(entityId)) return { entity: findEntity(entityId), commands: [] };
  if (state.commandDefinitions.has(entityId)) return state.commandDefinitions.get(entityId);
  const pending = api(`/api/entities/${encodeURIComponent(entityId)}/commands`)
    .catch((error) => ({ error: error.message, entity: findEntity(entityId), commands: [] }));
  state.commandDefinitions.set(entityId, pending);
  const resolved = await pending;
  state.commandDefinitions.set(entityId, resolved);
  return resolved;
}

function getCommandDefinitions(entityId) {
  const value = state.commandDefinitions.get(entityId);
  return value && typeof value.then !== "function" ? value : null;
}

function renderCommandStep(step) {
  const wrapper = document.createElement("div");
  wrapper.className = "wide command-editor";
  const grid = document.createElement("div");
  grid.className = "step-grid";
  grid.append(entityField("Command-capable entity", step.entity_id || "", (value) => {
    step.entity_id = value;
    step.cmd_id = "";
    step.params = {};
    loadCommandDefinitions(value).then(() => { if (selectedAutomation()) renderEditor(); });
  }, { commandable: true }));

  if (step.entity_id && isSensor(step.entity_id)) {
    const note = document.createElement("div");
    note.className = "read-only-note wide";
    note.textContent = "Sensors are read-only entities. Select a command-capable entity for this step.";
    grid.append(note);
    wrapper.append(grid);
    return wrapper;
  }

  const definitions = getCommandDefinitions(step.entity_id);
  if (!definitions && step.entity_id) {
    const loading = fieldWrap("Command");
    const input = document.createElement("input");
    input.disabled = true;
    input.placeholder = "Loading Remote command metadata…";
    loading.append(input);
    grid.append(loading);
    loadCommandDefinitions(step.entity_id).then(() => { if (selectedAutomation()) renderEditor(); });
  } else if (definitions?.commands?.length) {
    const field = fieldWrap("Command");
    const control = document.createElement("select");
    if (step.cmd_id && !definitions.commands.some((item) => item.id === step.cmd_id)) {
      control.append(new Option(`${step.cmd_id} (not currently advertised)`, step.cmd_id));
    }
    for (const command of definitions.commands) {
      control.append(new Option(`${localizedName(command.name, command.id)} · ${command.id}`, command.id));
    }
    control.value = step.cmd_id || definitions.commands[0].id;
    if (!step.cmd_id) {
      step.cmd_id = control.value;
      step.params = defaultsForCommand(definitions.commands[0]);
    }
    control.addEventListener("change", () => {
      step.cmd_id = control.value;
      const command = definitions.commands.find((item) => item.id === step.cmd_id);
      step.params = defaultsForCommand(command);
      markDirty();
      renderEditor();
    });
    field.append(control);
    grid.append(field);
    grid.append(renderCommandParameters(step, definitions.commands.find((item) => item.id === step.cmd_id), definitions.entity));
  } else {
    const note = document.createElement("div");
    note.className = "read-only-note wide";
    note.textContent = definitions?.error
      ? `Remote command metadata could not be loaded: ${definitions.error}`
      : "This entity does not advertise any commands and cannot be used as a command step.";
    grid.append(note);
  }
  wrapper.append(grid);
  return wrapper;
}

function defaultsForCommand(command) {
  const params = {};
  for (const definition of command?.params || []) {
    if (definition.default !== undefined) params[definition.param] = definition.default;
    else if (!definition.optional && definition.type === "bool") params[definition.param] = false;
    else if (!definition.optional && Array.isArray(definition.values) && definition.values.length) params[definition.param] = definition.values[0];
  }
  return params;
}

function renderCommandParameters(step, command, entity) {
  const holder = document.createElement("div");
  holder.className = "wide parameter-grid";
  const definitions = command?.params || [];
  if (!definitions.length) {
    const text = document.createElement("p");
    text.className = "metadata-note";
    text.textContent = "This command has no parameters.";
    holder.append(text);
    return holder;
  }
  step.params ||= {};
  for (const definition of definitions) holder.append(commandParameterField(step, definition, entity));
  const advanced = document.createElement("details");
  advanced.className = "wide advanced-params";
  const summary = document.createElement("summary");
  summary.textContent = "Advanced JSON parameters";
  advanced.append(summary, jsonField("Parameters", step.params, (value) => { step.params = value; }, true));
  holder.append(advanced);
  return holder;
}

function commandParameterField(step, definition, entity) {
  const name = definition.param;
  const labelText = localizedName(definition.name, name) + (definition.optional ? " (optional)" : "");
  const label = fieldWrap(labelText);
  let input;
  let values = definition.values;
  if (definition.type === "selection" && !Array.isArray(values)) {
    const items = definition.items || {};
    const source = entity?.[items.source] || {};
    values = source?.[items.field] || [];
  }
  if (definition.type === "enum" || definition.type === "selection") {
    input = document.createElement("select");
    if (definition.optional) input.append(new Option("Not set", ""));
    for (const value of values || []) input.append(new Option(String(value), String(value)));
    input.value = step.params[name] ?? definition.default ?? "";
    input.addEventListener("change", () => setCommandParam(step, name, input.value === "" && definition.optional ? undefined : input.value));
  } else if (definition.type === "bool") {
    input = document.createElement("select");
    if (definition.optional) input.append(new Option("Not set", ""));
    input.append(new Option("True", "true"), new Option("False", "false"));
    const current = step.params[name];
    input.value = current === undefined ? "" : String(Boolean(current));
    input.addEventListener("change", () => setCommandParam(step, name, input.value === "" ? undefined : input.value === "true"));
  } else {
    input = document.createElement("input");
    input.type = definition.type === "number" ? "number" : "text";
    if (definition.min !== undefined) input.min = definition.min;
    if (definition.max !== undefined) input.max = definition.max;
    if (definition.step !== undefined) input.step = definition.step;
    if (definition.regex) input.pattern = definition.regex;
    input.value = step.params[name] ?? definition.default ?? "";
    input.placeholder = definition.optional ? "Not set" : "Required";
    input.addEventListener("input", () => {
      const raw = input.value;
      setCommandParam(step, name, raw === "" && definition.optional ? undefined : definition.type === "number" ? Number(raw) : raw);
    });
  }
  label.append(input);
  if (definition.unit) {
    const small = document.createElement("small");
    small.textContent = `Unit: ${definition.unit}`;
    label.append(small);
  }
  return label;
}

function setCommandParam(step, name, value) {
  step.params ||= {};
  if (value === undefined) delete step.params[name];
  else step.params[name] = value;
  markDirty();
}

function rangeFields(step) {
  const wrap = fieldWrap("Accepted HTTP status range");
  const row = document.createElement("div");
  row.style.display = "grid";
  row.style.gridTemplateColumns = "1fr 1fr";
  row.style.gap = "7px";
  const min = document.createElement("input");
  min.type = "number"; min.min = "100"; min.max = "599"; min.value = step.status_min ?? 200;
  min.addEventListener("input", () => { step.status_min = Number(min.value); markDirty(); });
  const max = document.createElement("input");
  max.type = "number"; max.min = "100"; max.max = "599"; max.value = step.status_max ?? 299;
  max.addEventListener("input", () => { step.status_max = Number(max.value); markDirty(); });
  row.append(min, max);
  wrap.append(row);
  return wrap;
}

function conditionGroup(group) {
  const wrapper = document.createElement("div");
  wrapper.className = "condition-list";
  const toolbar = document.createElement("div");
  toolbar.className = "condition-toolbar";
  toolbar.append(selectField("Evaluate conditions as", group.mode || "all", [
    { value: "all", label: "Require every condition to match" },
    { value: "any", label: "Continue when any condition matches" },
  ], (value) => { group.mode = value; }));
  const addEntity = document.createElement("button");
  addEntity.type = "button"; addEntity.className = "button ghost small button-with-icon";
  setButtonContent(addEntity, "add", "Entity condition");
  addEntity.addEventListener("click", () => { group.conditions.push(makeCondition("entity")); markDirty(); renderEditor(); });
  const addTime = document.createElement("button");
  addTime.type = "button"; addTime.className = "button ghost small button-with-icon";
  setButtonContent(addTime, "add", "Time condition");
  addTime.addEventListener("click", () => { group.conditions.push(makeCondition("time")); markDirty(); renderEditor(); });
  toolbar.append(addEntity, addTime);
  wrapper.append(toolbar);
  (group.conditions || []).forEach((condition, index) => wrapper.append(conditionRow(condition, index, group.conditions)));
  return wrapper;
}

function conditionRow(condition, index, conditions) {
  const row = document.createElement("div");
  row.className = `condition-row ${(condition.kind || "entity") === "time" ? "time" : ""}`;
  row.append(selectField("Source", condition.kind || "entity", [
    { value: "entity", label: "entity attribute" },
    { value: "time", label: "Time window" },
  ], (value) => { conditions[index] = makeCondition(value); markDirty(); renderEditor(); }));

  if ((condition.kind || "entity") === "time") {
    row.append(
      timeField("Start", condition.start || "18:00", (value) => { condition.start = value; }),
      selectField("Operator", condition.operator || "between", ["between", "outside"], (value) => { condition.operator = value; }),
      timeField("End", condition.end || "23:59", (value) => { condition.end = value; }),
    );
  } else {
    row.append(
      entityField("entity", condition.entity_id || "", (value) => {
        condition.entity_id = value;
        condition.attribute = defaultAttribute(value);
        renderEditor();
      }),
      attributeField("Attribute", condition.entity_id, condition.attribute || "state", (value) => { condition.attribute = value; }),
      selectField("Operator", condition.operator || "eq", [
        "eq", "ne", "gt", "gte", "lt", "lte", "contains", "not_contains", "in", "not_in", "exists", "not_exists", "truthy", "falsy",
      ], (value) => { condition.operator = value; renderEditor(); }),
      conditionValueField(condition),
    );
  }
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "button danger ghost small icon-only-button";
  remove.append(materialIcon("delete"));
  remove.setAttribute("aria-label", "Remove condition");
  remove.addEventListener("click", async () => {
    if (conditions.length <= 1) {
      await openMessageDialog({ title: "Condition required", message: "A condition group must contain at least one condition." });
      return;
    }
    conditions.splice(index, 1);
    markDirty();
    renderEditor();
  });
  row.append(remove);
  return row;
}

function conditionValueField(condition) {
  const noValue = ["exists", "not_exists", "truthy", "falsy"].includes(condition.operator);
  if (noValue) {
    const holder = fieldWrap("Value");
    const input = document.createElement("input");
    input.disabled = true;
    input.placeholder = "Not required";
    holder.append(input);
    return holder;
  }
  return textField("Value", valueToInput(condition.value), (value) => { condition.value = parseLooseValue(value); }, "ON, 20, true…");
}

function branchEditor(label, key, steps, parent) {
  const branch = document.createElement("section");
  branch.className = `branch ${key}`;
  const head = document.createElement("div");
  head.className = "branch-head";
  const title = document.createElement("strong");
  title.textContent = label;
  const add = document.createElement("button");
  add.type = "button";
  add.className = "button ghost small button-with-icon";
  setButtonContent(add, "add", "Add step");
  add.addEventListener("click", () => openStepPicker(parent[key]));
  head.append(title, add);
  const container = document.createElement("div");
  container.className = "steps";
  renderSteps(container, steps, key);
  branch.append(head, container);
  return branch;
}

function executionPolicy(step) {
  step.failure_action ||= step.continue_on_error ? "continue" : "fail";
  step.failure_steps ||= [];
  const details = document.createElement("details");
  details.className = "advanced-options execution-policy";
  const summary = document.createElement("summary");
  summary.textContent = "Execution policy";
  const grid = document.createElement("div");
  grid.className = "step-grid";
  grid.append(
    numberField("Step timeout (ms)", step.execution_timeout_ms ?? 0, (value) => { step.execution_timeout_ms = value; }, 0, 86400000),
    numberField("Retry count", step.retry_count ?? 0, (value) => { step.retry_count = value; }, 0, 20),
    numberField("Retry delay (ms)", step.retry_delay_ms ?? 1000, (value) => { step.retry_delay_ms = value; }, 0, 86400000),
    selectField("Retry delay behavior", step.retry_backoff || "fixed", [
      { value: "fixed", label: "Fixed delay" },
      { value: "exponential", label: "Exponential backoff" },
    ], (value) => { step.retry_backoff = value; }),
    selectField("After final failure", step.failure_action, [
      { value: "fail", label: "Fail automation" },
      { value: "continue", label: "Continue sequence" },
      { value: "branch", label: "Run failure branch" },
      { value: "rollback", label: "Run automation rollback" },
    ], (value) => { step.failure_action = value; renderEditor(); }),
  );
  details.append(summary, grid);
  if (step.failure_action === "branch") details.append(branchEditor("Failure branch", "failure_steps", step.failure_steps, step));
  return details;
}

function fieldWrap(labelText) {
  const label = document.createElement("label");
  label.className = "field";
  const title = document.createElement("span");
  title.textContent = labelText;
  label.append(title);
  return label;
}

function textField(labelText, value, onChange, placeholder = "") {
  const label = fieldWrap(labelText);
  const input = document.createElement("input");
  input.type = "text";
  input.value = value ?? "";
  input.placeholder = placeholder;
  input.addEventListener("input", () => { onChange(input.value); markDirty(); });
  label.append(input);
  return label;
}

function valueField(labelText, value, onChange, placeholder) {
  return textField(labelText, value == null ? "" : valueToInput(value), (raw) => {
    onChange(raw.trim() === "" ? null : parseLooseValue(raw));
  }, placeholder);
}

function numberField(labelText, value, onChange, min, max) {
  const label = fieldWrap(labelText);
  const input = document.createElement("input");
  input.type = "number";
  input.value = value;
  input.min = min;
  input.max = max;
  input.addEventListener("input", () => { onChange(Number(input.value)); markDirty(); });
  label.append(input);
  return label;
}

function timeField(labelText, value, onChange) {
  const label = fieldWrap(labelText);
  const input = document.createElement("input");
  input.type = "time";
  input.value = value;
  input.addEventListener("input", () => { onChange(input.value); markDirty(); });
  label.append(input);
  return label;
}

function selectField(labelText, value, options, onChange) {
  const label = fieldWrap(labelText);
  const select = document.createElement("select");
  for (const item of options) {
    const optionValue = typeof item === "string" ? item : item.value;
    const optionLabel = typeof item === "string" ? item : item.label;
    select.append(new Option(optionLabel, optionValue));
  }
  select.value = value;
  select.addEventListener("change", () => { onChange(select.value); markDirty(); });
  label.append(select);
  return label;
}

function checkField(text, checked, onChange) {
  const label = document.createElement("label");
  label.className = "check-row";
  const input = document.createElement("input");
  input.type = "checkbox";
  input.checked = checked;
  input.addEventListener("change", () => { onChange(input.checked); markDirty(); });
  const span = document.createElement("span");
  span.textContent = text;
  label.append(input, span);
  return label;
}

function jsonField(labelText, value, onChange, wide = false) {
  const label = fieldWrap(labelText);
  if (wide) label.classList.add("wide");
  const textarea = document.createElement("textarea");
  textarea.value = JSON.stringify(value, null, 2);
  textarea.addEventListener("input", () => {
    try {
      onChange(JSON.parse(textarea.value || "{}"));
      textarea.style.borderColor = "";
      textarea.dataset.invalid = "false";
      markDirty();
    } catch (_) {
      textarea.style.borderColor = "var(--danger)";
      textarea.dataset.invalid = "true";
    }
  });
  label.append(textarea);
  return label;
}

function valueToInput(value) {
  if (typeof value === "string") return value;
  try { return JSON.stringify(value); } catch (_) { return String(value); }
}

function parseLooseValue(value) {
  try { return JSON.parse(value); } catch (_) { return value; }
}

function validateStepsDraft(steps, prefix, errors) {
  steps.forEach((step, index) => {
    const path = `${prefix}.${index}`;
    if (step.type === "command") {
      if (!step.entity_id) errors.push({ field: `${path}.entity_id`, msg: "Select a command-capable entity" });
      else if (isSensor(step.entity_id)) errors.push({ field: `${path}.entity_id`, msg: "Sensors are read-only and cannot receive commands" });
      if (!step.cmd_id) errors.push({ field: `${path}.cmd_id`, msg: "Select a command" });
    } else if (step.type === "condition" || step.type === "wait") {
      if (!Array.isArray(step.conditions) || !step.conditions.length) errors.push({ field: `${path}.conditions`, msg: "Add at least one condition" });
      (step.conditions || []).forEach((condition, conditionIndex) => {
        if ((condition.kind || "entity") === "entity") {
          if (!condition.entity_id) errors.push({ field: `${path}.conditions.${conditionIndex}.entity_id`, msg: "Select an entity" });
          if (!condition.attribute) errors.push({ field: `${path}.conditions.${conditionIndex}.attribute`, msg: "Select an attribute" });
        }
      });
      if (step.type === "condition") {
        validateStepsDraft(step.then || [], `${path}.then`, errors);
        validateStepsDraft(step.else || [], `${path}.else`, errors);
      } else {
        if (!["continue", "stop", "branch"].includes(step.on_match || "continue")) errors.push({ field: `${path}.on_match`, msg: "Select what happens when the condition matches" });
        if (!["continue", "stop", "fail", "branch"].includes(step.on_timeout || "fail")) errors.push({ field: `${path}.on_timeout`, msg: "Select what happens when the timeout expires" });
        validateStepsDraft(step.match_steps || [], `${path}.match_steps`, errors);
        validateStepsDraft(step.timeout_steps || [], `${path}.timeout_steps`, errors);
      }
    } else if (step.type === "parallel") {
      if (!Array.isArray(step.branches) || step.branches.length < 2) errors.push({ field: `${path}.branches`, msg: "Add at least two parallel branches" });
      (step.branches || []).forEach((branch, branchIndex) => validateStepsDraft(branch.steps || [], `${path}.branches.${branchIndex}.steps`, errors));
    } else if (step.type === "http") {
      if (!/^https?:\/\//.test(step.url || "")) errors.push({ field: `${path}.url`, msg: "URL must start with http:// or https://" });
    } else if (step.type === "log" && !step.message?.trim()) {
      errors.push({ field: `${path}.message`, msg: "Log message is required" });
    }
    if ((step.failure_action || "fail") === "branch") validateStepsDraft(step.failure_steps || [], `${path}.failure_steps`, errors);
  });
}

function openStepPicker(target) {
  state.stepTarget = target;
  $("stepDialog").showModal();
}

function closeStepPicker() {
  state.stepTarget = null;
  $("stepDialog").close();
}
