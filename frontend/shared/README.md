# Frontend shared

> [简体中文](README.zh-CN.md)

`frontend/shared` is the TypeScript package shared by the CLI and Web UI. It provides gateway protocol types, conversation state, and presentation models. See the [project README](../../README.md) for installation.

| Export | Purpose |
| --- | --- |
| `@juice-agents/shared/gateway/types` | Gateway request, response, and event types |
| `@juice-agents/shared/conversation` | Streaming messages and conversation state |
| `@juice-agents/shared/presenter/stream` | Convert stream events into message blocks |
| `@juice-agents/shared/presenter/command` | Convert command results into presentation content |

## Development conventions

- Agent and Team presenters expose effective Registry sources (`local` / `builtin`). `TeamInfo` and `TeamManifest` carry `source` and a nullable `manifest_path`; built-in Teams never display a nonexistent file. Local overrides are saved on the first successful edit and deleting them restores built-ins. Resource discovery uses `/agents` and `/teams`; `/resources`, its presenter, protocol types, and `list_mode_resources` request have been removed.
- `WorkspaceRuntimeConfig` describes both effective reads and sparse project patches. CLI `load_effective_config` and Web `effective_config` come from backend layering; `load_workspace_config` still returns project overrides only. Save only fields changed by the user, preserving inherited defaults.
- Keep gateway types aligned with Python gateway serialization; CLI and Web must not maintain separate protocol copies.
- `StrategyConfiguration` contains only positive integer `breadth` or `reset`. `StrategySwitchResult` distinguishes pending/applied configuration from linear/candidate-selection execution and reports whether a scorer exists.
- `RewardModelConfiguration` describes the `configure_reward_model` response: selected catalog `model_name` (or `null`), scorer availability, unchanged `breadth`, and effective execution. `/rm` opens a picker with the shared `/models` catalog, current reward model, and a clear option; `/rm <model_name>` and `/rm reset` remain available. Selection and clearing preserve breadth and the generation model; the gateway constructs the scorer and resolves credentials.
- Presenters generate view models from input only and never mutate Runner or task state. Read Team updates from `team_event.snapshot`; `eligible_members` is the execution scope and `claimed_by` is the actual claimant.
- `conversation` owns message state and cancellation behavior; UI components only render and interact.
- Put new cross-interface logic here when appropriate, and add meaningful regression tests for pure logic.

Verify from the repository root:

```bash
conda activate juice-agents
cd frontend
npm test --workspace @juice-agents/shared
npm run typecheck --workspace @juice-agents/shared
npm run build --workspace @juice-agents/shared
```
