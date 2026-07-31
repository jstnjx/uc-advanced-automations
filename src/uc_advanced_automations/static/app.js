const state = {
  automations: [],
  selectedId: null,
  entities: [],
  lastLog: 0,
  visibleLogs: [],
  settings: null,
  dirty: false,
  commandDefinitions: new Map(),
  drag: null,
  stepTarget: null,
};

const $ = (id) => document.getElementById(id);

class ApiError extends Error {
  constructor(message, status = 0, details = []) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.details = Array.isArray(details) ? details : [];
  }
}

function createId() {
  const cryptoApi = typeof globalThis !== "undefined" ? globalThis.crypto : null;
  if (cryptoApi && typeof cryptoApi.randomUUID === "function") return cryptoApi.randomUUID();

  const bytes = new Uint8Array(16);
  if (cryptoApi && typeof cryptoApi.getRandomValues === "function") {
    cryptoApi.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256);
    }
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0"));
  return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-${hex.slice(6, 8).join("")}-${hex.slice(8, 10).join("")}-${hex.slice(10).join("")}`;
}

async function api(path, options = {}) {
  const { returnResponse = false, ...fetchOptions } = options;
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(fetchOptions.headers || {}) },
    ...fetchOptions,
  });
  let data = null;
  if (response.status !== 204) {
    const contentType = response.headers.get("content-type") || "";
    try {
      data = contentType.includes("application/json") ? await response.json() : { error: await response.text() };
    } catch (_) {
      data = null;
    }
  }
  if (!response.ok) {
    throw new ApiError(data?.error || `Request failed (${response.status})`, response.status, data?.details || []);
  }
  return returnResponse ? { data, response } : data;
}

function selectedAutomation() {
  return state.automations.find((item) => item.id === state.selectedId) || null;
}

function markDirty() {
  state.dirty = true;
  const button = $("saveAutomation");
  if (button) button.textContent = "Save changes";
}

function showNotice(message, type = "success", timeout = 4500) {
  const notice = $("notice");
  notice.textContent = message;
  notice.className = `notice ${type}`;
  if (timeout) setTimeout(() => notice.classList.add("hidden"), timeout);
}

function cleanValidationMessage(message) {
  return String(message || "Invalid value")
    .replace(/^Value error,\s*/i, "")
    .replace(/^Assertion failed,\s*/i, "");
}

function normalizeDetails(details) {
  return (details || []).map((item) => ({
    field: item.field || (Array.isArray(item.loc) ? item.loc.join(".") : ""),
    msg: cleanValidationMessage(item.msg || item.message),
  }));
}

function openMessageDialog({
  title,
  message = "",
  details = [],
  confirmLabel = "OK",
  cancelLabel = "Cancel",
  showCancel = false,
  danger = false,
} = {}) {
  const dialog = $("messageDialog");
  $("messageDialogTitle").textContent = title || "Message";
  $("messageDialogText").textContent = message;
  const list = $("messageDialogDetails");
  list.replaceChildren();
  const normalized = normalizeDetails(details);
  list.classList.toggle("hidden", !normalized.length);
  normalized.forEach((detail) => {
    const item = document.createElement("li");
    const field = detail.field ? `${friendlyPath(detail.field)}: ` : "";
    item.textContent = `${field}${detail.msg}`;
    list.append(item);
  });
  const cancel = $("messageDialogCancel");
  cancel.textContent = cancelLabel;
  cancel.classList.toggle("hidden", !showCancel);
  const confirm = $("messageDialogConfirm");
  confirm.textContent = confirmLabel;
  confirm.className = `button ${danger ? "danger" : "primary"}`;

  return new Promise((resolve) => {
    const onClose = () => {
      dialog.removeEventListener("close", onClose);
      resolve(dialog.returnValue === "default");
    };
    dialog.addEventListener("close", onClose);
    dialog.showModal();
  });
}

function friendlyPath(path) {
  return String(path)
    .replace(/steps\.(\d+)/g, (_, index) => `Step ${Number(index) + 1}`)
    .replace(/triggers\.(\d+)/g, (_, index) => `Trigger ${Number(index) + 1}`)
    .replace(/\.then\./g, " → Then → ")
    .replace(/\.else\./g, " → Else → ")
    .replace(/\.(\w+)$/, " · $1")
    .replaceAll("_", " ");
}

function showError(error, title = "Unable to complete the request") {
  const message = error instanceof Error ? error.message : String(error);
  const details = error?.details || [];
  return openMessageDialog({ title, message, details, confirmLabel: "Close" });
}

function displayName(entity) {
  if (typeof entity?.name === "string") return entity.name;
  if (entity?.name && typeof entity.name === "object") return entity.name.en || Object.values(entity.name)[0];
  return entity?.entity_id || "Unknown entity";
}

function localizedName(value, fallback = "") {
  if (typeof value === "string") return value;
  if (value && typeof value === "object") return value.en || Object.values(value)[0] || fallback;
  return fallback;
}

function findEntity(entityId) {
  return state.entities.find((entity) => entity.entity_id === entityId) || null;
}

function isSensor(entityOrId) {
  const entity = typeof entityOrId === "string" ? findEntity(entityOrId) : entityOrId;
  return String(entity?.entity_type || "").toLowerCase() === "sensor";
}

function commandableEntities() {
  return state.entities.filter((entity) => !isSensor(entity));
}

function firstEntityId({ commandable = false } = {}) {
  const entities = commandable ? commandableEntities() : state.entities;
  return entities[0]?.entity_id || "";
}

function newAutomation() {
  const number = state.automations.length + 1;
  return {
    id: createId(),
    name: `Automation ${number}`,
    command: `AUTOMATION_${number}`,
    description: "",
    enabled: true,
    command_enabled: true,
    mode: "single",
    trigger_mode: "any",
    triggers: [],
    steps: [],
    _new: true,
  };
}

async function allowDiscardChanges() {
  if (!state.dirty) return true;
  return openMessageDialog({
    title: "Discard unsaved changes?",
    message: "The current automation contains changes that have not been saved.",
    confirmLabel: "Discard changes",
    showCancel: true,
    danger: true,
  });
}

async function addAutomation() {
  const wasDirty = state.dirty;
  if (!(await allowDiscardChanges())) return;
  if (wasDirty) await loadAutomations();
  const automation = newAutomation();
  state.automations.push(automation);
  state.selectedId = automation.id;
  state.dirty = true;
  renderAll();
  $("automationName").focus();
}

async function selectAutomation(id) {
  if (id === state.selectedId) return;
  if (!(await allowDiscardChanges())) return;
  if (state.dirty) await loadAutomations();
  state.selectedId = id;
  state.dirty = false;
  renderAll();
}

function renderAll() {
  renderAutomationList();
  renderEditor();
}

function renderAutomationList() {
  const list = $("automationList");
  list.replaceChildren();
  $("automationCount").textContent = `${state.automations.length} configured`;
  if (!state.automations.length) {
    const empty = document.createElement("p");
    empty.className = "log-empty";
    empty.textContent = "No automations yet.";
    list.append(empty);
    return;
  }
  for (const automation of state.automations) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `automation-item${automation.id === state.selectedId ? " active" : ""}`;
    const top = document.createElement("span");
    top.className = "item-top";
    const name = document.createElement("strong");
    name.textContent = automation.name || "Untitled";
    const dot = document.createElement("span");
    dot.className = `dot${automation.enabled ? " enabled" : ""}`;
    top.append(name, dot);
    const summary = document.createElement("small");
    const triggerCount = (automation.triggers || []).filter((item) => item.enabled !== false).length;
    const commandText = automation.command_enabled !== false ? automation.command : "Background only";
    const logic = triggerCount > 1 ? ` · ${automation.trigger_mode === "all" ? "AND" : "OR"}` : "";
    summary.textContent = triggerCount ? `${commandText} · ${triggerCount} trigger${triggerCount === 1 ? "" : "s"}${logic}` : commandText;
    button.append(top, summary);
    button.addEventListener("click", () => selectAutomation(automation.id));
    list.append(button);
  }
}

function renderEditor() {
  const automation = selectedAutomation();
  $("emptyState").classList.toggle("hidden", Boolean(automation));
  $("editor").classList.toggle("hidden", !automation);
  if (!automation) return;

  automation.triggers ||= [];
  automation.steps ||= [];
  automation.trigger_mode ||= "any";
  $("editorTitle").textContent = automation.name || "Untitled automation";
  $("automationName").value = automation.name || "";
  $("automationCommand").value = automation.command || "";
  $("automationMode").value = automation.mode || "single";
  $("triggerMode").value = automation.trigger_mode || "any";
  $("automationDescription").value = automation.description || "";
  $("automationEnabled").checked = automation.enabled !== false;
  $("automationCommandEnabled").checked = automation.command_enabled !== false;
  $("automationCommand").disabled = automation.command_enabled === false;
  $("saveAutomation").textContent = automation._new ? "Create automation" : state.dirty ? "Save changes" : "Save";
  renderTriggers(automation);
  renderSteps($("steps"), automation.steps, "root");
}

function bindEditorFields() {
  $("automationName").addEventListener("input", (event) => {
    const automation = selectedAutomation();
    if (!automation) return;
    automation.name = event.target.value;
    $("editorTitle").textContent = automation.name || "Untitled automation";
    renderAutomationList();
    markDirty();
  });
  $("automationCommand").addEventListener("input", (event) => {
    const automation = selectedAutomation();
    if (!automation) return;
    automation.command = event.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, "_");
    event.target.value = automation.command;
    renderAutomationList();
    markDirty();
  });
  $("automationMode").addEventListener("change", (event) => {
    const automation = selectedAutomation();
    if (!automation) return;
    automation.mode = event.target.value;
    markDirty();
  });
  $("triggerMode").addEventListener("change", (event) => {
    const automation = selectedAutomation();
    if (!automation) return;
    automation.trigger_mode = event.target.value;
    renderAutomationList();
    markDirty();
  });
  $("automationDescription").addEventListener("input", (event) => {
    const automation = selectedAutomation();
    if (!automation) return;
    automation.description = event.target.value;
    markDirty();
  });
  $("automationEnabled").addEventListener("change", (event) => {
    const automation = selectedAutomation();
    if (!automation) return;
    automation.enabled = event.target.checked;
    renderAutomationList();
    markDirty();
  });
  $("automationCommandEnabled").addEventListener("change", (event) => {
    const automation = selectedAutomation();
    if (!automation) return;
    automation.command_enabled = event.target.checked;
    $("automationCommand").disabled = !event.target.checked;
    renderAutomationList();
    markDirty();
  });
}

function makeTrigger() {
  return {
    id: createId(),
    type: "entity_state",
    enabled: true,
    entity_id: firstEntityId(),
    attribute: "state",
    from_value: null,
    to_value: null,
    debounce_ms: 0,
    cooldown_ms: 0,
  };
}

function renderTriggers(automation) {
  const container = $("triggers");
  container.replaceChildren();
  const triggers = automation.triggers;
  if (!triggers.length) {
    const empty = document.createElement("div");
    empty.className = "steps-empty";
    empty.textContent = "No background triggers. Use the UC command or add a trigger.";
    container.append(empty);
    return;
  }
  triggers.forEach((trigger, index) => container.append(renderTrigger(trigger, index, triggers)));
}

function renderTrigger(trigger, index, triggers) {
  const card = document.createElement("article");
  card.className = "trigger-card";
  const head = document.createElement("div");
  head.className = "trigger-card-head";
  const title = document.createElement("div");
  title.className = "trigger-title";
  const handle = dragHandle();
  const label = document.createElement("strong");
  label.textContent = `Trigger ${index + 1}`;
  title.append(handle, label);
  const remove = toolButton("×", "Delete trigger", () => {
    triggers.splice(index, 1);
    markDirty();
    renderEditor();
  });
  head.append(title, remove);

  const body = document.createElement("div");
  body.className = "trigger-body";
  const grid = document.createElement("div");
  grid.className = "trigger-grid";
  grid.append(
    entityField("UC entity", trigger.entity_id || "", (value) => {
      trigger.entity_id = value;
      trigger.attribute = defaultAttribute(value);
      renderEditor();
    }),
    attributeField("Attribute", trigger.entity_id, trigger.attribute || "state", (value) => { trigger.attribute = value; }),
    valueField("From", trigger.from_value, (value) => { trigger.from_value = value; }, "Any previous value"),
    valueField("To", trigger.to_value, (value) => { trigger.to_value = value; }, "Any new value"),
  );
  const advanced = document.createElement("details");
  advanced.className = "advanced-options";
  const summary = document.createElement("summary");
  summary.textContent = "Timing and trigger options";
  const timing = document.createElement("div");
  timing.className = "trigger-grid";
  timing.append(
    numberField("Stable for (ms)", trigger.debounce_ms ?? 0, (value) => { trigger.debounce_ms = value; }, 0, 86400000),
    numberField("Cooldown (ms)", trigger.cooldown_ms ?? 0, (value) => { trigger.cooldown_ms = value; }, 0, 86400000),
  );
  const enabled = checkField("Trigger enabled", trigger.enabled !== false, (checked) => {
    trigger.enabled = checked;
    renderAutomationList();
  });
  advanced.append(summary, timing, enabled);
  body.append(grid, advanced);
  card.append(head, body);
  attachSortable(handle, card, triggers, index, "trigger");
  return card;
}

function makeStep(type) {
  switch (type) {
    case "command": return { type, entity_id: firstEntityId({ commandable: true }), cmd_id: "", params: {} };
    case "delay": return { type, milliseconds: 1000 };
    case "condition": return { type, mode: "all", conditions: [makeCondition("entity")], then: [], else: [] };
    case "wait": return { type, mode: "all", conditions: [makeCondition("entity")], timeout_ms: 30000, interval_ms: 500 };
    case "http": return { type, method: "POST", url: "http://", headers: {}, body: {}, timeout_seconds: 10, status_min: 200, status_max: 299 };
    case "log": return { type, message: "Automation reached this step", level: "info" };
    default: throw new Error(`Unknown step type: ${type}`);
  }
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
  const wrapper = document.createElement("article");
  wrapper.className = "step";
  const head = document.createElement("div");
  head.className = "step-head";
  const title = document.createElement("div");
  title.className = "step-title";
  const handle = dragHandle();
  const number = document.createElement("span");
  number.className = "step-number";
  number.textContent = String(index + 1).padStart(2, "0");
  const label = document.createElement("span");
  label.textContent = stepLabel(step.type);
  title.append(handle, number, label);
  const tools = document.createElement("div");
  tools.className = "step-tools";
  tools.append(toolButton("×", "Delete step", () => {
    siblings.splice(index, 1);
    markDirty();
    renderEditor();
  }));
  head.append(title, tools);
  const body = document.createElement("div");
  body.className = "step-body";
  body.append(renderStepBody(step));
  if (step.type !== "condition") body.append(continueOnError(step));
  wrapper.append(head, body);
  attachSortable(handle, wrapper, siblings, index, "step");
  return wrapper;
}

function dragHandle() {
  const handle = document.createElement("span");
  handle.className = "drag-handle";
  handle.textContent = "⋮⋮";
  handle.title = "Drag to reorder";
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
    command: "UC command",
    delay: "Delay",
    condition: "If / else condition",
    wait: "Wait until",
    http: "HTTP request",
    log: "Log message",
  }[type] || type;
}

function toolButton(text, label, handler) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = text;
  button.title = label;
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
    block.append(conditionGroup(step), branchEditor("Then", "then", step.then, step), branchEditor("Else", "else", step.else, step));
    grid.append(block);
  } else if (step.type === "wait") {
    const conditions = document.createElement("div");
    conditions.className = "wide";
    conditions.append(conditionGroup(step));
    grid.append(
      conditions,
      numberField("Timeout (ms)", step.timeout_ms ?? 30000, (value) => { step.timeout_ms = value; }, 1, 86400000),
      numberField("Poll interval (ms)", step.interval_ms ?? 500, (value) => { step.interval_ms = value; }, 100, 60000),
    );
  } else if (step.type === "http") {
    grid.append(
      selectField("Method", step.method || "POST", ["GET", "POST", "PUT", "PATCH", "DELETE"], (value) => { step.method = value; }),
      textField("URL", step.url || "", (value) => { step.url = value; }, "http://home-assistant.local:8123/api/…"),
      jsonField("Headers (JSON)", step.headers || {}, (value) => { step.headers = value; }, true),
      jsonField("Body (JSON or value)", step.body ?? {}, (value) => { step.body = value; }, true),
      numberField("Timeout (seconds)", step.timeout_seconds ?? 10, (value) => { step.timeout_seconds = value; }, 1, 120),
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
  grid.append(entityField("Command-capable UC entity", step.entity_id || "", (value) => {
    step.entity_id = value;
    step.cmd_id = "";
    step.params = {};
    loadCommandDefinitions(value).then(() => { if (selectedAutomation()) renderEditor(); });
  }, { commandable: true }));

  if (step.entity_id && isSensor(step.entity_id)) {
    const note = document.createElement("div");
    note.className = "read-only-note wide";
    note.textContent = "Sensors are read-only UC entities. Select a command-capable entity for this step.";
    grid.append(note);
    wrapper.append(grid);
    return wrapper;
  }

  const definitions = getCommandDefinitions(step.entity_id);
  if (!definitions && step.entity_id) {
    const loading = fieldWrap("Command");
    const input = document.createElement("input");
    input.disabled = true;
    input.placeholder = "Loading UC command metadata…";
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
      ? `UC command metadata could not be loaded: ${definitions.error}`
      : "This UC entity does not advertise any commands and cannot be used as a command step.";
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
  toolbar.append(selectField("Condition logic", group.mode || "all", [
    { value: "all", label: "AND — all conditions must match" },
    { value: "any", label: "OR — any condition may match" },
  ], (value) => { group.mode = value; }));
  const addEntity = document.createElement("button");
  addEntity.type = "button"; addEntity.className = "button ghost small"; addEntity.textContent = "+ Entity condition";
  addEntity.addEventListener("click", () => { group.conditions.push(makeCondition("entity")); markDirty(); renderEditor(); });
  const addTime = document.createElement("button");
  addTime.type = "button"; addTime.className = "button ghost small"; addTime.textContent = "+ Time condition";
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
    { value: "entity", label: "UC entity attribute" },
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
      entityField("UC entity", condition.entity_id || "", (value) => {
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
  remove.className = "button danger ghost small";
  remove.textContent = "×";
  remove.title = "Remove condition";
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
  add.className = "button ghost small";
  add.textContent = "Add step";
  add.addEventListener("click", () => openStepPicker(parent[key]));
  head.append(title, add);
  const container = document.createElement("div");
  container.className = "steps";
  renderSteps(container, steps, key);
  branch.append(head, container);
  return branch;
}

function continueOnError(step) {
  return checkField("Continue when this step fails", Boolean(step.continue_on_error), (checked) => { step.continue_on_error = checked; });
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

function entityField(labelText, value, onChange, { commandable = false } = {}) {
  const label = fieldWrap(labelText);
  const select = document.createElement("select");
  const candidates = commandable ? commandableEntities() : state.entities;
  if (!candidates.length) {
    const text = commandable ? "No command-capable UC entities found" : "Connect to UC Core to load entities";
    select.append(new Option(value || text, value || ""));
    select.disabled = true;
  } else {
    const known = candidates.some((entity) => entity.entity_id === value);
    if (value && !known) {
      const suffix = isSensor(value) && commandable ? "read-only sensor" : "not currently found";
      select.append(new Option(`${value} (${suffix})`, value));
    }
    for (const entity of candidates) {
      const type = entity.entity_type || "entity";
      const readOnly = isSensor(entity) ? " · read-only" : "";
      select.append(new Option(`${displayName(entity)} · ${type}${readOnly} · ${entity.entity_id}`, entity.entity_id));
    }
  }
  select.value = value || select.options[0]?.value || "";
  select.addEventListener("change", () => { onChange(select.value); markDirty(); });
  label.append(select);
  return label;
}

function flattenAttributes(value, prefix = "", output = [], depth = 0) {
  if (!value || typeof value !== "object" || Array.isArray(value) || depth >= 4) {
    if (prefix) output.push({ path: prefix, value });
    return output;
  }
  const entries = Object.entries(value);
  if (!entries.length && prefix) output.push({ path: prefix, value });
  for (const [key, child] of entries) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (child && typeof child === "object" && !Array.isArray(child)) flattenAttributes(child, path, output, depth + 1);
    else output.push({ path, value: child });
  }
  return output;
}

function defaultAttribute(entityId) {
  const paths = flattenAttributes(findEntity(entityId)?.attributes || {});
  return paths.some((item) => item.path === "state") ? "state" : paths[0]?.path || "state";
}

function getAttributeValue(entityId, path) {
  let current = findEntity(entityId)?.attributes;
  for (const part of String(path || "").split(".")) {
    if (!part) continue;
    if (!current || typeof current !== "object" || !(part in current)) return undefined;
    current = current[part];
  }
  return current;
}

function attributeField(labelText, entityId, value, onChange) {
  const label = fieldWrap(labelText);
  const select = document.createElement("select");
  const options = flattenAttributes(findEntity(entityId)?.attributes || {});
  if (!options.some((item) => item.path === value) && value) options.unshift({ path: value, value: undefined });
  if (!options.length) options.push({ path: value || "state", value: undefined });
  for (const item of options) {
    select.append(new Option(item.path, item.path));
  }
  select.value = value || options[0].path;
  select.addEventListener("change", () => { onChange(select.value); markDirty(); renderEditor(); });
  label.append(select);
  const current = document.createElement("small");
  current.className = "attribute-current";
  const currentValue = getAttributeValue(entityId, select.value);
  current.textContent = currentValue === undefined ? "Current value unavailable" : `Current: ${valueToInput(currentValue)}`;
  label.append(current);
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

function cleanAutomation(automation) {
  const copy = typeof structuredClone === "function" ? structuredClone(automation) : JSON.parse(JSON.stringify(automation));
  delete copy._new;
  return copy;
}

function validateAutomationDraft(automation) {
  const errors = [];
  const add = (field, msg) => errors.push({ field, msg });
  if (!automation.name?.trim()) add("name", "Name is required");
  if (automation.command_enabled !== false && !/^[A-Z][A-Z0-9_]{1,63}$/.test(automation.command || "")) {
    add("command", "UC command must use A–Z, numbers and underscores");
  }
  (automation.triggers || []).forEach((trigger, index) => {
    if (!trigger.entity_id) add(`triggers.${index}.entity_id`, "Select a UC entity");
    if (!trigger.attribute) add(`triggers.${index}.attribute`, "Select an attribute");
  });
  validateStepsDraft(automation.steps || [], "steps", errors);
  if (document.querySelector('textarea[data-invalid="true"]')) add("sequence", "Fix invalid JSON fields before saving");
  return errors;
}

function validateStepsDraft(steps, prefix, errors) {
  steps.forEach((step, index) => {
    const path = `${prefix}.${index}`;
    if (step.type === "command") {
      if (!step.entity_id) errors.push({ field: `${path}.entity_id`, msg: "Select a command-capable UC entity" });
      else if (isSensor(step.entity_id)) errors.push({ field: `${path}.entity_id`, msg: "Sensors are read-only and cannot receive commands" });
      if (!step.cmd_id) errors.push({ field: `${path}.cmd_id`, msg: "Select a command" });
    } else if (step.type === "condition" || step.type === "wait") {
      if (!Array.isArray(step.conditions) || !step.conditions.length) errors.push({ field: `${path}.conditions`, msg: "Add at least one condition" });
      (step.conditions || []).forEach((condition, conditionIndex) => {
        if (condition.kind === "entity") {
          if (!condition.entity_id) errors.push({ field: `${path}.conditions.${conditionIndex}.entity_id`, msg: "Select a UC entity" });
          if (!condition.attribute) errors.push({ field: `${path}.conditions.${conditionIndex}.attribute`, msg: "Select an attribute" });
        }
      });
      if (step.type === "condition") {
        validateStepsDraft(step.then || [], `${path}.then`, errors);
        validateStepsDraft(step.else || [], `${path}.else`, errors);
      }
    } else if (step.type === "http") {
      if (!/^https?:\/\//.test(step.url || "")) errors.push({ field: `${path}.url`, msg: "URL must start with http:// or https://" });
    } else if (step.type === "log" && !step.message?.trim()) {
      errors.push({ field: `${path}.message`, msg: "Log message is required" });
    }
  });
}

function refreshMessage(response, prefix) {
  const status = response.headers.get("X-UC-Entity-Refresh") || "unknown";
  const detail = response.headers.get("X-UC-Entity-Refresh-Message");
  const labels = {
    refreshed: "UC commands and pages refreshed automatically.",
    reloaded: "The integration reloaded and the UC entity was refreshed.",
    current: "The UC entity is already current.",
    unchanged: "The UC entity definition was unchanged.",
    "not-configured": "Add the UC Advanced Automations entity to the UC Remote to expose its commands.",
    "api-key-required": "Run integration setup to create the UC Core API key.",
    "refresh-pending": "The integration reload was requested; UC Core is still applying the entity definition.",
    failed: detail || "Automatic UC entity refresh failed.",
  };
  return `${prefix} ${labels[status] || detail || ""}`.trim();
}

async function refreshUcEntity() {
  try {
    const result = await api("/api/integration/refresh", { method: "POST", body: "{}" });
    const message = result.message || ({
      refreshed: "UC commands and touchscreen pages refreshed.",
      reloaded: "Integration connection reloaded and UC entity refreshed.",
      current: "The UC entity is already current.",
      "not-configured": "Add the UC Advanced Automations entity to the UC Remote first.",
    }[result.status] || `Refresh status: ${result.status}`);
    if (result.status === "failed") await showError(new ApiError(message), "UC entity refresh failed");
    else showNotice(message, "success", 7000);
  } catch (error) {
    await showError(error, "UC entity refresh failed");
  }
}

async function saveCurrent() {
  const automation = selectedAutomation();
  if (!automation) return;
  const errors = validateAutomationDraft(automation);
  if (errors.length) {
    await openMessageDialog({
      title: "Automation needs attention",
      message: "Correct the following fields before saving.",
      details: errors,
      confirmLabel: "Review automation",
    });
    return;
  }
  try {
    const wasNew = automation._new;
    const payload = cleanAutomation(automation);
    const result = wasNew
      ? await api("/api/automations", { method: "POST", body: JSON.stringify(payload), returnResponse: true })
      : await api(`/api/automations/${encodeURIComponent(automation.id)}`, { method: "PUT", body: JSON.stringify(payload), returnResponse: true });
    const saved = result.data;
    const index = state.automations.findIndex((item) => item.id === automation.id);
    state.automations[index] = saved;
    state.selectedId = saved.id;
    state.dirty = false;
    renderAll();
    showNotice(refreshMessage(result.response, "Automation saved."));
  } catch (error) {
    await showError(error, error.status === 400 ? "Automation needs attention" : "Automation could not be saved");
  }
}

async function deleteCurrent() {
  const automation = selectedAutomation();
  if (!automation) return;
  const confirmed = await openMessageDialog({
    title: "Delete automation?",
    message: `“${automation.name}” and its trigger/sequence configuration will be removed.`,
    confirmLabel: "Delete automation",
    showCancel: true,
    danger: true,
  });
  if (!confirmed) return;
  try {
    let response = null;
    if (!automation._new) {
      const result = await api(`/api/automations/${encodeURIComponent(automation.id)}`, { method: "DELETE", returnResponse: true });
      response = result.response;
    }
    state.automations = state.automations.filter((item) => item.id !== automation.id);
    state.selectedId = state.automations[0]?.id || null;
    state.dirty = false;
    renderAll();
    showNotice(response ? refreshMessage(response, "Automation deleted.") : "Automation deleted.");
  } catch (error) {
    await showError(error, "Automation could not be deleted");
  }
}

async function runCurrent() {
  const automation = selectedAutomation();
  if (!automation) return;
  if (automation._new || state.dirty) {
    await openMessageDialog({ title: "Save required", message: "Save the automation before running it." });
    return;
  }
  try {
    const result = await api(`/api/automations/${encodeURIComponent(automation.id)}/run`, { method: "POST", body: "{}" });
    showNotice(`Run accepted: ${result.run_id}`);
    pollLogs();
  } catch (error) {
    await showError(error, "Automation could not be started");
  }
}

async function loadAutomations() {
  const data = await api("/api/automations");
  state.automations = data.automations;
  if (!state.selectedId || !state.automations.some((item) => item.id === state.selectedId)) state.selectedId = state.automations[0]?.id || null;
  renderAll();
}

async function loadEntities() {
  try {
    const data = await api("/api/entities");
    state.entities = data.entities;
    state.commandDefinitions.clear();
    if (selectedAutomation()) renderEditor();
  } catch (_) {
    state.entities = [];
  }
}

async function pollStatus() {
  try {
    const status = await api("/api/status");
    const badge = $("connectionBadge");
    badge.className = `status-badge ${status.core_connected ? "connected" : status.core_error ? "error" : ""}`;
    badge.innerHTML = `<span></span>${status.core_connected ? `UC connected · ${status.running} running` : status.api_key_configured ? "UC not connected" : "Setup required"}`;
  } catch (_) {}
}

async function pollLogs() {
  try {
    const data = await api(`/api/logs?after=${state.lastLog}`);
    if (data.logs.length) {
      state.visibleLogs.push(...data.logs);
      state.visibleLogs = state.visibleLogs.slice(-250);
      state.lastLog = data.logs[data.logs.length - 1].sequence;
      renderLogs();
    }
  } catch (_) {}
}

function renderLogs() {
  const container = $("logs");
  container.replaceChildren();
  if (!state.visibleLogs.length) {
    const empty = document.createElement("div");
    empty.className = "log-empty";
    empty.textContent = "No runs yet.";
    container.append(empty);
    return;
  }
  for (const entry of [...state.visibleLogs].reverse()) {
    const row = document.createElement("div");
    row.className = "log-row";
    const timestamp = document.createElement("time");
    timestamp.textContent = new Date(entry.timestamp).toLocaleString();
    const level = document.createElement("span");
    level.className = `log-level ${entry.level}`;
    level.textContent = entry.level;
    const message = document.createElement("span");
    const automation = state.automations.find((item) => item.id === entry.automation_id);
    message.textContent = `${automation?.name || entry.automation_id}: ${entry.message}`;
    row.append(timestamp, level, message);
    container.append(row);
  }
}

async function openSettings() {
  try {
    state.settings = await api("/api/settings");
    $("coreUrl").value = state.settings.core_url;
    $("apiKey").value = "";
    $("apiKeyHint").textContent = state.settings.api_key_configured
      ? "Created during UC integration setup. Leave blank to keep it."
      : "Run UC integration setup to create a persistent key, or paste one manually.";
    $("timezone").value = state.settings.timezone;
    $("requestTimeout").value = state.settings.request_timeout_seconds;
    $("webHost").value = state.settings.web_host;
    $("webPort").value = state.settings.web_port;
    $("settingsResult").className = "inline-result hidden";
    $("settingsDialog").showModal();
  } catch (error) {
    await showError(error, "Settings could not be loaded");
  }
}

function settingsPayload() {
  return {
    core_url: $("coreUrl").value,
    api_key: $("apiKey").value,
    timezone: $("timezone").value,
    request_timeout_seconds: Number($("requestTimeout").value),
    web_host: $("webHost").value,
    web_port: Number($("webPort").value),
  };
}

function settingsResult(message, type) {
  const result = $("settingsResult");
  result.textContent = message;
  result.className = `inline-result ${type}`;
}

async function saveSettings() {
  try {
    const result = await api("/api/settings", { method: "PUT", body: JSON.stringify(settingsPayload()) });
    settingsResult(result.restart_required ? "Saved. Restart the service to apply the web host or port change." : "Settings saved.", "success");
    await pollStatus();
    return true;
  } catch (error) {
    await showError(error, "Settings could not be saved");
    return false;
  }
}

async function testConnection() {
  if (!(await saveSettings())) return;
  try {
    const result = await api("/api/settings/test", { method: "POST", body: "{}" });
    settingsResult(`Connected. ${result.entity_count} configured UC entities found.`, "success");
    await loadEntities();
    await pollStatus();
  } catch (error) {
    await showError(error, "UC connection test failed");
  }
}

function openStepPicker(target) {
  state.stepTarget = target;
  $("stepDialog").showModal();
}

function closeStepPicker() {
  state.stepTarget = null;
  $("stepDialog").close();
}

function setupEvents() {
  $("addAutomation").addEventListener("click", addAutomation);
  $("emptyAdd").addEventListener("click", addAutomation);
  $("saveAutomation").addEventListener("click", saveCurrent);
  $("deleteAutomation").addEventListener("click", deleteCurrent);
  $("runAutomation").addEventListener("click", runCurrent);
  $("settingsButton").addEventListener("click", openSettings);
  $("saveSettings").addEventListener("click", saveSettings);
  $("testConnection").addEventListener("click", testConnection);
  $("clearLogView").addEventListener("click", () => { state.visibleLogs = []; renderLogs(); });
  $("refreshEntity").addEventListener("click", refreshUcEntity);
  $("addTrigger").addEventListener("click", () => {
    const automation = selectedAutomation();
    if (!automation) return;
    automation.triggers.push(makeTrigger());
    markDirty();
    renderEditor();
  });
  $("addRootStep").addEventListener("click", () => {
    const automation = selectedAutomation();
    if (automation) openStepPicker(automation.steps);
  });
  $("closeStepDialog").addEventListener("click", closeStepPicker);
  $("stepPicker").addEventListener("click", (event) => {
    const button = event.target.closest("[data-step-type]");
    if (!button || !state.stepTarget) return;
    state.stepTarget.push(makeStep(button.dataset.stepType));
    markDirty();
    closeStepPicker();
    renderEditor();
  });
  bindEditorFields();
}

async function init() {
  setupEvents();
  try {
    await loadAutomations();
    await Promise.allSettled([loadEntities(), pollStatus(), pollLogs()]);
  } catch (error) {
    await showError(error, "UC Advanced Automations could not be loaded");
  }
  setInterval(pollStatus, 5000);
  setInterval(pollLogs, 2000);
}

init();
