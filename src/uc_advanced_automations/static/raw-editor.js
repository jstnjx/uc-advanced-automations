/* Advanced Automations v1.0.11 */

function openRawEditor() {
  const automation = selectedAutomation();
  if (!automation) return;
  const value = JSON.stringify(cleanAutomation(automation), null, 2);
  $("rawAutomationJson").value = value;
  state.rawUndoStack = [];
  state.rawRedoStack = [];
  state.rawLastValue = value;
  $("rawEditorResult").className = "inline-result hidden";
  updateUndoButtons();
  $("rawEditorDialog").showModal();
}

function recordRawEdit() {
  const value = $("rawAutomationJson").value;
  if (value === state.rawLastValue) return;
  state.rawUndoStack.push(state.rawLastValue);
  if (state.rawUndoStack.length > 100) state.rawUndoStack.shift();
  state.rawRedoStack = [];
  state.rawLastValue = value;
  updateUndoButtons();
}

function rawUndo() {
  const value = state.rawUndoStack.pop();
  if (value == null) return;
  state.rawRedoStack.push($("rawAutomationJson").value);
  $("rawAutomationJson").value = value;
  state.rawLastValue = value;
  updateUndoButtons();
}

function rawRedo() {
  const value = state.rawRedoStack.pop();
  if (value == null) return;
  state.rawUndoStack.push($("rawAutomationJson").value);
  $("rawAutomationJson").value = value;
  state.rawLastValue = value;
  updateUndoButtons();
}

function formatRawEditor() {
  const result = $("rawEditorResult");
  try {
    const value = JSON.parse($("rawAutomationJson").value);
    $("rawAutomationJson").value = JSON.stringify(value, null, 2);
    result.textContent = "JSON is valid.";
    result.className = "inline-result success";
  } catch (error) {
    result.textContent = `Invalid JSON: ${error.message}`;
    result.className = "inline-result error";
  }
}

async function applyRawEditor() {
  const current = selectedAutomation();
  if (!current) return;
  const result = $("rawEditorResult");
  try {
    const parsed = JSON.parse($("rawAutomationJson").value);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("Automation JSON must be an object");
    parsed.id = current.id;
    parsed._new = current._new;
    parsed.entity_ids = Array.isArray(parsed.entity_ids) ? parsed.entity_ids : collectReferencedEntityIds(parsed);
    parsed.triggers = Array.isArray(parsed.triggers) ? parsed.triggers : [];
    parsed.steps = Array.isArray(parsed.steps) ? parsed.steps : [];
    const index = state.automations.findIndex((item) => item.id === current.id);
    state.automations[index] = parsed;
    state.selectedId = parsed.id;
    markDirty("raw_editor");
    state.viewMode = "edit";
    $("rawEditorDialog").close();
    renderAll();
    showNotice("Raw JSON changes applied. Save the automation to persist them.");
  } catch (error) {
    result.textContent = `Unable to apply JSON: ${error.message}`;
    result.className = "inline-result error";
  }
}
