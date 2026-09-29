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

- Keep gateway types aligned with Python gateway serialization; CLI and Web must not maintain separate protocol copies.
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
