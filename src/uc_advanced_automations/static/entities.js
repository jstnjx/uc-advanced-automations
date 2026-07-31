/* Advanced Automations v1.0.8 */

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

function selectedEntityIds() {
  const automation = selectedAutomation();
  return Array.isArray(automation?.entity_ids) ? automation.entity_ids : [];
}

function scopedEntities({ commandable = false } = {}) {
  const selected = new Set(selectedEntityIds());
  const source = selected.size ? state.entities.filter((entity) => selected.has(entity.entity_id)) : [];
  return commandable ? source.filter((entity) => !isSensor(entity)) : source;
}

function commandableEntities() {
  return scopedEntities({ commandable: true });
}

function firstEntityId({ commandable = false } = {}) {
  const entities = scopedEntities({ commandable });
  return entities[0]?.entity_id || "";
}

function collectReferencedEntityIds(automation) {
  const result = [];
  const seen = new Set();
  const add = (value) => {
    if (typeof value === "string" && value && !value.startsWith("$entity:") && !seen.has(value)) {
      seen.add(value);
      result.push(value);
    }
  };
  (automation?.triggers || []).forEach((trigger) => {
    if (["entity_state", "entity_duration", "numeric_threshold", "entity_change"].includes(trigger.type || "entity_state")) add(trigger.entity_id);
  });
  const walk = (steps) => {
    (steps || []).forEach((step) => {
      if (step.type === "command") add(step.entity_id);
      if (step.type === "condition" || step.type === "wait") {
        (step.conditions || []).forEach((condition) => {
          if ((condition.kind || "entity") === "entity") add(condition.entity_id);
        });
      }
      ["then", "else", "failure_steps", "match_steps", "timeout_steps"].forEach((key) => walk(step[key] || []));
      if (step.type === "parallel") (step.branches || []).forEach((branch) => walk(branch.steps || []));
    });
  };
  walk(automation?.steps || []);
  walk(automation?.cancellation_steps || []);
  walk(automation?.rollback_steps || []);
  return result;
}

function entityUsage(automation) {
  return new Set(collectReferencedEntityIds(automation));
}

function entityIntegration(entity) {
  const value = entity?.integration_name ?? entity?.integration_id ?? entity?.integration;
  if (typeof value === "string" && value.trim()) return value.trim();
  if (value && typeof value === "object") {
    return localizedName(value.name, value.id || value.driver_id || "Unknown integration");
  }
  return "Unknown integration";
}

function syncEntityFilterOptions() {
  const types = new Set(state.entities.map((entity) => String(entity.entity_type || "entity")));
  const integrations = new Set(state.entities.map(entityIntegration));
  for (const type of types) {
    if (!state.knownEntityTypes.has(type)) state.entityTypeFilters.add(type);
    state.knownEntityTypes.add(type);
  }
  for (const integration of integrations) {
    if (!state.knownEntityIntegrations.has(integration)) state.entityIntegrationFilters.add(integration);
    state.knownEntityIntegrations.add(integration);
  }
}

function filteredEntityOptions() {
  const query = String(state.entitySearch || "").trim().toLowerCase();
  return state.entities.filter((entity) => {
    const type = String(entity.entity_type || "entity");
    const integration = entityIntegration(entity);
    const haystack = `${displayName(entity)} ${entity.entity_id || ""}`.toLowerCase();
    return state.entityTypeFilters.has(type)
      && state.entityIntegrationFilters.has(integration)
      && (!query || haystack.includes(query));
  });
}

function renderFilterCheckboxes(containerId, values, selectedSet) {
  const container = $(containerId);
  container.replaceChildren();
  [...values].sort((a, b) => a.localeCompare(b)).forEach((value) => {
    const label = document.createElement("label");
    label.className = "filter-check";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = selectedSet.has(value);
    input.addEventListener("change", () => {
      if (input.checked) selectedSet.add(value);
      else selectedSet.delete(value);
      renderEntitySelection();
    });
    const text = document.createElement("span");
    text.textContent = value;
    label.append(input, text);
    container.append(label);
  });
}

function renderEntitySelection() {
  const automation = selectedAutomation();
  if (!automation) return;
  syncEntityFilterOptions();
  const container = $("automationEntities");
  container.replaceChildren();
  const selected = new Set(automation.entity_ids || []);
  const used = entityUsage(automation);
  const entities = filteredEntityOptions();
  $("selectedEntityCount").textContent = `${selected.size} selected`;
  $("entityDropdownSummary").textContent = selected.size
    ? `${selected.size} entit${selected.size === 1 ? "y" : "ies"} selected`
    : "Select entities…";
  $("entityDropdownPanel").classList.toggle("hidden", !state.entityDropdownOpen);
  $("entityDropdownToggle").setAttribute("aria-expanded", String(state.entityDropdownOpen));
  renderFilterCheckboxes("entityTypeFilters", state.knownEntityTypes, state.entityTypeFilters);
  renderFilterCheckboxes("entityIntegrationFilters", state.knownEntityIntegrations, state.entityIntegrationFilters);

  if (!state.entities.length) {
    const empty = document.createElement("div");
    empty.className = "steps-empty wide-empty";
    empty.textContent = "Connect to the Remote to load entities.";
    container.append(empty);
    return;
  }
  if (!entities.length) {
    const empty = document.createElement("div");
    empty.className = "steps-empty wide-empty";
    empty.textContent = "No entities match the selected filters.";
    container.append(empty);
    return;
  }

  entities.forEach((entity) => {
    const label = document.createElement("label");
    label.className = `entity-dropdown-option${selected.has(entity.entity_id) ? " selected" : ""}`;
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = selected.has(entity.entity_id);
    const inUse = used.has(entity.entity_id);
    input.addEventListener("change", async () => {
      if (!input.checked && inUse) {
        input.checked = true;
        await openMessageDialog({
          title: "Entity is still in use",
          message: "Remove this entity from its triggers, conditions and sequence steps before deselecting it.",
          confirmLabel: "Close",
        });
        return;
      }
      const next = new Set(automation.entity_ids || []);
      if (input.checked) next.add(entity.entity_id);
      else next.delete(entity.entity_id);
      automation.entity_ids = [...next];
      markDirty();
      renderEntitySelection();
    });
    const content = document.createElement("span");
    content.className = "entity-option-content";
    const name = document.createElement("strong");
    name.textContent = displayName(entity);
    const metadata = document.createElement("small");
    const tags = [entity.entity_type || "entity", entityIntegration(entity)];
    if (isSensor(entity)) tags.push("read-only");
    if (inUse) tags.push("in use");
    metadata.textContent = tags.join(" · ");
    content.append(name, metadata);
    label.append(input, content);
    container.append(label);
  });
}

async function loadEntities() {
  try {
    const data = await api("/api/entities");
    state.entities = data.entities;
    syncEntityFilterOptions();
    state.commandDefinitions.clear();
    if (selectedAutomation()) renderEditor();
  } catch (_) {
    state.entities = [];
  }
}

async function refreshEntities() {
  try {
    const result = await api("/api/integration/refresh", { method: "POST", body: "{}" });
    const message = result.message || ({
      refreshed: "Entities refreshed.",
      reloaded: "Entities refreshed.",
      current: "Entities are already current.",
      "not-configured": "Add the Advanced Automations entity to the Remote first.",
    }[result.status] || `Refresh status: ${result.status}`);
    if (result.status === "failed") await showError(new ApiError(message), "entity refresh failed");
    else showNotice(message, "success", 7000);
  } catch (error) {
    await showError(error, "entity refresh failed");
  }
}

function entityField(labelText, value, onChange, { commandable = false } = {}) {
  const label = fieldWrap(labelText);
  const select = document.createElement("select");
  const candidates = scopedEntities({ commandable });
  if (!candidates.length) {
    const text = commandable ? "No selected command-capable entities" : "Choose entities in step 2";
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
  const options = flattenAttributes(findEntity(entityId)?.attributes || {});
  if (!options.some((item) => item.path === value) && value) options.unshift({ path: value, value: undefined });
  if (!options.length) options.push({ path: value || "state", value: undefined });
  const selectedPath = value || options[0].path;
  const currentValue = getAttributeValue(entityId, selectedPath);
  const currentText = currentValue === undefined ? "Current value unavailable" : `Current: ${valueToInput(currentValue)}`;
  const label = fieldWrap(`${labelText} - ${currentText}`);
  const select = document.createElement("select");
  for (const item of options) select.append(new Option(item.path, item.path));
  select.value = selectedPath;
  select.addEventListener("change", () => { onChange(select.value); markDirty(); renderEditor(); });
  label.append(select);
  return label;
}
