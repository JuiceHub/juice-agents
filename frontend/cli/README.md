# Terminal interface (CLI)

> [简体中文](README.zh-CN.md)

`./juice` runs an Agent in a terminal with session recovery, tool approvals, task delegation, and project workspaces. See the [project README](../../README.md) for installation and startup. Enter `/help` for the complete command list.

## Install once, launch from any project

After installing dependencies, bind this source checkout and its current Python/Node runtimes:

```bash
conda activate juice-agents
./install-juice                       # default: ~/.local/bin/juice
# Optional: ./install-juice --bin-dir /your/bin
cd /path/to/project
juice
juice --workspace /path/to/other-project
```

Add `~/.local/bin` to your shell `PATH` once. Later launches need no Conda activation.
The launcher keeps your project `PATH`, checks dependencies without installing them,
and records Node separately from Python (including NVM installations). If the checkout
or interpreters move, rerun `install-juice`; repair frontend dependencies explicitly
with `cd frontend && npm ci`.

Workspace priority is `--workspace` → `JUICE_WORKSPACE_DIR` → launch directory
(`INIT_CWD` preserves the launch directory for direct npm use). Missing directories
fail before the gateway starts. The gateway runs in the workspace and imports the
framework through explicit module paths.

CLI initialization and `/config` read backend `load_effective_config`: SDK/default
configuration → `~/.juice/config.yaml` → project `.juice/config.yaml` → launch selections.
`/config` and `/model` save only changed project fields. Credentials resolve from the
process environment, project `.env`, then `~/.juice/.env`; opening CLI and help does
not require a key. User/project AGENTS rules join the system prompt on start and resume.

## Common operations

| Operation | Effect |
| --- | --- |
| `Enter` / `Alt+Enter` | Submit input / insert a newline; input during a run is queued |
| `PgUp` / `PgDn` | Browse the current session |
| `Esc` / `Ctrl+C` | Interrupt the current turn; `Ctrl+C` on an idle empty input exits |
| `Shift` + drag (iTerm2: `Option`) | Select text to copy (bypasses in-app mouse scrolling) |
| `Ctrl+S` / `Ctrl+T` | View child Agents / background tasks |
| `/resume` | Resume an existing session |
| `/mode` / `/permissions` | Change the execution mode / approval policy |
| `/model` / `/config` | Select a model / edit workspace configuration |
| `/rm [model_name\|reset]` | Open the reward model picker, select by name, or clear using the `/models` catalog |
| `/strategy breadth=N` / `/strategy reset` | Set candidates per step / restore linear execution |
| `/agents` / `/teams` | View available Agents / Teams |
| `/worktree` | Reserved; reports how to use an existing Git worktree |

Type `/` for command and argument completion. Managed `--worktree` and `/worktree` operations are not implemented. Create a directory using `git worktree add`, then use `juice --workspace <directory>`.

Type `/str` to find `/strategy`; after `/strategy `, completion offers `breadth=3`
and `reset`, with the inline hint `<breadth=N|reset>`. The count can be edited to
any positive integer. Completed arguments close the candidate list.

Use the same model names and connection settings shown by `/models` for reward scoring:

```text
/models
/rm <model_name>
/strategy breadth=3
/rm
/rm reset
```

`/rm` opens a picker like `/model`, using the same catalog and marking the current reward model. The picker also offers a clear option. Selection and reset leave `breadth` unchanged and do not switch the generation model. After `/rm `, completion offers catalog model names and `reset`; extra arguments and effort options are rejected.

The reward model setting lasts for the current gateway process; configure it again after restarting the CLI. `/strategy reset` preserves the selected reward model, while `/rm reset` clears it and preserves candidate breadth.

`breadth` is a positive integer. With a reward model or SDK-supplied scorer, the Agent generates that many outputs from the same context, scores them, selects one, and executes its actions once before the next step. Reward model scoring adds a model request. `max_steps` still limits executed steps. No scorer, `breadth=1`, and `/strategy reset` execute linearly. Choices made before the first message apply when the session acquires its root Agent. Depth, beam width, temperature, and breadth lists are unsupported.

`/agents [name]` and `/teams [name]` show effective Registry declarations and their `local` or `builtin` source without creating resources. Local definitions override built-ins; the first successful edit saves an override, and deleting it restores the built-in. Built-in Team details have no manifest path. `/resources` and its gateway API have been removed from commands, help, and completion.

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
- Keep workspace resolution in the entry point and pass it to the gateway; never change the framework process cwd globally. Use gateway requests for session and child-Agent input and cancellation.
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
The strategy and reward model command regressions live under `tests/frontend/` and are included in
the CLI test script. To run them alone from the repository root, use
`frontend/node_modules/.bin/tsx --test tests/frontend/test_strategy_command.ts tests/frontend/test_reward_model_command.ts tests/frontend/test_reward_model_selector.ts`.
