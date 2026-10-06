"use strict";

const PROTOCOL_VERSION = 1;

const CLIENT_METHODS = {
  INITIALIZE: "initialize",
  AUTHENTICATE: "authenticate",
  SESSION_NEW: "session/new",
  SESSION_LOAD: "session/load",
  SESSION_RESUME: "session/resume",
  SESSION_CLOSE: "session/close",
  SESSION_LIST: "session/list",
  SESSION_DELETE: "session/delete",
  SESSION_PROMPT: "session/prompt",
  SESSION_CANCEL: "session/cancel",
  SESSION_SET_MODE: "session/set_mode",
  SESSION_SET_CONFIG_OPTION: "session/set_config_option",
};

const AGENT_METHODS = {
  SESSION_UPDATE: "session/update",
  SESSION_REQUEST_PERMISSION: "session/request_permission",
  FS_READ_TEXT_FILE: "fs/read_text_file",
  FS_WRITE_TEXT_FILE: "fs/write_text_file",
};

const SESSION_UPDATE_KINDS = {
  USER_MESSAGE_CHUNK: "user_message_chunk",
  AGENT_MESSAGE_CHUNK: "agent_message_chunk",
  AGENT_THOUGHT_CHUNK: "agent_thought_chunk",
  TOOL_CALL: "tool_call",
  TOOL_CALL_UPDATE: "tool_call_update",
  PLAN: "plan",
  AVAILABLE_COMMANDS_UPDATE: "available_commands_update",
  CURRENT_MODE_UPDATE: "current_mode_update",
  CONFIG_OPTION_UPDATE: "config_option_update",
  SESSION_INFO_UPDATE: "session_info_update",
  USAGE_UPDATE: "usage_update",
};

const STOP_REASONS = ["end_turn", "max_tokens", "max_turn_requests", "refusal", "cancelled"];

const TOOL_CALL_STATUSES = ["pending", "in_progress", "completed", "failed", "cancelled"];

const PERMISSION_OUTCOME_CANCELLED = { outcome: { outcome: "cancelled" } };

function permissionOutcomeSelected(optionId) {
  return { outcome: { outcome: "selected", optionId } };
}

const JSONRPC_ERRORS = {
  PARSE_ERROR: -32700,
  INVALID_REQUEST: -32600,
  METHOD_NOT_FOUND: -32601,
  INVALID_PARAMS: -32602,
  INTERNAL_ERROR: -32603,
};

function buildRequest(id, method, params) {
  return { jsonrpc: "2.0", id, method, params };
}

function buildNotification(method, params) {
  return { jsonrpc: "2.0", method, params };
}

function buildResult(id, result) {
  return { jsonrpc: "2.0", id, result };
}

function buildError(id, code, message, data) {
  const error = { code, message };
  if (data !== undefined) error.data = data;
  return { jsonrpc: "2.0", id, error };
}

function clientCapabilities() {
  return {
    fs: { readTextFile: true, writeTextFile: true },
    session: { configOptions: { boolean: {} } },
  };
}

function clientInfo() {
  return { name: "acp-web-client", title: "ACP Web Client", version: "0.1.0" };
}

function textContentBlock(text) {
  return { type: "text", text };
}

module.exports = {
  PROTOCOL_VERSION,
  CLIENT_METHODS,
  AGENT_METHODS,
  SESSION_UPDATE_KINDS,
  STOP_REASONS,
  TOOL_CALL_STATUSES,
  PERMISSION_OUTCOME_CANCELLED,
  permissionOutcomeSelected,
  JSONRPC_ERRORS,
  buildRequest,
  buildNotification,
  buildResult,
  buildError,
  clientCapabilities,
  clientInfo,
  textContentBlock,
};
