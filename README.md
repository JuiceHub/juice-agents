# juice-agents

> [简体中文](README.zh-CN.md)

A lightweight Python agent framework for building ReAct and CodeAct agents with **persistent sessions**, **workflow orchestration**, and **multi-agent collaboration**.

**Current version:** `0.1.0` (development release)

## Core Features

| Feature | Description |
| --- | --- |
| 🔄 **Session Resume** | Resume agent sessions after process restart |
| 🎯 **Multiple Agent Modes** | `agent`, `plan`, `group`, `team` — one runtime, four collaboration patterns |
| 🧠 **Per-step Candidate Selection** | Generate multiple outputs, score valid candidates, and execute one |
| 🌐 **Workflow Orchestration** | Run Python workflows with inner agents and saved artifacts |
| 🛠️ **Rich Toolset** | Shell, Python, file, web, browser, image, MCP, and custom tools |
| ⚡ **Concurrent ReAct Actions** | Actions in one step run concurrently by default; dependent work belongs in later steps |
| 💾 **Persistent State** | Sessions, workflow artifacts, background task records, and team boards survive restarts |
| 🚀 **Multiple Interfaces** | Python SDK, terminal CLI, and local web workbench |

```text
User request → Runner → Agent / Workflow / Team → Tools
                    │                              │
                    └──── persistence, approval, audit ────┘
```

## Durable inbox and autonomous rounds

User input, Team messages/assignments and background task notifications all enter
`AgentManager.post(agent_id, attachment)`. Each Agent runs rounds serially;
different Agents run concurrently. A persistent Agent returns to idle when its
round finishes, and a new message wakes it without an open foreground request.

```text
post → agents/<agent_id>/inbox.json → session + round checkpoint → producer ack
                 ↓                         ↓
          message_received       round_start → steps → round_end
```

The inbox is a durable pending queue, not conversation history. Entries leave
only after the receiving session is saved and its producer is acknowledged.
The backend publishes `message_received` after accepting input, so CLI/Web show
background completion even while the owner is still computing its reply.
`round_start.data.input` and saved session attachments restore child tasks and
messages; snapshots also expose `pending_messages` during a running call.
Frontends display these backend facts; scheduling, delivery and task
outcomes remain backend responsibilities.

`run/stream` activate scheduling automatically and wait for their own round.
For background hosts, bind `subscribe_events` and `set_ask_handler` before calling
`runner.activate()`. Use `runner.cancel_round(round_id)` for a single round;
independent background jobs use `async_task_stop`. Release resources with `close()`.
Runner, Agents, workflows and background jobs execute through native coroutines.
`ModelManager` owns model calls, request events, usage and connection cleanup;
`TeamManager` owns member dispatch and reconciliation. Synchronous entry points
adapt complete operations at the SDK boundary. Cancellation interrupts request setup, stream reads and
SDK retry waits, and waits for request cleanup before publishing a round result.
Recovery resumes active round budgets and deduplicates committed messages. One
running Runner owns a session; tool side effects are not guaranteed exactly once.

Runtime managers live together in [`core/managers/`](sdk/src/juice_agents/core/managers/README.md):
Agent, Model, Tool, Workflow, AsyncTask and Team. `core/agent/` retains Agent protocols and context.

## Async SDK

```python
from juice_agents import Runner

async def answer(workspace):
    async with await Runner.acreate(base_dir=workspace) as runner:
        return await runner.arun("Explain this project")
```

## Lifecycle and public events

Agent instances follow `idle → running → idle/released`; independent round results
are `completed / failed / cancelled`. Manager alone commits terminal results and
publishes steps/results after successful persistence. `Runner.run()` returns
successful output or raises `RoundFailedError / RoundCancelledError` with `.result`;
`run_round()` returns the full `RoundResult`.

Public events use `kind + data` with event, Runner, Agent, round and step identity.
Gateway forwards each event once through its permanent subscription; CLI/Web share
a reducer and presenter. Closing an observer detaches it; `cancel_round()` produces
a real cancellation only after execution unwinds, while `close()` stops the Runner.
Recovery accepts Runner v6 / Agent v2 / Context v3; old files are retained and rejected.

The CLI keeps message blocks and rendered rows in memory, without a separate
display journal. Resume rebuilds them from backend session and pending-message
snapshots. Permanent stdio notifications omit `request_id`; the CLI routes them
to its always-bound handler even while a foreground request is active.

## Quick Start

### Installation

Requires Conda. Run from repository root:

```bash
conda env create -f environment.yml
conda activate juice-agents
python -m pip install -e ./sdk
python -m pip install -r adapters/requirements.txt
npm ci --prefix frontend
./install-juice
```

The local installer binds this checkout and the active Python/Node executables
to `~/.local/bin/juice` (keep that directory on your shell's `PATH`). After this
one-time setup, daily use needs neither this checkout as cwd nor Conda activation.
Re-run the installer after moving the checkout or replacing either interpreter.
Startup checks dependencies; install or repair them explicitly with the commands
above. It never installs packages or edits lock files during a normal launch.

If the launch directory was deleted, `cd /path/to/existing/project` before retrying
`juice`. The launcher diagnoses an unavailable cwd before starting interpreters;
only missing Python modules produce dependency installation advice. Other import
failures retain their traceback.

Configure shared settings and credentials for online models:

```bash
mkdir -p ~/.juice
cp .env.example ~/.juice/.env
# Edit ~/.juice/.env with your API keys
# Optional: put shared model/runtime overrides in ~/.juice/config.yaml
```

The default `doubao_lite` model uses `doubao-seed-2-1-lite-260915` on
Volcengine Ark. Select another configured model with `/model` when needed.

The CLI shares Up/Down input history across Runners through
`~/.juice/cli_history.jsonl`; see the [CLI guide](frontend/cli/README.md#common-operations).
The CLI updates one tool record from `tool_start` to `tool_end`, matched by
`call_id`. Parameters show only a short, single-line summary in the title.
Results and caught exceptions stay inside that record; ordinary
results preview six terminal rows after wrapping, and explicit failure details remain fully visible.
Status comes from execution fields, never from result text. Complete results
remain in session data, and restored history uses the same tool style.

Browser tools use Playwright Chromium by default; install it with
`python -m playwright install chromium`. The CLI passes its Node.js executable to
the gateway for the Playwright driver unless `PLAYWRIGHT_NODEJS_PATH` is already
set. For local Chromium and standalone SDK setup, see the
[browser setup](sdk/src/juice_agents/core/agent/README.md#browser-setup).
The built-in `web_search` uses Bing by default. See the
[Agent documentation](sdk/src/juice_agents/core/agent/README.md#web-search) for engine selection.

### First Program

```python
from juice_agents import Runner

runner = Runner.create(base_dir=".", runner_config="agent", permission_mode="default")

# Stream execution events
try:
    for event in runner.stream("Analyze this project's module boundaries"):
        print(event)
    runner_id = runner.runner_id
finally:
    runner.close()

# Resume the same session later (even after process restart)
runner = Runner.resume(base_dir=".", runner_id=runner_id)
try:
    print(runner.run("Turn the recommendations into an implementation order"))
    print(runner.token_usage())  # generation / reward / total
finally:
    runner.close()
```

`Runner.create/resume` are the public session entry points. Pass the workspace as
`base_dir`, bind UI callbacks with `runner.set_ask_handler(handler)` after either
entry point, and release resources with `close()`. Scheduled prompts use
[`core.cron`](sdk/src/juice_agents/core/cron/README.md), with read-only CLI idle
polling until a task is due.

Agents forward model usage to `ModelManager`, which persists and totals calls in
`agents/model_usage.jsonl`; Runner delegates to it. Totals include rejected
candidates and survive resume. Known counts and `missing_usage_requests` remain
separate; no token or cost estimates are made. See [Runner](sdk/src/juice_agents/core/runner/README.md#token-usage).

### Agent Modes

| Mode | Best For |
| --- | --- |
| `agent` | Single agent execution with optional delegation |
| `plan` | Investigate and plan before approval and execution |
| `group` | Root agent coordinating specialist agents |
| `team` | Persistent task board with member claims and messages |

Mode switches preserve the current ReAct/CodeAct protocol unless a new
`agent_type` is supplied.
ReAct repairs repeated closing `</actions>` tags at the end of a model response;
other trailing content remains invalid.

### CLI and Web

```bash
cd /path/to/project
juice                         # This directory is the workspace
juice --workspace /other/project
# From the framework checkout:
./juice-web   # Local web workbench (localhost only)
```

```text
Configuration: SDK defaults (or runtime_config_path)
                  → ~/.juice/config.yaml → <workspace>/.juice/config.yaml
                  → launch options
Credentials:   process environment > <workspace>/.env > ~/.juice/.env
Rules:         ~/.juice/AGENTS.md → Git root → startup directory
```

SDK defaults are bundled in `juice_agents/core/config/defaults.yaml`; keep
changes in user/project overrides. The SDK ships no demonstration Skills;
provide them through `.juice/skills`, `.agents/skills`, or configured external directories.

Connections are shared through `providers`; chat and GRM select independently
from `models`, while image generation uses `image_models`:

```text
providers → models       → /model (chat), /rm (GRM)
          → image_models → /image_model (images)
```

```yaml
providers:
  ark:
    backend: doubao
    api_base: https://ark.cn-beijing.volces.com/api/v3
    api_key_env: DOUBAO_API_KEY
models:
  doubao_lite:
    provider: ark
    provider_model_name: doubao-seed-2-1-lite-260915
image_models:
  seedream_4:
    provider: ark
    provider_model_name: doubao-seedream-4-0-250828
image:
  enabled: false
  model_name: seedream_4
```

`/image_model` opens the image picker; `/image_model seedream_4` selects directly.
Enable generation separately in `/config`; Plan mode always excludes it. Ark
supports generation and editing with independent image deployments. Move old
inline connection fields into `providers` and reference them with `provider`;
legacy entries report migration guidance and are never rewritten automatically.
See [configuration](sdk/src/juice_agents/core/config/README.md) for layering and
credential precedence, and [Runner](sdk/src/juice_agents/core/runner/README.md)
for explicit configuration reloads that preserve conversation and GRM state.

OpenAI/Ark image requests preserve HTTP(S) links and encode local images as
MIME-correct base64 data URLs at send time. Session state keeps local paths;
see [image input](sdk/src/juice_agents/core/models/README.md#image-input).

`--workspace` wins over `JUICE_WORKSPACE_DIR` and the startup directory; it must
exist. Root, child, Team and Workflow Agents use that workspace for tools,
Skills and `.juice` session state. Global config is managed as files; `/config`
and `/model` save only explicit project overrides. Missing credentials are
reported when a provider is needed; help and configuration remain available.

Sessions and their artifacts live in `.juice/sessions/<runner_id>/`.
In CLI and Web, `/clear` creates a blank session with the current model, effort,
execution mode, Agent type, permissions, strategy and reward model. During a
running round it waits in FIFO order; later messages follow it into the new
session. The transcript clears only after creation succeeds. CLI `/resume` lists
saved sessions with their first request. `/resume <runner_id>` restores the whole
Runner: main and child Agent histories, task records, and session state together.
The old `.juice/runners/` layout is neither
discovered nor migrated.

At each rule directory a nonempty `AGENTS.override.md` replaces `AGENTS.md`.
Non-Git workspaces read only their own project rules. Rules carry source/scope,
are capped at 32 KiB with a diagnostic, and are reloaded when a session resumes.
Agents must inspect deeper directory rules before editing there. Existing Git
worktrees work as ordinary workspaces; built-in CLI worktree management remains
unimplemented and reports that boundary explicitly.

### Runnable Examples

Every example has a default task: run its main file directly, including from an
IDE or an absolute path. Configure its model in
`examples/<name>/.juice/config.yaml`; provider keys can stay in the repository
root `.env`, shared by all examples (see [Examples](examples/README.md)).

```bash
python examples/agent_tot/__main__.py       # Three candidates per step, model scoring, winner execution
python examples/team_delivery/__main__.py  # Team collaboration on a built-in delivery task
python examples/agent_tot/__main__.py "Design a caching strategy" --reward-model <model_name>
```

The example directory stays its workspace regardless of the current directory.
Credentials resolve in this order: process environment, example `.env`, repository
root `.env`, then `~/.juice/.env`. This also applies when launched from another directory. Model settings
remain separate for each example; `--model <model_name>` selects a configured model.
`python -m examples.<name>` also works from the repository root. Runs show the
task, Runner ID, tool activity, and result; ToT also shows candidate scores and
the selected candidate. They use real models and require configured credentials.

### Exploration Strategies

Choose one output at each step, then continue from its execution result:

```text
Context → generate breadth candidates → validate → score → execute one → next step
```

```python
from juice_agents.core.agent.context.strategies import TreeOfThoughtsStrategy
from juice_agents.core.models.reward_models import GenerativeRewardModel

# The scorer owns its async SDK client; credentials come from OPENAI_API_KEY.
scorer = GenerativeRewardModel(model_name="your-model")
# await scorer.evaluate_batch(...) returns {"scores": [...], "usage": ..., "usage_calls": [...]}.
# scores has one finite number per valid candidate; higher wins, ties pick first.
agent.set_exploration_strategy(TreeOfThoughtsStrategy(breadth=3, reward_model=scorer))
```

The [model layer](sdk/src/juice_agents/core/models/README.md) provides one concrete
chat class, `ChatModel`, with `backend="openai"`, `"doubao"`, or `"anthropic"`.
It keeps `generate()` separate from reward scoring and includes complete examples.
Models expose native `async generate()` and `async evaluate_batch()` methods; use
`async with` / `aclose()` for owned connections. Agent/Runner retains its synchronous
entry points alongside `arun/astream/aclose`; the execution chain awaits models directly. SDKs handle network retries;
the chat layer retries only empty responses. Anthropic requests with
large output budgets use streaming even without a cancellation token.
`GenerativeRewardModel` (GRM) calls the provider SDK to generate JSON scores and parses
them into numbers without configuration files. To use a configured logical model
name, choose `GenerativeRewardModel.from_config("judge", config_context)` instead.
Exploration strategies use `BaseRewardModel`; custom scorers subclass it and
override `evaluate_batch`, following the same base-class convention as chat models.

| Configuration | Execution |
| --- | --- |
| Default, `breadth=1`, or no scorer | One model request per step, no scoring |
| `breadth=N` with a scorer | N outputs from the same context; only the winner executes |

The CLI configures the reward model independently of the candidate count:

```text
/models                 # List logical model names
/rm                     # Open the reward model picker
/strategy breadth=3     # Generate three candidates and execute the best
/rm <model_name>         # Alternatively, select a catalog model directly
/rm reset               # Clear scoring while keeping breadth
/strategy reset         # Return to breadth=1 while keeping the /rm choice
```

`/rm` opens a picker like `/model`, marks the current reward model, and offers
a clear option that preserves breadth. It reuses the `/models` catalog, connection
settings, and credentials without changing the generation model. With breadth greater than
one, one extra model request scores the valid candidates in a batch. Without a
scorer, execution stays linear. Configuration makes no model requests. `breadth`
must be a positive integer; `max_steps` still controls executed steps.

Manager persists all candidates, usage, scores, and rejected status. Normal messages
and compression follow the selected branch. Scorers are not serialized: `/rm`
choices stay in the current CLI process, including choices made before startup
or recovery in the same workspace. After restarting the CLI, configure `/rm`
again (or inject an SDK scorer). There is no parallel path execution or backtracking.

See [examples/agent_tot](examples/agent_tot) for complete examples.

## Architecture

```text
Registry (static config) → Manager (runtime state) ← Runner (execution)
```

- **Registry**: Resolve, validate, and instantiate objects
- **Manager**: Handle lifecycles, persistence, and cancellation
- **Runner**: Compose managers and schedule requests
- **Adapter**: Map stdio/HTTP/WebSocket protocols

All agent modes share the same execution path through Runner. State persists to `.juice/` in the workspace.

Restored and inherited sessions own independent nested state: changing their
outputs or metadata cannot mutate the source tree or snapshot.

Registries resolve local declarations first, then read-only built-ins. Viewing,
validating, constructing and running presets never saves default Agent YAML or
Team manifests. The first successful edit saves a complete local override;
deleting it restores the built-in. Pure built-ins cannot be deleted. `root` is
the entry point; `general` is a child Agent. Runner applies mode settings and
paths to memory copies and continues saving sessions, tasks and run records.
New tools are saved to ToolRegistry and added to the requesting Agent's declaration;
the current Agent can call them from its next step.
Root discovers child Agents dynamically through the same availability query used
by `agents_list` and dispatch. A newly saved eligible Agent is callable immediately;
its name and description appear in the next model step. Plan policy, Team
membership, and Runner/mode-local unloads still apply.

```text
read / run → Registry → local override → built-in fallback
edit       → validate → atomic save in .juice
```

Use `/agents [name]` and `/teams [name]` for discovery; `/resources` and its
`list_mode_resources` gateway API have been removed. Queries report `local` or
`builtin`; built-in Teams have `manifest_path: null`.

## Development

Run tests:

```bash
conda activate juice-agents
python -m pytest tests -q
npm test --prefix frontend --workspaces --if-present
npm run typecheck --prefix frontend --workspaces --if-present
npm run build --prefix frontend --workspaces --if-present
```

Contributions should include tests, documentation updates, and verification results. See [AGENTS.md](AGENTS.md) for development guidelines.

Keep tool-specific prompt rules and examples in each tool's own definition so
ReAct and CodeAct use the same contract. See the [tool creation contract](sdk/src/juice_agents/core/agent/README.md#tool-creation-contract).

## Module Documentation

- [SDK](sdk/README.md) · [Adapters](adapters/README.md) · [Examples](examples/README.md)
- [Core](sdk/src/juice_agents/core/README.md) · [Workflow](sdk/src/juice_agents/core/workflow/README.md)
- [CLI](frontend/cli/README.md) · [Web](frontend/web/README.md) · [Frontend Shared](frontend/shared/README.md)

## License

[Apache License 2.0](LICENSE)
