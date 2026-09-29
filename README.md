# juice-agents

> [简体中文](README.zh-CN.md)

`juice-agents` is a lightweight Python agent framework. Build ReAct and CodeAct agents with one runtime, call tools, orchestrate graphs, and resume sessions and tasks after a process restart.

The project is currently a `0.1.0` development release. Persisted state uses the current schema only; back up `.juice/` before upgrading.

## What it provides

| Capability | Use |
| --- | --- |
| Agents and tools | Shell, Python, file, web, browser, image, MCP, and other tools |
| Graphs | Branching, parallel work, loops, and checkpoints |
| Multiple agents | Built-in `agent`, `plan`, `group`, and `team` configurations |
| Persistence | Resumable sessions, graphs, background tasks, and Team boards |
| Entry points | Python SDK, terminal CLI, and local Web workbench |

```text
User request → Runner → Agent / Graph / Team → Tools
                    │                         │
                    └── sessions, tasks, approvals, and audit ──┘
```

## Install from source

Conda is required. Run these commands from the repository root; the environment file installs Python and Node.js.

```bash
conda env create -f environment.yml
conda activate juice-agents
python -m pip install -e ./sdk
python -m pip install -r adapters/requirements.txt
npm ci --prefix frontend
```

Before using an online model, copy the templates and add the credentials for your provider:

```bash
mkdir -p .juice
cp sdk/src/juice_agents/_assets/config.example.yaml .juice/config.yaml
cp .env.example .env
```

Do not commit `.env` or `.juice/`. Browser tools also require `python -m playwright install chromium`.

## First SDK program

```python
from juice_agents import Juice

juice = Juice(workspace=".")
runner = juice.runners.create(runner_config="agent", permission_mode="default")

for event in runner.stream("Analyze this project's module boundaries"):
    print(event)

# runner_id can restore the same session in a later process.
runner = juice.runners.resume(runner_id=runner.runner_id)
print(runner.run("Turn the recommendations into an implementation order"))
```

All four built-in configurations share the Runner execution path:

| Configuration | Best for |
| --- | --- |
| `agent` | One Agent executing directly, with optional delegation |
| `plan` | Investigating and planning before approval and execution |
| `group` | A root coordinating a group of specialist Agents |
| `team` | A persistent task board, member claims, and messages |

A Team is created or selected by root, then populated with members. Existing member definitions can be reused on a new board. See the [Runner](sdk/src/juice_agents/core/runner/README.md) and [Team](sdk/src/juice_agents/core/team/README.md) documentation for configuration and runtime rules.

## CLI and Web

```bash
./juice       # terminal interface
./juice-web   # local Web workbench
```

The Web gateway is local-only and has no remote authentication; it rejects non-loopback bind addresses. The Python SDK can be built independently. CLI, Web, and adapters currently run from the repository source and are not published as separate packages.

## Development conventions

```text
Registry (static declarations) → Manager (runtime objects and state) ← Runner (request scheduling)
```

| Boundary | Convention |
| --- | --- |
| Registry | Resolve, validate, and instantiate objects; never keep active sessions or execution state |
| Manager | Own Agent, tool, graph, and background-task lifecycles, persistence, and cancellation |
| Runner | Compose Managers and schedule requests; every mode uses the same execution path |
| Adapter | Map stdio / HTTP / WebSocket protocols; the SDK core must not import `adapters/` |

Agents call tools through the Runner context so permissions, cancellation, and audit apply to one request. `RunnerConfig` is the execution contract; a custom configuration cannot install another execution loop. Team tasks, messages, and state are persisted by Managers, and only root can create tasks or change task eligibility. Plan approval is initiated by the current Runner root; old Runner manifests are not migrated automatically.

Runtime state is written to `.juice/` in the caller workspace. New writes must be atomic, and cancellation, recovery, and failures must produce logs that can be associated with the Runner ID. Reuse existing interfaces, add behavior tests under `tests/`, and update affected module READMEs.

## Testing and contributing

```bash
conda activate juice-agents
python -m pytest tests -q
cd frontend && npm test --workspaces --if-present
```

Changes should include relevant tests, documentation updates, and verification results. Use GitHub Issues for general questions and feature requests. Report security issues through the repository's private vulnerability channel instead of a public issue.

The project is released under the [Apache License 2.0](LICENSE). Confirm that contributions can be distributed under that license; third-party dependencies retain their own licenses.

Before a release, run Python and frontend tests, type checks, and builds in a clean environment. Inspect SDK wheel files and metadata. Build public artifacts from a reviewed source snapshot without private Git history, then enable CI, secret scanning, dependency alerts, and private vulnerability reporting. SDK wheels do not contain CLI or Web code.

## Module documentation

- [SDK](sdk/README.md) · [Adapters](adapters/README.md) · [Examples](examples/README.md)
- [Core](sdk/src/juice_agents/core/README.md) · [Graph](sdk/src/juice_agents/core/graph/README.md)
- [CLI](frontend/cli/README.md) · [Web](frontend/web/README.md) · [Frontend shared](frontend/shared/README.md)
