# ACP Web Client

A lightweight, agent-agnostic web client for the [Agent Client Protocol](https://agentclientprotocol.com)
(ACP). It connects to any ACP-compliant coding agent over stdio and renders whatever
capabilities, sessions, commands, config options, tool calls and permission requests
that agent actually advertises — no agent-specific logic in the frontend.

Full design spec: [SPEC.md](./SPEC.md). Protocol reference used to build this:
[agent-client-protocol-std/](./agent-client-protocol-std/) (trimmed local copy of the
official spec repo, see its own README for details).

## Quick start

```bash
npm install     # zero runtime dependencies, just here for completeness
node server/server.js
```

Open `http://localhost:8787/?debug=1` (the `?debug=1` enables the in-app Debug panel,
showing raw JSON-RPC traffic).

Pick an agent, set a working directory (absolute path to the project you want the
agent to operate on), connect, create a session, and chat.

## Supported agents

Configured in [agents.json](./agents.json). Out of the box:

- [`kilo acp`](https://kilo.ai) — requires the `kilo` CLI on `PATH`
- [`opencode acp`](https://opencode.ai) — requires the `opencode` CLI on `PATH`

Add any other ACP-compliant agent by appending `{ id, label, command, args }` to
`agents.json` — no code changes required.

## Architecture

```
acp/              Generic ACP JSON-RPC client (protocol v1), spawns the agent
                   subprocess and speaks newline-delimited JSON-RPC over stdio.
server/           Zero-dependency Node HTTP server: serves public/, exposes a
                   small REST + SSE API, bridges it to the ACP client.
public/           Vanilla HTML/CSS/JS frontend. No framework, no build step.
agent-client-protocol-std/   Trimmed local copy of the official ACP spec, for reference.
```

The frontend never special-cases an agent by name — it only reacts to capabilities
and events the connected agent actually declares (see SPEC.md §5, §14, §19).

## Requirements

- Node.js >= 18
- At least one ACP-compliant agent binary on `PATH` (e.g. `kilo`, `opencode`)
