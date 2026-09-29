# Registry

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

## 开发约束

- Registry 只做 `resolve()`、`validate()`、`instantiate()`。前两者不导入可执行 Graph/Plugin 代码或创建 session；后者每次返回新对象，不缓存或执行。
- 声明写入需原子完成。Agent、Team 的静态定义与 Runner/Manager 的 session、任务、工具记录分开保存；不做旧布局的隐式迁移。
- `allowed_modes` 可省略表示全部模式，显式值为非空列表，允许自定义 mode 名称。Agent 可用性统一复用 `agents.availability.is_agent_available()`；`root` 只在 Runner 内存中存在，不能写入 YAML 或列入 `/agents`。
- 删除被 Team 引用的共享 Agent，或移除它的 Team 可用性时，报告所有引用的 Team。测试放 `tests/core/registry/`，覆盖独立校验、fresh 构造和无 live 状态。
