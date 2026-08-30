// Advanced Automations v2.0.0
/* Advanced Automations v2 sequence extensions */

function installSequenceV2Editor() {
  const originalMakeStep = makeStep;
  const originalStepLabel = stepLabel;
  const originalRenderStepBody = renderStepBody;
  const originalValidateStepsDraft = validateStepsDraft;

  const newTypes = new Set([
    "set_variable", "template", "choose", "wait_event", "run_automation",
    "stop_automation", "command_sequence", "activity",
  ]);

  function firstEntityOfType(type) {
    return scopedEntities().find((entity) => String(entity.entity_type || "").toLowerCase() === type)?.entity_id || "";
  }

  function automationField(labelText, value, onChange, { excludeCurrent = false } = {}) {
    const label = fieldWrap(labelText);
    const select = document.createElement("select");
    const current = selectedAutomation();
    const candidates = state.automations.filter((item) => !excludeCurrent || item.id !== current?.id);
    if (!candidates.length) {
      select.append(new Option("No other automation available", ""));
      select.disabled = true;
    } else {
      if (value && !candidates.some((item) => item.id === value)) {
        select.append(new Option(`${value} (not currently found)`, value));
      }
      candidates.forEach((item) => select.append(new Option(item.name || item.id, item.id)));
    }
    select.value = value || select.options[0]?.value || "";
    select.addEventListener("change", () => { onChange(select.value); markDirty(); });
    label.append(select);
    return label;
  }

  function typedEntityField(labelText, value, entityType, onChange) {
    const label = fieldWrap(labelText);
    const select = document.createElement("select");
    const candidates = scopedEntities().filter(
      (entity) => String(entity.entity_type || "").toLowerCase() === entityType,
    );
    if (!candidates.length) {
      select.append(new Option(`No selected ${entityType} entities`, value || ""));
      select.disabled = true;
    } else {
      if (value && !candidates.some((entity) => entity.entity_id === value)) {
        select.append(new Option(`${value} (not currently found)`, value));
      }
      candidates.forEach((entity) => {
        select.append(new Option(`${displayName(entity)} · ${entity.entity_id}`, entity.entity_id));
      });
    }
    select.value = value || select.options[0]?.value || "";
    select.addEventListener("change", () => { onChange(select.value); markDirty(); });
    label.append(select);
    return label;
  }

  makeStep = function makeExtendedStep(type) {
    if (!newTypes.has(type)) return originalMakeStep(type);
    let step;
    switch (type) {
      case "set_variable":
        step = { type, name: "value", source: "literal", value: "", source_variable: "", entity_id: "", attribute: "state" };
        break;
      case "template":
        step = { type, name: "result", template: "{{ value }}", output_type: "auto", transform: "none" };
        break;
      case "choose":
        step = { type, expression: "{{ value }}", cases: [{ name: "Case 1", operator: "eq", value: "", steps: [] }], default_steps: [] };
        break;
      case "wait_event":
        step = { type, event: "entity_change", filters: {}, timeout_ms: 30000, store_as: "event", on_timeout: "fail" };
        break;
      case "run_automation": {
        const current = selectedAutomation();
        const target = state.automations.find((item) => item.id !== current?.id);
        step = { type, automation_id: target?.id || "", wait: true, propagate_failure: true, pass_variables: false };
        break;
      }
      case "stop_automation":
        step = { type, target: "current", automation_id: "", require_running: false };
        break;
      case "command_sequence":
        step = {
          type,
          mode: "commands",
          commands: [{ entity_id: firstEntityId({ commandable: true }), cmd_id: "", params: {}, delay_ms: 0 }],
          macro_id: firstEntityOfType("macro"),
        };
        break;
      case "activity":
        step = { type, activity_id: firstEntityOfType("activity"), action: "on" };
        break;
      default:
        return originalMakeStep(type);
    }
    return withExecutionPolicy({ ...step, _ui_id: createId() });
  };

  stepLabel = function extendedStepLabel(type) {
    return {
      set_variable: "Set variable",
      template: "Template / Transform value",
      choose: "Choose / Switch",
      wait_event: "Wait for event",
      run_automation: "Run automation",
      stop_automation: "Stop automation",
      command_sequence: "Command sequence / Macro",
      activity: "Activity control",
    }[type] || originalStepLabel(type);
  };

  function helpNote(text) {
    const note = document.createElement("div");
    note.className = "read-only-note wide";
    note.textContent = text;
    return note;
  }

  function chooseCaseEditor(step, item, index) {
    const section = document.createElement("section");
    section.className = "branch";
    const head = document.createElement("div");
    head.className = "branch-head";
    const name = document.createElement("input");
    name.value = item.name || `Case ${index + 1}`;
    name.placeholder = `Case ${index + 1}`;
    name.addEventListener("input", () => { item.name = name.value; markDirty(); });

    const actions = document.createElement("div");
    const add = document.createElement("button");
    add.type = "button";
    add.className = "button ghost small button-with-icon";
    setButtonContent(add, "add", "Add step");
    add.addEventListener("click", () => openStepPicker(item.steps || (item.steps = [])));
    const remove = toolButton("delete", "Delete case", () => {
      if (step.cases.length <= 1) return;
      step.cases.splice(index, 1);
      markDirty();
      renderEditor();
    });
    remove.disabled = step.cases.length <= 1;
    actions.append(add, remove);
    head.append(name, actions);

    const config = document.createElement("div");
    config.className = "step-grid";
    config.append(selectField("Operator", item.operator || "eq", [
      { value: "eq", label: "Equals" },
      { value: "ne", label: "Does not equal" },
      { value: "gt", label: "Greater than" },
      { value: "gte", label: "Greater than or equal to" },
      { value: "lt", label: "Less than" },
      { value: "lte", label: "Less than or equal to" },
      { value: "contains", label: "Contains" },
      { value: "not_contains", label: "Does not contain" },
      { value: "in", label: "Is in" },
      { value: "not_in", label: "Is not in" },
      { value: "truthy", label: "Is truthy" },
      { value: "falsy", label: "Is falsy" },
    ], (value) => { item.operator = value; renderEditor(); }));
    if (!["truthy", "falsy"].includes(item.operator || "eq")) {
      config.append(valueField("Compare with", item.value, (value) => { item.value = value; }, "movie, 20, true…"));
    }

    const children = document.createElement("div");
    children.className = "steps";
    renderSteps(children, item.steps || (item.steps = []), "choose");
    section.append(head, config, children);
    return section;
  }

  function renderCommandSequence(step) {
    const wrapper = document.createElement("div");
    wrapper.className = "wide";
    const modeGrid = document.createElement("div");
    modeGrid.className = "step-grid";
    modeGrid.append(selectField("Mode", step.mode || "commands", [
      { value: "commands", label: "Command sequence" },
      { value: "macro", label: "Remote macro" },
    ], (value) => { step.mode = value; renderEditor(); }));
    wrapper.append(modeGrid);

    if ((step.mode || "commands") === "macro") {
      wrapper.append(typedEntityField("Macro", step.macro_id || "", "macro", (value) => { step.macro_id = value; }));
      return wrapper;
    }

    step.commands ||= [];
    const list = document.createElement("div");
    list.className = "parallel-branches";
    step.commands.forEach((command, index) => {
      command.params ||= {};
      command.delay_ms ??= 0;
      const section = document.createElement("section");
      section.className = "branch";
      const head = document.createElement("div");
      head.className = "branch-head";
      const title = document.createElement("strong");
      title.textContent = `Command ${index + 1}`;
      const remove = toolButton("delete", "Delete command", () => {
        if (step.commands.length <= 1) return;
        step.commands.splice(index, 1);
        markDirty();
        renderEditor();
      });
      remove.disabled = step.commands.length <= 1;
      head.append(title, remove);
      const editor = renderCommandStep(command);
      const delay = numberField("Delay after command (ms)", command.delay_ms || 0, (value) => { command.delay_ms = value; }, 0, 86400000);
      section.append(head, editor, delay);
      list.append(section);
    });
    const add = document.createElement("button");
    add.type = "button";
    add.className = "button ghost small button-with-icon";
    setButtonContent(add, "add", "Add command");
    add.addEventListener("click", () => {
      step.commands.push({ entity_id: firstEntityId({ commandable: true }), cmd_id: "", params: {}, delay_ms: 0 });
      markDirty();
      renderEditor();
    });
    list.append(add);
    wrapper.append(list);
    return wrapper;
  }

  renderStepBody = function renderExtendedStepBody(step) {
    if (!newTypes.has(step.type)) return originalRenderStepBody(step);
    const grid = document.createElement("div");
    grid.className = "step-grid";

    if (step.type === "set_variable") {
      step.source ||= "literal";
      grid.append(
        textField("Variable name", step.name || "", (value) => { step.name = value; }, "mode"),
        selectField("Value source", step.source, [
          { value: "literal", label: "Literal value" },
          { value: "variable", label: "Another run variable" },
          { value: "entity", label: "Entity attribute" },
        ], (value) => { step.source = value; renderEditor(); }),
      );
      if (step.source === "literal") {
        grid.append(valueField("Value", step.value, (value) => { step.value = value; }, "movie, 20, true…"));
      } else if (step.source === "variable") {
        grid.append(textField("Source variable", step.source_variable || "", (value) => { step.source_variable = value; }, "other_value"));
      } else {
        grid.append(entityField("Entity", step.entity_id || "", (value) => {
          step.entity_id = value;
          step.attribute = defaultAttribute(value);
          renderEditor();
        }));
        grid.append(attributeField("Attribute", step.entity_id, step.attribute || "state", (value) => { step.attribute = value; }));
      }
    } else if (step.type === "template") {
      grid.append(
        textField("Store result as", step.name || "", (value) => { step.name = value; }, "message"),
        textField("Template", step.template || "", (value) => { step.template = value; }, "{{ mode|upper }}"),
        selectField("Output type", step.output_type || "auto", [
          { value: "auto", label: "Automatic / preserve type" },
          { value: "string", label: "String" },
          { value: "number", label: "Number" },
          { value: "boolean", label: "Boolean" },
          { value: "json", label: "JSON" },
        ], (value) => { step.output_type = value; }),
        selectField("Transform result", step.transform || "none", [
          { value: "none", label: "None" },
          { value: "lower", label: "Lowercase" },
          { value: "upper", label: "Uppercase" },
          { value: "trim", label: "Trim whitespace" },
          { value: "length", label: "Length" },
          { value: "json", label: "Serialize as JSON" },
        ], (value) => { step.transform = value; }),
        helpNote("Templates support {{ variable }}, {{ vars.variable }}, {{ automation.name }}, {{ run.id }} and filters such as |upper, |lower, |trim, |json, |int, |float and |bool."),
      );
    } else if (step.type === "choose") {
      step.cases ||= [{ name: "Case 1", operator: "eq", value: "", steps: [] }];
      step.default_steps ||= [];
      grid.append(
        textField("Expression", step.expression || "", (value) => { step.expression = value; }, "{{ mode }}"),
        helpNote("Cases are evaluated from top to bottom. The first match runs; otherwise the Default branch runs."),
      );
      const cases = document.createElement("div");
      cases.className = "wide parallel-branches";
      step.cases.forEach((item, index) => cases.append(chooseCaseEditor(step, item, index)));
      const add = document.createElement("button");
      add.type = "button";
      add.className = "button ghost small button-with-icon";
      setButtonContent(add, "add", "Add case");
      add.addEventListener("click", () => {
        step.cases.push({ name: `Case ${step.cases.length + 1}`, operator: "eq", value: "", steps: [] });
        markDirty();
        renderEditor();
      });
      cases.append(add);
      grid.append(cases, branchEditor("Default", "default_steps", step.default_steps, step));
    } else if (step.type === "wait_event") {
      grid.append(
        textField("Core event name", step.event || "", (value) => { step.event = value; }, "entity_change"),
        jsonField("Payload filters (JSON)", step.filters || {}, (value) => { step.filters = value; }, true),
        numberField("Timeout (ms)", step.timeout_ms ?? 30000, (value) => { step.timeout_ms = value; }, 1, 86400000),
        textField("Store event payload as (optional)", step.store_as || "", (value) => { step.store_as = value; }, "event"),
        selectField("When timeout expires", step.on_timeout || "fail", [
          { value: "fail", label: "Fail automation" },
          { value: "continue", label: "Continue sequence" },
          { value: "stop", label: "Stop successfully" },
        ], (value) => { step.on_timeout = value; }),
        helpNote("Filters use dotted payload paths and all filters must match. Example: {\"entity_id\":\"hass.light.living_room\"}."),
      );
    } else if (step.type === "run_automation") {
      grid.append(
        automationField("Automation", step.automation_id || "", (value) => { step.automation_id = value; }, { excludeCurrent: true }),
        checkField("Wait for the automation to finish", step.wait !== false, (value) => { step.wait = value; renderEditor(); }),
        checkField("Propagate child failure/cancellation", step.propagate_failure !== false, (value) => { step.propagate_failure = value; }),
        checkField("Pass current run variables to child", Boolean(step.pass_variables), (value) => { step.pass_variables = value; }),
      );
    } else if (step.type === "stop_automation") {
      grid.append(selectField("Target", step.target || "current", [
        { value: "current", label: "Current automation" },
        { value: "automation", label: "Another automation" },
      ], (value) => { step.target = value; renderEditor(); }));
      if ((step.target || "current") === "automation") {
        grid.append(
          automationField("Automation", step.automation_id || "", (value) => { step.automation_id = value; }, { excludeCurrent: true }),
          checkField("Fail if target is not running", Boolean(step.require_running), (value) => { step.require_running = value; }),
        );
      }
    } else if (step.type === "command_sequence") {
      grid.append(renderCommandSequence(step));
    } else if (step.type === "activity") {
      grid.append(
        typedEntityField("Activity", step.activity_id || "", "activity", (value) => { step.activity_id = value; }),
        selectField("Action", step.action || "on", [
          { value: "on", label: "Start activity" },
          { value: "off", label: "Stop activity" },
          { value: "toggle", label: "Toggle based on current state" },
        ], (value) => { step.action = value; }),
      );
    }
    return grid;
  };

  function validVariableName(value) {
    return /^[A-Za-z_][A-Za-z0-9_]{0,63}$/.test(String(value || ""));
  }

  validateStepsDraft = function validateExtendedStepsDraft(steps, prefix, errors) {
    originalValidateStepsDraft(steps, prefix, errors);
    (steps || []).forEach((step, index) => {
      if (!newTypes.has(step.type)) return;
      const path = `${prefix}.${index}`;
      if (step.type === "set_variable") {
        if (!validVariableName(step.name)) errors.push({ field: `${path}.name`, msg: "Enter a valid variable name" });
        if (step.source === "variable" && !validVariableName(step.source_variable)) errors.push({ field: `${path}.source_variable`, msg: "Enter a valid source variable" });
        if (step.source === "entity") {
          if (!step.entity_id) errors.push({ field: `${path}.entity_id`, msg: "Select an entity" });
          if (!step.attribute) errors.push({ field: `${path}.attribute`, msg: "Select an attribute" });
        }
      } else if (step.type === "template") {
        if (!validVariableName(step.name)) errors.push({ field: `${path}.name`, msg: "Enter a valid output variable name" });
        if (!String(step.template || "").trim()) errors.push({ field: `${path}.template`, msg: "Template is required" });
      } else if (step.type === "choose") {
        if (!String(step.expression || "").trim()) errors.push({ field: `${path}.expression`, msg: "Expression is required" });
        if (!Array.isArray(step.cases) || !step.cases.length) errors.push({ field: `${path}.cases`, msg: "Add at least one case" });
        (step.cases || []).forEach((item, caseIndex) => {
          if (!String(item.name || "").trim()) errors.push({ field: `${path}.cases.${caseIndex}.name`, msg: "Case name is required" });
          validateStepsDraft(item.steps || [], `${path}.cases.${caseIndex}.steps`, errors);
        });
        validateStepsDraft(step.default_steps || [], `${path}.default_steps`, errors);
      } else if (step.type === "wait_event") {
        if (!String(step.event || "").trim()) errors.push({ field: `${path}.event`, msg: "Core event name is required" });
        if (step.store_as && !validVariableName(step.store_as)) errors.push({ field: `${path}.store_as`, msg: "Enter a valid variable name" });
      } else if (step.type === "run_automation") {
        if (!step.automation_id) errors.push({ field: `${path}.automation_id`, msg: "Select an automation" });
      } else if (step.type === "stop_automation") {
        if ((step.target || "current") === "automation" && !step.automation_id) errors.push({ field: `${path}.automation_id`, msg: "Select an automation" });
      } else if (step.type === "command_sequence") {
        if ((step.mode || "commands") === "macro") {
          if (!step.macro_id) errors.push({ field: `${path}.macro_id`, msg: "Select a macro" });
        } else {
          if (!Array.isArray(step.commands) || !step.commands.length) errors.push({ field: `${path}.commands`, msg: "Add at least one command" });
          (step.commands || []).forEach((command, commandIndex) => {
            if (!command.entity_id) errors.push({ field: `${path}.commands.${commandIndex}.entity_id`, msg: "Select an entity" });
            else if (isSensor(command.entity_id)) errors.push({ field: `${path}.commands.${commandIndex}.entity_id`, msg: "Sensors are read-only" });
            if (!command.cmd_id) errors.push({ field: `${path}.commands.${commandIndex}.cmd_id`, msg: "Select a command" });
          });
        }
      } else if (step.type === "activity" && !step.activity_id) {
        errors.push({ field: `${path}.activity_id`, msg: "Select an activity" });
      }
    });
  };

  function walkSequenceEntityReferences(automation, callback) {
    (automation?.triggers || []).forEach((trigger, index) => {
      if (["entity_state", "entity_duration", "numeric_threshold", "entity_change"].includes(trigger.type || "entity_state") && trigger.entity_id) {
        callback(trigger, "entity_id", `Trigger ${index + 1}`, false);
      }
    });
    const walk = (steps, prefix) => {
      (steps || []).forEach((step, index) => {
        const path = `${prefix} ${index + 1}`;
        if (step.type === "command" && step.entity_id) callback(step, "entity_id", `${path} · Entity command`, true);
        if (step.type === "set_variable" && step.source === "entity" && step.entity_id) callback(step, "entity_id", `${path} · Variable source`, false);
        if (step.type === "activity" && step.activity_id) callback(step, "activity_id", `${path} · Activity`, true);
        if (step.type === "command_sequence") {
          if ((step.mode || "commands") === "macro" && step.macro_id) callback(step, "macro_id", `${path} · Macro`, true);
          (step.commands || []).forEach((command, commandIndex) => {
            if (command.entity_id) callback(command, "entity_id", `${path} · Command ${commandIndex + 1}`, true);
          });
        }
        if (step.type === "condition" || step.type === "wait") {
          (step.conditions || []).forEach((condition, conditionIndex) => {
            if ((condition.kind || "entity") === "entity" && condition.entity_id) callback(condition, "entity_id", `${path} · Condition ${conditionIndex + 1}`, false);
          });
        }
        ["then", "else", "failure_steps", "match_steps", "timeout_steps"].forEach(
          (key) => walk(step[key] || [], `${path} · ${key.replaceAll("_", " ")}`),
        );
        if (step.type === "parallel") {
          (step.branches || []).forEach((branch, branchIndex) => walk(branch.steps || [], `${path} · ${branch.name || `Branch ${branchIndex + 1}`}`));
        }
        if (step.type === "choose") {
          (step.cases || []).forEach((item, caseIndex) => walk(item.steps || [], `${path} · ${item.name || `Case ${caseIndex + 1}`}`));
          walk(step.default_steps || [], `${path} · Default`);
        }
      });
    };
    walk(automation?.steps || [], "Step");
    walk(automation?.cancellation_steps || [], "Cancellation step");
    walk(automation?.rollback_steps || [], "Rollback step");
  }

  collectReferencedEntityIds = function collectExtendedReferencedEntityIds(automation) {
    const result = [];
    const seen = new Set();
    walkSequenceEntityReferences(automation, (object, key) => {
      const value = object[key];
      if (typeof value === "string" && value && !value.startsWith("$entity:") && !seen.has(value)) {
        seen.add(value);
        result.push(value);
      }
    });
    return result;
  };

  walkAutomationEntityReferences = function walkExtendedAutomationEntityReferences(automation, callback) {
    walkSequenceEntityReferences(automation, callback);
  };

  collectBlueprintPlaceholders = function collectExtendedBlueprintPlaceholders(automation) {
    const result = [];
    const seen = new Set();
    walkSequenceEntityReferences(automation, (object, key, path, commandable) => {
      const value = object[key];
      if (typeof value !== "string" || !value.startsWith("$entity:")) return;
      const slot = value.slice(8);
      if (seen.has(slot)) return;
      seen.add(slot);
      result.push({ slot, path, commandable });
    });
    return result;
  };

  const picker = $("stepPicker");
  [
    ["set_variable", "description", "Set variable", "Store a value for this run"],
    ["template", "code", "Template / Transform", "Build or transform a run value"],
    ["choose", "alt_route", "Choose / Switch", "Run the first matching case"],
    ["wait_event", "hourglass_empty", "Wait for event", "Wait for a Remote Core event"],
    ["run_automation", "play_arrow", "Run automation", "Start another automation"],
    ["stop_automation", "close", "Stop automation", "Stop this or another automation"],
    ["command_sequence", "account_tree", "Command sequence / Macro", "Run commands in order or a Remote macro"],
    ["activity", "devices", "Activity control", "Start, stop or toggle an activity"],
  ].forEach(([type, icon, title, description]) => {
    if (picker.querySelector(`[data-step-type="${type}"]`)) return;
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.stepType = type;
    button.innerHTML = `<span class="mi mi-${icon} step-choice-icon" aria-hidden="true"></span><strong>${title}</strong><span>${description}</span><span class="mi mi-arrow_forward step-choice-arrow" aria-hidden="true"></span>`;
    picker.append(button);
  });
}

installSequenceV2Editor();
