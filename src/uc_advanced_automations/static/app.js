const state = {
  automations: [],
  selectedId: null,
  entities: [],
  lastLog: 0,
  visibleLogs: [],
  settings: null,
  dirty: false,
};

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const data = await response.json();
      message = data.error || message;
      if (data.details) message += `: ${data.details.map((item) => item.msg).join(", ")}`;
    } catch (_) {}
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
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

function displayName(entity) {
  if (typeof entity.name === "string") return entity.name;
  if (entity.name && typeof entity.name === "object") return entity.name.en || Object.values(entity.name)[0];
  return entity.entity_id;
}

function newAutomation() {
  const number = state.automations.length + 1;
  return {
    id: crypto.randomUUID(),
    name: `Automation ${number}`,
    command: `AUTOMATION_${number}`,
    description: "",
    enabled: true,
    mode: "single",
    steps: [],
    _new: true,
  };
}

function addAutomation() {
  const automation = newAutomation();
  state.automations.push(automation);
  state.selectedId = automation.id;
  state.dirty = true;
  renderAll();
  $("automationName").focus();
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
    const command = document.createElement("small");
    command.textContent = automation.command;
    button.append(top, command);
    button.addEventListener("click", () => {
      state.selectedId = automation.id;
      state.dirty = false;
      renderAll();
    });
    list.append(button);
  }
}

function renderEditor() {
  const automation = selectedAutomation();
  $("emptyState").classList.toggle("hidden", Boolean(automation));
  $("editor").classList.toggle("hidden", !automation);
  if (!automation) return;

  $("editorTitle").textContent = automation.name || "Untitled automation";
  $("automationName").value = automation.name || "";
  $("automationCommand").value = automation.command || "";
  $("automationMode").value = automation.mode || "single";
  $("automationDescription").value = automation.description || "";
  $("automationEnabled").checked = automation.enabled !== false;
  $("saveAutomation").textContent = automation._new ? "Create automation" : "Save";
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
    selectedAutomation().mode = event.target.value;
    markDirty();
  });
  $("automationDescription").addEventListener("input", (event) => {
    selectedAutomation().description = event.target.value;
    markDirty();
  });
  $("automationEnabled").addEventListener("change", (event) => {
    selectedAutomation().enabled = event.target.checked;
    renderAutomationList();
    markDirty();
  });
}

function makeStep(type) {
  switch (type) {
    case "command": return { type, entity_id: state.entities[0]?.entity_id || "", cmd_id: "on", params: {} };
    case "delay": return { type, milliseconds: 1000 };
    case "condition": return {
      type,
      mode: "all",
      conditions: [makeCondition("entity")],
      then: [],
      else: [],
    };
    case "wait": return {
      type,
      mode: "all",
      conditions: [makeCondition("entity")],
      timeout_ms: 30000,
      interval_ms: 500,
    };
    case "http": return {
      type,
      method: "POST",
      url: "http://",
      headers: {},
      body: {},
      timeout_seconds: 10,
      status_min: 200,
      status_max: 299,
    };
    case "log": return { type, message: "Automation reached this step", level: "info" };
    default: throw new Error(`Unknown step type: ${type}`);
  }
}

function makeCondition(kind) {
  if (kind === "time") {
    return { kind: "time", operator: "between", start: "18:00", end: "23:59", weekdays: [0, 1, 2, 3, 4, 5, 6] };
  }
  return {
    kind: "entity",
    entity_id: state.entities[0]?.entity_id || "",
    attribute: "state",
    operator: "eq",
    value: "ON",
  };
}

function renderSteps(container, steps, branchName) {
  container.replaceChildren();
  if (!steps.length) {
    const empty = document.createElement("div");
    empty.className = "steps-empty";
    empty.textContent = branchName === "root" ? "No steps. Add the first action above." : "No steps in this branch.";
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
  const number = document.createElement("span");
  number.className = "step-number";
  number.textContent = String(index + 1).padStart(2, "0");
  const label = document.createElement("span");
  label.textContent = stepLabel(step.type);
  title.append(number, label);

  const tools = document.createElement("div");
  tools.className = "step-tools";
  tools.append(
    toolButton("↑", "Move up", () => moveStep(siblings, index, -1)),
    toolButton("↓", "Move down", () => moveStep(siblings, index, 1)),
    toolButton("×", "Delete step", () => { siblings.splice(index, 1); markDirty(); renderEditor(); }),
  );
  head.append(title, tools);

  const body = document.createElement("div");
  body.className = "step-body";
  body.append(renderStepBody(step));
  if (!["condition"].includes(step.type)) {
    body.append(continueOnError(step));
  }
  wrapper.append(head, body);
  return wrapper;
}

function stepLabel(type) {
  return {
    command: "Device command",
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

function moveStep(steps, index, direction) {
  const target = index + direction;
  if (target < 0 || target >= steps.length) return;
  [steps[index], steps[target]] = [steps[target], steps[index]];
  markDirty();
  renderEditor();
}

function renderStepBody(step) {
  const grid = document.createElement("div");
  grid.className = "step-grid";

  if (step.type === "command") {
    grid.append(
      entityField("Entity", step.entity_id, (value) => { step.entity_id = value; }),
      textField("Command ID", step.cmd_id, (value) => { step.cmd_id = value; }, "on, off, toggle, send_cmd…"),
      jsonField("Parameters (JSON)", step.params || {}, (value) => { step.params = value; }, true),
    );
  } else if (step.type === "delay") {
    grid.append(numberField("Milliseconds", step.milliseconds, (value) => { step.milliseconds = value; }, 0, 86400000));
  } else if (step.type === "condition") {
    const block = document.createElement("div");
    block.className = "wide";
    block.append(conditionGroup(step));
    block.append(branchEditor("Then", "then", step.then, step));
    block.append(branchEditor("Else", "else", step.else, step));
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

function rangeFields(step) {
  const wrap = document.createElement("div");
  wrap.className = "field";
  const label = document.createElement("span");
  label.textContent = "Accepted HTTP status range";
  const row = document.createElement("div");
  row.style.display = "grid";
  row.style.gridTemplateColumns = "1fr 1fr";
  row.style.gap = "7px";
  const min = document.createElement("input");
  min.type = "number";
  min.min = "100";
  min.max = "599";
  min.value = step.status_min ?? 200;
  min.addEventListener("input", () => { step.status_min = Number(min.value); markDirty(); });
  const max = document.createElement("input");
  max.type = "number";
  max.min = "100";
  max.max = "599";
  max.value = step.status_max ?? 299;
  max.addEventListener("input", () => { step.status_max = Number(max.value); markDirty(); });
  row.append(min, max);
  wrap.append(label, row);
  return wrap;
}

function conditionGroup(group) {
  const wrapper = document.createElement("div");
  wrapper.className = "condition-list";
  const mode = selectField("Condition mode", group.mode || "all", [
    { value: "all", label: "All conditions must match" },
    { value: "any", label: "Any condition may match" },
  ], (value) => { group.mode = value; });
  wrapper.append(mode);

  (group.conditions || []).forEach((condition, index) => {
    wrapper.append(conditionRow(condition, index, group.conditions));
  });
  const add = document.createElement("button");
  add.type = "button";
  add.className = "button ghost small";
  add.textContent = "+ Add condition";
  add.addEventListener("click", () => {
    group.conditions.push(makeCondition("entity"));
    markDirty();
    renderEditor();
  });
  wrapper.append(add);
  return wrapper;
}

function conditionRow(condition, index, conditions) {
  const row = document.createElement("div");
  row.className = "condition-row";

  const kind = selectField("Source", condition.kind || "entity", [
    { value: "entity", label: "Entity attribute" },
    { value: "time", label: "Time window" },
  ], (value) => {
    conditions[index] = makeCondition(value);
    markDirty();
    renderEditor();
  });
  row.append(kind);

  if ((condition.kind || "entity") === "time") {
    row.append(
      timeField("Start", condition.start || "18:00", (value) => { condition.start = value; }),
      selectField("Operator", condition.operator || "between", ["between", "outside"], (value) => { condition.operator = value; }),
      timeField("End", condition.end || "23:59", (value) => { condition.end = value; }),
    );
  } else {
    row.append(
      entityField("Entity", condition.entity_id || "", (value) => { condition.entity_id = value; }),
      textField("Attribute", condition.attribute || "state", (value) => { condition.attribute = value; }, "state"),
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
  remove.addEventListener("click", () => {
    if (conditions.length <= 1) {
      showNotice("A condition group needs at least one condition.", "error");
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
    const holder = document.createElement("div");
    holder.className = "field";
    const label = document.createElement("span");
    label.textContent = "Value";
    const text = document.createElement("input");
    text.disabled = true;
    text.placeholder = "Not required";
    holder.append(label, text);
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
  const controls = document.createElement("div");
  controls.className = "branch-controls";
  const select = document.createElement("select");
  for (const type of ["command", "delay", "condition", "wait", "http", "log"]) {
    const option = document.createElement("option");
    option.value = type;
    option.textContent = stepLabel(type);
    select.append(option);
  }
  const add = document.createElement("button");
  add.type = "button";
  add.className = "button ghost small";
  add.textContent = "Add";
  add.addEventListener("click", () => {
    parent[key].push(makeStep(select.value));
    markDirty();
    renderEditor();
  });
  controls.append(select, add);
  head.append(title, controls);
  const container = document.createElement("div");
  container.className = "steps";
  renderSteps(container, steps, key);
  branch.append(head, container);
  return branch;
}

function continueOnError(step) {
  const label = document.createElement("label");
  label.className = "check-row";
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = Boolean(step.continue_on_error);
  checkbox.addEventListener("change", () => { step.continue_on_error = checkbox.checked; markDirty(); });
  const text = document.createElement("span");
  text.textContent = "Continue when this step fails";
  label.append(checkbox, text);
  return label;
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
    const option = document.createElement("option");
    option.value = typeof item === "string" ? item : item.value;
    option.textContent = typeof item === "string" ? item : item.label;
    select.append(option);
  }
  select.value = value;
  select.addEventListener("change", () => { onChange(select.value); markDirty(); });
  label.append(select);
  return label;
}

function entityField(labelText, value, onChange) {
  const label = fieldWrap(labelText);
  const select = document.createElement("select");
  if (!state.entities.length) {
    const option = document.createElement("option");
    option.value = value || "";
    option.textContent = value || "Connect to the Remote to load entities";
    select.append(option);
  } else {
    const known = state.entities.some((entity) => entity.entity_id === value);
    if (value && !known) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = `${value} (not currently found)`;
      select.append(option);
    }
    for (const entity of state.entities) {
      const option = document.createElement("option");
      option.value = entity.entity_id;
      option.textContent = `${displayName(entity)} · ${entity.entity_type} · ${entity.entity_id}`;
      select.append(option);
    }
  }
  select.value = value || select.options[0]?.value || "";
  select.addEventListener("change", () => { onChange(select.value); markDirty(); });
  label.append(select);
  return label;
}

function jsonField(labelText, value, onChange, wide = false) {
  const label = fieldWrap(labelText);
  if (wide) label.classList.add("wide");
  const textarea = document.createElement("textarea");
  textarea.value = JSON.stringify(value, null, 2);
  textarea.addEventListener("input", () => {
    try {
      const parsed = JSON.parse(textarea.value || "{}");
      onChange(parsed);
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
  return JSON.stringify(value);
}

function parseLooseValue(value) {
  try { return JSON.parse(value); } catch (_) { return value; }
}

function cleanAutomation(automation) {
  const copy = structuredClone(automation);
  delete copy._new;
  return copy;
}

async function saveCurrent() {
  const automation = selectedAutomation();
  if (!automation) return;
  if (!automation.name.trim()) return showNotice("Name is required.", "error");
  if (!/^[A-Z][A-Z0-9_]{1,63}$/.test(automation.command)) {
    return showNotice("Remote command must use A–Z, numbers and underscores.", "error");
  }
  if (document.querySelector('textarea[data-invalid="true"]')) {
    return showNotice("Fix invalid JSON before saving.", "error");
  }

  try {
    const wasNew = automation._new;
    const payload = cleanAutomation(automation);
    const saved = wasNew
      ? await api("/api/automations", { method: "POST", body: JSON.stringify(payload) })
      : await api(`/api/automations/${encodeURIComponent(automation.id)}`, { method: "PUT", body: JSON.stringify(payload) });
    const index = state.automations.findIndex((item) => item.id === automation.id);
    state.automations[index] = saved;
    state.selectedId = saved.id;
    state.dirty = false;
    renderAll();
    showNotice("Automation saved. Refresh the entity in the Remote configurator after changing its command list.");
  } catch (error) {
    showNotice(error.message, "error", 7000);
  }
}

async function deleteCurrent() {
  const automation = selectedAutomation();
  if (!automation) return;
  if (!confirm(`Delete “${automation.name}”?`)) return;
  try {
    if (!automation._new) await api(`/api/automations/${encodeURIComponent(automation.id)}`, { method: "DELETE" });
    state.automations = state.automations.filter((item) => item.id !== automation.id);
    state.selectedId = state.automations[0]?.id || null;
    state.dirty = false;
    renderAll();
    showNotice("Automation deleted.");
  } catch (error) {
    showNotice(error.message, "error");
  }
}

async function runCurrent() {
  const automation = selectedAutomation();
  if (!automation) return;
  if (automation._new || state.dirty) {
    return showNotice("Save the automation before running it.", "error");
  }
  try {
    const result = await api(`/api/automations/${encodeURIComponent(automation.id)}/run`, { method: "POST", body: "{}" });
    showNotice(`Run accepted: ${result.run_id}`);
    pollLogs();
  } catch (error) {
    showNotice(error.message, "error");
  }
}

async function loadAutomations() {
  const data = await api("/api/automations");
  state.automations = data.automations;
  if (!state.selectedId || !state.automations.some((item) => item.id === state.selectedId)) {
    state.selectedId = state.automations[0]?.id || null;
  }
  renderAll();
}

async function loadEntities() {
  try {
    const data = await api("/api/entities");
    state.entities = data.entities;
    if (selectedAutomation()) renderEditor();
  } catch (error) {
    state.entities = [];
  }
}

async function pollStatus() {
  try {
    const status = await api("/api/status");
    const badge = $("connectionBadge");
    badge.className = `status-badge ${status.core_connected ? "connected" : status.core_error ? "error" : ""}`;
    badge.innerHTML = `<span></span>${status.core_connected ? `Connected · ${status.running} running` : status.api_key_configured ? "Not connected" : "API key required"}`;
    $("runtimeTarget").textContent = `Running on: ${status.runtime_name}`;
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
    $("runtimeInfo").textContent = state.settings.runs_on_remote
      ? `Embedded mode · configuration is stored on the Remote · open this interface at http://REMOTE-IP:${state.settings.web_port}`
      : `External mode · configuration directory: ${state.settings.data_dir}`;
    $("apiKey").value = "";
    $("apiKeyHint").textContent = state.settings.api_key_configured ? "A key is stored. Leave blank to keep it." : "No API key is configured.";
    $("timezone").value = state.settings.timezone;
    $("requestTimeout").value = state.settings.request_timeout_seconds;
    $("webHost").value = state.settings.web_host;
    $("webPort").value = state.settings.web_port;
    $("settingsResult").className = "inline-result hidden";
    $("settingsDialog").showModal();
  } catch (error) {
    showNotice(error.message, "error");
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
  } catch (error) {
    settingsResult(error.message, "error");
  }
}

async function testConnection() {
  try {
    await saveSettings();
    const result = await api("/api/settings/test", { method: "POST", body: "{}" });
    settingsResult(`Connected. ${result.entity_count} configured entities found.`, "success");
    await loadEntities();
    await pollStatus();
  } catch (error) {
    settingsResult(error.message, "error");
  }
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
  $("addRootStep").addEventListener("click", () => {
    const automation = selectedAutomation();
    if (!automation) return;
    automation.steps.push(makeStep($("rootStepType").value));
    markDirty();
    renderEditor();
  });
  bindEditorFields();
  window.addEventListener("beforeunload", (event) => {
    if (!state.dirty) return;
    event.preventDefault();
    event.returnValue = "";
  });
}

async function init() {
  setupEvents();
  try {
    await loadAutomations();
    await Promise.allSettled([loadEntities(), pollStatus(), pollLogs()]);
  } catch (error) {
    showNotice(error.message, "error", 0);
  }
  setInterval(pollStatus, 5000);
  setInterval(pollLogs, 2000);
}

init();
