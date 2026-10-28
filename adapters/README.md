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

The Web service listens on `127.0.0.1:8003` by default. It exposes session and model state, read-only workspace previews, Runner events, approvals, and the current Runner's browser preview and controls. The stdio service exposes matching RPCs for sessions, models, configurations, Skills, Plugins, Teams, and streaming messages; the CLI normally starts it.

The stdio `switch_strategy` RPC accepts `{"breadth": 3}` or `{"reset": true}`.
The independent `configure_reward_model` RPC accepts `{}` to query,
`{"model_name": "<catalog_name>"}` to set, or `{"reset": true}` to clear scoring.
It uses `GenerativeRewardModel.from_config(name, context)` to generate JSON scores
through the provider SDK and parse them into numbers. Resolution reuses the
`list_models` catalog and configuration context; construction and empty batches
do not resolve a provider. SDK callers can also inject
`GenerativeRewardModel(model_name="your-model", client=sdk_client)` into a strategy
without configuration files; `sdk_client` is a native SDK client matching the backend.
With a scorer and `breadth>1`, each step generates candidates,
scores the valid ones in one request, and executes only the winner. Otherwise
execution stays linear. Clearing the reward model retains breadth; resetting
the strategy sets breadth to one and retains a catalog reward-model choice.
Both RPCs report actual execution state. Choices transfer across startup
or recovery in the same workspace; they are process-local and never serialized.

Agent and Team queries resolve local declarations first, then read-only built-ins through the SDK Registry. Cold queries never create `.juice`; creating a Runner saves run state without saving preset Agent YAML or Team manifests. The first successful edit saves a complete local override, and deleting that override restores the built-in. Query responses report `source` as `local` or `builtin`; built-in Teams have `manifest_path: null`. Use `list_available_agents`, `list_team_configs`, and `get_team_config` for discovery. The dedicated `list_mode_resources` RPC has been removed and returns an unknown-method error.

## Configuration and workspace contract

The local CLI installer binds the checkout/Python/Node paths. Its gateway child uses
the target workspace as cwd and explicit repository/SDK module paths, retaining the
user's `PATH`; SDK execution never depends on global `os.chdir()`.

| Interface | Behavior |
| --- | --- |
| `load_effective_config` / `GET /api/effective-config` | Read merged base, `~/.juice/config.yaml`, project overrides, and current launch choices |
| `load_workspace_config` | Read project `.juice/config.yaml` only, preserving its original contract |
| `save_workspace_config` / `PUT /api/workspace-config` | Merge only fields changed by the user into project overrides |
| `switch_agent_mode` | Omit `agent_type` to retain the current type; pass `react` or `codeact` to change it explicitly |
| Web bootstrap | Return both `workspace_config` and `effective_config`; initialize UI from the latter |

Launch choices use Runner `runtime_overrides` without copying inherited defaults to
the project. Mode switches preserve the inherited or explicitly selected Agent type
before and after session startup, without creating project configuration overrides.
Existing Git worktree directories are valid workspaces; built-in
worktree management returns an explicit not-implemented diagnostic.

## Development conventions

```text
CLI / Web ── JSON-RPC / HTTP / WebSocket ──> adapters ──> juice_agents Runner
```

- Dependencies point from `adapters` to `juice_agents`. Put new protocol entry points in a separate `adapters/` subdirectory; do not duplicate Runner, Agent, Workflow, or configuration-storage logic.
- stdout from stdio carries JSON-RPC only; write logs to stderr. Stdio and WebSocket share event serialization and pass through Team task state, session title previews, and model capability data.
- Read effective configuration through SDK layering; write only project patches through SDK configuration APIs. Cold Plugin, Skill, and Team queries must not create a Runner; reassembly changes only on an existing Runner.
- Keep strategy and reward-model configuration transport-local. Compose the Manager's runtime binding hook to apply pending choices to lazy or reacquired roots, preserving the existing Runner binding and Manager persistence path. Import `GenerativeRewardModel` from `juice_agents.core.models.reward_models` for scoring; never put provider calls in protocol handlers.
- The Web service has no authentication and may bind only to loopback. Restrict HTTP/WebSocket Host and browser WebSocket Origin to the local host. File previews must stay inside the workspace and must not follow symlinked directories outside it.
- Browser controls reuse the current Runner's Playwright session. Playwright objects are used only by their owning thread; `/ws/browser/live` handles navigation and input, and `/api/browser/live/new-tab` creates a real tab.
- Continue receiving `answer_ask` and `cancel_stream` while streaming. Cancellation during approval must return a cancellation result; a wrong `request_id` reports an error and keeps waiting for the correct answer.

See the [project README](../README.md) for installation and overall usage.
