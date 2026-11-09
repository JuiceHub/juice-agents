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

SDK imports do not resolve the process cwd. Pass an absolute workspace to
`ConfigurationContext.from_workspace()` to read its configuration even if the
process's former cwd was deleted. Implicit workspace paths require a valid cwd.

Model entries reference `providers`, which own backend, endpoint and credential
source. Chat and GRM select independently from `models`; image generation uses
`image_models` and `image.model_name`, with `image.enabled: false` by default.
Providers accept optional `api_base_env`, following the credential precedence
above. Built-in Claude providers use `ANTHROPIC_BASE_URL`. After editing settings,
call `runner.reconfigure(reload_config=True)` at idle to retain history and
exploration choices while refreshing connections. See [configuration conventions](src/juice_agents/core/config/README.md).

## Quick Start

### Single Agent

```python
from juice_agents import Runner

runner = Runner.create(
    base_dir=".",
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

Persistent sessions and their task, browser, and image artifacts live in
`.juice/sessions/<runner_id>/`. `Runner.create()` starts with empty context;
`Runner.resume()` restores a previous session. Historical `.juice/runners/`
data is not loaded or migrated.

```python
# First session
runner = Runner.create(base_dir=".", runner_config="agent")
runner.run("Start analyzing the codebase")
runner_id = runner.runner_id
runner.close()

# After restart (even different process)
runner = Runner.resume(base_dir=".", runner_id=runner_id)
runner.run("Continue with the implementation")
```

### Autonomous execution

```python
runner = Runner.resume(base_dir=".", runner_id=runner_id)
unsubscribe = runner.subscribe_events(lambda event: print(event))
runner.set_ask_handler(ask_handler)  # Bind interactive capabilities first.
runner.activate()                  # Idempotent; resume queued/active rounds.
# To stop a round: runner.cancel_round(round_id).
# To shut down: runner.close(), then unsubscribe() after cleanup finishes.
```

`run/stream` also activate automatically. User, Team and async completion messages
share a durable Agent inbox, stable message IDs and serial per-Agent rounds.
`round_start/round_end` carry independent round IDs; subscriptions also receive
rounds started after a foreground request ends. Cancellation persists before it
returns, interrupts synchronous children, and leaves independent async tasks running.
A functional invocation finishes its work before returning and cannot launch detached work.


### Round results and event contract

```python
from juice_agents import RoundFailedError, RoundCancelledError

result = runner.run_round("Review this change")  # Retains partial failed/cancelled output.
print(result.status, result.reason, result.output)
# runner.run(...) returns output on success, otherwise raises with .result.
```

| Public events | Main data fields |
| --- | --- |
| `agent_state` | `status: idle/running/released`, lifecycle and lightweight round summary |
| `round_start / round_end` | Input message identity / `result: {round_id,status,output,reason,error}` |
| `step_start / step_end` | Step identity / committed `step` snapshot |
| `model_start / model_end` | Same `model_call_id`, `purpose: generation/scoring/compression` |
| `thinking`, `tool_start / tool_end` | Thought content / same framework `call_id` |
| `workflow_progress / team_update / message_deferred` | Call identity, business progress and reassignment |

Every event carries `event_id`, a seconds-based `timestamp`, Runner/Agent identity and
applicable `round_id/step_num`. `subscribe_events()` also observes child Agents; the
main conversation consumes root only. `describe_agent_sessions(agent_id=...)` reads
history with round, step and tool identities on demand. Closing `stream()` detaches
its observer. Models expose native async methods and async connection cleanup. The synchronous
Agent/Runner bridges each complete invocation once; cancellation interrupts
request setup, stream reads and retry waits; see [model conventions](src/juice_agents/core/models/README.md).
Explicit cancellation saves intent, stops model/pending tool work and
waits for started calls to unwind; it never overwrites an already committed result.
The step budget retains one final submit opportunity; otherwise the result is
`failed/max_steps_exceeded` with partial output. Current formats are Runner v6,
Agent v2 and Context v3; old sessions are rejected and their files preserved.

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

runner = Runner.create(base_dir=".", runner_config="plan")
runner.set_ask_handler(ask_handler)
runner.run("Refactor the authentication module")
```

The agent writes `plans/root.md` under the Runner state directory and requests approval through `exit_plan`. The `ask_handler` also handles agent questions and permission requests; applications should display the request options and return the user's choice.

### Group Coordination

```python
runner = Runner.create(
    base_dir=".",
    runner_config="group",
    permission_mode="default"
)

# Root coordinates specialist agents
runner.run("Research competitors and write a market analysis report")
# Root spawns: researcher, analyzer, writer
```

### Team Collaboration

```python
runner = Runner.create(
    base_dir=".",
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

Tool events use `tool_start` / `tool_end` with the same `call_id`. The end event
retains the existing execution status, observation and caught exception; result
text never determines failure. CodeAct saves one public Python wrapper alongside
its internal audit records so history can reproduce the live tool display.
See the [Agent event contract](src/juice_agents/core/agent/README.md).

Agents have access to built-in tools, including:

- Filesystem: `read`, `write`, `edit`, `glob`, `grep`
- Execution: `shell`, `python`
- Web: `web_search` (Bing by default), `browser_open_url`, `browser_screenshot`
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
runner = Runner.create(
    base_dir=".",
    runner_config="agent",
    permission_mode="accept"
)
```

## Cron and Scheduling

Schedule recurring tasks:

```python
from juice_agents.core.cron import create_cron_task, execute_due_cron_tasks

create_cron_task(".", "0 9 * * *", "Write a daily report")
# The host owns the clock and calls this when it wants to execute due jobs.
results = execute_due_cron_tasks(".", runner_config="agent")
```

## API Reference

### Runner Creation

Use `Runner.create/resume` directly and pass the workspace as `base_dir`.
`runtime_config_path` selects the base configuration; `runtime_overrides` adds
launch-only overrides. Bind `runner.set_ask_handler(handler)` after creation or
resume: UI callbacks are process-local and are never persisted. Always call
`runner.close()` in `finally` when finished. See
[Cron](src/juice_agents/core/cron/README.md) for scheduling APIs.

```python
runner = Runner.create(
    base_dir=".",
    runner_config="agent",  # or "plan", "group", "team", or custom config
    permission_mode="default"
)
```

### Runner Resume

```python
runner = Runner.resume(base_dir=".", runner_id="run_abc123")
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
runner.close()    # Release resources
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
Runner.create(base_dir=..., runner_config=...) / Runner.resume(...)
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

Async hosts use `await Runner.acreate/aresume(...)`, `await runner.arun/arun_round(...)`,
`async for event in runner.astream(...)` and `await runner.aclose()` (or `async with`).
Sync methods share the same coroutine execution chain. ModelManager owns requests,
usage and clients; TeamManager owns member dispatch and reconciliation.
