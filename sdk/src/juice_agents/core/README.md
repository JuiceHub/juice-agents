# Core Runtime

> [简体中文](README.zh-CN.md)

The core runtime provides the foundation for building, executing, and managing intelligent agents with persistent state and controlled execution.

## Architecture

```text
Registry (static config) → Manager (runtime state) ← Runner (execution)
```

**Separation of concerns:**

- **Registry**: Resolve, validate, and instantiate objects from declarations
- **Manager**: Own lifecycles, persistence, cancellation of runtime objects
- **Runner**: Compose managers and orchestrate request execution

**Key principle:** Registry has no live state, Manager owns mutable objects, Runner schedules requests. Core never depends on `adapters/`.

## Core Modules

### Execution Layer

| Module | Responsibility |
| --- | --- |
| [Agent](agent/README.md) | ReAct/CodeAct protocols, session management, context compression |
| [Runner](runner/README.md) | Unified request entry point, mode orchestration, manager composition |
| [Managers](managers/README.md) | Tool/Workflow/Task lifecycle ownership, persistence, cancellation |
| [Permissions](permissions/README.md) | Authorization, policy enforcement, approval flows |

### Declaration Layer

| Module | Responsibility |
| --- | --- |
| [Registry](registry/README.md) | Static declarations, validation, instantiation |
| [Prebuilt](prebuilt/README.md) | Built-in agent, tool, and workflow configurations |
| [Group](group/README.md) | Multi-agent group declarations and coordination |
| [Team](team/README.md) | Persistent team boards, task management, member collaboration |

### Service Layer

| Module | Responsibility |
| --- | --- |
| [Models](models/README.md) | LLM provider integration, streaming, structured output |
| [Memory](memory/README.md) | Persistent knowledge base, case-insensitive text search, retrieval |
| [Workflow](workflow/README.md) | Python workflow scripts, inner agents, artifact storage |
| [Cron](cron/README.md) | Scheduled tasks, recurring jobs, background execution |

## Execution Flow

```text
User request
  ↓
Runner.stream()
  ↓
AgentManager.acquire() → Agent instance + session restore
  ↓
Agent.step() → reasoning loop
  ↓
RunnerContext.execute_tools()
  ↓
ToolManager.execute_for_agent()
  ↓ (permission check, binding, execution, audit)
Observations → Agent
  ↓
AgentManager.checkpoint() → persist session
  ↓
Runner emits events → User
```

## Agent Modes

All modes share the same execution path, differing only in configuration:

| Mode | Pattern | Use Case |
| --- | --- | --- |
| **agent** | Single agent, optional delegation | Direct task execution |
| **plan** | Investigate → Plan → Approve → Execute | Large changes, multi-step work |
| **group** | Root coordinates specialists | Parallel work, domain experts |
| **team** | Persistent board, member claims | Long-running collaboration |

## Persistence

Runtime state lives in `.juice/` within the workspace:

```text
.juice/
├── config.yaml           # Workspace configuration
├── runners/              # Runner sessions
│   └── <runner_id>/
│       ├── manifest.json # RunnerConfig, mode, state
│       ├── agents/       # Agent session snapshots
│       ├── workflows/    # Workflow artifacts
│       └── plans/        # Plan artifacts
├── workflows/            # Workspace workflow scripts
├── skills/               # Custom skills
├── memory/               # Knowledge base
└── teams/                # Team boards and tasks
```

**Atomicity:** All writes are atomic. Recovery handles partial writes and stale state.

## Development Boundaries

### Registry

- Resolves declarations from YAML, Python, or built-ins
- Validates schemas and dependencies
- Instantiates fresh objects on demand
- **Never** holds live sessions or execution state

### Manager

- Owns live Agent, Tool, Workflow, Task instances
- Handles lifecycle: create, bind, execute, checkpoint, cancel, release
- Persists state atomically
- Provides recovery and reconciliation

### Runner

- Composes managers for a single request
- Schedules root and member agent turns
- Enforces permission boundaries
- Emits progress events
- **Never** directly instantiates domain objects

### Adapter

- Maps stdio/HTTP/WebSocket protocols to SDK
- Lives in `adapters/`, outside SDK package
- SDK core must not import adapters

## Capabilities and Policies

**Capabilities** enable agent features:

```yaml
capabilities:
  workflows:
    enabled: true
  self_evolution:
    enabled: true
  planning:
    mode: "full"  # full | read_only | disabled
```

**Policies** restrict agent actions:

```yaml
tool_policy:
  read_only: true
  read_only_exceptions: ["ask_user", "submit_output"]
  allowed_tools: ["read_file", "list_directory", "web_search"]
  allowed_agent_names: ["researcher", "analyzer"]
```

Capabilities are agent powers; policies are runner-enforced constraints.

## Concurrency

- One runner handles one request at a time
- Agents are reentrant: same Agent instance never executes concurrently
- Tools may run in parallel (within runner concurrency limit)
- Background tasks run in dedicated workers
- Workflows can parallelize inner agents

## Testing

```bash
conda activate juice-agents
python -m pytest tests/core -q
```

Tests verify:
- Registry has no live state
- Manager owns lifecycle
- Runner doesn't directly instantiate domain objects
- Persistence is atomic
- Recovery handles failures
- Cancellation propagates correctly

Add tests in the corresponding `tests/core/<module>/` directory when changing boundaries.

## Extension Points

- **Custom Agents**: Subclass `MultiStepAgent`
- **Custom Tools**: Implement `Tool` protocol
- **Custom Workflows**: Write `.juice/workflows/<name>.py`
- **Custom Skills**: Add `.juice/skills/<name>.md`
- **Custom Modes**: Register `RunnerConfig` with capabilities and policies

See module READMEs for detailed extension guides.
