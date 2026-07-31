/* Advanced Automations v1.0.9 */

async function pollStatus() {
  try {
    const status = await api("/api/status");
    const badge = $("connectionBadge");
    badge.className = `status-badge ${status.core_connected ? "connected" : status.core_error ? "error" : ""}`;
    badge.innerHTML = `<span></span>${status.core_connected ? `Remote connected · ${status.running} running` : status.api_key_configured ? "Remote not connected" : "Setup required"}`;
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

function setContinuousLogPolling(enabled) {
  state.continuousLogs = Boolean(enabled);
  if (state.logPollTimer) {
    clearInterval(state.logPollTimer);
    state.logPollTimer = null;
  }
  if (state.continuousLogs) {
    pollLogs();
    state.logPollTimer = setInterval(pollLogs, 2000);
  }
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
