# ACP spec reference (trimmed copy)

Source: https://github.com/agentclientprotocol/agent-client-protocol

This is a stripped-down local copy kept only as an offline reference for implementing
`acp-web-client`. It is **not** a clone with history — `.git`, the Rust crate, tooling
configs, RFDs, announcements and assets were removed. For anything beyond what's kept
here (issues, discussions, other SDKs, full history), go to the GitHub repo above.

## What's kept and why

- `docs/protocol/v1/*.mdx` — the **stable** protocol docs. This is what `kilo acp` and
  `opencode acp` actually implement, and what `acp/`, `server/acp-bridge.js` are built against.
- `docs/protocol/v2/*.mdx` — draft/unstable next revision, not implemented by any real
  agent yet. Kept only for the v1↔v2 compatibility notes.
- `schema/v1/{meta,schema}.json` — canonical v1 method list and JSON schema.
- `schema/v2/{meta,schema}.json` — same, for v2 comparison.
- `LICENSE`, `CHANGELOG.md` — kept with the retained docs.

## Protocol version used by this project

`acp-web-client` implements **protocol version 1** (see `schema/v1/meta.json`), the one
real ACP agents speak over newline-delimited JSON-RPC on stdio.
