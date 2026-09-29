# Terminal interface (CLI)

> [简体中文](README.zh-CN.md)

`./juice` runs an Agent in a terminal with session recovery, tool approvals, task delegation, and Git worktrees. See the [project README](../../README.md) for installation and startup. Enter `/help` for the complete command list.

## Common operations

| Operation | Effect |
| --- | --- |
| `Enter` / `Alt+Enter` | Submit input / insert a newline; input during a run is queued |
| `PgUp` / `PgDn` | Browse the current session |
| `Esc` / `Ctrl+C` | Interrupt the current turn |
| `Ctrl+S` / `Ctrl+T` | View child Agents / background tasks |
| `/resume` | Resume an existing session |
| `/mode` / `/permissions` | Change the execution mode / approval policy |
| `/model` / `/config` | Select a model / edit workspace configuration |
| `/agents` / `/teams` | View available Agents / Teams |
| `/worktree` | View or switch the current Git worktree |

Type `/` for command and argument completion. `./juice --worktree [name]` starts a task in an isolated worktree; unmerged changes are kept when the process exits.

## Code entry points and boundaries

| Location | Responsibility |
| --- | --- |
| `src/entry.tsx`, `src/app.tsx` | Startup, session state, and UI composition |
| `src/gateway/client.ts` | stdio gateway communication and request routing |
| `src/components/` | Input, selectors, and task panels |
| `src/ink-ext/` | Alternate screen and row scrolling; see the [module README](src/ink-ext/README.md) |
| `src/lib/` | Command dispatch, text layout, and view models |
| `../shared/` | Protocol, session state, and presentation shared by CLI and Web |

### Development conventions

- Reuse `@juice-agents/shared/conversation` for message lifecycle, and the shared package for gateway protocol and stream/command presentation. The CLI owns terminal interaction only.
- Ink renders the transcript and input area in the alternate screen. Convert messages to visual rows, then use `VirtualScrollList` for the visible range; `computeOverlayBudget` controls total height to avoid Ink's clear-screen path. Do not add a second message renderer on stdout.
- Selectors, approvals, and task panels own input focus. Hide the input while a panel is open; answer `ask_request` only through `answer_ask`, then close it after acceptance.
- Plain `Enter` submits; only `Alt/Meta+Enter` inserts a newline. Queue ordinary messages FIFO while a run is active. Cancellation clears the current question and waiting state without blocking later messages.
- `/mode` changes the current Runner Agent mode, `/permissions` changes approval policy, and `/agent-type` changes only the default for future Agents. Workspace configuration is stored in the `runtime` section of `.juice/config.yaml`.
- `--worktree` and `/worktree` operate through the gateway rather than calling Git from the CLI. Use the corresponding gateway requests for session and child-Agent input and cancellation.
- Use theme tokens and components from `src/components/design-system/` for new visual state. Animation components must provide a static display when `JUICE_NO_ANIMATION=1`.

## Verification

Run from the repository root:

```bash
conda activate juice-agents
cd frontend
npm test --workspace juice-cli
npm run typecheck --workspace juice-cli
```

When changing gateway requests or session semantics, also run the relevant tests under `tests/adapters/stdio_gateway/`.
