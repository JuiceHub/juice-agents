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
| 💾 **Persistent State** | Sessions, workflow artifacts, background task records, and team boards survive restarts |
| 🚀 **Multiple Interfaces** | Python SDK, terminal CLI, and local web workbench |

```text
User request → Runner → Agent / Workflow / Team → Tools
                    │                              │
                    └──── persistence, approval, audit ────┘
```

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

Configure shared settings and credentials for online models:

```bash
mkdir -p ~/.juice
cp .env.example ~/.juice/.env
# Edit ~/.juice/.env with your API keys
# Optional: put shared model/runtime overrides in ~/.juice/config.yaml
```

Browser tools require: `python -m playwright install chromium`

### First Program

```python
from juice_agents import Juice

juice = Juice(workspace=".")
runner = juice.runners.create(runner_config="agent", permission_mode="default")

# Stream execution events
for event in runner.stream("Analyze this project's module boundaries"):
    print(event)

# Resume the same session later (even after process restart)
runner = juice.runners.resume(runner_id=runner.runner_id)
print(runner.run("Turn the recommendations into an implementation order"))
print(runner.token_usage())  # generation / reward / total
```

Agents forward model usage to `AgentManager`, which persists and totals calls in
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

`--workspace` wins over `JUICE_WORKSPACE_DIR` and the startup directory; it must
exist. Root, child, Team and Workflow Agents use that workspace for tools,
Skills and `.juice` session state. Global config is managed as files; `/config`
and `/model` save only explicit project overrides. Missing credentials are
reported when a provider is needed; help and configuration remain available.

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
from openai import OpenAI

from juice_agents.core.agent.context.strategies import TreeOfThoughtsStrategy
from juice_agents.core.models.reward_models import GenerativeRewardModel

# Native SDK client; OpenAI reads OPENAI_API_KEY from the environment.
scorer = GenerativeRewardModel(model_name="your-model", client=OpenAI())
# evaluate_batch(...) returns {"scores": [...], "usage": ..., "usage_calls": [...]}.
# scores has one finite number per valid candidate; higher wins, ties pick first.
agent.set_exploration_strategy(TreeOfThoughtsStrategy(breadth=3, reward_model=scorer))
```

The [model layer](sdk/src/juice_agents/core/models/README.md) provides one concrete
chat class, `ChatModel`, with `backend="openai"`, `"doubao"`, or `"anthropic"`.
It keeps `generate()` separate from reward scoring and includes complete examples.
Chat calls retry transient failures and empty responses; invalid requests,
credentials, and unclassified errors fail immediately. Anthropic requests with
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
