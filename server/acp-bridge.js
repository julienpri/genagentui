"use strict";

const fs = require("fs");
const path = require("path");
const { randomUUID } = require("crypto");

const { ACPClient } = require("../acp/client");
const { ACP_EVENTS } = require("../acp/events");
const protocol = require("../acp/protocol");

const agentsConfig = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "agents.json"), "utf8"));

const KNOWN_SESSION_UPDATE_KINDS = new Set(Object.values(protocol.SESSION_UPDATE_KINDS));

class Connection {
  constructor(id, agentConfig, cwd) {
    this.id = id;
    this.agentConfig = agentConfig;
    this.cwd = cwd;
    this.acpClient = null;
    this.agentInfo = null;
    this.sessions = new Map();
    this.activeSessionId = null;
    this.activePromptSessionIds = new Set();
    this.pendingPermissions = new Map();
    this.sseClients = new Set();
  }

  broadcast(event, data) {
    const payload = `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
    for (const res of this.sseClients) {
      res.write(payload);
    }
  }

  debugFrame(direction, method, payload, level = "debug", scope = "transport") {
    this.broadcast("debug", { ts: Date.now(), direction, method, payload, level, scope });
  }
}

const connections = new Map();

function findAgentConfig(agentId) {
  const cfg = agentsConfig.agents.find((a) => a.id === agentId);
  if (!cfg) throw new Error(`Unknown agent id: ${agentId}`);
  return cfg;
}

function listAgents() {
  return agentsConfig.agents.map((a) => ({ id: a.id, label: a.label }));
}

function isPathWithin(root, target) {
  const rel = path.relative(root, target);
  return rel === "" || (!rel.startsWith("..") && !path.isAbsolute(rel));
}

async function handleFsReadTextFile(connection, msg) {
  const { path: filePath, line, limit } = msg.params || {};
  if (!filePath || !isPathWithin(connection.cwd, filePath)) {
    connection.acpClient.respondError(msg.id, protocol.JSONRPC_ERRORS.INVALID_PARAMS, "Path outside session cwd");
    return;
  }
  try {
    const raw = await fs.promises.readFile(filePath, "utf8");
    let content = raw;
    if (line || limit) {
      const lines = raw.split("\n");
      const start = Math.max((line || 1) - 1, 0);
      const end = limit ? start + limit : lines.length;
      content = lines.slice(start, end).join("\n");
    }
    connection.acpClient.respond(msg.id, { content });
  } catch (err) {
    connection.acpClient.respondError(msg.id, protocol.JSONRPC_ERRORS.INTERNAL_ERROR, err.message);
  }
}

async function handleFsWriteTextFile(connection, msg) {
  const { path: filePath, content } = msg.params || {};
  if (!filePath || !isPathWithin(connection.cwd, filePath)) {
    connection.acpClient.respondError(msg.id, protocol.JSONRPC_ERRORS.INVALID_PARAMS, "Path outside session cwd");
    return;
  }
  try {
    await fs.promises.mkdir(path.dirname(filePath), { recursive: true });
    await fs.promises.writeFile(filePath, content ?? "", "utf8");
    connection.acpClient.respond(msg.id, {});
  } catch (err) {
    connection.acpClient.respondError(msg.id, protocol.JSONRPC_ERRORS.INTERNAL_ERROR, err.message);
  }
}

function handleAgentRequest(connection, msg) {
  switch (msg.method) {
    case protocol.AGENT_METHODS.SESSION_REQUEST_PERMISSION: {
      const requestId = String(msg.id);
      connection.pendingPermissions.set(requestId, msg.id);
      connection.broadcast("permission_request", {
        requestId,
        sessionId: msg.params.sessionId,
        toolCall: msg.params.toolCall,
        options: msg.params.options,
      });
      return;
    }
    case protocol.AGENT_METHODS.FS_READ_TEXT_FILE:
      handleFsReadTextFile(connection, msg);
      return;
    case protocol.AGENT_METHODS.FS_WRITE_TEXT_FILE:
      handleFsWriteTextFile(connection, msg);
      return;
    default:
      connection.debugFrame("in", msg.method, msg, "warn", "unknown");
      connection.acpClient.respondError(
        msg.id,
        protocol.JSONRPC_ERRORS.METHOD_NOT_FOUND,
        `Method not supported by this client: ${msg.method}`
      );
  }
}

function handleNotification(connection, msg) {
  if (msg.method !== protocol.AGENT_METHODS.SESSION_UPDATE) {
    connection.debugFrame("in", msg.method, msg, "warn", "unknown");
    return;
  }
  const update = (msg.params && msg.params.update) || {};
  if (!KNOWN_SESSION_UPDATE_KINDS.has(update.sessionUpdate)) {
    connection.debugFrame("in", `session/update:${update.sessionUpdate}`, msg, "warn", "unknown");
  }
  connection.broadcast("session_update", { sessionId: msg.params.sessionId, update });
}

function wireClient(connection) {
  const client = connection.acpClient;
  client.on(ACP_EVENTS.FRAME, ({ direction, payload }) => {
    const method = payload.method || (payload.error ? "(error)" : "(result)");
    connection.debugFrame(direction, method, payload, "debug", "transport");
  });
  client.on(ACP_EVENTS.STDERR, (text) => {
    connection.debugFrame("in", "(stderr)", text, "warn", "transport");
  });
  client.on(ACP_EVENTS.AGENT_REQUEST, (msg) => handleAgentRequest(connection, msg));
  client.on(ACP_EVENTS.NOTIFICATION, (msg) => handleNotification(connection, msg));
  client.on(ACP_EVENTS.ERROR, (err) => {
    connection.broadcast("error_event", { message: err.message });
  });
  client.on(ACP_EVENTS.EXIT, ({ code, signal }) => {
    connection.broadcast("error_event", { message: `Agent process exited (code=${code}, signal=${signal})` });
    for (const sessionId of connection.activePromptSessionIds) {
      connection.broadcast("status", { sessionId, state: "failed", stopReason: "agent_exited" });
    }
    connections.delete(connection.id);
  });
}

async function connect(agentId, cwd) {
  const agentConfig = findAgentConfig(agentId);
  const resolvedCwd = path.resolve(process.cwd(), cwd || ".");
  const connection = new Connection(randomUUID(), agentConfig, resolvedCwd);
  connection.acpClient = new ACPClient(agentConfig.command, agentConfig.args, {
    cwd: resolvedCwd,
    env: agentConfig.env,
  });
  wireClient(connection);
  connection.acpClient.spawn();

  const initResult = await connection.acpClient.request(protocol.CLIENT_METHODS.INITIALIZE, {
    protocolVersion: protocol.PROTOCOL_VERSION,
    clientCapabilities: protocol.clientCapabilities(),
    clientInfo: protocol.clientInfo(),
  });

  connection.agentInfo = {
    name: (initResult.agentInfo && initResult.agentInfo.name) || agentConfig.label,
    title: (initResult.agentInfo && initResult.agentInfo.title) || agentConfig.label,
    version: (initResult.agentInfo && initResult.agentInfo.version) || "",
    protocolVersion: initResult.protocolVersion,
    capabilities: initResult.agentCapabilities || {},
    authMethods: initResult.authMethods || [],
  };

  connections.set(connection.id, connection);
  return { connectionId: connection.id, agent: connection.agentInfo };
}

function getConnection(connectionId) {
  const connection = connections.get(connectionId);
  if (!connection) throw new Error("Unknown or closed connection");
  return connection;
}

function disconnect(connectionId) {
  const connection = connections.get(connectionId);
  if (!connection) return;
  connection.acpClient.kill();
  for (const res of connection.sseClients) res.end();
  connections.delete(connectionId);
}

function addSSEClient(connectionId, res) {
  const connection = getConnection(connectionId);
  connection.sseClients.add(res);
  res.on("close", () => connection.sseClients.delete(res));
}

function normalizeSessionResult(result) {
  return {
    sessionId: result.sessionId,
    availableCommands: [],
    configOptions: Array.isArray(result.configOptions) ? result.configOptions : [],
    modes: result.modes || null,
  };
}

async function newSession(connectionId) {
  const connection = getConnection(connectionId);
  const result = await connection.acpClient.request(protocol.CLIENT_METHODS.SESSION_NEW, {
    cwd: connection.cwd,
    mcpServers: [],
  });
  connection.sessions.set(result.sessionId, { sessionId: result.sessionId });
  connection.activeSessionId = result.sessionId;
  return normalizeSessionResult(result);
}

async function resumeSession(connectionId, sessionId) {
  const connection = getConnection(connectionId);
  const caps = connection.agentInfo.capabilities || {};
  let result;
  if (caps.loadSession) {
    result = await connection.acpClient.request(protocol.CLIENT_METHODS.SESSION_LOAD, {
      sessionId,
      cwd: connection.cwd,
      mcpServers: [],
    });
  } else if (caps.sessionCapabilities && caps.sessionCapabilities.resume) {
    result = await connection.acpClient.request(protocol.CLIENT_METHODS.SESSION_RESUME, {
      sessionId,
      cwd: connection.cwd,
      mcpServers: [],
    });
  } else {
    throw new Error("This agent does not support resuming sessions");
  }
  result.sessionId = result.sessionId || sessionId;
  connection.sessions.set(result.sessionId, { sessionId: result.sessionId });
  connection.activeSessionId = result.sessionId;
  return normalizeSessionResult(result);
}

async function closeSession(connectionId, sessionId) {
  const connection = getConnection(connectionId);
  const caps = connection.agentInfo.capabilities || {};
  if (caps.sessionCapabilities && caps.sessionCapabilities.close) {
    try {
      await connection.acpClient.request(protocol.CLIENT_METHODS.SESSION_CLOSE, { sessionId });
    } catch (err) {
      connection.debugFrame("in", "session/close", { error: err.message }, "warn", "session");
    }
  }
  connection.sessions.delete(sessionId);
  if (connection.activeSessionId === sessionId) connection.activeSessionId = null;
}

async function listSessions(connectionId) {
  const connection = getConnection(connectionId);
  try {
    const result = await connection.acpClient.request(protocol.CLIENT_METHODS.SESSION_LIST, {});
    const sessions = Array.isArray(result) ? result : result.sessions;
    if (Array.isArray(sessions)) return sessions;
  } catch (err) {
    connection.debugFrame("in", "session/list", { error: err.message }, "debug", "session");
  }
  return Array.from(connection.sessions.values());
}

function startPromptInternal(connection, sessionId, promptBlocks) {
  connection.activePromptSessionIds.add(sessionId);
  connection.broadcast("status", { sessionId, state: "running" });
  connection.acpClient
    .request(protocol.CLIENT_METHODS.SESSION_PROMPT, { sessionId, prompt: promptBlocks })
    .then((result) => {
      connection.activePromptSessionIds.delete(sessionId);
      const stopReason = result && result.stopReason;
      const state = stopReason === "cancelled" ? "cancelled" : stopReason === "refusal" ? "failed" : "completed";
      connection.broadcast("status", { sessionId, state, stopReason });
    })
    .catch((err) => {
      connection.activePromptSessionIds.delete(sessionId);
      connection.broadcast("status", { sessionId, state: "failed", stopReason: err.message });
    });
}

function prompt(connectionId, sessionId, text) {
  const connection = getConnection(connectionId);
  if (connection.activePromptSessionIds.has(sessionId)) {
    throw new Error("A prompt is already running for this session");
  }
  startPromptInternal(connection, sessionId, [protocol.textContentBlock(text)]);
}

function executeCommand(connectionId, sessionId, command, args) {
  const text = `/${command}${args ? " " + args : ""}`;
  prompt(connectionId, sessionId, text);
}

function cancel(connectionId, sessionId) {
  const connection = getConnection(connectionId);
  connection.acpClient.notify(protocol.CLIENT_METHODS.SESSION_CANCEL, { sessionId });
  connection.broadcast("status", { sessionId, state: "cancelling" });
  for (const [requestId, rawId] of connection.pendingPermissions.entries()) {
    connection.acpClient.respond(rawId, protocol.PERMISSION_OUTCOME_CANCELLED);
    connection.pendingPermissions.delete(requestId);
    connection.broadcast("permission_cancelled", { requestId });
  }
}

function respondToPermission(connectionId, requestId, optionId, outcome) {
  const connection = getConnection(connectionId);
  const rawId = connection.pendingPermissions.get(requestId);
  if (rawId === undefined) throw new Error("Unknown or already-resolved permission request");
  connection.pendingPermissions.delete(requestId);
  const payload =
    outcome === "cancelled" ? protocol.PERMISSION_OUTCOME_CANCELLED : protocol.permissionOutcomeSelected(optionId);
  connection.acpClient.respond(rawId, payload);
}

async function setConfigOption(connectionId, sessionId, id, value, type) {
  const connection = getConnection(connectionId);
  if (id === "__mode") {
    await connection.acpClient.request(protocol.CLIENT_METHODS.SESSION_SET_MODE, { sessionId, modeId: value });
    return {};
  }
  const params = { sessionId, configId: id, value };
  if (type === "boolean") params.type = "boolean";
  const result = await connection.acpClient.request(protocol.CLIENT_METHODS.SESSION_SET_CONFIG_OPTION, params);
  return result;
}

module.exports = {
  listAgents,
  connect,
  disconnect,
  addSSEClient,
  newSession,
  resumeSession,
  closeSession,
  listSessions,
  prompt,
  executeCommand,
  cancel,
  respondToPermission,
  setConfigOption,
};
