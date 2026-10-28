# Registry

> [简体中文](README.zh-CN.md)

Registries own static definitions only. They are the boundary between workspace
declarations and fresh domain construction; runtime ownership starts only when
a Manager receives the new object.

```text
name / config ──> resolve() ──> validate() ──> instantiate() ──> Manager
                         static data only          fresh object
```

| Registry | Static responsibility | Runtime owner after construction |
| --- | --- | --- |
| `AgentRegistry` | Agent YAML and class selection | `AgentManager` |
| `ToolRegistry` | Tool declaration and class mapping | `ToolManager` |
| `WorkflowRegistry` | Workflow source validation | `WorkflowRunManager` |
| `SkillRegistry` / `PluginRegistry` | metadata discovery | requesting Manager |
| `TeamRegistry` | Team relationship declaration | Runner capability dispatch |

Registries do not retain sessions, pools, permission contexts, Runner
references or live objects. `instantiate()` always returns a fresh object;
execution, cancellation and persistence are Manager responsibilities.

Workspace declarations live under `.juice/agents`, `.juice/tools`,
`.juice/workflows`, `.juice/skills` and `.juice/teams`. They are never copied
into a Runner snapshot.

`ConfigurationContext.workspace_dir` is the project directory;
`ConfigurationContext.juice_root` is its `.juice` directory. `ToolRegistry`
binds file roots, Shell/Python execution, Skills and plan/todo paths when it
constructs fresh tools, including tools added at runtime. All construction
paths reuse this entry point; declaration normalization never injects machine
paths. Supply the same context to Agent and Tool registries when embedding:

```python
context = ConfigurationContext.from_workspace("/path/to/project")
tools = ToolRegistry(config_context=context)
read = tools.instantiate("read")  # relative paths refer to the project
```

`SkillRegistry(workspace_dir=...)` uses that directory for both local and
project external Skills. A custom `local_dir` changes writable storage only;
it never infers a project from arbitrary parent directories.
The SDK ships no built-in Skills. Optional read-only `builtin_dir` sources must
be passed explicitly; discovery precedence is local, explicit built-in, then
external. Editing a read-only source creates a local copy. Skill tests generate
minimal fixtures in temporary directories instead of depending on demo content.

Team declarations use `.juice/teams/<team_name>/manifest.yaml`. They can be
created without members. Shared Agent declarations live in `.juice/agents/`;
reusable Team-specific member definitions live in
`.juice/teams/<team_name>/members/`. A later Team run reuses those definitions
and starts with a new task board. Shared members resolve through AgentRegistry,
including built-ins without YAML; private members read only their own directory.
The empty built-in `default` Team saves its first manifest on a successful edit.
Failed member creation rolls back staged private files. Deleting an Agent override
is permitted only when its effective fallback satisfies every referencing Team's
mode and persistent lifecycle constraints; otherwise the error names those Teams.

Agent declarations have one workspace-global name and an optional
`allowed_modes` list. Omit it (or use `null`) to allow every built-in and
custom mode; an explicit list restricts visibility and dispatch to those
modes. The unified built-in catalog contains `root`, `general`, `explore`, Group
workers and Team candidates. `root` has no YAML until its first successful edit;
Runner applies Plan/Team additions and paths to an in-memory copy.
It is still the Runner's entry point, so `list_available_agents()` excludes it
from child-agent discovery and dispatch.

`list_available_agents(mode_id=..., runner=...)` is the public availability
projection. It combines declaration `allowed_modes`, active Runner policy or
Team membership, and that Runner/mode's disabled set without creating a
Runner, Agent or default YAML for a cold query. `load_config()` and `list_configs()`
return effective declarations from local YAML first, then read-only built-ins;
Stores return disk contents only. `get_source()` reports `local` or `builtin`,
and `config_path()` returns `None` for built-in Agents. The first successful edit atomically
saves a complete override; deleting it restores the built-in. Pure built-ins
cannot be deleted. Resolution accepts an Agent name, serialized
declaration or `AgentConfig`; it never accepts a live Agent.
In Team mode, root's read-only `agents_list` and `agent_view` additionally
show eligible shared declarations so an empty Team can select its first member.
That inspection scope does not expand Team dispatch or Agent write authority.

## Development conventions

- The common Registry protocol is `resolve()`, `validate()`, and `instantiate()`; declaration CRUD stays in the corresponding Registry. The first two must not import executable Workflow/Plugin code or create sessions; the third returns a fresh object every time without caching or executing it.
- Write declarations atomically. Keep static Agent and Team definitions separate from Runner/Manager sessions, tasks, and tool records; do not silently migrate the old layout.
- An omitted `allowed_modes` allows every mode; an explicit value is a non-empty list and may contain custom mode names. Reuse `agents.availability.is_agent_available()` for availability. Save the common `root` declaration only after an edit; never save Runner-specific plan paths or mode overlays into it, or list it as a child Agent.
- Agent writes validate tools and Team eligibility before saving. Deletion validates the effective fallback for referenced Agents, and reports every affected Team when no valid fallback exists. Private scopes never fall back to global built-ins. Put tests in `tests/core/registry/` covering independent validation, fresh construction, and the absence of live state.

Built-in Workflow scripts follow the same first-edit override rule. Unknown names
and invalid source fail before writing. Skills retain their copy-on-first-edit
flow, Plugins remain read-only, and Python built-in Tool names cannot be overridden.
The seed APIs and `fallback_configs` argument have been removed; consumers query
the Registry directly.
