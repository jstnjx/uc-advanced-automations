/* Advanced Automations v1.0.5 */

function walkAutomationEntityReferences(automation, callback) {
  (automation.triggers || []).forEach((trigger, index) => {
    if (["entity_state", "entity_duration", "numeric_threshold", "entity_change"].includes(trigger.type || "entity_state") && trigger.entity_id) {
      callback(trigger, "entity_id", `Trigger ${index + 1}`, false);
    }
  });
  const walk = (steps, prefix) => {
    (steps || []).forEach((step, index) => {
      const path = `${prefix} ${index + 1}`;
      if (step.type === "command" && step.entity_id) callback(step, "entity_id", `${path} · Entity command`, true);
      if (step.type === "condition" || step.type === "wait") {
        (step.conditions || []).forEach((condition, conditionIndex) => {
          if ((condition.kind || "entity") === "entity" && condition.entity_id) callback(condition, "entity_id", `${path} · Condition ${conditionIndex + 1}`, false);
        });
      }
      ["then", "else", "failure_steps", "match_steps", "timeout_steps"].forEach((key) => walk(step[key] || [], `${path} · ${key.replaceAll("_", " ")}`));
      if (step.type === "parallel") (step.branches || []).forEach((branch, branchIndex) => walk(branch.steps || [], `${path} · ${branch.name || `Branch ${branchIndex + 1}`}`));
    });
  };
  walk(automation.steps || [], "Step");
  walk(automation.cancellation_steps || [], "Cancellation step");
  walk(automation.rollback_steps || [], "Rollback step");
}

function commandEntityReferences(automation) {
  const result = new Set();
  walkAutomationEntityReferences(automation, (object, key, _path, commandable) => {
    if (commandable) result.add(object[key]);
  });
  return result;
}

function replaceExactStrings(value, replacements) {
  if (typeof value === "string") return replacements.has(value) ? replacements.get(value) : value;
  if (Array.isArray(value)) return value.map((item) => replaceExactStrings(item, replacements));
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, replaceExactStrings(item, replacements)]));
  }
  return value;
}

function buildBlueprint(automation) {
  const template = cleanAutomation(automation);
  delete template.id;
  template.entity_ids = [];
  (template.triggers || []).forEach((trigger) => { delete trigger.id; });
  let counter = 0;
  walkAutomationEntityReferences(template, (object, key) => {
    counter += 1;
    const slot = `reference_${counter}`;
    object[key] = `$entity:${slot}`;
  });
  return {
    format: "advanced-automations-blueprint",
    version: 2,
    metadata: {
      name: automation.name || "Automation blueprint",
      description: automation.description || "",
      exported_at: new Date().toISOString(),
    },
    automation: template,
  };
}

function showBlueprintTab(tab) {
  const exporting = tab === "export";
  $("blueprintExportTab").classList.toggle("active", exporting);
  $("blueprintImportTab").classList.toggle("active", !exporting);
  $("blueprintExportPanel").classList.toggle("hidden", !exporting);
  $("blueprintImportPanel").classList.toggle("hidden", exporting);
}

function openBlueprintDialog(tab = "export") {
  const automation = selectedAutomation();
  const canExport = Boolean(automation);
  $("blueprintExportTab").disabled = !canExport;
  if (canExport) $("blueprintExportJson").value = JSON.stringify(buildBlueprint(automation), null, 2);
  state.blueprint = null;
  $("blueprintEntityMappings").replaceChildren();
  $("blueprintEntityMappings").classList.add("hidden");
  $("createFromBlueprint").classList.add("hidden");
  $("blueprintImportResult").className = "inline-result hidden";
  showBlueprintTab(canExport ? tab : "import");
  $("blueprintDialog").showModal();
}

async function copyText(textarea) {
  textarea.focus();
  textarea.select();
  try {
    if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(textarea.value);
    else document.execCommand("copy");
    showNotice("Blueprint copied to the clipboard.");
  } catch (error) {
    await showError(error, "Blueprint could not be copied");
  }
}

function safeFileName(value) {
  return String(value || "automation-blueprint")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "") || "automation-blueprint";
}

function downloadBlueprint() {
  const blueprint = JSON.parse($("blueprintExportJson").value);
  const blob = new Blob([JSON.stringify(blueprint, null, 2) + "\n"], { type: "application/json" });
  const link = document.createElement("a");
  const objectUrl = URL.createObjectURL(blob);
  link.href = objectUrl;
  link.download = `${safeFileName(blueprint.metadata?.name)}.blueprint.json`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
}

function blueprintResult(message, type = "success") {
  const result = $("blueprintImportResult");
  result.textContent = message;
  result.className = `inline-result ${type}`;
}

function collectBlueprintPlaceholders(automation) {
  const result = [];
  const seen = new Set();
  const add = (value, path, commandable) => {
    if (typeof value !== "string" || !value.startsWith("$entity:")) return;
    const slot = value.slice(8);
    if (seen.has(slot)) return;
    seen.add(slot);
    result.push({ slot, path, commandable });
  };
  (automation.triggers || []).forEach((trigger, index) => add(trigger.entity_id, `Trigger ${index + 1}`, false));
  const walk = (steps, prefix) => {
    (steps || []).forEach((step, index) => {
      const path = `${prefix} ${index + 1}`;
      if (step.type === "command") add(step.entity_id, `${path} · Entity command`, true);
      if (["condition", "wait"].includes(step.type)) {
        (step.conditions || []).forEach((condition, conditionIndex) => add(condition.entity_id, `${path} · Condition ${conditionIndex + 1}`, false));
      }
      ["then", "else", "failure_steps", "match_steps", "timeout_steps"].forEach((key) => walk(step[key] || [], `${path} · ${key.replaceAll("_", " ")}`));
      if (step.type === "parallel") (step.branches || []).forEach((branch, branchIndex) => walk(branch.steps || [], `${path} · ${branch.name || `Branch ${branchIndex + 1}`}`));
    });
  };
  walk(automation.steps || [], "Step");
  walk(automation.cancellation_steps || [], "Cancellation step");
  walk(automation.rollback_steps || [], "Rollback step");
  return result;
}

function parseBlueprint() {
  try {
    const blueprint = JSON.parse($("blueprintImportJson").value);
    if (blueprint?.format !== "advanced-automations-blueprint") throw new Error("This is not an Advanced Automations blueprint");
    if (![1, 2].includes(blueprint.version)) throw new Error(`Unsupported blueprint version: ${blueprint.version}`);
    if (!blueprint.automation || typeof blueprint.automation !== "object") throw new Error("Blueprint automation is missing");
    if (blueprint.version === 1 && Array.isArray(blueprint.entities)) {
      blueprint.references = blueprint.entities.map((item) => ({ slot: item.slot, path: item.name || item.slot, commandable: Boolean(item.commandable) }));
    } else {
      blueprint.references = collectBlueprintPlaceholders(blueprint.automation);
    }
    state.blueprint = blueprint;
    renderBlueprintMappings();
    blueprintResult(`Blueprint ready: ${blueprint.metadata?.name || blueprint.automation.name || "Untitled"}`);
  } catch (error) {
    state.blueprint = null;
    $("blueprintEntityMappings").classList.add("hidden");
    $("createFromBlueprint").classList.add("hidden");
    blueprintResult(error.message, "error");
  }
}

function renderBlueprintMappings() {
  const container = $("blueprintEntityMappings");
  container.replaceChildren();
  const blueprint = state.blueprint;
  if (!blueprint) return;
  (blueprint.references || []).forEach((reference, index) => {
    const row = document.createElement("label");
    row.className = "blueprint-mapping-row";
    const details = document.createElement("span");
    details.className = "mapping-source";
    const title = document.createElement("strong");
    title.textContent = reference.path || `Entity reference ${index + 1}`;
    const subtitle = document.createElement("small");
    subtitle.textContent = reference.commandable ? "Command target" : "Trigger or condition entity";
    details.append(title, subtitle);
    const select = document.createElement("select");
    select.dataset.blueprintSlot = reference.slot;
    select.append(new Option("Select entity…", ""));
    const candidates = reference.commandable ? state.entities.filter((entity) => !isSensor(entity)) : state.entities;
    candidates.forEach((entity) => select.append(new Option(`${displayName(entity)} · ${entity.entity_type || "entity"}`, entity.entity_id)));
    row.append(details, select);
    container.append(row);
  });
  container.classList.remove("hidden");
  $("createFromBlueprint").classList.remove("hidden");
}

function uniqueCommand(base) {
  let command = String(base || "IMPORTED_AUTOMATION").toUpperCase().replace(/[^A-Z0-9_]/g, "_");
  if (!/^[A-Z]/.test(command)) command = `AUTOMATION_${command}`;
  command = command.slice(0, 64);
  const existing = new Set(state.automations.map((item) => item.command));
  if (!existing.has(command)) return command;
  let number = 2;
  while (existing.has(`${command.slice(0, 60)}_${number}`)) number += 1;
  return `${command.slice(0, 60)}_${number}`;
}

async function createFromBlueprint() {
  const blueprint = state.blueprint;
  if (!blueprint) return;
  const mapping = new Map();
  const missing = [];
  document.querySelectorAll("[data-blueprint-slot]").forEach((select) => {
    if (!select.value) missing.push(select.dataset.blueprintSlot);
    else mapping.set(`$entity:${select.dataset.blueprintSlot}`, select.value);
  });
  if (missing.length) {
    blueprintResult("Choose an entity for every trigger, condition, and sequence reference.", "error");
    return;
  }
  if (!(await allowDiscardChanges())) return;
  if (state.dirty) await loadAutomations();
  const imported = replaceExactStrings(blueprint.automation, mapping);
  imported.id = createId();
  imported._new = true;
  imported.name = imported.name || blueprint.metadata?.name || "Imported automation";
  imported.command = uniqueCommand(imported.command || imported.name);
  imported.triggers = (imported.triggers || []).map((trigger) => ({ ...trigger, id: createId() }));
  imported.steps = Array.isArray(imported.steps) ? imported.steps : [];
  imported.cancellation_steps = Array.isArray(imported.cancellation_steps) ? imported.cancellation_steps : [];
  imported.rollback_steps = Array.isArray(imported.rollback_steps) ? imported.rollback_steps : [];
  imported.entity_ids = collectReferencedEntityIds(imported);
  state.automations.push(imported);
  state.selectedId = imported.id;
  state.dirty = true;
  state.editSource = "blueprint_import";
  state.flowStep = 0;
  state.viewMode = "edit";
  resetEditHistory();
  state.editSource = "blueprint_import";
  $("blueprintDialog").close();
  renderAll();
  showNotice("Blueprint imported. Map and review each step, then save the automation.");
}
