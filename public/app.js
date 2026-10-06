"use strict";

/* ---------------------------------------------------------------------- */
/* Logger / Debug panel (spec §20)                                        */
/* ---------------------------------------------------------------------- */

const SENSITIVE_KEY_RE = /token|secret|password|api[_-]?key|authorization/i;

function maskSensitive(value) {
  if (Array.isArray(value)) return value.map(maskSensitive);
  if (value && typeof value === "object") {
    const out = {};
    for (const [k, v] of Object.entries(value)) {
      out[k] = SENSITIVE_KEY_RE.test(k) ? "***redacted***" : maskSensitive(v);
    }
    return out;
  }
  return value;
}

const debugState = {
  active: false,
  buffer: [],
  maxEntries: 1000,
};

function formatTimestamp(date) {
  const p = (n, len = 2) => String(n).padStart(len, "0");
  return `${p(date.getHours())}:${p(date.getMinutes())}:${p(date.getSeconds())}.${p(date.getMilliseconds(), 3)}`;
}

function pushDebugEntry(entry) {
  entry.ts = entry.ts || Date.now();
  debugState.buffer.push(entry);
  if (debugState.buffer.length > debugState.maxEntries) debugState.buffer.shift();
  if (debugState.active) renderDebugEntry(entry);
}

function renderDebugEntry(entry) {
  const el = document.getElementById("debug-log");
  if (!el) return;
  const div = document.createElement("div");
  div.className = `debug-entry level-${entry.level}`;
  const dirSymbol = entry.direction === "out" ? "→" : entry.direction === "in" ? "←" : "•";
  const dirClass = entry.direction === "out" ? "dir-out" : "dir-in";
  const payload = entry.data !== undefined ? JSON.stringify(maskSensitive(entry.data)) : "";
  div.innerHTML =
    `<span class="ts">${formatTimestamp(new Date(entry.ts))}</span>` +
    `<span class="${dirClass}">${dirSymbol}</span> ` +
    `<strong>[${entry.scope}]</strong> ${escapeHtml(entry.msg)} ` +
    `<span>${escapeHtml(payload).slice(0, 2000)}</span>`;
  el.appendChild(div);
  el.scrollTop = el.scrollHeight;
}

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function makeLogger() {
  const base = (level) => (scope, msg, data) => {
    pushDebugEntry({ level, scope, msg, data, direction: data && data.__direction });
    const consoleFn = console[level] || console.log;
    if (debugState.active) consoleFn(`[${scope}] ${msg}`, data);
  };
  return {
    debug: base("debug"),
    info: base("info"),
    warn: base("warn"),
    error: base("error"),
  };
}

const log = makeLogger();

function setDebugActive(active) {
  debugState.active = active;
  const panel = document.getElementById("debug-panel");
  panel.hidden = !active;
  document.getElementById("debug-toggle-btn").classList.toggle("ghost", !active);
  if (active) {
    const el = document.getElementById("debug-log");
    el.innerHTML = "";
    debugState.buffer.forEach(renderDebugEntry);
  }
}

/* ---------------------------------------------------------------------- */
/* Application state                                                      */
/* ---------------------------------------------------------------------- */

const state = {
  connectionId: null,
  eventSource: null,
  agentInfo: null,
  capabilities: {},
  sessionId: null,
  sessions: [],
  availableCommands: [],
  configOptions: [],
  toolCalls: new Map(),
  lastBubble: null,
  promptState: "idle",
  permissionQueue: [],
  currentPermission: null,
};

/* ---------------------------------------------------------------------- */
/* HTTP helper                                                            */
/* ---------------------------------------------------------------------- */

async function fetchJSON(url, options) {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const direction = "out";
  log.debug("transport", `HTTP ${options && options.method ? options.method : "GET"} ${url}`, {
    __direction: direction,
    body: options && options.body ? JSON.parse(options.body) : undefined,
  });
  let body = null;
  const text = await res.text();
  if (text) {
    try { body = JSON.parse(text); } catch { body = text; }
  }
  if (!res.ok) {
    const message = (body && body.error) || `HTTP ${res.status}`;
    throw new Error(message);
  }
  return body;
}

/* ---------------------------------------------------------------------- */
/* Frontend ACP abstraction (spec §15)                                    */
/* ---------------------------------------------------------------------- */

const agent = {
  async initialize(agentId, cwd) {
    const result = await fetchJSON("/api/connect", {
      method: "POST",
      body: JSON.stringify({ agentId, cwd }),
    });
    state.connectionId = result.connectionId;
    state.agentInfo = result.agent;
    state.capabilities = result.agent.capabilities || {};
    connectEvents(result.connectionId);
    return result;
  },

  async newSession() {
    state.availableCommands = [];
    renderCommands();
    const result = await fetchJSON("/api/session/new", {
      method: "POST",
      body: JSON.stringify({ connectionId: state.connectionId }),
    });
    applySessionResult(result);
    return result;
  },

  async resumeSession(sessionId) {
    state.availableCommands = [];
    renderCommands();
    const result = await fetchJSON("/api/session/resume", {
      method: "POST",
      body: JSON.stringify({ connectionId: state.connectionId, sessionId }),
    });
    applySessionResult(result);
    return result;
  },

  async closeSession() {
    if (!state.sessionId) return;
    await fetchJSON("/api/session/close", {
      method: "POST",
      body: JSON.stringify({ connectionId: state.connectionId, sessionId: state.sessionId }),
    });
    resetSessionState();
  },

  async prompt(text) {
    if (!state.sessionId) return;
    setPromptState("running");
    appendUserMessage(text);
    try {
      await fetchJSON("/api/prompt", {
        method: "POST",
        body: JSON.stringify({ connectionId: state.connectionId, sessionId: state.sessionId, text }),
      });
    } catch (err) {
      setPromptState("failed");
      toast(err.message, "error");
    }
  },

  async cancel() {
    if (!state.sessionId) return;
    setPromptState("cancelling");
    await fetchJSON("/api/cancel", {
      method: "POST",
      body: JSON.stringify({ connectionId: state.connectionId, sessionId: state.sessionId }),
    });
  },

  async respondToPermission(requestId, optionId, outcome) {
    await fetchJSON("/api/permission/respond", {
      method: "POST",
      body: JSON.stringify({ connectionId: state.connectionId, requestId, optionId, outcome }),
    });
  },

  async setConfigOption(id, value, type) {
    return fetchJSON("/api/config/set", {
      method: "POST",
      body: JSON.stringify({ connectionId: state.connectionId, sessionId: state.sessionId, id, value, type }),
    });
  },

  async executeCommand(command, args) {
    appendUserMessage(`/${command}${args ? " " + args : ""}`);
    setPromptState("running");
    try {
      await fetchJSON("/api/command", {
        method: "POST",
        body: JSON.stringify({ connectionId: state.connectionId, sessionId: state.sessionId, command, args }),
      });
    } catch (err) {
      setPromptState("failed");
      toast(err.message, "error");
    }
  },

  async listSessions() {
    const result = await fetchJSON(`/api/sessions?connectionId=${encodeURIComponent(state.connectionId)}`);
    state.sessions = result.sessions || [];
    renderSessionList();
    return state.sessions;
  },
};

function applySessionResult(result) {
  state.sessionId = result.sessionId;
  if (Array.isArray(result.availableCommands) && result.availableCommands.length) {
    state.availableCommands = result.availableCommands;
  }
  state.configOptions = normalizeConfigOptions(result.configOptions, result.modes);
  state.toolCalls.clear();
  state.lastBubble = null;
  document.getElementById("conversation").innerHTML = "";
  document.getElementById("tool-calls-list").innerHTML = "";
  renderCommands();
  renderConfig();
  renderPlan(null);
  document.getElementById("session-id-label").textContent = `Session: ${shortId(result.sessionId)}`;
  document.getElementById("close-session-btn").disabled = false;
  setComposerEnabled(true);
}

function resetSessionState() {
  state.sessionId = null;
  state.availableCommands = [];
  state.configOptions = [];
  state.toolCalls.clear();
  state.lastBubble = null;
  document.getElementById("session-id-label").textContent = "";
  document.getElementById("close-session-btn").disabled = true;
  setComposerEnabled(false);
  renderCommands();
  renderConfig();
  renderPlan(null);
}

function shortId(id) {
  return id && id.length > 10 ? id.slice(0, 8) + "…" : id;
}

/* ---------------------------------------------------------------------- */
/* SSE dispatcher (spec §8)                                                */
/* ---------------------------------------------------------------------- */

function connectEvents(connectionId) {
  if (state.eventSource) state.eventSource.close();
  const es = new EventSource(`/api/events?connectionId=${encodeURIComponent(connectionId)}`);
  state.eventSource = es;

  es.addEventListener("session_update", (ev) => {
    const data = JSON.parse(ev.data);
    log.debug("session", `update: ${data.update && data.update.sessionUpdate}`, { __direction: "in", ...data });
    handleACPUpdate(data.update);
  });

  es.addEventListener("permission_request", (ev) => {
    const data = JSON.parse(ev.data);
    log.debug("permission", "request_permission", { __direction: "in", ...data });
    state.permissionQueue.push(data);
    maybeShowNextPermission();
  });

  es.addEventListener("status", (ev) => {
    const data = JSON.parse(ev.data);
    log.debug("session", `status: ${data.state}`, { __direction: "in", ...data });
    setPromptState(data.state, data.stopReason);
  });

  es.addEventListener("debug", (ev) => {
    const data = JSON.parse(ev.data);
    pushDebugEntry({
      level: data.level || "debug",
      scope: data.scope || "transport",
      msg: `${data.method || ""}`.trim(),
      data: data.payload,
      direction: data.direction,
      ts: data.ts,
    });
  });

  es.addEventListener("permission_cancelled", (ev) => {
    const data = JSON.parse(ev.data);
    if (state.currentPermission && state.currentPermission.requestId === data.requestId) {
      document.getElementById("permission-modal").hidden = true;
      state.currentPermission = null;
      maybeShowNextPermission();
    } else {
      state.permissionQueue = state.permissionQueue.filter((p) => p.requestId !== data.requestId);
    }
  });

  es.addEventListener("error_event", (ev) => {
    const data = JSON.parse(ev.data);
    log.error("transport", data.message, data);
    toast(data.message, "error");
  });

  es.onerror = () => {
    log.warn("transport", "SSE connection error", {});
  };
}

function handleACPUpdate(update) {
  if (!update || typeof update !== "object") {
    handleUnknownUpdate(update);
    return;
  }
  switch (update.sessionUpdate) {
    case "user_message_chunk":
      renderMessageChunk("user", update.content);
      break;
    case "agent_message_chunk":
      renderMessageChunk("agent", update.content);
      break;
    case "agent_thought_chunk":
      renderMessageChunk("thought", update.content);
      break;
    case "tool_call":
      renderToolCall(update);
      break;
    case "tool_call_update":
      updateToolCall(update);
      break;
    case "plan":
      renderPlan(update);
      break;
    case "available_commands_update":
      state.availableCommands = update.availableCommands || [];
      renderCommands();
      break;
    case "config_option_update":
      state.configOptions = normalizeConfigOptions(update.configOptions, null);
      renderConfig();
      break;
    case "current_mode_update":
      applyModeUpdate(update.currentModeId);
      renderConfig();
      break;
    case "usage_update":
      renderUsage(update);
      break;
    default:
      handleUnknownUpdate(update);
  }
}

function handleUnknownUpdate(update) {
  log.warn("unknown", `unknown update type: ${update && update.sessionUpdate}`, update);
  renderStatus(`[extension inconnue] ${update && update.sessionUpdate ? update.sessionUpdate : JSON.stringify(update)}`);
}

/* ---------------------------------------------------------------------- */
/* Rendering — conversation                                                */
/* ---------------------------------------------------------------------- */

function contentBlockToText(block) {
  if (block == null) return "";
  if (typeof block === "string") return block;
  if (block.type === "text" && typeof block.text === "string") return block.text;
  if (typeof block.text === "string") return block.text;
  if (block.type === "image") return "[image]";
  if (block.type === "audio") return "[audio]";
  if (block.type === "resource_link") return `[resource: ${block.uri || block.title || ""}]`;
  if (block.type === "resource") return `[resource]`;
  return JSON.stringify(block);
}

function renderMessageChunk(role, content) {
  const text = contentBlockToText(content);
  const conv = document.getElementById("conversation");
  if (state.lastBubble && state.lastBubble.role === role) {
    state.lastBubble.text += text;
    state.lastBubble.el.textContent = state.lastBubble.text;
  } else {
    const el = document.createElement("div");
    el.className = `msg ${role === "thought" ? "thought" : role === "user" ? "user" : "agent"}`;
    el.textContent = text;
    conv.appendChild(el);
    state.lastBubble = { role, el, text, kind: "message" };
  }
  conv.scrollTop = conv.scrollHeight;
}

function appendUserMessage(text) {
  const conv = document.getElementById("conversation");
  const el = document.createElement("div");
  el.className = "msg user";
  el.textContent = text;
  conv.appendChild(el);
  conv.scrollTop = conv.scrollHeight;
  state.lastBubble = null;
}

function renderStatus(text) {
  const conv = document.getElementById("conversation");
  const el = document.createElement("div");
  el.className = "msg status";
  el.textContent = text;
  conv.appendChild(el);
  conv.scrollTop = conv.scrollHeight;
  state.lastBubble = null;
}

/* ---------------------------------------------------------------------- */
/* Rendering — tool calls (spec §12)                                       */
/* ---------------------------------------------------------------------- */

function toolCallSummary(tc) {
  const parts = [];
  if (tc.content && tc.content.length) {
    for (const c of tc.content) {
      if (c.type === "content") parts.push(contentBlockToText(c.content));
      else if (c.type === "diff") parts.push(`--- ${c.path}\n${diffPreview(c.oldText, c.newText)}`);
      else if (c.type === "terminal") parts.push(`[terminal ${c.terminalId}]`);
      else if (c.text) parts.push(c.text);
      else parts.push(JSON.stringify(c));
    }
  }
  if (tc.locations && tc.locations.length) {
    parts.push(tc.locations.map((l) => `${l.path}${l.line ? ":" + l.line : ""}`).join(", "));
  }
  if (tc.rawOutput) parts.push(typeof tc.rawOutput === "string" ? tc.rawOutput : JSON.stringify(tc.rawOutput));
  return parts.join("\n");
}

function diffPreview(oldText, newText) {
  const before = oldText == null ? "(nouveau fichier)" : oldText;
  return `- ${String(before).slice(0, 400)}\n+ ${String(newText || "").slice(0, 400)}`;
}

function renderToolCall(update) {
  const id = update.toolCallId;
  const conv = document.getElementById("conversation");
  const sideList = document.getElementById("tool-calls-list");

  const inlineEl = document.createElement("div");
  inlineEl.className = "tool-call";
  conv.appendChild(inlineEl);

  const sideEl = document.createElement("li");
  sideEl.className = "tool-call";
  sideList.appendChild(sideEl);

  const record = {
    id,
    title: update.title || id,
    kind: update.kind || "other",
    status: update.status || "pending",
    content: update.content || [],
    rawOutput: update.rawOutput,
    inlineEl,
    sideEl,
  };
  state.toolCalls.set(id, record);
  paintToolCall(record);
  state.lastBubble = null;
  conv.scrollTop = conv.scrollHeight;
}

function updateToolCall(update) {
  const record = state.toolCalls.get(update.toolCallId);
  if (!record) {
    log.warn("tool_call", `tool_call_update for unknown id ${update.toolCallId}`, update);
    renderToolCall({ ...update, title: update.title || update.toolCallId });
    return;
  }
  if (update.title !== undefined) record.title = update.title;
  if (update.status !== undefined) record.status = update.status;
  if (update.content !== undefined) record.content = update.content;
  if (update.rawOutput !== undefined) record.rawOutput = update.rawOutput;
  paintToolCall(record);
}

function paintToolCall(record) {
  const icon = "🔧";
  const summary = toolCallSummary(record);
  record.inlineEl.innerHTML =
    `<div class="tc-title"><span>${icon} ${escapeHtml(record.title)}</span>` +
    `<span class="tc-status ${record.status}">${record.status}</span></div>` +
    (summary ? `<pre>${escapeHtml(summary).slice(0, 4000)}</pre>` : "");
  record.sideEl.innerHTML =
    `<div class="tc-title"><span>${icon} ${escapeHtml(record.title)}</span>` +
    `<span class="tc-status ${record.status}">${record.status}</span></div>`;
}

/* ---------------------------------------------------------------------- */
/* Rendering — plan (spec §6 M6 / agent-plan)                               */
/* ---------------------------------------------------------------------- */

function renderPlan(update) {
  const heading = document.getElementById("plan-heading");
  const list = document.getElementById("plan-list");
  const entries = update && update.entries;
  if (!entries || !entries.length) {
    heading.hidden = true;
    list.hidden = true;
    list.innerHTML = "";
    return;
  }
  heading.hidden = false;
  list.hidden = false;
  list.innerHTML = "";
  for (const entry of entries) {
    const li = document.createElement("li");
    li.className = `status-${entry.status || "pending"}`;
    li.textContent = entry.content || JSON.stringify(entry);
    list.appendChild(li);
  }
}

/* ---------------------------------------------------------------------- */
/* Rendering — commands (spec §10)                                         */
/* ---------------------------------------------------------------------- */

function renderCommands() {
  const heading = document.getElementById("commands-heading");
  const list = document.getElementById("commands-list");
  list.innerHTML = "";
  if (!state.availableCommands.length) {
    heading.hidden = true;
    list.hidden = true;
    return;
  }
  heading.hidden = false;
  list.hidden = false;
  for (const cmd of state.availableCommands) {
    const li = document.createElement("li");
    li.textContent = `/${cmd.name}${cmd.description ? " — " + cmd.description : ""}`;
    li.addEventListener("click", () => agent.executeCommand(cmd.name));
    list.appendChild(li);
  }
}

function filteredCommands(prefix) {
  return state.availableCommands.filter((c) => c.name.toLowerCase().startsWith(prefix.toLowerCase()));
}

/* ---------------------------------------------------------------------- */
/* Rendering — configuration (spec §11)                                    */
/* ---------------------------------------------------------------------- */

const MODE_CONFIG_ID = "__mode";

function normalizeConfigOptions(configOptions, modes) {
  if (Array.isArray(configOptions) && configOptions.length) return configOptions;
  if (modes && Array.isArray(modes.availableModes) && modes.availableModes.length) {
    return [{
      id: MODE_CONFIG_ID,
      name: "Mode",
      type: "select",
      currentValue: modes.currentModeId,
      options: modes.availableModes.map((m) => ({ value: m.id, name: m.name || m.id, description: m.description })),
    }];
  }
  return [];
}

function applyModeUpdate(currentModeId) {
  const modeOpt = state.configOptions.find((o) => o.id === MODE_CONFIG_ID);
  if (modeOpt) modeOpt.currentValue = currentModeId;
}

// Modèles/options qu'on ne veut jamais voir dans l'UI (préférence locale, purement cosmétique).
// Ajouter un pattern par ligne ; testé contre `value` et `name` de chaque choix.
const CONFIG_OPTION_EXCLUDE_PATTERNS = [
  /^kilo\//i, // modèles routés via Kilo Gateway (facturation Kilo)
  /kilo gateway/i,
];

function isChoiceExcluded(choice) {
  const haystack = `${choice.value ?? ""} ${choice.name ?? ""}`;
  return CONFIG_OPTION_EXCLUDE_PATTERNS.some((re) => re.test(haystack));
}

function flattenConfigChoices(options) {
  const flat = [];
  for (const opt of options || []) {
    if (opt.group) {
      const filtered = (opt.options || []).filter((c) => !isChoiceExcluded(c));
      if (filtered.length) flat.push({ group: opt.group, options: filtered });
    } else if (!isChoiceExcluded(opt)) {
      flat.push({ group: null, options: [opt] });
    }
  }
  return flat;
}

function renderConfig() {
  const heading = document.getElementById("config-heading");
  const panel = document.getElementById("config-panel");
  panel.innerHTML = "";
  if (!state.configOptions.length) {
    heading.hidden = true;
    panel.hidden = true;
    return;
  }
  heading.hidden = false;
  panel.hidden = false;
  for (const opt of state.configOptions) {
    const wrap = document.createElement("div");
    wrap.className = "config-option";
    const label = document.createElement("label");
    label.textContent = opt.name || opt.id;
    if (opt.description) label.title = opt.description;
    wrap.appendChild(label);

    let input;
    if (opt.type === "boolean") {
      input = document.createElement("input");
      input.type = "checkbox";
      input.checked = !!opt.currentValue;
    } else if (opt.type === "select" || Array.isArray(opt.options)) {
      input = document.createElement("select");
      for (const group of flattenConfigChoices(opt.options)) {
        const container = group.group ? document.createElement("optgroup") : input;
        if (group.group) {
          container.label = group.group;
          input.appendChild(container);
        }
        for (const choice of group.options) {
          const o = document.createElement("option");
          o.value = choice.value;
          o.textContent = choice.name || choice.value;
          container.appendChild(o);
        }
      }
      input.value = opt.currentValue;
    } else {
      log.warn("config", `unknown config option type: ${opt.type}`, opt);
      continue;
    }
    input.addEventListener("change", () => {
      const value = input.type === "checkbox" ? input.checked : input.value;
      agent.setConfigOption(opt.id, value, opt.type)
        .then((result) => {
          if (result && Array.isArray(result.configOptions)) {
            state.configOptions = result.configOptions;
            renderConfig();
          }
        })
        .catch((err) => toast(err.message, "error"));
    });
    wrap.appendChild(input);
    panel.appendChild(wrap);
  }
}

function renderUsage(update) {
  const el = document.getElementById("usage-indicator");
  if (!update || typeof update.used !== "number") {
    el.hidden = true;
    return;
  }
  el.hidden = false;
  el.textContent = `${update.used}${update.size ? "/" + update.size : ""} tokens`;
}

/* ---------------------------------------------------------------------- */
/* Rendering — capabilities                                                */
/* ---------------------------------------------------------------------- */

function renderCapabilities() {
  const list = document.getElementById("capabilities-list");
  list.innerHTML = "";
  const caps = state.capabilities || {};
  const keys = Object.keys(caps);
  if (!keys.length) {
    const li = document.createElement("li");
    li.textContent = "(aucune capacité annoncée)";
    list.appendChild(li);
    return;
  }
  for (const key of keys) {
    const li = document.createElement("li");
    const truthy = isCapabilityOn(caps[key]);
    li.className = truthy ? "on" : "off";
    li.textContent = key;
    list.appendChild(li);
  }
}

function isCapabilityOn(value) {
  return value !== undefined && value !== null && value !== false;
}

/* ---------------------------------------------------------------------- */
/* Permissions (spec §9)                                                   */
/* ---------------------------------------------------------------------- */

function maybeShowNextPermission() {
  if (state.currentPermission || !state.permissionQueue.length) return;
  const req = state.permissionQueue.shift();
  state.currentPermission = req;
  const modal = document.getElementById("permission-modal");
  const body = document.getElementById("permission-body");
  const options = document.getElementById("permission-options");
  const known = req.toolCall && state.toolCalls.get(req.toolCall.toolCallId);
  const merged = known ? { ...known, ...req.toolCall } : req.toolCall;
  const title = (merged && merged.title) || "Permission required";
  document.getElementById("permission-title").textContent = title;
  const detail = merged ? toolCallSummary(merged) : "";
  body.innerHTML = detail ? `<pre>${escapeHtml(detail)}</pre>` : "";
  options.innerHTML = "";
  for (const opt of req.options || []) {
    const btn = document.createElement("button");
    btn.textContent = opt.name || opt.optionId;
    btn.className = `kind-${opt.kind || "default"}`;
    btn.addEventListener("click", async () => {
      modal.hidden = true;
      await agent.respondToPermission(req.requestId, opt.optionId);
      state.currentPermission = null;
      maybeShowNextPermission();
    });
    options.appendChild(btn);
  }
  modal.hidden = false;
}

/* ---------------------------------------------------------------------- */
/* Prompt state machine (spec §13)                                         */
/* ---------------------------------------------------------------------- */

function setPromptState(nextState, stopReason) {
  state.promptState = nextState;
  const sendBtn = document.getElementById("send-btn");
  const stopBtn = document.getElementById("stop-btn");
  const running = nextState === "running" || nextState === "cancelling";
  sendBtn.disabled = running || !state.sessionId;
  stopBtn.disabled = !running || nextState === "cancelling";
  if (["completed", "failed", "cancelled"].includes(nextState)) {
    renderStatus(`[${nextState}]${stopReason ? " " + stopReason : ""}`);
  }
  if (nextState === "cancelled") {
    for (const record of state.toolCalls.values()) {
      if (record.status === "pending" || record.status === "in_progress") {
        record.status = "cancelled";
        paintToolCall(record);
      }
    }
  }
}

function setComposerEnabled(enabled) {
  document.getElementById("prompt-input").disabled = !enabled;
  document.getElementById("send-btn").disabled = !enabled;
  setPromptState("idle");
}

/* ---------------------------------------------------------------------- */
/* Toasts                                                                   */
/* ---------------------------------------------------------------------- */

function toast(message, level = "info") {
  const container = document.getElementById("toast-container");
  const el = document.createElement("div");
  el.className = `toast ${level}`;
  el.textContent = message;
  container.appendChild(el);
  setTimeout(() => el.remove(), 6000);
}

/* ---------------------------------------------------------------------- */
/* Command palette (spec §10)                                              */
/* ---------------------------------------------------------------------- */

let paletteIndex = -1;

function updateCommandPalette() {
  const input = document.getElementById("prompt-input");
  const palette = document.getElementById("command-palette");
  const value = input.value;
  if (!value.startsWith("/") || value.includes("\n") || value.includes(" ")) {
    palette.hidden = true;
    return;
  }
  const matches = filteredCommands(value.slice(1));
  if (!matches.length) {
    palette.hidden = true;
    return;
  }
  palette.innerHTML = "";
  matches.forEach((cmd, i) => {
    const div = document.createElement("div");
    div.className = `cmd-item${i === paletteIndex ? " active" : ""}`;
    div.innerHTML = `<div class="cmd-name">/${escapeHtml(cmd.name)}</div>` +
      (cmd.description ? `<div class="cmd-desc">${escapeHtml(cmd.description)}</div>` : "");
    div.addEventListener("click", () => {
      palette.hidden = true;
      input.value = "";
      agent.executeCommand(cmd.name);
    });
    palette.appendChild(div);
  });
  palette.hidden = false;
}

/* ---------------------------------------------------------------------- */
/* Session list rendering (spec §6 / §5 M5)                                 */
/* ---------------------------------------------------------------------- */

function renderSessionList() {
  const select = document.getElementById("resume-session-select");
  select.innerHTML = '<option value="">Resume session…</option>';
  for (const s of state.sessions) {
    const o = document.createElement("option");
    o.value = s.sessionId;
    o.textContent = shortId(s.sessionId);
    select.appendChild(o);
  }
  const list = document.getElementById("session-list");
  list.innerHTML = "";
  for (const s of state.sessions) {
    const li = document.createElement("li");
    li.textContent = shortId(s.sessionId);
    list.appendChild(li);
  }
}

/* ---------------------------------------------------------------------- */
/* Wiring                                                                   */
/* ---------------------------------------------------------------------- */

async function loadAgentList() {
  const result = await fetchJSON("/api/agents");
  const select = document.getElementById("agent-select");
  select.innerHTML = "";
  for (const a of result.agents) {
    const o = document.createElement("option");
    o.value = a.id;
    o.textContent = a.label;
    select.appendChild(o);
  }
}

function initUI() {
  const params = new URLSearchParams(location.search);
  if (params.get("debug") === "1") setDebugActive(true);

  document.getElementById("debug-toggle-btn").addEventListener("click", () => setDebugActive(!debugState.active));
  document.getElementById("close-debug-btn").addEventListener("click", () => setDebugActive(false));
  document.getElementById("clear-logs-btn").addEventListener("click", () => {
    debugState.buffer = [];
    document.getElementById("debug-log").innerHTML = "";
  });
  document.getElementById("copy-logs-btn").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(maskSensitive(debugState.buffer), null, 2));
      toast("Logs copiés", "info");
    } catch {
      toast("Impossible de copier les logs", "error");
    }
  });

  document.getElementById("connect-btn").addEventListener("click", async () => {
    const agentId = document.getElementById("agent-select").value;
    const cwd = document.getElementById("cwd-input").value || ".";
    try {
      const result = await agent.initialize(agentId, cwd);
      document.getElementById("connection-form").hidden = true;
      document.getElementById("connection-status").hidden = false;
      document.getElementById("agent-name").textContent = result.agent.title || result.agent.name || agentId;
      document.getElementById("agent-version").textContent = result.agent.version || "";
      document.getElementById("protocol-version").textContent = `ACP v${result.agent.protocolVersion}`;
      document.getElementById("app").hidden = false;
      renderCapabilities();
      agent.listSessions().catch(() => {});
    } catch (err) {
      toast(err.message, "error");
    }
  });

  document.getElementById("disconnect-btn").addEventListener("click", async () => {
    try { await fetchJSON("/api/disconnect", { method: "POST", body: JSON.stringify({ connectionId: state.connectionId }) }); } catch {}
    if (state.eventSource) state.eventSource.close();
    location.reload();
  });

  document.getElementById("new-session-btn").addEventListener("click", async () => {
    try { await agent.newSession(); await agent.listSessions(); } catch (err) { toast(err.message, "error"); }
  });

  document.getElementById("resume-session-select").addEventListener("change", (e) => {
    document.getElementById("resume-session-btn").disabled = !e.target.value;
  });

  document.getElementById("resume-session-btn").addEventListener("click", async () => {
    const sessionId = document.getElementById("resume-session-select").value;
    if (!sessionId) return;
    try { await agent.resumeSession(sessionId); } catch (err) { toast(err.message, "error"); }
  });

  document.getElementById("close-session-btn").addEventListener("click", async () => {
    try { await agent.closeSession(); } catch (err) { toast(err.message, "error"); }
  });

  const promptInput = document.getElementById("prompt-input");
  promptInput.addEventListener("input", () => { paletteIndex = -1; updateCommandPalette(); });
  promptInput.addEventListener("keydown", (e) => {
    const palette = document.getElementById("command-palette");
    if (!palette.hidden) {
      const items = palette.querySelectorAll(".cmd-item");
      if (e.key === "ArrowDown") { e.preventDefault(); paletteIndex = Math.min(paletteIndex + 1, items.length - 1); updateCommandPalette(); return; }
      if (e.key === "ArrowUp") { e.preventDefault(); paletteIndex = Math.max(paletteIndex - 1, 0); updateCommandPalette(); return; }
      if (e.key === "Escape") { palette.hidden = true; return; }
      if (e.key === "Enter" && paletteIndex >= 0) { e.preventDefault(); items[paletteIndex].click(); return; }
    }
    if (e.key === "Enter" && !e.shiftKey && palette.hidden) {
      e.preventDefault();
      document.getElementById("composer").requestSubmit();
    }
  });

  document.getElementById("composer").addEventListener("submit", (e) => {
    e.preventDefault();
    const text = promptInput.value.trim();
    if (!text) return;
    promptInput.value = "";
    document.getElementById("command-palette").hidden = true;
    agent.prompt(text);
  });

  document.getElementById("stop-btn").addEventListener("click", () => agent.cancel());

  loadAgentList().catch((err) => toast(err.message, "error"));
}

document.addEventListener("DOMContentLoaded", initUI);
