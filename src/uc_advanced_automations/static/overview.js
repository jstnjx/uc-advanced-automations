/* Advanced Automations v1.0.6 */

function describeValue(value, fallback = "any value") {
  if (value === null || value === undefined || value === "") return fallback;
  return valueToInput(value);
}

function describeTrigger(trigger) {
  const type = trigger.type || "entity_state";
  const entity = findEntity(trigger.entity_id);
  const name = displayName(entity || { entity_id: trigger.entity_id || "Unselected entity" });
  if (type === "entity_state") {
    const attribute = trigger.attribute || "state";
    const from = describeValue(trigger.from_value);
    const to = describeValue(trigger.to_value);
    if (trigger.from_value == null && trigger.to_value == null) return `${name} · ${attribute} changes`;
    if (trigger.from_value == null) return `${name} · ${attribute} becomes ${to}`;
    if (trigger.to_value == null) return `${name} · ${attribute} changes from ${from}`;
    return `${name} · ${attribute}: ${from} → ${to}`;
  }
  if (type === "entity_duration") return `${name} · ${trigger.attribute || "state"} remains ${describeValue(trigger.value)} for ${Number(trigger.duration_ms || 0) / 1000} seconds`;
  if (type === "numeric_threshold") return `${name} · ${trigger.attribute || "value"} crosses ${trigger.direction || "above"} ${trigger.threshold}`;
  if (type === "entity_change") return `${name} · ${trigger.attribute || "any attribute"} changes`;
  if (type === "schedule") return `At ${trigger.time || "00:00"} on ${(trigger.weekdays || []).length} selected day(s)`;
  if (type === "interval") return `Every ${Number(trigger.interval_ms || 0) / 1000} seconds`;
  if (type === "remote_event") return trigger.event === "startup" ? "When the Remote first connects" : "When the Remote reconnects";
  if (type === "webhook") return `Webhook ${trigger.webhook_id || "not configured"}`;
  if (type === "automation_outcome") {
    const source = state.automations.find((item) => item.id === trigger.automation_id);
    return `${source?.name || "Another automation"} · ${trigger.outcome || "success"}`;
  }
  if (type === "manual") return `Manual virtual button · ${trigger.label || "Run automation"}`;
  return type;
}

function describeConditionGroup(step) {
  const count = (step.conditions || []).length;
  const behavior = step.mode === "any" ? "any condition" : "every condition";
  return `${count} ${count === 1 ? "condition" : "conditions"}; require ${behavior}`;
}

function describeStep(step) {
  if (step.type === "command") {
    const entity = findEntity(step.entity_id);
    return `Control ${displayName(entity || { entity_id: step.entity_id || "an entity" })}: ${step.cmd_id || "command not selected"}`;
  }
  if (step.type === "delay") return `Delay for ${Number(step.milliseconds || 0) / 1000} seconds`;
  if (step.type === "condition") return `Choose a branch using ${describeConditionGroup(step)}`;
  if (step.type === "wait") {
    const reference = step.time_reference === "trigger" ? "from the automation trigger" : "from when this step begins";
    return `Wait up to ${Number(step.timeout_ms || 0) / 1000} seconds ${reference}; on match: ${step.on_match || "continue"}; on timeout: ${step.on_timeout || "fail"}`;
  }
  if (step.type === "parallel") return `Run ${(step.branches || []).length} branches in parallel and wait for ${step.wait_for || "all"}`;
  if (step.type === "http") return `${String(step.method || "POST").toUpperCase()} ${step.url || "endpoint not configured"}`;
  if (step.type === "log") return `Write ${step.level || "info"} log: ${step.message || "message not configured"}`;
  return stepLabel(step.type);
}

function appendTimelineItem(container, title, detail, kind = "step", depth = 0) {
  const item = document.createElement("article");
  item.className = `timeline-item ${kind}`;
  item.style.setProperty("--timeline-depth", String(depth));
  const marker = document.createElement("span");
  marker.className = "timeline-marker";
  const content = document.createElement("div");
  const heading = document.createElement("strong");
  heading.textContent = title;
  const text = document.createElement("p");
  text.textContent = detail;
  content.append(heading, text);
  item.append(marker, content);
  container.append(item);
}

function appendSequenceTimeline(container, steps, depth = 0, prefix = "") {
  (steps || []).forEach((step, index) => {
    const label = `${prefix}${index + 1}. ${stepLabel(step.type)}`;
    appendTimelineItem(container, label, describeStep(step), step.type, depth);
    const branches = [
      ["then", "Then branch", "T"],
      ["else", "Else branch", "E"],
      ["failure_steps", "Failure branch", "F"],
      ["match_steps", "Condition matched branch", "M"],
      ["timeout_steps", "Timeout branch", "O"],
    ];
    branches.forEach(([key, title, branchPrefix]) => {
      if ((step[key] || []).length) {
        appendTimelineItem(container, title, `${step[key].length} step(s)`, "branch", depth + 1);
        appendSequenceTimeline(container, step[key], depth + 2, branchPrefix);
      }
    });
    if (step.type === "parallel") {
      (step.branches || []).forEach((branch, branchIndex) => {
        appendTimelineItem(container, branch.name || `Branch ${branchIndex + 1}`, `${(branch.steps || []).length} step(s)`, "branch", depth + 1);
        appendSequenceTimeline(container, branch.steps || [], depth + 2, `P${branchIndex + 1}.`);
      });
    }
  });
}

function formatTimestamp(value) {
  if (!value) return "Never";
  try { return new Date(value).toLocaleString(); } catch (_) { return String(value); }
}

function formatDuration(milliseconds) {
  if (milliseconds == null) return "—";
  const value = Number(milliseconds);
  if (value < 1000) return `${Math.round(value)} ms`;
  if (value < 60000) return `${(value / 1000).toFixed(value < 10000 ? 1 : 0)} s`;
  return `${(value / 60000).toFixed(1)} min`;
}

function renderOverview(automation) {
  $("overviewTitle").textContent = automation.name || "Untitled automation";
  $("overviewDescription").textContent = automation.description || "No description provided.";
  const status = $("overviewStatus");
  status.textContent = automation.enabled === false ? "Disabled" : "Enabled";
  status.className = `overview-status${automation.enabled === false ? " disabled" : ""}`;

  const history = state.history[automation.id] || {};
  const metrics = $("overviewMetrics");
  metrics.replaceChildren();
  const values = [
    ["Run mode", automation.mode || "single"],
    ["Last run", formatTimestamp(history.last_run?.started_at)],
    ["Last successful run", formatTimestamp(history.last_successful_run?.started_at)],
    ["Last failure", formatTimestamp(history.last_failure?.started_at)],
    ["Average duration", formatDuration(history.average_duration_ms)],
    ["Currently active step", history.currently_active_step || "Not running"],
  ];
  values.forEach(([label, value]) => {
    const card = document.createElement("div");
    const small = document.createElement("span");
    small.textContent = label;
    const strong = document.createElement("strong");
    strong.textContent = value;
    card.append(small, strong);
    metrics.append(card);
  });

  const timeline = $("automationTimeline");
  timeline.replaceChildren();
  const enabledTriggers = (automation.triggers || []).filter((trigger) => trigger.enabled !== false);
  if (enabledTriggers.length) {
    appendTimelineItem(timeline, "Start", automation.trigger_mode === "all"
      ? "A matching trigger starts the automation only when all configured state requirements are also true."
      : "Any matching enabled trigger starts the automation.", "trigger");
    enabledTriggers.forEach((trigger, index) => appendTimelineItem(timeline, `Trigger ${index + 1}`, describeTrigger(trigger), "trigger", 1));
  }
  if (automation.command_enabled !== false) appendTimelineItem(timeline, "Remote command", automation.command || "Not configured", "trigger");
  if ((automation.steps || []).length) appendSequenceTimeline(timeline, automation.steps);
  else appendTimelineItem(timeline, "No sequence steps", "Edit the automation and add actions.", "warning");
  if ((automation.cancellation_steps || []).length) {
    appendTimelineItem(timeline, "Cancellation cleanup", `${automation.cancellation_steps.length} cleanup step(s)`, "branch");
    appendSequenceTimeline(timeline, automation.cancellation_steps, 1, "C");
  }
  if ((automation.rollback_steps || []).length) {
    appendTimelineItem(timeline, "Rollback", `${automation.rollback_steps.length} rollback step(s)`, "branch");
    appendSequenceTimeline(timeline, automation.rollback_steps, 1, "R");
  }

  const recent = $("recentRunHistory");
  recent.replaceChildren();
  const runs = history.recent_runs || [];
  if (!runs.length) {
    const empty = document.createElement("div");
    empty.className = "steps-empty";
    empty.textContent = "No runs recorded yet.";
    recent.append(empty);
  } else {
    runs.forEach((run) => {
      const row = document.createElement("article");
      row.className = `run-history-row status-${run.status}`;
      const statusText = document.createElement("strong");
      statusText.className = `run-status ${run.status}`;
      statusText.textContent = run.status;
      const details = document.createElement("span");
      details.textContent = `${formatTimestamp(run.started_at)} · ${formatDuration(run.duration_ms)} · ${run.source || "unknown source"}`;
      const current = document.createElement("small");
      current.textContent = run.current_step || run.error || "";
      row.append(statusText, details, current);
      recent.append(row);
    });
  }
}

async function loadAutomationHistory(automationId = state.selectedId) {
  if (!automationId) return;
  try {
    state.history[automationId] = await api(`/api/automations/${encodeURIComponent(automationId)}/history`);
    if (state.selectedId === automationId && state.viewMode === "overview") renderOverview(selectedAutomation());
    renderAutomationList();
  } catch (_) {
    // History should never block editing or running an automation.
  }
}
