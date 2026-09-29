> [English](README.md)

# Registry

Registry 只持有静态定义，是 workspace 声明与全新领域对象构造之间的边界。Manager 接收新对象后，运行时所有权才开始。

```text
名称 / 配置 ──> resolve() ──> validate() ──> instantiate() ──> Manager
                         仅静态数据              全新对象
```

| Registry | 静态职责 | 构造后的运行时所有者 |
| --- | --- | --- |
| `AgentRegistry` | Agent YAML 与类选择 | `AgentManager` |
| `ToolRegistry` | Tool 声明与类映射 | `ToolManager` |
| `GraphRegistry` | Graph 源码校验 | `GraphRunManager` |
| `SkillRegistry` / `PluginRegistry` | 元数据发现 | 发起请求的 Manager |
| `TeamRegistry` | Team 关系声明 | Runner 能力分发 |

Registry 不保留 session、线程池、权限上下文、Runner 引用或 live 对象。`instantiate()` 每次都会返回全新对象；执行、取消和持久化都属于 Manager。

workspace 声明保存在 `.juice/agents`、`.juice/tools`、`.juice/graphs`、`.juice/skills` 和 `.juice/teams` 下，不复制进 Runner snapshot。

Team 声明位于 `.juice/teams/<team_name>/manifest.yaml`，可以先创建空 Team。共享 Agent 声明放在 `.juice/agents/`；可复用的 Team 专属成员定义放在 `.juice/teams/<team_name>/members/`。后续运行会复用这些定义，但创建新的任务板。删除被引用的共享 Agent 时，会报告引用它的 Team 并拒绝删除。

Agent 声明在 workspace 中使用全局唯一名称，并可选 `allowed_modes`。省略该字段或设置为 `null` 时，所有内置和自定义 mode 均可见；显式列表会限制可见性和分发范围。`root` 是 Runner 内存中的身份，不写入 Agent YAML，也不会由 `list_available_agents()` 返回。

公开查询 `list_available_agents(mode_id=..., runner=...)` 会组合声明中的 `allowed_modes`、当前 Runner 策略或 Team 成员关系，以及该 Runner/mode 的禁用列表；冷查询不会创建 Runner、Agent 或默认 YAML。适配器可以传入脱离状态的预置声明，供全新 workspace 展示；同名 workspace YAML 优先。解析可以接收 Agent 名称、序列化声明或 `AgentConfig`，不能接收 live Agent。Team mode 下 root 的只读 `agents_list` 和 `agent_view` 还会显示符合成员资格的共享声明，以便空 Team 选择首位成员；这不会扩大 Team 分发范围或 Agent 写权限。

## 开发约定

- Registry 只实现 `resolve()`、`validate()` 和 `instantiate()`。前两者不得导入可执行 Graph/Plugin 代码或创建 session；第三者每次返回全新对象，不缓存也不执行。
- 声明写入必须原子完成。Agent、Team 静态定义与 Runner/Manager 的 session、任务、工具记录分开保存；不隐式迁移旧布局。
- 省略 `allowed_modes` 表示全部 mode；显式值必须是非空列表，可包含自定义 mode 名称。Agent 可用性统一复用 `agents.availability.is_agent_available()`。`root` 只存在于 Runner 内存中，不能写入 YAML 或加入 `/agents` 列表。
- 删除被 Team 引用的共享 Agent，或移除 Agent 对某 Team 的可用资格时，要报告所有引用的 Team。测试放在 `tests/core/registry/`，覆盖独立校验、全新对象构造和无 live 状态。
