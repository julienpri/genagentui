"use strict";

const http = require("http");
const fs = require("fs");
const path = require("path");
const { URL } = require("url");

const bridge = require("./acp-bridge");

const PORT = process.env.PORT ? Number(process.env.PORT) : 8787;
const PUBLIC_DIR = path.join(__dirname, "..", "public");

const MIME_TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
};

function readJSONBody(req) {
  return new Promise((resolve, reject) => {
    let data = "";
    req.on("data", (chunk) => {
      data += chunk;
      if (data.length > 10 * 1024 * 1024) req.destroy(new Error("Request body too large"));
    });
    req.on("end", () => {
      if (!data) return resolve({});
      try {
        resolve(JSON.parse(data));
      } catch (err) {
        reject(new Error("Invalid JSON body"));
      }
    });
    req.on("error", reject);
  });
}

function sendJSON(res, status, body) {
  const text = JSON.stringify(body);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(text),
  });
  res.end(text);
}

function serveStatic(req, res, pathname) {
  const relPath = pathname === "/" ? "/index.html" : pathname;
  const filePath = path.normalize(path.join(PUBLIC_DIR, relPath));
  if (!filePath.startsWith(PUBLIC_DIR)) {
    sendJSON(res, 403, { error: "Forbidden" });
    return;
  }
  fs.readFile(filePath, (err, data) => {
    if (err) {
      sendJSON(res, 404, { error: "Not found" });
      return;
    }
    const ext = path.extname(filePath);
    res.writeHead(200, { "Content-Type": MIME_TYPES[ext] || "application/octet-stream" });
    res.end(data);
  });
}

function handleEvents(req, res, query) {
  const connectionId = query.get("connectionId");
  if (!connectionId) {
    sendJSON(res, 400, { error: "Missing connectionId" });
    return;
  }
  try {
    bridge.addSSEClient(connectionId, res);
  } catch (err) {
    sendJSON(res, 400, { error: err.message });
    return;
  }
  res.writeHead(200, {
    "Content-Type": "text/event-stream; charset=utf-8",
    "Cache-Control": "no-cache",
    Connection: "keep-alive",
    "X-Accel-Buffering": "no",
  });
  res.write(": connected\n\n");
  const keepAlive = setInterval(() => {
    res.write(": ping\n\n");
  }, 25000);
  req.on("close", () => clearInterval(keepAlive));
}

const ROUTES = {
  "GET /api/agents": async () => ({ agents: bridge.listAgents() }),

  "POST /api/connect": async (body) => bridge.connect(body.agentId, body.cwd),

  "POST /api/disconnect": async (body) => {
    bridge.disconnect(body.connectionId);
    return {};
  },

  "POST /api/session/new": async (body) => bridge.newSession(body.connectionId),

  "POST /api/session/resume": async (body) => bridge.resumeSession(body.connectionId, body.sessionId),

  "POST /api/session/close": async (body) => {
    await bridge.closeSession(body.connectionId, body.sessionId);
    return {};
  },

  "GET /api/sessions": async (_body, query) => {
    const sessions = await bridge.listSessions(query.get("connectionId"));
    return { sessions: sessions.map((s) => (typeof s === "string" ? { sessionId: s } : { sessionId: s.sessionId || s.id })) };
  },

  "POST /api/prompt": async (body) => {
    bridge.prompt(body.connectionId, body.sessionId, body.text);
    return { status: "started" };
  },

  "POST /api/command": async (body) => {
    bridge.executeCommand(body.connectionId, body.sessionId, body.command, body.args);
    return { status: "started" };
  },

  "POST /api/cancel": async (body) => {
    bridge.cancel(body.connectionId, body.sessionId);
    return { status: "cancelling" };
  },

  "POST /api/permission/respond": async (body) => {
    bridge.respondToPermission(body.connectionId, body.requestId, body.optionId, body.outcome);
    return {};
  },

  "POST /api/config/set": async (body) =>
    bridge.setConfigOption(body.connectionId, body.sessionId, body.id, body.value, body.type),

  "POST /api/voice/start": async (body) =>
    bridge.startVoice(body.connectionId, body.sessionId, `http://127.0.0.1:${PORT}`),

  "POST /api/voice/stop": async (body) => bridge.stopVoice(body.connectionId),

  "POST /api/voice/event": async (body) => {
    bridge.relayVoiceEvent(body.connectionId, body.event, body.data);
    return {};
  },
};

async function handleRequest(req, res) {
  const url = new URL(req.url, `http://${req.headers.host}`);
  const routeKey = `${req.method} ${url.pathname}`;

  if (req.method === "GET" && url.pathname === "/api/events") {
    handleEvents(req, res, url.searchParams);
    return;
  }

  const handler = ROUTES[routeKey];
  if (handler) {
    try {
      const body = req.method === "POST" ? await readJSONBody(req) : {};
      const result = await handler(body, url.searchParams);
      sendJSON(res, 200, result);
    } catch (err) {
      sendJSON(res, 400, { error: err.message });
    }
    return;
  }

  if (req.method === "GET" && !url.pathname.startsWith("/api/")) {
    serveStatic(req, res, url.pathname);
    return;
  }

  sendJSON(res, 404, { error: "Not found" });
}

const server = http.createServer((req, res) => {
  handleRequest(req, res).catch((err) => {
    console.error("Unhandled request error:", err);
    if (!res.headersSent) {
      sendJSON(res, 500, { error: "Internal server error" });
    } else {
      res.end();
    }
  });
});

server.listen(PORT, () => {
  console.log(`ACP Web Client listening on http://localhost:${PORT}`);
});
