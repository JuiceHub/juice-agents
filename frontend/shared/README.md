# Frontend shared

> [简体中文](README.zh-CN.md)

`frontend/shared` is the TypeScript package shared by the CLI and Web UI. It provides gateway protocol types, conversation state, and presentation models. See the [project README](../../README.md) for installation.

| Export | Purpose |
| --- | --- |
| `@juice-agents/shared/gateway/types` | Gateway request, response, and event types |
| `@juice-agents/shared/conversation` | Streaming messages and conversation state |
| `@juice-agents/shared/presenter/stream` | Convert stream events into message blocks |
| `@juice-agents/shared/presenter/command` | Convert command results into presentation content |


Public events use typed `kind + data` envelopes with `event_id`, numeric `timestamp`, Runner/Agent identities, and applicable round/step identities. Agent state is `idle/running/released`; round results are `completed/failed/cancelled`. The shared reducer owns event deduplication, root/child transcript isolation, tool rows and workflow progress keyed by call ID. CLI and Web use the same stream presenter.

Session startup and recovery follow `bindRunner → describe_agent_sessions → applySnapshot → activate_session`; pass optional `agent_id` to inspect one Agent's history. Cancellation shows a stopping indicator until the committed `round_end`; rejection, request errors and disconnects never invent termination. Reconcile through `describe_agent_sessions` after reconnecting. Older snapshot formats are rejected explicitly and remain on disk.

`message_received.data.attachments` displays backend-accepted input, including
async completion receipts before the owner replies. `round_start.data.input`
displays saved initial tasks, including functional child calls. Saved task/action
attachments and snapshot `pending_messages` recover messages after reconnect;
stable message IDs keep live and
restored rows aligned. The backend owns the inbox, scheduling, acknowledgements
and outcomes. Presenters only format its explicit message/status fields.

Stdio permanent-subscription notifications omit `request_id`; the notification
types mark it optional. Missing/null/empty IDs select the permanent event or Ask
handler; an explicit ID must match an active request. Conversation state and
presentation caches are in memory; recovery reads backend snapshots rather than
a persisted frontend event stream.

## Development conventions

- Agent and Team presenters expose effective Registry sources (`local` / `builtin`). `TeamInfo` and `TeamManifest` carry `source` and a nullable `manifest_path`; built-in Teams never display a nonexistent file. Local overrides are saved on the first successful edit and deleting them restores built-ins. Resource discovery uses `/agents` and `/teams`; `/resources`, its presenter, protocol types, and `list_mode_resources` request have been removed.
- `WorkspaceRuntimeConfig` describes both effective reads and sparse project patches. CLI `load_effective_config` and Web `effective_config` come from backend layering; `load_workspace_config` still returns project overrides only. Save only fields changed by the user, preserving inherited defaults.
- Keep gateway types aligned with Python gateway serialization; CLI and Web must not maintain separate protocol copies.
- `StrategyConfiguration` contains only positive integer `breadth` or `reset`. `StrategySwitchResult` distinguishes pending/applied configuration from linear/candidate-selection execution and reports whether a scorer exists.
- `RewardModelConfiguration` describes the `configure_reward_model` response: selected catalog `model_name` (or `null`), scorer availability, unchanged `breadth`, and effective execution. `/rm` opens a picker with the shared `/models` catalog, current reward model, and a clear option; `/rm <model_name>` and `/rm reset` remain available. Selection and clearing preserve breadth and the generation model; the gateway constructs the scorer and resolves credentials.
- `ImageModelsResponse` describes read-only `list_image_models`: the independent image catalog, current image model and image switch. `RuntimeImageModelInfo` exposes public Provider metadata only. `set_image_config` accepts optional `model_name` and `enabled` independently and returns `ImageConfigResponse` (`enabled`, `runner_id`, `image_model_name`); model selection must not implicitly enable image tools or change chat/RM configuration.
- Presenters generate view models from input only and never mutate Runner or task state. Read Team updates from `event.data.snapshot`; `eligible_members` is the execution scope and `claimed_by` is the actual claimant.
- Restored history uses committed `round_result` records for final answers and failures; a submitted action supplies the answer only when its round has no committed result. Live streams render final answers from `round_end` with the same round identity.
- On session replacement, call `bindRunner(runnerId)` with the returned identity before React commits status. This immediately resets round tracking and rejects old-session events; the caller then replaces the corresponding history and session projections.
- `conversation` owns message state and cancellation behavior; UI components only render and interact. Recovery snapshots rebuild active rounds and reconcile root and child history, tool state, and progress. Do not append a second copy of restored root history in the UI.
- Keyed tool blocks update in place by Runner/Agent/round/`call_id`; immutable replacement refreshes terminal row caches while preserving concurrent call order. `ToolCallBlock.observation` carries returned text separately from `error`. Lifecycle status uses existing execution fields and aliases, never output text; missing status remains unknown. Committed `step_end` snapshots reconcile those same records and restore missed notifications; history uses the same lifecycle projection.
- `conversation` tracks `round_start` / `round_end` by Runner and round ID, including autonomous events with no request ID. It deduplicates `event_id` across subscriptions and foreground streams, preserves other active rounds when a request settles, and rejects events from a previous Runner. Pass the current `runnerId` to the hook; cancellation sends that Runner and round identity.
- Ask requests belong to their Runner and round; only that round or its synchronous caller's cancellation round clears the interaction. An independent child Ask survives the root round ending.
- Pass `runnerId: ""` when no session is active to reject detached-session events. When sending immediately after creation, pass the returned ID as `sendMessage(text, modeOverride, runnerId)` so the first request binds before React commits status. Scope changes preserve visible messages; clear them only after successful creation. Session artifacts use `.juice/sessions/<runner_id>/`.
- Put new cross-interface logic here when appropriate, and add meaningful regression tests for pure logic.

Verify from the repository root:

```bash
conda activate juice-agents
cd frontend
npm test --workspace @juice-agents/shared
npm run typecheck --workspace @juice-agents/shared
npm run build --workspace @juice-agents/shared
```
