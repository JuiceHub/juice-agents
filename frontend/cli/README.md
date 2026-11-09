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

If the launch directory has been deleted, the launcher reports an unavailable
working directory: `cd` to an existing project and retry. This check runs before
Python/Node startup. Python preflight suggests installing dependencies only for
missing modules; other import errors retain their traceback and report startup failure.

The CLI supplies its current Node.js executable to the gateway as
`PLAYWRIGHT_NODEJS_PATH` for the Playwright driver. An explicitly set
`PLAYWRIGHT_NODEJS_PATH` takes precedence; use a Node.js version compatible with
Playwright and the host system. See [browser setup](../../sdk/src/juice_agents/core/agent/README.md#browser-setup)
for Chromium configuration and other entry points.

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
| `Enter` / `Alt+Enter` | Submit input / insert a newline; input during a run enters the persistent inbox |
| `↑` / `↓` | Recall shared input history / return to the current draft |
| `PgUp` / `PgDn` | Browse the current session |
| `Esc` / `Ctrl+C` | Interrupt the current turn; `Ctrl+C` on an idle empty input exits |
| `Shift` + drag (iTerm2: `Option`) | Select text to copy (bypasses in-app mouse scrolling) |
| `Ctrl+S` / `Ctrl+T` | View child Agents / background tasks |
| `/resume` | Resume an existing session |
| `/clear` | Create an empty session with the current settings; keep the old session available through `/resume` |
| `/mode` / `/permissions` | Change the execution mode / approval policy |
| `/model` / `/config` | Select a model / edit workspace configuration |
| `/rm [model_name\|reset]` | Open the reward model picker, select by name, or clear using the `/models` catalog |
| `/image_model [model_name]` | Open the image model picker or select from `image_models`; keep the image switch unchanged |
| `/strategy breadth=N` / `/strategy reset` | Set candidates per step / restore linear execution |
| `/agents` / `/teams` | View available Agents / Teams |
| `/worktree` | Reserved; reports how to use an existing Git worktree |

Type `/` for command and argument completion. Managed `--worktree` and `/worktree` operations are not implemented. Create a directory using `git worktree add`, then use `juice --workspace <directory>`.

Each tool has one record: `tool_start` creates it and `tool_end` updates it by
`call_id`, retaining its position even when concurrent calls finish out of order.
Parameters show only the first nonempty value's first line in the title, capped
at 60 characters; `…` marks omitted text or lines. Complete parameters remain in
raw events and saved sessions. Ordinary results preview at most six terminal rows
after wrapping to the current width, with omitted rows counted below the preview.
This also bounds single-line browser HTML and serialized page text. Explicit failures and
caught exceptions keep all details under the tool. Result text never determines
status. Final replies from `submit_output` are displayed once, including CodeAct
submissions; event payloads and saved observations remain intact.

```text
▸ Running shell (command: pytest)
              ↓ same call_id
✗ Failed shell (command: pytest)
  ⎿ tool result / exception details
```

Sessions are stored under `.juice/sessions/<runner_id>/` in the workspace. `/clear`
creates a session immediately, including before the first prompt, and preserves the
current model, effort, execution mode, Agent type, approval policy, strategy, and
reward model. During a running round it waits in FIFO order; later prompts follow
it into the new session. It also works while viewing a child Agent and returns to
the main transcript after success. Creation failures keep the visible history and
reconcile the active session with the gateway before another prompt is sent.

`/resume` lists saved sessions with a preview of their first request;
`/resume <runner_id>` switches the whole Runner: main and child Agent histories,
task records and session status. It also works in a child Agent view and returns
to the main transcript after success. Switching waits in FIFO order during a
running round; following inputs wait until session selection and loading finish.

CLI inputs, including slash commands and multiline prompts, are shared across
workspaces and Runners in `~/.juice/cli_history.jsonl`. Submissions are appended
immediately, and entering history navigation reloads the file to pick up entries
from other processes. The file contains submitted text and can be deleted to clear
the saved history. Bash history is separate.

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

`/model` and `/rm` share the `models` catalog. `/image_model` uses the separate
`image_models` catalog, and both catalogs reference shared `providers` for
connections and credential environment names. Open `/image_model` to view the
current image model and available deployments, or enter `/image_model seedream_4`.
The selection is saved to `image.model_name`; use `/config` to enable image tools
(disabled by default). Selecting a model preserves that switch, chat model,
reward model and strategy. Plan mode excludes image tools. Opening or canceling
the picker writes nothing; selections wait in FIFO order during a running turn.
After `/image_model `, completion offers only image model names.

`breadth` is a positive integer. With a reward model or SDK-supplied scorer, the Agent generates that many outputs from the same context, scores them, selects one, and executes its actions once before the next step. Reward model scoring adds a model request. `max_steps` still limits executed steps. No scorer, `breadth=1`, and `/strategy reset` execute linearly. Choices made before the first message apply when the session acquires its root Agent. Depth, beam width, temperature, and breadth lists are unsupported.

`/agents [name]` and `/teams [name]` show effective Registry declarations and their `local` or `builtin` source without creating resources. Local definitions override built-ins; the first successful edit saves an override, and deleting it restores the built-in. Built-in Team details have no manifest path. `/resources` and its gateway API have been removed from commands, help, and completion.

Scheduled prompts are checked once per second while the CLI is idle. UI refreshes
do not restart polling, and a pending check prevents overlapping requests. When no
task is due, checks are read-only and do not create `.juice/scheduled_tasks.lock`.
Pausing polling preserves prompts already claimed by an in-flight check.
Closing the CLI stops its polling; see [Cron](../../sdk/src/juice_agents/core/cron/README.md).

## Code entry points and boundaries

```text
Backend event → stdio notification → GatewayClient → shared conversation/presenter
                                                → MessageBlock → RowVM → Ink
Resume        → describe_agent_sessions snapshot ────────────┘
```

Permanent `stream_step` and `ask_request` notifications omit `params.request_id`.
Missing/null/empty IDs route to the permanent handler; explicit IDs route only
to their matching request. Unknown explicit IDs are dropped. Public execution
events and Ask requests have separate handlers.

Message blocks, deduplication state and the row cache exist only in memory.
Resume rebuilds the conversation from backend `session.json` and pending input
from `inbox.json` through `describe_agent_sessions`; it never parses `log.txt`.
There is no CLI display/event-journal directory. `~/.juice/cli_history.jsonl`
saves submitted input for recall, not rendered conversation or tool results.

| Location | Responsibility |
| --- | --- |
| `src/entry.tsx`, `src/app.tsx` | Startup, session state, and UI composition |
| `src/gateway/client.ts` | stdio gateway communication and request routing |
| `src/components/` | Input, selectors, and task panels |
| `src/ink-ext/` | Alternate screen and row scrolling; see the [module README](src/ink-ext/README.md) |
| `src/lib/` | Command dispatch, text layout, and view models |
| `../shared/` | Protocol, session state, and presentation shared by CLI and Web |

### Development conventions

Autonomous rounds remain visible after the initiating request finishes. Shared state uses `round_start` / `round_end`, deduplicates events by `event_id`, and scopes cancellation to the current Runner and round. Session switches detach the old backend subscription and reject its late events.

- Reuse `@juice-agents/shared/conversation` for message lifecycle and the shared package for gateway protocol and stream/command presentation. Pass the CLI presenter through `presentEvent` for foreground and autonomous rounds. Tool records use Runner, Agent, round and `call_id` as identity; names, indexes and step numbers cannot identify concurrent calls. Ignore tool events without `call_id`, and never mutate transport events.
- Committed `step_end` snapshots update existing thought/tool rows by identity and restore missed deliveries. History uses the same projection and the saved Python wrapper for CodeAct. `round_end.data.result` owns live final replies.
- Ink renders the transcript and input area in the alternate screen. Convert messages to visual rows, then use `VirtualScrollList` for the visible range; `computeOverlayBudget` controls total height to avoid Ink's clear-screen path. Do not add a second message renderer on stdout.
- Selectors, approvals, and task panels own input focus. Hide the input while a panel is open; answer `ask_request` only through `answer_ask`, then close it after acceptance.
- Plain `Enter` submits; only `Alt/Meta+Enter` inserts a newline. Send ordinary messages to the persistent inbox while a run is active; configuration commands wait for an idle boundary. Cancellation clears the current question and waiting state without blocking later messages.
- Keep shared input history append-only as JSON Lines, with one complete input per record; refresh on entry to history navigation, not during traversal.
- `/mode` changes the current Runner Agent mode, `/permissions` changes approval policy, and `/agent-type` changes only the default for future Agents. Workspace configuration is stored in the `runtime` section of `.juice/config.yaml`.
- Treat `/clear` as a global FIFO command and preserve its queue barrier for subsequent prompts. Clear the transcript and reset the child Agent view only after `start_session` succeeds; a failed replacement may already have stopped the previous Runner.
- All resume entry points adopt the returned Runner through one session transition. Refresh child sessions and tasks together, reset old panels and pending changes, and reject late RPC results from earlier session loads. Pause FIFO consumption while the session picker is open; canceling it continues in the current Runner.
- Identify active sessions by a nonempty `runner_id`; `started=false` still permits an empty session, while an empty ID is only a launch preview.
- Keep workspace resolution in the entry point and pass it to the gateway; never change the framework process cwd globally. Use gateway requests for session and child-Agent input and cancellation.
- Keep Cron polling callbacks stable across renders and allow only one in-flight check, including across idle/busy transitions.
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

Public events use typed `kind + data` envelopes with `event_id`, numeric `timestamp`, Runner/Agent identities, and applicable round/step identities. Agent state is `idle/running/released`; round results are `completed/failed/cancelled`. The shared reducer owns event deduplication, root/child transcript isolation, tool rows and workflow progress keyed by call ID. CLI and Web use the same stream presenter.

Session startup and recovery follow `bindRunner → describe_agent_sessions → applySnapshot → activate_session`; pass optional `agent_id` to inspect one Agent's history. Cancellation shows a stopping indicator until the committed `round_end`; rejection, request errors and disconnects never invent termination. Reconcile through `describe_agent_sessions` after reconnecting. Older snapshot formats are rejected explicitly and remain on disk.

Backend `message_received` events display async completion receipts before the
owner replies. Child views show initial tasks, user/Team messages and saved
attachments; sending errors stay in that child view. Reconnect snapshots include
`pending_messages` while an Agent is still computing. The backend owns scheduling,
delivery and acknowledgements; the CLI only displays events and snapshots.
