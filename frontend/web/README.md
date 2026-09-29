# Local Web workbench

> [简体中文](README.zh-CN.md)

`./juice-web` provides browser views for conversations, tasks, and workspace files. Follow the [project README](../../README.md) to install the SDK, gateway, and frontend dependencies, then run:

```bash
conda activate juice-agents
./juice-web
```

Open the `http://127.0.0.1:5173/` URL printed in the terminal. The script starts the local Web gateway and Vite development server. The gateway accepts local connections only and has no remote authentication.

## Interface and capabilities

| Area | Purpose |
| --- | --- |
| Left | Select projects, sessions, and child Agents; search history and view Skills |
| Center | Read conversations, tool results, and approvals; queue input while a run is active |
| Right | Operate the current Runner Browser or preview workspace files read-only |

Team sessions show task completion, member state, and errors. The input supports `/agents`, `/teams`, `/skills`, `/deep-research`, and other commands, plus model and thinking-effort selection.

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

- The Web Runner is the task conversation, and child Agents belong to it. Use `send_actor_message` and `interrupt_actor` for child-Agent input and stopping; root uses its own stream and cancellation requests.
- Route WebSocket stream events by request `id`. Reject pending requests and show an error when a connection closes or fails; the message queue must remain usable.
- Reuse shared message lifecycle and stream/command presentation models. Web components own styling only; render model Markdown without raw HTML and show user input and tool arguments as plain text.
- Read-only commands such as `/teams` must not create a Runner. The gateway persists model and effort choices in workspace configuration; `/agent-type` changes only the default for future sessions.
- Browser actions operate the current Runner Playwright session. Show real pages through the gateway's live view and interaction APIs rather than an iframe or virtual tab. File reads must pass gateway workspace path checks.
- The gateway is local-only. Any remote-access feature requires designed authentication and access control.

## Verification

Run from the repository root:

```bash
conda activate juice-agents
cd frontend
npm test --workspace juice-web
npm run typecheck --workspace juice-web
npm run build --workspace juice-web
```

When changing gateway or Browser behavior, also run the relevant tests under `tests/adapters/web_gateway/`.
