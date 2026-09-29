# Adapters

> [简体中文](README.zh-CN.md)

`adapters/` connects `juice_agents` Runners to the local CLI and Web UI. It is not included in the SDK wheel. The SDK owns execution and state; adapters handle protocols, event serialization, approval interaction, and the current Runner reference.

| Module | Purpose | Entry point |
| --- | --- | --- |
| `stdio_gateway/` | stdin/stdout JSON-RPC service used by the CLI | `python -m adapters.stdio_gateway.entry` |
| `web_gateway/` | Local HTTP/WebSocket service | `./juice-web` or `python -m adapters.web_gateway.entry` |

Install the SDK and adapter dependencies from the repository root:

```bash
conda activate juice-agents
python -m pip install -e ./sdk
python -m pip install -r adapters/requirements.txt
python -m adapters.web_gateway.entry
```

The Web service listens on `127.0.0.1:8003` by default. It exposes session and model state, read-only workspace previews, Runner events, approvals, and the current Runner's browser preview and controls. The stdio service exposes matching RPCs for sessions, models, configurations, Skills, Plugins, Teams, worktrees, and streaming messages; the CLI normally starts it.

## Development conventions

```text
CLI / Web ── JSON-RPC / HTTP / WebSocket ──> adapters ──> juice_agents Runner
```

- Dependencies point from `adapters` to `juice_agents`. Put new protocol entry points in a separate `adapters/` subdirectory; do not duplicate Runner, Agent, Graph, or configuration-storage logic.
- stdout from stdio carries JSON-RPC only; write logs to stderr. Stdio and WebSocket share event serialization and pass through Team task state, session title previews, and model capability data.
- Workspace configuration is read and written only through `.juice/config.yaml` and SDK configuration APIs. Cold Plugin, Skill, and Team queries must not create a Runner; reassembly changes only on an existing Runner.
- The Web service has no authentication and may bind only to loopback. Restrict HTTP/WebSocket Host and browser WebSocket Origin to the local host. File previews must stay inside the workspace and must not follow symlinked directories outside it.
- Browser controls reuse the current Runner's Playwright session. Playwright objects are used only by their owning thread; `/ws/browser/live` handles navigation and input, and `/api/browser/live/new-tab` creates a real tab.
- Continue receiving `answer_ask` and `cancel_stream` while streaming. Cancellation during approval must return a cancellation result; a wrong `request_id` reports an error and keeps waiting for the correct answer.

See the [project README](../README.md) for installation and overall usage.
