# juice-agents

> [简体中文](README.zh-CN.md)

A lightweight Python agent framework for building ReAct and CodeAct agents with **persistent sessions**, **workflow orchestration**, and **multi-agent collaboration**.

**Current version:** `0.1.0` (development release)

## Core Features

| Feature | Description |
| --- | --- |
| 🔄 **Session Resume** | Resume agents, workflows, and tasks after process restart |
| 🎯 **Multiple Agent Modes** | `agent`, `plan`, `group`, `team` — one runtime, four collaboration patterns |
| 🧠 **Exploration Strategies** | Greedy, Best-of-N, Beam Search, Tree-of-Thoughts for multi-path reasoning |
| 🌐 **Workflow Orchestration** | Branch, parallelize, loop, and checkpoint complex workflows |
| 🛠️ **Rich Toolset** | Shell, Python, file, web, browser, image, MCP, and custom tools |
| 💾 **Persistent State** | Sessions, workflows, background tasks, and team boards survive restarts |
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
```

Configure API credentials for online models:

```bash
mkdir -p .juice
cp sdk/src/juice_agents/_assets/config.example.yaml .juice/config.yaml
cp .env.example .env
# Edit .env with your API keys
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
```

### Agent Modes

| Mode | Best For |
| --- | --- |
| `agent` | Single agent execution with optional delegation |
| `plan` | Investigate and plan before approval and execution |
| `group` | Root agent coordinating specialist agents |
| `team` | Persistent task board with member claims and messages |

### CLI and Web

```bash
./juice       # Terminal interface
./juice-web   # Local web workbench (localhost only)
```

### Exploration Strategies

Control how agents explore solution spaces with `/strategy` in CLI or SDK:

```bash
# CLI: Switch strategies dynamically
/strategy reset                           # Default greedy (single path)
/strategy breadth=5 depth=1               # Best-of-N: generate 5, select best
/strategy breadth=3 depth=5 beam_width=3  # Beam Search: keep 3 parallel paths
/strategy breadth=3 depth=5 beam_width=1  # Single-path exploration
```

```python
# SDK: Configure exploration strategy
from juice_agents.core.agent.context.strategies import TreeOfThoughtsStrategy

strategy = TreeOfThoughtsStrategy(
    breadth=3,        # Generate 3 candidates per step
    depth_limit=5,    # Explore up to 5 layers deep
    keep_top_k=2      # Keep top-2 paths at each step
)
agent.set_exploration_strategy(strategy)
```

| Strategy | Configuration | Use Case |
| --- | --- | --- |
| **Greedy** | `breadth=1, depth=1` | Fast, deterministic decisions |
| **Best-of-N** | `breadth=N, depth=1, beam_width=1` | One-shot candidate generation |
| **Beam Search** | `breadth=N, depth=D, beam_width=N` | Parallel path exploration |
| **ToT-style** | `breadth=N, depth=D, beam_width=1` | Deep single-path with backtracking |

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

## Development

Run tests:

```bash
conda activate juice-agents
python -m pytest tests -q
cd frontend && npm test --workspaces --if-present
```

Contributions should include tests, documentation updates, and verification results. See [AGENTS.md](AGENTS.md) for development guidelines.

## Module Documentation

- [SDK](sdk/README.md) · [Adapters](adapters/README.md) · [Examples](examples/README.md)
- [Core](sdk/src/juice_agents/core/README.md) · [Workflow](sdk/src/juice_agents/core/workflow/README.md)
- [CLI](frontend/cli/README.md) · [Web](frontend/web/README.md) · [Frontend Shared](frontend/shared/README.md)

## License

[Apache License 2.0](LICENSE)
