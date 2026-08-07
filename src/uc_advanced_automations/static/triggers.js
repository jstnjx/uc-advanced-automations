/* Advanced Automations v1.0.11 */

function renderTriggerModeHelp() {
  const automation = selectedAutomation();
  if (!automation) return;
  $("triggerModeHelp").textContent = automation.trigger_mode === "all"
    ? "A changed trigger must match, and the target value of every other enabled trigger must also be true at that moment."
    : "The automation starts as soon as any enabled trigger matches.";
}

function makeTrigger(type = "entity_state") {
  const base = { id: createId(), type, enabled: true, cooldown_ms: 0 };
  const entityId = firstEntityId();
  if (type === "entity_state") return { ...base, entity_id: entityId, attribute: defaultAttribute(entityId), from_value: null, to_value: null, debounce_ms: 0 };
  if (type === "entity_duration") return { ...base, entity_id: entityId, attribute: defaultAttribute(entityId), value: "ON", duration_ms: 10000 };
  if (type === "numeric_threshold") return { ...base, entity_id: entityId, attribute: "value", threshold: 0, direction: "above", hysteresis: 0 };
  if (type === "entity_change") return { ...base, entity_id: entityId, attribute: "" };
  if (type === "schedule") return { ...base, time: "08:00", weekdays: [0, 1, 2, 3, 4, 5, 6] };
  if (type === "interval") return { ...base, interval_ms: 60000, start_delay_ms: 0 };
  if (type === "remote_event") return { ...base, event: "reconnect" };
  if (type === "webhook") return { ...base, webhook_id: createId() };
  if (type === "automation_outcome") return { ...base, automation_id: state.automations.find((item) => item.id !== state.selectedId)?.id || "", outcome: "success" };
  if (type === "manual") return { ...base, label: "Run automation" };
  return base;
}

function triggerTypeOptions() {
  return [
    { value: "entity_state", label: "Entity state changes" },
    { value: "entity_duration", label: "Entity remains in state" },
    { value: "numeric_threshold", label: "Numeric threshold crossing" },
    { value: "entity_change", label: "Any attribute change" },
    { value: "schedule", label: "Scheduled time" },
    { value: "interval", label: "Periodic interval" },
    { value: "remote_event", label: "Remote connection event" },
    { value: "webhook", label: "Webhook" },
    { value: "automation_outcome", label: "Another automation completes" },
    { value: "manual", label: "Manual virtual button" },
  ];
}

function renderTriggers(automation) {
  const container = $("triggers");
  container.replaceChildren();
  const triggers = automation.triggers || [];
  if (!triggers.length) {
    const empty = document.createElement("div");
    empty.className = "steps-empty";
    empty.textContent = "No triggers configured.";
    container.append(empty);
    return;
  }
  triggers.forEach((trigger, index) => container.append(renderTrigger(trigger, index, triggers)));
}

function renderTrigger(trigger, index, triggers) {
  trigger.type ||= "entity_state";
  const card = document.createElement("article");
  card.className = "trigger-card";
  const collapsed = state.collapsedTriggers.has(trigger.id);
  card.classList.toggle("collapsed", collapsed);
  const head = document.createElement("div");
  head.className = "trigger-card-head collapsible-head";
  const title = document.createElement("div");
  title.className = "trigger-title";
  const handle = dragHandle();
  const toggle = toolButton(collapsed ? "expand_more" : "expand_less", collapsed ? "Expand trigger" : "Collapse trigger", () => {
    if (state.collapsedTriggers.has(trigger.id)) state.collapsedTriggers.delete(trigger.id);
    else state.collapsedTriggers.add(trigger.id);
    renderTriggers(selectedAutomation());
  });
  const label = document.createElement("strong");
  label.textContent = `Trigger ${index + 1} · ${triggerTypeOptions().find((item) => item.value === trigger.type)?.label || trigger.type}`;
  title.append(handle, toggle, label);
  const remove = toolButton("delete", "Delete trigger", () => {
    triggers.splice(index, 1);
    markDirty();
    renderEditor();
  });
  head.append(title, remove);

  const body = document.createElement("div");
  body.className = "trigger-body";
  body.classList.toggle("hidden", collapsed);
  const grid = document.createElement("div");
  grid.className = "trigger-grid";
  grid.append(selectField("Trigger type", trigger.type, triggerTypeOptions(), (value) => {
    const replacement = makeTrigger(value);
    replacement.id = trigger.id;
    replacement.enabled = trigger.enabled !== false;
    triggers[index] = replacement;
    markDirty();
    renderEditor();
  }));

  const updateEntity = (value) => {
    trigger.entity_id = value;
    trigger.attribute = defaultAttribute(value);
    renderEditor();
  };
  if (trigger.type === "entity_state") {
    grid.append(
      entityField("Entity", trigger.entity_id || "", updateEntity),
      attributeField("Attribute", trigger.entity_id, trigger.attribute || "state", (value) => { trigger.attribute = value; }),
      valueField("From", trigger.from_value, (value) => { trigger.from_value = value; }, "Any previous value"),
      valueField("To", trigger.to_value, (value) => { trigger.to_value = value; }, "Any new value"),
      numberField("Stable for (ms)", trigger.debounce_ms ?? 0, (value) => { trigger.debounce_ms = value; }, 0, 86400000),
    );
  } else if (trigger.type === "entity_duration") {
    grid.append(
      entityField("Entity", trigger.entity_id || "", updateEntity),
      attributeField("Attribute", trigger.entity_id, trigger.attribute || "state", (value) => { trigger.attribute = value; }),
      valueField("Required value", trigger.value, (value) => { trigger.value = value; }, "Any value"),
      numberField("Must remain for (ms)", trigger.duration_ms ?? 10000, (value) => { trigger.duration_ms = value; }, 100, 86400000),
    );
  } else if (trigger.type === "numeric_threshold") {
    grid.append(
      entityField("Entity", trigger.entity_id || "", updateEntity),
      attributeField("Numeric attribute", trigger.entity_id, trigger.attribute || "value", (value) => { trigger.attribute = value; }),
      numberField("Threshold", trigger.threshold ?? 0, (value) => { trigger.threshold = value; }, -1000000000, 1000000000),
      selectField("Crossing direction", trigger.direction || "above", [
        { value: "above", label: "Crosses above" },
        { value: "below", label: "Crosses below" },
        { value: "crosses", label: "Crosses in either direction" },
      ], (value) => { trigger.direction = value; }),
      numberField("Hysteresis", trigger.hysteresis ?? 0, (value) => { trigger.hysteresis = value; }, 0, 1000000000),
    );
  } else if (trigger.type === "entity_change") {
    grid.append(
      entityField("Entity", trigger.entity_id || "", updateEntity),
      optionalAttributeField("Attribute", trigger.entity_id, trigger.attribute || "", (value) => { trigger.attribute = value; }),
    );
  } else if (trigger.type === "schedule") {
    grid.append(timeField("Run at", trigger.time || "08:00", (value) => { trigger.time = value; }), weekdayField(trigger));
  } else if (trigger.type === "interval") {
    grid.append(
      numberField("Interval (ms)", trigger.interval_ms ?? 60000, (value) => { trigger.interval_ms = value; }, 1000, 31536000000),
      numberField("Initial delay (ms)", trigger.start_delay_ms ?? 0, (value) => { trigger.start_delay_ms = value; }, 0, 31536000000),
    );
  } else if (trigger.type === "remote_event") {
    grid.append(selectField("Remote event", trigger.event || "reconnect", [
      { value: "startup", label: "Initial Remote connection" },
      { value: "reconnect", label: "Remote reconnects" },
    ], (value) => { trigger.event = value; }));
  } else if (trigger.type === "webhook") {
    grid.append(textField("Webhook identifier", trigger.webhook_id || "", (value) => { trigger.webhook_id = value; }));
    const endpoint = document.createElement("div");
    endpoint.className = "read-only-note wide";
    endpoint.textContent = `${location.origin}/api/webhooks/${trigger.webhook_id || "…"}`;
    grid.append(endpoint);
  } else if (trigger.type === "automation_outcome") {
    const options = state.automations.filter((item) => item.id !== state.selectedId).map((item) => ({ value: item.id, label: item.name }));
    grid.append(
      selectField("Automation", trigger.automation_id || "", options.length ? options : [{ value: "", label: "No other automation available" }], (value) => { trigger.automation_id = value; }),
      selectField("Outcome", trigger.outcome || "success", [
        { value: "success", label: "Completes successfully" },
        { value: "failure", label: "Fails" },
        { value: "any", label: "Completes with any outcome" },
      ], (value) => { trigger.outcome = value; }),
    );
  } else if (trigger.type === "manual") {
    grid.append(textField("Button label", trigger.label || "Run automation", (value) => { trigger.label = value; }));
    const test = document.createElement("button");
    test.className = "button secondary button-with-icon";
    test.type = "button";
    setButtonContent(test, "play_arrow", "Run virtual button");
    test.disabled = Boolean(selectedAutomation()?._new || state.dirty);
    test.addEventListener("click", async () => {
      try {
        const result = await api(`/api/triggers/${encodeURIComponent(trigger.id)}/run`, { method: "POST", body: "{}" });
        showNotice(`Run accepted: ${result.run_id}`);
      } catch (error) { await showError(error, "Manual trigger could not be started"); }
    });
    grid.append(test);
  }
  grid.append(
    numberField("Cooldown (ms)", trigger.cooldown_ms ?? 0, (value) => { trigger.cooldown_ms = value; }, 0, 86400000),
    checkField("Trigger enabled", trigger.enabled !== false, (checked) => { trigger.enabled = checked; renderAutomationList(); }),
  );
  body.append(grid);
  card.append(head, body);
  attachSortable(handle, card, triggers, index, "trigger");
  return card;
}

function optionalAttributeField(labelText, entityId, value, onChange) {
  const label = fieldWrap(labelText);
  const select = document.createElement("select");
  select.append(new Option("Any attribute", ""));
  flattenAttributes(findEntity(entityId)?.attributes || {}).forEach((item) => select.append(new Option(item.path, item.path)));
  if (value && ![...select.options].some((option) => option.value === value)) select.append(new Option(value, value));
  select.value = value || "";
  select.addEventListener("change", () => { onChange(select.value); markDirty(); });
  label.append(select);
  return label;
}

function weekdayField(trigger) {
  const wrapper = document.createElement("fieldset");
  wrapper.className = "weekday-field wide";
  const legend = document.createElement("legend");
  legend.textContent = "Weekdays";
  wrapper.append(legend);
  ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].forEach((name, day) => {
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = (trigger.weekdays || []).includes(day);
    input.addEventListener("change", () => {
      const set = new Set(trigger.weekdays || []);
      if (input.checked) set.add(day); else set.delete(day);
      trigger.weekdays = [...set].sort();
      markDirty();
    });
    label.append(input, document.createTextNode(name));
    wrapper.append(label);
  });
  return wrapper;
}
