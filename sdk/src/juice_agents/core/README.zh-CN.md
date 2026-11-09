> [English](README.md)

# Core Runtime

核心运行时为构建、执行和管理具有持久状态和受控执行的智能体提供基础。


消息统一由 `AgentManager.post` 写入持久 inbox；AgentManager 管理每个 Agent 的串行 round。
TeamManager 管业务消息与任务，AsyncTaskManager 管独立后台任务。恢复先绑定监听与 Ask，
再 `activate()`；`session.json` 同次保存上下文、消息去重记录与 round 检查点。


## 统一执行边界

Agent 实例只有 `idle/running/released`；回合结果独立为
`completed/failed/cancelled`。AgentManager 负责执行与终态保存，Inbox 负责消息与调度。
Runner、Gateway、CLI/Web 共用[结果与事件契约](../../../README.zh-CN.md)。
恢复格式为 Runner v6、Agent v2、Context v3；写入前校验，拒绝旧格式且保留文件。

## 架构

```text
Registry（静态配置） → Manager（运行时状态） ← Runner（执行调度）
```

**关注点分离：**

- **Registry**：从声明中解析、验证和实例化对象
- **Manager**：拥有运行时对象的生命周期、持久化和取消
- **Runner**：组合管理器并编排请求执行

**核心原则：** Registry 无活动状态，Manager 拥有可变对象，Runner 调度请求。Core 从不依赖 `adapters/`。

## 核心模块

### 执行层

| 模块 | 职责 |
| --- | --- |
| [Agent](agent/README.zh-CN.md) | ReAct/CodeAct 协议、会话管理、上下文压缩 |
| [Runner](runner/README.zh-CN.md) | 统一请求入口、模式编排、管理器组合 |
| [Managers](managers/README.zh-CN.md) | Agent/模型/工具/Workflow/任务生命周期、持久化、取消 |
| [Permissions](permissions/README.zh-CN.md) | 授权、策略执行、审批流程 |

### 声明层

| 模块 | 职责 |
| --- | --- |
| [Registry](registry/README.zh-CN.md) | 静态声明、验证、实例化 |
| [Group](group/README.zh-CN.md) | 多智能体组声明和协调 |
| [Team](team/README.zh-CN.md) | 持久团队看板、任务管理、成员协作 |

### 服务层

| 模块 | 职责 |
| --- | --- |
| [Models](models/README.zh-CN.md) | LLM 提供商集成、流式输出、结构化生成 |
| [Configuration](config/README.zh-CN.md) | 共享 Provider、语言/图片模型目录、分层配置与凭据 |
| [Memory](memory/README.zh-CN.md) | 持久知识库、不区分大小写的文本搜索、检索 |
| [Workflow](workflow/README.zh-CN.md) | Python 工作流脚本、内部 agent、产物存储 |
| [Cron](cron/README.zh-CN.md) | 定时任务存储、到期领取与执行 |

## 执行流程

```text
providers → models       → 聊天 / GRM（独立选择）
          → image_models → image.model_name（Ark 生图与编辑）
```

两个模型目录共用连接配置，GRM 选择只保存在进程内；生图默认关闭，Plan 模式始终禁用。
`Runner.reconfigure(reload_config=True)` 在空闲边界刷新客户端，保留 Runner ID、历史和探索策略。

```text
Runner.create(base_dir=...) / Runner.resume(base_dir=..., runner_id=...)
  ↓
用户请求
  ↓
Runner.stream()
  ↓
AgentManager.acquire() → 智能体实例 + 会话恢复
  ↓
Agent.step() → 推理循环
  ↓
RunnerContext.execute_tools()
  ↓
ToolManager.execute_for_agent()
  ↓ (权限检查、绑定、执行、审计)
观测 → 智能体
  ↓
AgentManager.checkpoint() → 持久化会话
  ↓
Runner 发出事件 → 用户
```

## 智能体模式

所有模式共享相同的执行路径，仅配置不同：

| 模式 | 模式 | 用例 |
| --- | --- | --- |
| **agent** | 单智能体，可选委派 | 直接任务执行 |
| **plan** | 调研 → 规划 → 审批 → 执行 | 大型变更、多步工作 |
| **group** | 根协调专家 | 并行工作、领域专家 |
| **team** | 持久看板、成员认领 | 长期协作 |

## 持久化

运行时状态位于工作区的 `.juice/`：

```text
.juice/
├── config.yaml           # 工作区配置
├── sessions/              # Runner 会话
│   └── <runner_id>/
│       ├── manifest.json # RunnerConfig、模式、状态
│       ├── agents/       # 智能体会话快照
│       ├── workflows/    # Workflow 产物
│       └── plans/        # 计划产物
├── workflows/            # 工作区 workflow 脚本
├── skills/               # 自定义技能
├── memory/               # 知识库
└── teams/                # 团队看板和任务
```

**原子性：** 所有写入都是原子的。恢复处理部分写入和陈旧状态。

## 开发边界

### Registry

- 从 YAML、Python 或内置解析声明
- 验证模式和依赖
- 按需实例化全新对象
- **从不**持有活动会话或执行状态

### Manager

- 拥有活动的 Agent、Tool、Workflow、Task 实例
- 处理生命周期：创建、绑定、执行、检查点、取消、释放
- 原子持久化状态
- 提供恢复和协调

### Runner

- 为单个请求组合管理器
- 调度根和成员智能体轮次
- 执行权限边界
- 发出进度事件
- **从不**直接实例化域对象

### Adapter

- 将 stdio/HTTP/WebSocket 协议映射到 SDK
- 位于 `adapters/`，在 SDK 包外
- SDK 核心不得导入适配器

## 能力与策略

**能力**启用智能体功能：

```yaml
capabilities:
  workflows:
    enabled: true
  self_evolution:
    enabled: true
  planning:
    mode: "full"  # full | read_only | disabled
```

**策略**限制智能体操作：

```yaml
tool_policy:
  read_only: true
  read_only_exceptions: ["ask_user", "submit_output"]
  allowed_tools: ["read_file", "list_directory", "web_search"]
  allowed_agent_names: ["researcher", "analyzer"]
```

能力是智能体权力；策略是 runner 强制的约束。

## 并发

- 一个 runner 一次处理一个请求
- 智能体是可重入的：相同的 Agent 实例永不并发执行
- ReAct 同一步 actions 默认并发（每个 Agent 默认 8 个 worker）；有依赖的调用放到后续 step
- 后台任务在专用工作器中运行
- Workflow 可并行化内部 agent

## 测试

```bash
conda activate juice-agents
python -m pytest tests/core -q
```

测试验证：
- Registry 无活动状态
- Manager 拥有生命周期
- Runner 不直接实例化域对象
- 持久化是原子的
- 恢复处理失败
- 取消正确传播

更改边界时，在相应的 `tests/core/<module>/` 目录添加测试。

## 扩展点

- **自定义智能体**：继承 `MultiStepAgent`
- **自定义工具**：实现 `Tool` 协议
- **自定义 workflow**：编写 `.juice/workflows/<name>.py`
- **自定义技能**：添加 `.juice/skills/<name>.md`
- **自定义模式**：注册带有能力和策略的 `RunnerConfig`

详细扩展指南见模块 README。

执行链使用协程：ModelManager 负责调用、用量及连接，TeamManager 负责成员分派和结果对账。同步 SDK 在边界适配完整异步操作。
