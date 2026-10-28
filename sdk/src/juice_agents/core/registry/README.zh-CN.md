> [English](README.md)

# Registry

## 工作区上下文

`ConfigurationContext.workspace_dir` 是项目目录，`juice_root` 是项目的 `.juice`。
`ToolRegistry` 在创建工具实例的统一入口绑定文件根目录、Shell/Python 工作目录、
Skills 和计划路径；运行时新增工具也复用该入口。声明规范化不再注入机器绝对路径。
嵌入 SDK 时，Agent 和 Tool Registry 应传入同一个上下文：

```python
context = ConfigurationContext.from_workspace("/path/to/project")
tools = ToolRegistry(config_context=context)
read = tools.instantiate("read")  # 相对路径从目标项目解析
```

`SkillRegistry(workspace_dir=...)` 同时使用目标目录定位 `.juice/skills` 与
`.agents/skills`。自定义 `local_dir` 只改变可写存储，不会从任意父目录推断项目。
SDK 不附带内置 Skill；只读 `builtin_dir` 来源由调用方显式传入。发现优先级为本地、
显式内置、外部；编辑只读来源时先创建本地副本。技能测试在临时目录生成最小样例，
不依赖演示内容。

Registry 只持有静态定义，是 workspace 声明与全新领域对象构造之间的边界。Manager 接收新对象后，运行时所有权才开始。

```text
名称 / 配置 ──> resolve() ──> validate() ──> instantiate() ──> Manager
                         仅静态数据              全新对象
```

| Registry | 静态职责 | 构造后的运行时所有者 |
| --- | --- | --- |
| `AgentRegistry` | Agent YAML 与类选择 | `AgentManager` |
| `ToolRegistry` | Tool 声明与类映射 | `ToolManager` |
| `WorkflowRegistry` | Workflow 源码校验 | `WorkflowRunManager` |
| `SkillRegistry` / `PluginRegistry` | 元数据发现 | 发起请求的 Manager |
| `TeamRegistry` | Team 关系声明 | Runner 能力分发 |

Registry 不保留 session、线程池、权限上下文、Runner 引用或 live 对象。`instantiate()` 每次都会返回全新对象；执行、取消和持久化都属于 Manager。

workspace 声明保存在 `.juice/agents`、`.juice/tools`、`.juice/workflows`、`.juice/skills` 和 `.juice/teams` 下，不复制进 Runner snapshot。

Team 声明位于 `.juice/teams/<team_name>/manifest.yaml`，可以先创建空 Team。共享 Agent 声明放在 `.juice/agents/`；可复用的 Team 专属成员定义放在 `.juice/teams/<team_name>/members/`。后续运行会复用这些定义，但创建新的任务板。共享成员通过 AgentRegistry 解析，可引用未落盘的内置 Agent；私有成员严格读取专属目录。内置空 `default` Team 首次成功修改才保存 manifest，创建失败会清理暂存的私有成员文件。删除 Agent 覆盖前，必须验证回退定义仍满足所有引用 Team 的模式与持久生命周期约束；不满足时报告相关 Team 并拒绝删除。

Agent 声明在 workspace 中使用全局唯一名称，并可选 `allowed_modes`。省略该字段或设置为 `null` 时，所有内置和自定义 mode 均可见；显式列表会限制可见性和分发范围。统一内置目录收录 `root`、`general`、`explore`、Group workers 和 Team 候选。`root` 首次成功编辑才保存 YAML，Runner 的 Plan/Team 配置与路径仅应用于内存副本。它仍是 Runner 的入口，不由 `list_available_agents()` 作为子 Agent 返回或分发。

公开查询 `list_available_agents(mode_id=..., runner=...)` 会组合声明中的 `allowed_modes`、当前 Runner 策略或 Team 成员关系，以及该 Runner/mode 的禁用列表；冷查询不会创建 Runner、Agent 或默认 YAML。`load_config()`、`list_configs()` 返回“本地 YAML 优先、只读内置兜底”的有效声明，Store 只返回磁盘内容。`get_source()` 返回 `local` / `builtin`，内置 Agent 的 `config_path()` 为 `None`。首次成功编辑才原子保存完整本地覆盖，删除覆盖恢复内置，纯内置资源不能删除。解析可以接收 Agent 名称、序列化声明或 `AgentConfig`，不能接收 live Agent。Team mode 下 root 的只读 `agents_list` 和 `agent_view` 还会显示符合成员资格的共享声明，以便空 Team 选择首位成员；这不会扩大 Team 分发范围或 Agent 写权限。

## 开发约定

- Registry 的公共协议为 `resolve()`、`validate()` 和 `instantiate()`，声明 CRUD 由对应 Registry 负责。前两者不得导入可执行 Workflow/Plugin 代码或创建 session；第三者每次返回全新对象，不缓存也不执行。
- 声明写入必须原子完成。Agent、Team 静态定义与 Runner/Manager 的 session、任务、工具记录分开保存；不隐式迁移旧布局。
- 省略 `allowed_modes` 表示全部 mode；显式值必须是非空列表，可包含自定义 mode 名称。Agent 可用性统一复用 `agents.availability.is_agent_available()`。仅在编辑后持久化 `root` 通用声明，不写回本次 Runner 的计划路径或模式叠加配置，也不将它列为子 Agent。
- Agent 保存前校验工具与 Team 成员约束；删除覆盖前校验有效回退，没有合格回退时报告所有引用 Team 并拒绝删除。私有作用域不回退到全局内置。测试放在 `tests/core/registry/`，覆盖独立校验、全新对象构造和无 live 状态。

内置 Workflow 也遵循首次编辑落盘，未知名称与非法源码在写入前报错。Skill 保留首次编辑复制机制，Plugin 保持只读，Python 内置 Tool 名称不允许同名覆盖。已移除 seed API 和 `fallback_configs` 参数，调用方直接查询 Registry。
