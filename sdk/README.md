# Juice Agents SDK

> [简体中文](README.zh-CN.md)

The independently buildable Python SDK for creating intelligent agents with persistent sessions, workflow orchestration, and multi-agent collaboration.

**Package:** `juice-agents` | **Import:** `juice_agents` | **Python:** 3.11+

## Core Features

| Feature | Description |
| --- | --- |
| 🤖 **Agents** | ReAct and CodeAct agents with 20+ built-in tools |
| 🔄 **Session Resume** | Persist and restore conversations after process restart |
| 🌐 **Workflows** | Python function workflows with inner agents and artifacts |
| 👥 **Multi-Agent** | `agent`, `plan`, `group`, `team` — one runtime, four patterns |
| 🛠️ **Tool Ecosystem** | Shell, filesystem, web, browser, memory, MCP, and custom tools |
| 💾 **Persistent State** | Sessions, tasks, and team boards survive restarts |
| 🎯 **Controlled Execution** | Permission modes, approval flows, audit logging |

## Installation

```bash
conda activate juice-agents
python -m pip install -e ./sdk
```

Run from repository root. For production, build a wheel:

```bash
python -m build --sdist --wheel --outdir sdk/dist sdk
pip install sdk/dist/juice_agents-*.whl
```

**Configuration:** SDK defaults work without copying the model catalog. Keep common settings and credentials in `~/.juice/`, and add only project overrides when needed:

```bash
mkdir -p ~/.juice
# Edit ~/.juice/config.yaml for common settings, ~/.juice/.env for API keys.
# Optional project overrides: .juice/config.yaml and .env in the workspace.
```

```text
Configuration: SDK defaults / config_path → ~/.juice/config.yaml → workspace/.juice/config.yaml → startup overrides
Credentials:   process environment > workspace/.env > ~/.juice/.env
```

`ConfigurationContext` is the shared read path for runners, models and tools. Credentials never mutate the process environment; config caches detect file creation, deletion, modification and atomic replacement. Missing credentials are reported when a model or keyed tool is used, so listing configuration remains available. Project config writes preserve only project overrides.

Model entries accept an optional `api_base_env`: the named variable overrides YAML `api_base` using the credential precedence above. Built-in Claude models use `ANTHROPIC_BASE_URL`. See [configuration conventions](src/juice_agents/core/config/README.md).

## Quick Start

### Single Agent

```python
from juice_agents import Juice

juice = Juice(workspace=".")
runner = juice.runners.create(
    runner_config="agent",
    permission_mode="default"
)

# Streaming execution
for event in runner.stream("Analyze this project's structure"):
    print(event)

# Blocking execution
result = runner.run("Summarize the findings")
print(result)
```

### Session Resume

```python
# First session
runner = juice.runners.create(runner_config="agent")
runner.run("Start analyzing the codebase")
runner_id = runner.runner_id

# After restart (even different process)
runner = juice.runners.resume(runner_id=runner_id)
runner.run("Continue with the implementation")
```

### Plan Mode

```python
def ask_handler(request):
    # Present the question and options to the user and return their answer.
    print(request["question"])
    options = request.get("options", [])
    for option in options:
        print(f"{option['value']}: {option['label']}")
    answer = input("Enter an option value or feedback: ").strip()
    selected = [option for option in options if option["value"] == answer]
    return {"status": "answered", "selected": selected, "custom_response": "" if selected else answer}

juice = Juice(workspace=".", ask_handler=ask_handler)
runner = juice.runners.create(runner_config="plan")
runner.run("Refactor the authentication module")
```

The agent writes `plans/root.md` under the Runner state directory and requests approval through `exit_plan`. The `ask_handler` also handles agent questions and permission requests; applications should display the request options and return the user's choice.

### Group Coordination

```python
runner = juice.runners.create(
    runner_config="group",
    permission_mode="default"
)

# Root coordinates specialist agents
runner.run("Research competitors and write a market analysis report")
# Root spawns: researcher, analyzer, writer
```

### Team Collaboration

```python
runner = juice.runners.create(
    runner_config="team",
    permission_mode="default"
)

# Create persistent team board
runner.run("Create a team for web scraping project")
runner.run("Add tasks: design schema, implement crawler, test")

# Members claim and work on tasks
# Tasks persist across sessions
```

## Agent Modes

| Mode | Execution Pattern | Best For |
| --- | --- | --- |
| `agent` | Single agent with optional delegation | Direct task execution, Q&A |
| `plan` | Investigate → Plan → Approve → Execute | Large refactors, multi-file changes |
| `group` | Root coordinates specialist agents | Parallel research, domain expertise |
| `team` | Persistent board, member task claims | Long-running projects, async work |

All modes use the same `Runner` execution path and share tool, permission, and persistence infrastructure.

## Tools

Agents have access to built-in tools, including:

- Filesystem: `read`, `write`, `edit`, `glob`, `grep`
- Execution: `shell`, `python`
- Web: `web_search`, `browser_open_url`, `browser_screenshot`
- Memory: `memory_read`, `memory_write`, `memory_search`, `memory_forget`
- Collaboration: `agent_tool`, `team_task_create`, `send_message`
- Workflows and interaction: `workflow_run`, `submit_output`, `ask`

Tools are dynamically available based on agent capabilities and Runner policy.

## Workflows

Create Python function workflows:

```python
# .juice/workflows/research.py
WORKFLOW_METADATA = {
    "name": "research",
    "description": "Multi-step research workflow",
    "input_schema": {"query": "string"},
}

def run(ctx, args):
    """Workflow entry point."""
    query = args["query"]
    
    # Call inner agents
    search_result = ctx.agent(
        task=f"Search: {query}",
        instructions="Find relevant sources and summarize the findings.",
        phase="search"
    )
    
    analysis = ctx.agent(
        task=f"Analyze: {search_result}",
        instructions="Analyze the findings and write a report.",
        phase="analyze"
    )
    
    # Save artifacts
    ctx.artifact("analysis.md", analysis)
    
    return {"report": analysis, "sources": search_result}
```

Call a workflow through the Runner (or use `$workflow:research` in an agent prompt):

```python
result = runner.workflow_manager.call("research", {"query": "AI trends 2024"})
```

## Permission Modes

| Mode | Behavior |
| --- | --- |
| `default` | Request user approval for tools that require it |
| `accept` | Automatically approve those tools, subject to permission rules and Runner policy |

Configure at runner creation:

```python
runner = juice.runners.create(
    runner_config="agent",
    permission_mode="accept"
)
```

## Cron and Scheduling

Schedule recurring tasks:

```python
# UI owns the clock; SDK provides tick primitives
juice.cron.tick()

# Create a job with juice.cron.create(cron="0 9 * * *", prompt="Write a daily report")
# Jobs are stored in workspace .juice/scheduled_tasks.json
```

## API Reference

### Juice Client

```python
juice = Juice(workspace=".", runtime_config_path=None)
```

- `workspace`: Working directory for `.juice/` state
- `runtime_config_path`: Optional path to the runtime configuration file

### Runner Creation

```python
runner = juice.runners.create(
    runner_config="agent",  # or "plan", "group", "team", or custom config
    permission_mode="default"
)
```

### Runner Resume

```python
runner = juice.runners.resume(runner_id="run_abc123")
```

### Execution

```python
# Streaming
for event in runner.stream("Task description"):
    print(event)  # Events are dictionaries

# Blocking (consumes stream internally)
result = runner.run("Task description")
```

### Runner State

```python
runner.runner_id  # Unique ID for resume
runner.config     # Immutable RunnerConfig
runner.stop()     # Cooperative cancellation
```

## Package Boundary

The SDK wheel contains:
- `juice_agents` package
- Type stubs
- Prompts and read-only defaults at `juice_agents/core/config/defaults.yaml`

**Not included:**
- `adapters/` (protocol gateways)
- `frontend/` (CLI and web UI)
- Examples
- `.env` or real `config.yaml`
- `.juice/` runtime data

Configuration and state are written to the caller's workspace.
The SDK ships no demonstration Skills. Put writable Skills in `.juice/skills`,
or load read-only sources from `.agents/skills` and configured external directories.

## Architecture

```text
Juice Client
  └─ runners.create(runner_config) → Runner
       └─ AgentManager, ToolManager, WorkflowRunManager
          └─ Agents execute via RunnerContext
             └─ Tools, permissions, audit
```

**Key boundaries:**
- Registry: Static declarations, no live state
- Manager: Lifecycle, persistence, cancellation
- Runner: Request orchestration, manager composition
- Adapter: Protocol mapping (outside SDK)

See [Core](src/juice_agents/core/README.md) for detailed architecture.

## Development

Run tests:

```bash
conda activate juice-agents
python -m pip install -e ./sdk
python -m pytest tests/sdk -q
```

Build package:

```bash
python -m build --sdist --wheel --outdir sdk/dist sdk
```

Artifacts: `sdk/build/`, `sdk/dist/`, `sdk/src/*.egg-info/`

## Module Documentation

- [Core Runtime](src/juice_agents/core/README.md)
- [Agent](src/juice_agents/core/agent/README.md)
- [Runner](src/juice_agents/core/runner/README.md)
- [Workflow](src/juice_agents/core/workflow/README.md)
- [Registry](src/juice_agents/core/registry/README.md)
- [Team](src/juice_agents/core/team/README.md)

## Contributing

SDK development follows core conventions:
- Registry has no live state
- Manager owns lifecycle and persistence
- Runner composes managers, doesn't instantiate domain objects
- Atomic writes, recovery-safe persistence
- Tests in `tests/sdk/` and `tests/core/`

See [AGENTS.md](../AGENTS.md) for project guidelines.
