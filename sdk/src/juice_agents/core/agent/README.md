# Agent

> [简体中文](README.zh-CN.md)

`core/agent` defines the Agent protocol and manages their runtime lifecycle. Agents are the core reasoning units that execute tasks using ReAct or CodeAct strategies.

## Core Capabilities

| Feature | Description |
| --- | --- |
| 🧠 **ReAct & CodeAct** | Thought-action reasoning (ReAct) or Python code execution (CodeAct) |
| 💾 **Session Persistence** | Resume conversations and context after process restart |
| 🔄 **Context Compression** | Automatic context window management with incremental compression |
| 🛠️ **Rich Toolset** | 20+ built-in tools: shell, filesystem, web, browser, image, MCP, Workflow, and more |
| 📎 **Attachments** | User messages, task notifications, and async results injection |
| 🎯 **Delegation** | Root agents can spawn and coordinate helper agents |
| 🔒 **Controlled Execution** | All tool calls go through permission and audit boundaries |

## Agent Types

### ReAct Agent

Reasoning and acting in structured steps:

```xml
<thought>I need to check the current directory structure</thought>
<actions>[{"name": "list_directory", "args": {"path": "."}}]</actions>
```

**Best for:** Multi-step reasoning, investigation, planning, complex decision-making.

### CodeAct Agent

Execute Python code with thought and action:

```xml
<thought>I need to check the project structure and read the README</thought>
<code>
import os
files = os.listdir('.')
print(f"Found {len(files)} files")

# Use tools through runtime
result = tools.read_file('README.md')
print(result)
</code>
```

**Best for:** Data processing, rapid iteration, computational tasks, algorithmic workflows.

## Architecture

```text
AgentBinding → AgentRegistry.instantiate() → AgentManager.acquire()
                                              → ManagedAgent
                                                 • session restore
                                                 • context binding
                                                 • checkpoint
                                                 • cancellation
```

**Key principle:** Agents are stateless step functions. `AgentManager` owns their lifecycle:

1. **Instantiate**: Fresh Agent object from Registry
2. **Restore**: Load session snapshot from disk
3. **Bind**: Attach `RunnerContext` for controlled tool execution
4. **Execute**: Process user request through ReAct/CodeAct loop
5. **Checkpoint**: Save session state
6. **Release**: Persist and release (functional) or retain (persistent)

## Tool Execution Flow

Agents don't own tools directly. All actions go through the Runner's permission boundary:

```text
Agent.step()
  → RunnerContext.execute_tools(actions)
     → Runner.tool_manager.execute_for_agent()
        → Permission check
        → Tool binding
        → Execution
        → Audit logging
        → Return observations
```

This ensures:
- Unified permission control
- Cancellation propagation
- Audit trail for all actions
- No Agent-owned worker pools or executors

## Built-in Tools

| Category | Tools |
| --- | --- |
| **Filesystem** | `read_file`, `write_file`, `edit_file`, `list_directory`, `search_files` |
| **Execution** | `shell`, `python_repl`, `python_script` |
| **Web** | `web_search`, `web_fetch`, `browser_navigate`, `browser_screenshot` |
| **Memory** | `memory_create`, `memory_update`, `memory_search`, `memory_list` |
| **Agent** | `agent_spawn`, `agent_list`, `agent_message` |
| **Workflow** | `workflow_call` |
| **Skills** | `skill_view`, `skills_list`, `skill_manage` |
| **Output** | `submit_output`, `ask_user` |
| **Task** | `task_create`, `task_update`, `task_list` |
| **Team** | `team_finish`, `team_members`, `team_broadcast` |

Tools are dynamically available based on Agent capabilities and Runner policy.

## Context Management

### Context Tree

Agent context is managed as a tree structure (`ContextTree`) that tracks:

```text
Root (TaskStep)
  ├─ ActionStep 1 (thought + tool calls + observations)
  ├─ ActionStep 2
  │   ├─ Branch A (exploration path)
  │   └─ Branch B (alternative path)
  └─ ActionStep 3
```

**Core concepts:**

- **ContextNode**: A single step in the conversation (TaskStep or ActionStep)
- **ExplorationPath**: A complete trajectory from root to leaf (for Best-of-N)
- **AgentStep**: Abstract base for all step types

### Agent Steps

**TaskStep** - User task input:
```python
TaskStep(
    task="Analyze the codebase",
    task_images=[...],        # Optional images
    attachments=[...]         # Runtime attachments
)
```

**ActionStep** - Agent reasoning and tool execution:
```python
ActionStep(
    step_num=1,
    thought="I need to list files first",
    tool_calls=[{"name": "list_directory", "args": {"path": "."}}],
    observations=["file1.py\nfile2.py\n..."],
    code_action="",           # For CodeAct agents
    reasoning_content="",     # Extended reasoning
    round_outcome="continue"  # continue|submitted|failed; only submit_output ends a round, empty actions continue to the next step
)
```

**SummaryStep** - Compressed history:
```python
SummaryStep(
    content="[Compressed summary of previous 10 steps...]"
)
```

### Session Persistence

Sessions persist across restarts with full fidelity:

```python
# First run
agent = manager.acquire("root", binding, workspace)
result = agent.step(user_input)
manager.checkpoint(agent)  # Saves context tree to disk

# After restart (even different process)
agent = manager.acquire("root", binding, workspace)
# Context tree automatically restored:
#   - Full conversation history
#   - Tool execution results
#   - Compressed sections (if any)
#   - Node structure and metadata
result = agent.step(continuation_input)
```

**Checkpoint includes:**
- `ContextTree` with all nodes
- System prompts (static + dynamic)
- Exploration paths and selected path
- Compression state
- Agent-specific configuration

**Storage:** `.juice/runners/<runner_id>/agents/<agent_id>/session.json`

### Message Serialization

Steps are converted to model messages:

```python
# TaskStep → user message
{"role": "user", "content": [{"type": "text", "text": "<task>...</task>"}]}

# ActionStep → assistant + user feedback
{"role": "assistant", "content": [{"type": "text", "text": "<thought>...</thought>..."}]}
{"role": "user", "content": [{"type": "text", "text": "<observations>...</observations>"}]}
```

The tree tracks the current active path and converts it to the message sequence sent to the model.

## Session Persistence

Sessions persist across process restarts with full fidelity:

```python
# First run
agent = manager.acquire("root", binding, workspace)
result = agent.step(user_input)
manager.checkpoint(agent)  # Saves to disk

# After restart (even different process)
agent = manager.acquire("root", binding, workspace)
# Session automatically restored
result = agent.step(continuation_input)
```

**What's persisted:**
- Complete ContextTree (all nodes and paths)
- System prompts (static + dynamic)
- Compression state
- Agent configuration

**Storage location:** `.juice/runners/<runner_id>/agents/<agent_id>/session.json`

**How it works:**
1. `manager.checkpoint()` atomically writes the session
2. On `acquire()`, manager loads snapshot from disk
3. Fresh Agent instance is created from Registry
4. Session is restored to the Agent
5. Agent continues from where it left off

## Context Compression

When approaching token limits, Juice automatically compresses history while preserving critical information.

### Compression Strategies

**CompactCompressStrategy** - Aggressive compression:
```python
# Compresses old steps into a single SummaryStep
# Keeps: current task, recent steps, unfinished actions
# Result: Maximum token savings
```

**RemainCompressStrategy** - Preserve recent context:
```python
# Keeps recent N steps intact
# Compresses only older history
# Result: Better context quality, less savings
```

**NoCompressStrategy** - Disabled:
```python
# No compression (fails when hitting limits)
```

### Incremental Compression

Compression is incremental - only new content since last compression is processed:

```text
Before compression:
  TaskStep → ActionStep1 → ... → ActionStep10

After first compression (compress steps 1-7):
  TaskStep → SummaryStep(1-7) → ActionStep8 → ActionStep9 → ActionStep10

After more steps + second compression:
  TaskStep → SummaryStep(1-7) → SummaryStep(8-12) → ActionStep13 → ActionStep14
```

**Never compressed:**
- Current TaskStep
- Steps after last compression
- Active observations
- Unfinished tool calls

**Compression trigger:**
- Approaching context window limit (configurable threshold)
- Manual trigger via compression API

**Storage:** Compressed state is checkpointed, so resumed sessions don't re-compress.

## Attachments

Runtime attachments inject information into Agent sessions:

```python
# User message
attachment = UserMessageAttachment(content="Additional context")
agent.attach(attachment)

# Task notification
attachment = TaskNotificationAttachment(
    task_id="task_123",
    status="completed",
    result={"output": "..."}
)
agent.attach(attachment)
```

Attachments appear in the Agent's next step as system messages.

## Module Structure

| File | Purpose |
| --- | --- |
| `agents.py` | `MultiStepAgent`, `ReActAgent`, `CodeActAgent` protocols |
| `manager.py` | `AgentManager` - lifecycle, snapshots, checkpoints |
| `attachments.py` | Runtime attachment protocol |
| `tools/` | Tool declarations, runtime binding, and adapters |
| `context/` | Context tree, compression, serialization |
| `prompts/` | System prompts and prompt segments |

## Development Conventions

- **Fresh instances**: `AgentManager.acquire()` always instantiates a fresh Agent from Registry, then restores session. Live Agents are never reconstructed in place.
- **No tool ownership**: Agents execute tools only through `RunnerContext`. They don't own `ToolManager`, thread pools, or executor instances.
- **Two lifecycles**: `persistent` Agents retain their instance; `functional` Agents are checkpointed and released after each request.
- **Atomic persistence**: Checkpoints are atomic writes. Include Runner/Agent IDs in all logs.
- **Plan mode**: Planning is a root Agent capability, not a special Agent type. There's no reserved `plan` Agent declaration.

Add lifecycle tests in `tests/core/agent/`, tool routing tests in `tests/core/managers/`.
