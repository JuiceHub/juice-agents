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
| `GraphRegistry` | Graph source validation | `GraphRunManager` |
| `SkillRegistry` / `PluginRegistry` | metadata discovery | requesting Manager |
| `TeamRegistry` | Team relationship declaration | Runner capability dispatch |

Registries do not retain sessions, pools, permission contexts, Runner
references or live objects. `instantiate()` always returns a fresh object;
execution, cancellation and persistence are Manager responsibilities.

Workspace declarations live under `.juice/agents`, `.juice/tools`,
`.juice/graphs`, `.juice/skills` and `.juice/teams`. They are never copied
into a Runner snapshot.

Team declarations use `.juice/teams/<team_name>/manifest.yaml`. They can be
created without members. Shared Agent declarations live in `.juice/agents/`;
reusable Team-specific member definitions live in
`.juice/teams/<team_name>/members/`. A later Team run reuses those definitions
and starts with a new task board. Deleting a referenced shared Agent is
rejected with the Team names that reference it.

Agent declarations have one workspace-global name and an optional
`allowed_modes` list. Omit it (or use `null`) to allow every built-in and
custom mode; an explicit list restricts visibility and dispatch to those
modes. `root` is a Runner-owned in-memory identity, so it is never written as
an Agent YAML or returned by `list_available_agents()`.

`list_available_agents(mode_id=..., runner=...)` is the public availability
projection. It combines declaration `allowed_modes`, active Runner policy or
Team membership, and that Runner/mode's disabled set without creating a
Runner, Agent or default YAML for a cold query. An adapter may pass detached
prebuilt declarations as fallbacks for a fresh-workspace display; workspace
YAML with the same name wins. Resolution accepts an Agent name, serialized
declaration or `AgentConfig`; it never accepts a live Agent.
In Team mode, root's read-only `agents_list` and `agent_view` additionally
show eligible shared declarations so an empty Team can select its first member.
That inspection scope does not expand Team dispatch or Agent write authority.

## Development conventions

- Registry only implements `resolve()`, `validate()`, and `instantiate()`. The first two must not import executable Graph/Plugin code or create sessions; the third returns a fresh object every time without caching or executing it.
- Write declarations atomically. Keep static Agent and Team definitions separate from Runner/Manager sessions, tasks, and tool records; do not silently migrate the old layout.
- An omitted `allowed_modes` allows every mode; an explicit value is a non-empty list and may contain custom mode names. Reuse `agents.availability.is_agent_available()` for availability. `root` exists only in Runner memory and must not be written to YAML or listed by `/agents`.
- When deleting a shared Agent referenced by a Team, or removing its Team availability, report every referencing Team. Put tests in `tests/core/registry/` covering independent validation, fresh construction, and the absence of live state.
