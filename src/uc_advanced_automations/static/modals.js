/* Advanced Automations v1.0.1 */

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

function setSaving(active, message = "Saving automation…") {
  $("savingOverlayText").textContent = message;
  $("savingOverlay").classList.toggle("hidden", !active);
  document.body.classList.toggle("saving", active);
}
