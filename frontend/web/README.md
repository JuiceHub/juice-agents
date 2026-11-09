# Local Web workbench

> [简体中文](README.zh-CN.md)

`./juice-web` provides browser views for conversations, tasks, and workspace files. Follow the [project README](../../README.md) to install the SDK, gateway, and frontend dependencies, then run:

```bash
conda activate juice-agents
./juice-web
```

Open the `http://127.0.0.1:5173/` URL printed in the terminal. The script starts the local Web gateway and Vite development server. The gateway accepts local connections only and has no remote authentication.

Initialization uses bootstrap `effective_config` (base → `~/.juice/config.yaml` →
project `.juice/config.yaml`); `workspace_config` retains project-only semantics.
`GET /api/effective-config` provides the same read-only merged view. Save sparse
project patches through `PUT /api/workspace-config`; launch selections do not
copy inherited defaults to disk. Credentials share process environment → project
`.env` → `~/.juice/.env` precedence.

## Interface and capabilities

| Area | Purpose |
| --- | --- |
| Left | Select projects, sessions, and child Agents; search history and view Skills |
| Center | Read conversations, tool results, and approvals; send input to the persistent inbox while a run is active |
| Right | Operate the current Runner Browser or preview workspace files read-only |

Team sessions show task completion, member state, and errors. The input supports `/agents`, `/teams`, `/skills` and other commands, `$<skill>` / `$workflow:<name> <task>` explicit invocation, plus model and thinking-effort selection.

The gateway client supports the separate image catalog through `listImageModels`
and sparse `setImageConfig({ model_name?, enabled? })` updates using shared
protocol types. Image model selection preserves the image switch and chat/RM
models. This version has no Web image model picker; use CLI `/image_model` or
workspace `image.model_name`, with image tools enabled separately in configuration.

`/clear` immediately creates and selects an empty session, including when no session is active. It keeps the current model, effort, execution mode, Agent type, permissions, strategy, and reward model. During a run it waits in FIFO order for the current round to finish; later input follows it into the new session. It also works from a child-Agent view and returns to the root conversation after creation. Failed creation preserves the visible transcript and reconciles the active session with the gateway. New sessions are stored in `.juice/sessions/<runner_id>/`; use `/resume <runner_id>` to reopen an earlier session.

`/agents [name]` and `/teams [name]` query the Registry without creating a Runner or resource files. They report the effective `local` or `builtin` source; built-in Teams have `manifest_path: null`. Local declarations take priority, are saved on the first successful edit, and restore built-ins when deleted. `/resources` and its dedicated gateway API have been removed from dispatch, help, and completion.

Browser uses a real Chromium page through Playwright. Run `python -m playwright install chromium` before first use. A desktop session is required for a separate visible window; pure SSH environments can use the embedded preview.

## Code entry points and boundaries

| Location | Responsibility |
| --- | --- |
| `src/App.tsx` | Layout, startup data, and stream state |
| `src/gateway/client.ts` | HTTP/WebSocket requests and event routing |
| `src/components/ThreadView.tsx` | Root and child-Agent messages and input |
| `src/components/PreviewPanel.tsx` | Browser / Files preview switching |
| `src/lib/` | Commands, completion, and layout helpers |
| `../shared/` | Shared protocol, conversation state, and presentation models |

### Development conventions

Saved CodeAct actions use the persisted Python wrapper for tool status and
details. Its result and caught exception stay inside the expandable tool entry;
do not add another Python entry from `code_action` when the wrapper exists.

- The Web Runner is the task conversation, and child Agents belong to it. Use `send_agent_message` and `interrupt_agent` for child-Agent input and stopping; root uses its own stream and cancellation requests.
- Receive public events through the permanent Runner subscription; `stream_message` only waits for its `RoundResult`. Shared state tracks `round_start` / `round_end`, deduplicates `event_id`, and scopes cancellation to `{runner_id, round_id}`. Session switches unsubscribe the old Runner and reject late events. Reject pending requests and show an error when a connection closes or fails; the message queue must remain usable.
- Reuse shared message lifecycle and stream/command presentation models. Web components own styling only; render model Markdown without raw HTML and show user input and tool arguments as plain text.
- Read-only commands such as `/teams` must not create a Runner. The gateway persists model and effort choices in workspace configuration; `/agent-type` changes only the default for future sessions.
- Commit `/clear` only after `start_session` succeeds. Reuse `src/lib/session.ts` for session creation and composer FIFO; check `/clear` before routing input to a child Agent. Preserve configured strategy and reward model through the gateway's same-workspace runtime replacement.
- Browser actions operate the current Runner Playwright session. Show real pages through the gateway's live view and interaction APIs rather than an iframe or virtual tab. File reads must pass gateway workspace path checks.
- The gateway is local-only. Any remote-access feature requires designed authentication and access control.


Public events use typed `kind + data` envelopes with `event_id`, numeric `timestamp`, Runner/Agent identities, and applicable round/step identities. Agent state is `idle/running/released`; round results are `completed/failed/cancelled`. The shared reducer owns event deduplication, root/child transcript isolation, tool rows and workflow progress keyed by call ID. CLI and Web use the same stream presenter.

Session startup and recovery follow `bindRunner → describe_agent_sessions → applySnapshot → activate_session`; pass optional `agent_id` to inspect one Agent's history. Cancellation shows a stopping indicator until the committed `round_end`; rejection, request errors and disconnects never invent termination. Reconcile through `describe_agent_sessions` after reconnecting. Older snapshot formats are rejected explicitly and remain on disk.

Backend `message_received` events display async completion receipts before the
owner replies. Actor views show initial tasks and user/Team messages. Saved
attachments and snapshot `pending_messages` recover them after reconnect. The
backend owns scheduling, delivery and acknowledgements; Web only displays its
events and snapshots through the shared presenter.

## Verification

Run from the repository root:

```bash
conda activate juice-agents
cd frontend
npm test --workspace juice-web
npm run typecheck --workspace juice-web
npm run build --workspace juice-web
npx tsx --test ../tests/frontend/test_web_clear.ts
```

When changing gateway or Browser behavior, also run the relevant tests under `tests/adapters/web_gateway/`.
