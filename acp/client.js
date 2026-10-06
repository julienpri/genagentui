"use strict";

const { spawn } = require("child_process");
const readline = require("readline");
const { EventEmitter } = require("events");
const { ACP_EVENTS } = require("./events");

class ACPClient extends EventEmitter {
  constructor(command, args, options = {}) {
    super();
    this.command = command;
    this.args = args || [];
    this.options = options;
    this.child = null;
    this._nextId = 1;
    this._pending = new Map();
  }

  spawn() {
    this.child = spawn(this.command, this.args, {
      cwd: this.options.cwd,
      env: { ...process.env, ...(this.options.env || {}) },
      stdio: ["pipe", "pipe", "pipe"],
    });

    this.child.on("error", (err) => {
      this._rejectAllPending(err);
      this.emit(ACP_EVENTS.ERROR, err);
    });

    this.child.on("exit", (code, signal) => {
      this._rejectAllPending(new Error(`agent process exited (code=${code}, signal=${signal})`));
      this.emit(ACP_EVENTS.EXIT, { code, signal });
    });

    this.child.stderr.on("data", (chunk) => {
      this.emit(ACP_EVENTS.STDERR, chunk.toString("utf8"));
    });

    const rl = readline.createInterface({ input: this.child.stdout, crlfDelay: Infinity });
    rl.on("line", (line) => this._handleLine(line));

    return this;
  }

  _handleLine(line) {
    const trimmed = line.trim();
    if (!trimmed) return;
    let msg;
    try {
      msg = JSON.parse(trimmed);
    } catch (err) {
      this.emit(ACP_EVENTS.ERROR, new Error(`invalid JSON-RPC line from agent: ${trimmed.slice(0, 500)}`));
      return;
    }

    this.emit(ACP_EVENTS.FRAME, { direction: "in", payload: msg });

    if (msg.method !== undefined && msg.id !== undefined) {
      this.emit(ACP_EVENTS.AGENT_REQUEST, msg);
      return;
    }
    if (msg.method !== undefined) {
      this.emit(ACP_EVENTS.NOTIFICATION, msg);
      return;
    }
    if (msg.id !== undefined) {
      const pending = this._pending.get(msg.id);
      if (!pending) return;
      this._pending.delete(msg.id);
      if (msg.error) {
        const err = new Error(msg.error.message || "ACP error");
        err.code = msg.error.code;
        err.data = msg.error.data;
        pending.reject(err);
      } else {
        pending.resolve(msg.result);
      }
    }
  }

  _rejectAllPending(err) {
    for (const pending of this._pending.values()) pending.reject(err);
    this._pending.clear();
  }

  _write(obj) {
    this.emit(ACP_EVENTS.FRAME, { direction: "out", payload: obj });
    this.child.stdin.write(JSON.stringify(obj) + "\n");
  }

  request(method, params) {
    const id = this._nextId++;
    return new Promise((resolve, reject) => {
      this._pending.set(id, { resolve, reject, method });
      this._write({ jsonrpc: "2.0", id, method, params });
    });
  }

  notify(method, params) {
    this._write({ jsonrpc: "2.0", method, params });
  }

  respond(id, result) {
    this._write({ jsonrpc: "2.0", id, result: result === undefined ? {} : result });
  }

  respondError(id, code, message, data) {
    const error = { code, message };
    if (data !== undefined) error.data = data;
    this._write({ jsonrpc: "2.0", id, error });
  }

  kill() {
    if (this.child && !this.child.killed) this.child.kill();
  }
}

module.exports = { ACPClient };
