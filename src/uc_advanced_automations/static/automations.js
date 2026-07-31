/* Advanced Automations v1.0.10 */

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
    max_runtime_ms: 0,
    entity_ids: [],
    trigger_mode: "any",
    triggers: [],
    steps: [],
    cancellation_steps: [],
    rollback_steps: [],
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
  state.flowStep = 0;
  state.viewMode = "edit";
  resetEditHistory();
  renderAll();
  $("automationName").focus();
}

async function selectAutomation(id) {
  if (!(await allowDiscardChanges())) return;
  if (state.dirty) await loadAutomations();
  state.selectedId = id;
  state.dirty = false;
  state.flowStep = 0;
  state.viewMode = "overview";
  state.entityDropdownOpen = false;
  resetEditHistory();
  await loadAutomationHistory(id);
  renderAll();
}

async function showAutomationOverview() {
  if (!(await allowDiscardChanges())) return;
  if (state.dirty) await loadAutomations();
  state.viewMode = "overview";
  state.entityDropdownOpen = false;
  resetEditHistory();
  renderAll();
}

function editCurrentAutomation() {
  if (!selectedAutomation()) return;
  state.viewMode = "edit";
  state.flowStep = 0;
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
    const dot = document.createElement("span");
    dot.className = `dot${automation.enabled ? " enabled" : ""}`;
    dot.setAttribute("aria-label", automation.enabled ? "Enabled" : "Disabled");
    const name = document.createElement("strong");
    name.textContent = automation.name || "Untitled";
    top.append(dot, name);
    const summary = document.createElement("small");
    const triggerCount = (automation.triggers || []).filter((item) => item.enabled !== false).length;
    const commandText = automation.command_enabled !== false ? automation.command : "Background only";
    const active = state.history[automation.id]?.currently_active_step;
    summary.textContent = active
      ? `Running · ${active}`
      : triggerCount
        ? `${commandText} · ${triggerCount} trigger${triggerCount === 1 ? "" : "s"}`
        : commandText;
    button.append(top, summary, materialIcon("arrow_forward", "automation-chevron"));
    button.addEventListener("click", () => selectAutomation(automation.id));
    list.append(button);
  }
}

function renderFlowState() {
  const step = Math.max(0, Math.min(3, state.flowStep));
  state.flowStep = step;
  document.querySelectorAll("[data-flow-panel]").forEach((panel) => {
    panel.classList.toggle("hidden", Number(panel.dataset.flowPanel) !== step);
  });
  document.querySelectorAll("[data-flow-step]").forEach((button) => {
    const buttonStep = Number(button.dataset.flowStep);
    button.classList.toggle("active", buttonStep === step);
    button.classList.toggle("complete", buttonStep < step);
    button.setAttribute("aria-current", buttonStep === step ? "step" : "false");
  });
  $("flowBack").disabled = step === 0;
  $("flowNextLabel").textContent = step === 3 ? "Review and save" : "Continue";
}

function setFlowStep(step) {
  state.flowStep = Math.max(0, Math.min(3, Number(step)));
  renderEditor();
  $("editor").scrollIntoView({ behavior: "smooth", block: "start" });
}

function validateFlowStep(step) {
  const automation = selectedAutomation();
  if (!automation) return [];
  const errors = [];
  if (step === 0) {
    if (!automation.name?.trim()) errors.push({ field: "name", msg: "Name is required" });
    if (automation.command_enabled !== false && !/^[A-Z][A-Z0-9_]{1,63}$/.test(automation.command || "")) errors.push({ field: "command", msg: "Remote command has an invalid format" });
  }
  if (step === 1) {
    const selected = new Set(automation.entity_ids || []);
    collectReferencedEntityIds(automation).forEach((entityId) => {
      if (!selected.has(entityId)) errors.push({ field: "entities", msg: `Select the referenced entity ${entityId}` });
    });
  }
  if (step === 2) validateTriggersDraft(automation.triggers || [], errors);
  if (step === 3) {
    validateStepsDraft(automation.steps || [], "steps", errors);
    validateStepsDraft(automation.cancellation_steps || [], "cancellation_steps", errors);
    validateStepsDraft(automation.rollback_steps || [], "rollback_steps", errors);
  }
  return errors;
}

function validateTriggersDraft(triggers, errors) {
  triggers.forEach((trigger, index) => {
    const path = `triggers.${index}`;
    const type = trigger.type || "entity_state";
    if (["entity_state", "entity_duration", "numeric_threshold", "entity_change"].includes(type)) {
      if (!trigger.entity_id) errors.push({ field: `${path}.entity_id`, msg: "Select an entity" });
      if (type !== "entity_change" && !trigger.attribute) errors.push({ field: `${path}.attribute`, msg: "Select an attribute" });
    }
    if (type === "schedule" && !/^\d{2}:\d{2}$/.test(trigger.time || "")) errors.push({ field: `${path}.time`, msg: "Select a scheduled time" });
    if (type === "webhook" && String(trigger.webhook_id || "").length < 8) errors.push({ field: `${path}.webhook_id`, msg: "Webhook identifier must contain at least 8 characters" });
    if (type === "automation_outcome" && !trigger.automation_id) errors.push({ field: `${path}.automation_id`, msg: "Select an automation" });
    if (type === "manual" && !trigger.label?.trim()) errors.push({ field: `${path}.label`, msg: "Button label is required" });
  });
}

async function continueFlow() {
  const errors = validateFlowStep(state.flowStep);
  if (errors.length) {
    await openMessageDialog({
      title: "This step needs attention",
      message: "Correct the highlighted configuration before continuing.",
      details: errors,
      confirmLabel: "Review step",
    });
    return;
  }
  if (state.flowStep < 3) setFlowStep(state.flowStep + 1);
  else await saveCurrent();
}

function renderEditor() {
  const automation = selectedAutomation();
  const hasAutomation = Boolean(automation);
  const showOverview = hasAutomation && state.viewMode === "overview" && !automation._new;
  const showEditor = hasAutomation && !showOverview;
  $("emptyState").classList.toggle("hidden", hasAutomation);
  $("automationOverview").classList.toggle("hidden", !showOverview);
  $("editor").classList.toggle("hidden", !showEditor);
  if (!automation) return;

  if (!Array.isArray(automation.entity_ids)) automation.entity_ids = collectReferencedEntityIds(automation);
  automation.triggers ||= [];
  automation.steps ||= [];
  automation.cancellation_steps ||= [];
  automation.rollback_steps ||= [];
  automation.trigger_mode ||= "any";
  automation.max_runtime_ms ||= 0;
  if (showOverview) {
    renderOverview(automation);
    return;
  }
  $("editorTitle").textContent = automation.name || "Untitled automation";
  $("automationName").value = automation.name || "";
  $("automationCommand").value = automation.command || "";
  $("automationMode").value = automation.mode || "single";
  $("automationMaxRuntime").value = automation.max_runtime_ms || 0;
  $("triggerMode").value = automation.trigger_mode || "any";
  $("automationDescription").value = automation.description || "";
  $("automationEnabled").checked = automation.enabled !== false;
  $("automationCommandEnabled").checked = automation.command_enabled !== false;
  $("automationCommand").disabled = automation.command_enabled === false;
  $("saveAutomationLabel").textContent = automation._new ? "Create automation" : state.dirty ? "Save changes" : "Save";
  renderFlowState();
  renderEntitySelection();
  renderTriggerModeHelp();
  renderTriggers(automation);
  renderSteps($("steps"), automation.steps, "root");
  renderSteps($("cancellationSteps"), automation.cancellation_steps, "cancellation");
  renderSteps($("rollbackSteps"), automation.rollback_steps, "rollback");
  updateUndoButtons();
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
    renderTriggerModeHelp();
    markDirty();
  });
  $("automationMaxRuntime").addEventListener("input", (event) => {
    const automation = selectedAutomation();
    if (!automation) return;
    automation.max_runtime_ms = Number(event.target.value || 0);
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

function cleanAutomation(automation) {
  const copy = typeof structuredClone === "function" ? structuredClone(automation) : JSON.parse(JSON.stringify(automation));
  const scrub = (value) => {
    if (Array.isArray(value)) return value.map(scrub);
    if (value && typeof value === "object") {
      const result = {};
      Object.entries(value).forEach(([key, item]) => {
        if (key.startsWith("_")) return;
        if (key === "continue_on_error") return;
        result[key] = scrub(item);
      });
      return result;
    }
    return value;
  };
  return scrub(copy);
}

function validateAutomationDraft(automation) {
  const errors = [];
  const add = (field, msg) => errors.push({ field, msg });
  if (!automation.name?.trim()) add("name", "Name is required");
  if (automation.command_enabled !== false && !/^[A-Z][A-Z0-9_]{1,63}$/.test(automation.command || "")) add("command", "Remote command has an invalid format");
  const selected = new Set(automation.entity_ids || []);
  collectReferencedEntityIds(automation).forEach((entityId) => {
    if (!selected.has(entityId)) add("entities", `Select the referenced entity ${entityId}`);
  });
  validateTriggersDraft(automation.triggers || [], errors);
  if (!(automation.steps || []).length) add("steps", "Add at least one sequence step");
  validateStepsDraft(automation.steps || [], "steps", errors);
  validateStepsDraft(automation.cancellation_steps || [], "cancellation_steps", errors);
  validateStepsDraft(automation.rollback_steps || [], "rollback_steps", errors);
  if (document.querySelector('textarea[data-invalid="true"]')) add("sequence", "Fix invalid JSON fields before saving");
  return errors;
}

function refreshMessage(response, prefix) {
  const status = response.headers.get("X-Entity-Refresh") || "unknown";
  const detail = response.headers.get("X-Entity-Refresh-Message");
  const labels = {
    refreshed: "Entities refreshed.",
    reloaded: "Entities refreshed.",
    current: "Entities are already current.",
    unchanged: "Entities are already current.",
    "not-configured": "Add the Advanced Automations entity to the Remote to expose its commands.",
    "api-key-required": "Run integration setup to create the Remote API key.",
    "refresh-pending": "Entities refreshed.",
    failed: detail || "Automatic entity refresh failed.",
  };
  return `${prefix} ${labels[status] || detail || ""}`.trim();
}

async function saveCurrent() {
  const automation = selectedAutomation();
  if (!automation) return;
  const errors = validateAutomationDraft(automation);
  if (errors.length) {
    const firstStepError = errors.find((item) => String(item.field).startsWith("steps") || String(item.field).startsWith("cancellation") || String(item.field).startsWith("rollback"));
    const firstTriggerError = errors.find((item) => String(item.field).startsWith("triggers"));
    const entityError = errors.find((item) => item.field === "entities");
    state.flowStep = firstStepError ? 3 : firstTriggerError ? 2 : entityError ? 1 : 0;
    renderEditor();
    await openMessageDialog({ title: "Automation needs attention", message: "Correct the following fields before saving.", details: errors, confirmLabel: "Review automation" });
    return;
  }
  setSaving(true);
  try {
    const wasNew = automation._new;
    const payload = cleanAutomation(automation);
    const request = {
      method: wasNew ? "POST" : "PUT",
      body: JSON.stringify(payload),
      returnResponse: true,
      headers: { "X-Edit-Source": state.editSource || "visual_editor" },
    };
    const result = wasNew
      ? await api("/api/automations", request)
      : await api(`/api/automations/${encodeURIComponent(automation.id)}`, request);
    const saved = result.data;
    const index = state.automations.findIndex((item) => item.id === automation.id);
    state.automations[index] = saved;
    state.selectedId = saved.id;
    state.dirty = false;
    state.viewMode = "overview";
    state.entityDropdownOpen = false;
    resetEditHistory();
    await loadAutomationHistory(saved.id);
    renderAll();
    showNotice(refreshMessage(result.response, "Automation saved."));
  } catch (error) {
    await showError(error, error.status === 400 ? "Automation needs attention" : "Automation could not be saved");
  } finally {
    setSaving(false);
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
  setSaving(true, "Deleting automation…");
  try {
    if (!automation._new) {
      await api(`/api/automations/${encodeURIComponent(automation.id)}`, { method: "DELETE", headers: { "X-Edit-Source": state.editSource || "visual_editor" } });
    }
    window.location.reload();
  } catch (error) {
    setSaving(false);
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
    setTimeout(() => loadAutomationHistory(automation.id), 350);
  } catch (error) {
    await showError(error, "Automation could not be started");
  }
}

async function loadAutomations() {
  const data = await api("/api/automations");
  state.automations = data.automations || [];
  state.history = data.history || {};
  if (!state.selectedId || !state.automations.some((item) => item.id === state.selectedId)) state.selectedId = state.automations[0]?.id || null;
  resetEditHistory();
  renderAll();
}

async function openSettings() {
  try {
    state.settings = await api("/api/settings");
    $("coreUrl").value = state.settings.core_url;
    $("apiKey").value = "";
    $("apiKeyHint").textContent = state.settings.api_key_configured
      ? "Created during integration setup. Leave blank to keep it."
      : "Run integration setup to create a persistent key, or paste one manually.";
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
  setSaving(true, "Saving settings…");
  try {
    const result = await api("/api/settings", { method: "PUT", body: JSON.stringify(settingsPayload()) });
    settingsResult(result.restart_required ? "Saved. Restart the service to apply the web host or port change." : "Settings saved.", "success");
    await pollStatus();
    return true;
  } catch (error) {
    await showError(error, "Settings could not be saved");
    return false;
  } finally {
    setSaving(false);
  }
}

async function testConnection() {
  if (!(await saveSettings())) return;
  try {
    const result = await api("/api/settings/test", { method: "POST", body: "{}" });
    settingsResult(`Connected. ${result.entity_count} configured entities found.`, "success");
    await loadEntities();
    await pollStatus();
  } catch (error) {
    await showError(error, "Remote connection test failed");
  }
}

async function openRevisions() {
  const automation = selectedAutomation();
  if (!automation || automation._new) {
    await openMessageDialog({ title: "Save required", message: "Save the automation before viewing revisions." });
    return;
  }
  try {
    const data = await api(`/api/automations/${encodeURIComponent(automation.id)}/revisions`);
    state.revisions = data.revisions || [];
    renderRevisions();
    $("revisionsDialog").showModal();
  } catch (error) {
    await showError(error, "Revisions could not be loaded");
  }
}

function renderRevisions() {
  const list = $("revisionList");
  list.replaceChildren();
  const from = $("revisionFrom");
  const to = $("revisionTo");
  from.replaceChildren();
  to.replaceChildren();
  if (!state.revisions.length) {
    const empty = document.createElement("div");
    empty.className = "steps-empty";
    empty.textContent = "No revisions recorded yet.";
    list.append(empty);
    return;
  }
  state.revisions.forEach((revision, index) => {
    const row = document.createElement("article");
    row.className = "revision-row";
    const copy = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = `${revision.action} · ${revision.source.replaceAll("_", " ")}`;
    const date = document.createElement("span");
    date.textContent = formatTimestamp(revision.created_at);
    copy.append(title, date);
    const restore = document.createElement("button");
    restore.type = "button";
    restore.className = "button ghost small button-with-icon";
    setButtonContent(restore, "restore", "Restore");
    restore.addEventListener("click", () => restoreRevision(revision.revision_id));
    row.append(copy, restore);
    list.append(row);
    const option = new Option(`${formatTimestamp(revision.created_at)} · ${revision.source}`, revision.revision_id);
    from.append(option.cloneNode(true));
    to.append(option);
    if (index === 1) from.value = String(revision.revision_id);
    if (index === 0) to.value = String(revision.revision_id);
  });
}

async function fetchRevision(revisionId) {
  return api(`/api/revisions/${encodeURIComponent(revisionId)}`);
}

async function compareRevisions() {
  if (!$("revisionFrom").value || !$("revisionTo").value) return;
  try {
    const [left, right] = await Promise.all([fetchRevision($("revisionFrom").value), fetchRevision($("revisionTo").value)]);
    const leftLines = JSON.stringify(left.automation, null, 2).split("\n");
    const rightLines = JSON.stringify(right.automation, null, 2).split("\n");
    const max = Math.max(leftLines.length, rightLines.length);
    const lines = [];
    for (let index = 0; index < max; index += 1) {
      const a = leftLines[index] ?? "";
      const b = rightLines[index] ?? "";
      if (a === b) lines.push(`  ${a}`);
      else {
        if (a) lines.push(`- ${a}`);
        if (b) lines.push(`+ ${b}`);
      }
    }
    $("revisionDiff").textContent = lines.join("\n");
    $("revisionDiff").classList.remove("hidden");
  } catch (error) {
    await showError(error, "Revisions could not be compared");
  }
}

async function restoreRevision(revisionId) {
  const automation = selectedAutomation();
  if (!automation) return;
  const confirmed = await openMessageDialog({
    title: "Restore this revision?",
    message: "The current persisted automation will be saved as another revision before restoration.",
    confirmLabel: "Restore revision",
    showCancel: true,
  });
  if (!confirmed) return;
  setSaving(true, "Restoring revision…");
  try {
    await api(`/api/automations/${encodeURIComponent(automation.id)}/revisions/${encodeURIComponent(revisionId)}/restore`, { method: "POST", body: "{}" });
    $("revisionsDialog").close();
    await loadAutomations();
    state.selectedId = automation.id;
    await loadAutomationHistory(automation.id);
    state.viewMode = "overview";
    renderAll();
    showNotice("Revision restored.");
  } catch (error) {
    await showError(error, "Revision could not be restored");
  } finally {
    setSaving(false);
  }
}


async function openDeletedRevisions() {
  try {
    const data = await api("/api/revisions/deleted");
    state.deletedRevisions = data.deleted || [];
    renderDeletedRevisions();
    $("deletedRevisionsDialog").showModal();
  } catch (error) {
    await showError(error, "Deleted automations could not be loaded");
  }
}

function renderDeletedRevisions() {
  const list = $("deletedRevisionList");
  list.replaceChildren();
  if (!state.deletedRevisions.length) {
    const empty = document.createElement("div");
    empty.className = "steps-empty";
    empty.textContent = "No deleted automations are available to restore.";
    list.append(empty);
    return;
  }
  state.deletedRevisions.forEach((revision) => {
    const row = document.createElement("article");
    row.className = "revision-row";
    const copy = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = revision.automation_name || "Automation";
    const date = document.createElement("span");
    date.textContent = `Deleted ${formatTimestamp(revision.created_at)} · ${String(revision.source || "visual_editor").replaceAll("_", " ")}`;
    copy.append(title, date);
    const restore = document.createElement("button");
    restore.type = "button";
    restore.className = "button ghost small button-with-icon";
    setButtonContent(restore, "restore", "Restore");
    restore.addEventListener("click", () => restoreDeletedRevision(revision.revision_id));
    row.append(copy, restore);
    list.append(row);
  });
}

async function restoreDeletedRevision(revisionId) {
  const confirmed = await openMessageDialog({
    title: "Restore deleted automation?",
    message: "The automation will be recreated with its original identifier and configuration.",
    confirmLabel: "Restore automation",
    showCancel: true,
  });
  if (!confirmed) return;
  setSaving(true, "Restoring automation…");
  try {
    const restored = await api(`/api/revisions/${encodeURIComponent(revisionId)}/restore-deleted`, {
      method: "POST",
      body: "{}",
      editSource: "rollback",
    });
    $("deletedRevisionsDialog").close();
    await loadAutomations();
    state.selectedId = restored.id;
    state.viewMode = "overview";
    await loadAutomationHistory(restored.id);
    renderAll();
    showNotice("Deleted automation restored.");
  } catch (error) {
    await showError(error, "Deleted automation could not be restored");
  } finally {
    setSaving(false);
  }
}
