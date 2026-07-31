/* Advanced Automations v1.0.4 */

function setupEvents() {
  $("addAutomation").addEventListener("click", addAutomation);
  $("deletedRevisionsButton").addEventListener("click", openDeletedRevisions);
  $("closeDeletedRevisions").addEventListener("click", () => $("deletedRevisionsDialog").close());
  $("emptyAdd").addEventListener("click", addAutomation);
  $("saveAutomation").addEventListener("click", saveCurrent);
  $("deleteAutomation").addEventListener("click", deleteCurrent);
  $("runAutomation").addEventListener("click", runCurrent);
  $("overviewBackButton").addEventListener("click", showAutomationOverview);
  $("editAutomation").addEventListener("click", editCurrentAutomation);
  $("overviewDelete").addEventListener("click", deleteCurrent);
  $("overviewRun").addEventListener("click", runCurrent);
  $("overviewBlueprint").addEventListener("click", () => openBlueprintDialog("export"));
  $("overviewRawEditor").addEventListener("click", openRawEditor);
  $("overviewRevisions").addEventListener("click", openRevisions);
  $("revisionsButton").addEventListener("click", openRevisions);
  $("closeRevisions").addEventListener("click", () => $("revisionsDialog").close());
  $("compareRevisions").addEventListener("click", compareRevisions);
  $("refreshAutomationHistory").addEventListener("click", () => loadAutomationHistory());
  $("undoEdit").addEventListener("click", undoEdit);
  $("redoEdit").addEventListener("click", redoEdit);
  $("settingsButton").addEventListener("click", openSettings);
  $("saveSettings").addEventListener("click", saveSettings);
  $("testConnection").addEventListener("click", testConnection);
  $("refreshLogs").addEventListener("click", pollLogs);
  $("continuousLogs").addEventListener("change", (event) => setContinuousLogPolling(event.target.checked));
  $("clearLogView").addEventListener("click", () => { state.visibleLogs = []; state.lastLog = 0; renderLogs(); });
  $("refreshEntity").addEventListener("click", refreshEntities);
  $("addTrigger").addEventListener("click", () => {
    const automation = selectedAutomation();
    if (!automation) return;
    automation.triggers.push(makeTrigger());
    markDirty();
    renderEditor();
  });
  $("addRootStep").addEventListener("click", () => {
    const automation = selectedAutomation();
    if (automation) openStepPicker(automation.steps);
  });
  $("addCancellationStep").addEventListener("click", () => {
    const automation = selectedAutomation();
    if (automation) openStepPicker(automation.cancellation_steps);
  });
  $("addRollbackStep").addEventListener("click", () => {
    const automation = selectedAutomation();
    if (automation) openStepPicker(automation.rollback_steps);
  });
  $("closeStepDialog").addEventListener("click", closeStepPicker);
  $("stepPicker").addEventListener("click", (event) => {
    const button = event.target.closest("[data-step-type]");
    if (!button || !state.stepTarget) return;
    state.stepTarget.push(makeStep(button.dataset.stepType));
    markDirty();
    closeStepPicker();
    renderEditor();
  });
  $("flowStepper").addEventListener("click", (event) => {
    const button = event.target.closest("[data-flow-step]");
    if (button) setFlowStep(button.dataset.flowStep);
  });
  $("flowBack").addEventListener("click", () => setFlowStep(state.flowStep - 1));
  $("flowNext").addEventListener("click", continueFlow);
  $("entityDropdownToggle").addEventListener("click", () => {
    state.entityDropdownOpen = !state.entityDropdownOpen;
    renderEntitySelection();
  });
  $("entitySearch").addEventListener("input", (event) => {
    state.entitySearch = event.target.value;
    renderEntitySelection();
  });
  $("selectAllEntities").addEventListener("click", () => {
    const automation = selectedAutomation();
    if (!automation) return;
    const next = new Set(automation.entity_ids || []);
    filteredEntityOptions().forEach((entity) => next.add(entity.entity_id));
    automation.entity_ids = [...next];
    markDirty();
    renderEditor();
  });
  $("clearEntitySelection").addEventListener("click", () => {
    const automation = selectedAutomation();
    if (!automation) return;
    const used = entityUsage(automation);
    automation.entity_ids = (automation.entity_ids || []).filter((entityId) => used.has(entityId));
    markDirty();
    renderEditor();
  });
  $("rawEditorButton").addEventListener("click", openRawEditor);
  $("closeRawEditor").addEventListener("click", () => $("rawEditorDialog").close());
  $("cancelRawEditor").addEventListener("click", () => $("rawEditorDialog").close());
  $("formatRawJson").addEventListener("click", formatRawEditor);
  $("rawAutomationJson").addEventListener("input", recordRawEdit);
  $("rawUndo").addEventListener("click", rawUndo);
  $("rawRedo").addEventListener("click", rawRedo);
  $("applyRawEditor").addEventListener("click", applyRawEditor);
  $("blueprintButton").addEventListener("click", () => openBlueprintDialog("export"));
  $("emptyBlueprint").addEventListener("click", () => openBlueprintDialog("import"));
  $("closeBlueprintDialog").addEventListener("click", () => $("blueprintDialog").close());
  $("blueprintExportTab").addEventListener("click", () => showBlueprintTab("export"));
  $("blueprintImportTab").addEventListener("click", () => showBlueprintTab("import"));
  $("copyBlueprint").addEventListener("click", () => copyText($("blueprintExportJson")));
  $("downloadBlueprint").addEventListener("click", downloadBlueprint);
  $("readBlueprint").addEventListener("click", parseBlueprint);
  $("createFromBlueprint").addEventListener("click", createFromBlueprint);
  $("blueprintFile").addEventListener("change", async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      $("blueprintImportJson").value = await file.text();
      parseBlueprint();
    } catch (error) {
      blueprintResult(`Unable to read blueprint: ${error.message}`, "error");
    }
  });
  document.addEventListener("click", (event) => {
    if (!state.entityDropdownOpen || event.target.closest(".entity-picker")) return;
    state.entityDropdownOpen = false;
    renderEntitySelection();
  });
  bindEditorFields();
}

async function init() {
  setupEvents();
  try {
    await loadAutomations();
    await Promise.allSettled([loadEntities(), pollStatus()]);
  } catch (error) {
    await showError(error, "Advanced Automations could not be loaded");
  }
  setInterval(pollStatus, 5000);
}

init();
