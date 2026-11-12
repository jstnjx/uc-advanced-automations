// Advanced Automations v2.0.0
/* Advanced Automations v1.0.11 */

const state = {
  automations: [],
  selectedId: null,
  entities: [],
  history: {},
  lastLog: 0,
  visibleLogs: [],
  settings: null,
  dirty: false,
  commandDefinitions: new Map(),
  drag: null,
  stepTarget: null,
  flowStep: 0,
  blueprint: null,
  entitySearch: "",
  viewMode: "overview",
  entityDropdownOpen: false,
  entityTypeFilters: new Set(),
  entityIntegrationFilters: new Set(),
  knownEntityTypes: new Set(),
  knownEntityIntegrations: new Set(),
  continuousLogs: false,
  logPollTimer: null,
  collapsedTriggers: new Set(),
  collapsedSteps: new Set(),
  undoStack: [],
  redoStack: [],
  lastSnapshot: null,
  editSource: "visual_editor",
  revisions: [],
  deletedRevisions: [],
  rawUndoStack: [],
  rawRedoStack: [],
  rawLastValue: "",
};

const $ = (id) => document.getElementById(id);

// Font Awesome 6 Free (solid) code points for the icon names used by the UI.
const ICON_CODES = {
  add: "f067", delete: "f2ed", edit: "f304", play_arrow: "f04b", refresh: "f021",
  settings: "f013", upload_file: "f093", share: "f1e0", code: "f121", save: "f0c7",
  arrow_back: "f060", arrow_forward: "f061", expand_more: "f078", expand_less: "f077",
  close: "f00d", filter_list: "f0b0", search: "f002", drag_indicator: "f58e", check: "f00c",
  content_copy: "f0c5", download: "f019", visibility: "f06e", devices: "f1e6", timer: "f2f2",
  alt_route: "f126", hourglass_empty: "f254", http: "f0ac", description: "f15c",
  select_all: "f14a", deselect: "f0c8", undo: "f0e2", redo: "f01e", history: "f1da",
  account_tree: "f0e8", compare_arrows: "f362", restore: "f829",
};

function iconChar(name) {
  return String.fromCodePoint(parseInt(ICON_CODES[name] || "f128", 16));
}

function materialIcon(name, className = "") {
  const icon = document.createElement("i");
  icon.className = `fa${className ? ` ${className}` : ""}`;
  icon.textContent = iconChar(name);
  icon.setAttribute("aria-hidden", "true");
  return icon;
}

function setButtonContent(button, iconName, text) {
  const label = document.createElement("span");
  label.textContent = text;
  button.replaceChildren(materialIcon(iconName), label);
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

function selectedAutomation() {
  return state.automations.find((item) => item.id === state.selectedId) || null;
}

function snapshotAutomation(automation = selectedAutomation()) {
  if (!automation) return null;
  return JSON.stringify(cleanAutomation(automation));
}

function updateUndoButtons() {
  const undo = $("undoEdit");
  const redo = $("redoEdit");
  if (undo) undo.disabled = state.undoStack.length === 0;
  if (redo) redo.disabled = state.redoStack.length === 0;
  const rawUndo = $("rawUndo");
  const rawRedo = $("rawRedo");
  if (rawUndo) rawUndo.disabled = state.rawUndoStack.length === 0;
  if (rawRedo) rawRedo.disabled = state.rawRedoStack.length === 0;
}

function resetEditHistory() {
  state.undoStack = [];
  state.redoStack = [];
  state.lastSnapshot = snapshotAutomation();
  state.editSource = "visual_editor";
  updateUndoButtons();
}

function markDirty(source = "visual_editor") {
  const current = snapshotAutomation();
  if (current && state.lastSnapshot && current !== state.lastSnapshot) {
    if (state.undoStack[state.undoStack.length - 1] !== state.lastSnapshot) {
      state.undoStack.push(state.lastSnapshot);
      if (state.undoStack.length > 100) state.undoStack.shift();
    }
    state.redoStack = [];
    state.lastSnapshot = current;
  }
  state.editSource = source || state.editSource || "visual_editor";
  state.dirty = true;
  const label = $("saveAutomationLabel");
  if (label) label.textContent = "Save changes";
  updateUndoButtons();
}

function applyEditorSnapshot(snapshot, targetStack) {
  const current = selectedAutomation();
  if (!current || !snapshot) return;
  const parsed = JSON.parse(snapshot);
  parsed.id = current.id;
  parsed._new = current._new;
  const index = state.automations.findIndex((item) => item.id === current.id);
  targetStack.push(snapshotAutomation(current));
  state.automations[index] = parsed;
  state.lastSnapshot = snapshotAutomation(parsed);
  state.dirty = true;
  renderAll();
  updateUndoButtons();
}

function undoEdit() {
  const snapshot = state.undoStack.pop();
  if (snapshot) applyEditorSnapshot(snapshot, state.redoStack);
}

function redoEdit() {
  const snapshot = state.redoStack.pop();
  if (snapshot) applyEditorSnapshot(snapshot, state.undoStack);
}
