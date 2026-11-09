> [English](README.md)

# Juice Agents SDK

用于创建具有持久会话、workflow 编排和多智能体协作的智能体的独立构建 Python SDK。

**包名：** `juice-agents` | **导入：** `juice_agents` | **Python：** 3.11+

## 自主运行与恢复

```python
runner = Runner.resume(base_dir=".", runner_id=runner_id)
unsubscribe = runner.subscribe_events(lambda event: print(event))
runner.set_ask_handler(ask_handler)  # 先绑定交互能力。
runner.activate()                  # 幂等；恢复待投递消息与 active round。
# 停止一轮：runner.cancel_round(round_id)。
# 关闭宿主：runner.close() 等待收尾，再 unsubscribe()。
```

`run/stream` 也会自动激活。用户、Team 与异步完成消息共享持久 inbox 和稳定 ID，
每个 Agent 串行执行 round。订阅通过独立 `round_start/round_end` 接收后续自主执行。
取消意图持久化后才返回，并中断同步子调用；独立异步任务继续运行。
functional 调用必须在返回前完成调用内工作，禁止启动脱离调用的后台任务。

## 核心功能

| 功能 | 说明 |
| --- | --- |
| 🤖 **智能体** | ReAct 和 CodeAct 智能体，20+ 内置工具 |
| 🔄 **会话恢复** | 进程重启后持久化和恢复对话 |
| 🌐 **工作流** | Python 函数式工作流，内部智能体、产物输出 |
| 👥 **多智能体** | `agent`、`plan`、`group`、`team` — 一个运行时，四种模式 |
| 🛠️ **工具生态** | Shell、文件系统、Web、浏览器、记忆、MCP 和自定义工具 |
| 💾 **持久状态** | 会话、任务和团队看板在重启后保留 |
| 🎯 **受控执行** | 权限模式、审批流程、审计日志 |

## 安装

```bash
conda activate juice-agents
python -m pip install -e ./sdk
```

从仓库根目录运行。生产环境构建 wheel：

```bash
python -m build --sdist --wheel --outdir sdk/dist sdk
pip install sdk/dist/juice_agents-*.whl
```

**配置：** 无需复制整个模型目录；通用配置和凭据放在 `~/.juice/`，仅在需要时增加项目覆盖：

```bash
mkdir -p ~/.juice
# ~/.juice/config.yaml 保存通用配置，~/.juice/.env 保存 API 密钥。
# 项目按需使用 workspace 下的 .juice/config.yaml 和 .env 覆盖。
```

```text
配置：SDK 默认 / config_path → ~/.juice/config.yaml → workspace/.juice/config.yaml → 启动参数
凭据：进程环境变量 > workspace/.env > ~/.juice/.env
```

Runner、模型和工具统一通过 `ConfigurationContext` 读取配置。凭据不写入进程环境；缓存识别文件新增、修改、删除和原子替换。仅在实际使用模型或需密钥的工具时检查凭据，因此仍可查看配置与帮助。项目配置写入仅保留项目覆盖。

SDK 导入不解析进程工作目录。即使原 cwd 已删除，仍可向
`ConfigurationContext.from_workspace()` 传入绝对工作区路径读取配置；
使用隐式工作区路径时需要有效 cwd。

模型条目引用 `providers`，后者统一维护 backend、地址与凭据来源。
聊天和 GRM 各自从同一份 `models` 选择；生图通过 `image_models` 和
`image.model_name` 选择，`image.enabled` 默认关闭。Provider 可选 `api_base_env`，
按上述凭据优先级覆盖 YAML 地址；内置 Claude Provider 使用 `ANTHROPIC_BASE_URL`。
修改设置后在空闲时调用 `runner.reconfigure(reload_config=True)`，保留历史和探索
选择并刷新连接。参见[配置开发约定](src/juice_agents/core/config/README.zh-CN.md)。

## 快速开始

会话及任务、浏览器和图片产物保存在 `.juice/sessions/<runner_id>/`。
`Runner.create()` 使用空上下文，`Runner.resume()` 恢复指定旧会话；
历史 `.juice/runners/` 数据不读取、不迁移。

### 单智能体

```python
from juice_agents import Runner

runner = Runner.create(
    base_dir=".",
    runner_config="agent",
    permission_mode="default"
)

# 流式执行
for event in runner.stream("分析这个项目的结构"):
    print(event)

# 阻塞执行
result = runner.run("总结发现")
print(result)
```

### 会话恢复

```python
# 第一次会话
runner = Runner.create(base_dir=".", runner_config="agent")
runner.run("开始分析代码库")
runner_id = runner.runner_id
runner.close()

# 重启后（甚至不同进程）
runner = Runner.resume(base_dir=".", runner_id=runner_id)
runner.run("继续实现")
```

### 计划模式

```python
def ask_handler(request):
    # 将问题和选项呈现给用户，并返回用户选择。
    print(request["question"])
    options = request.get("options", [])
    for option in options:
        print(f"{option['value']}: {option['label']}")
    answer = input("输入选项值或反馈：").strip()
    selected = [option for option in options if option["value"] == answer]
    return {"status": "answered", "selected": selected, "custom_response": "" if selected else answer}

runner = Runner.create(base_dir=".", runner_config="plan")
runner.set_ask_handler(ask_handler)
runner.run("重构身份验证模块")
```

智能体将计划写入 Runner 状态目录的 `plans/root.md`，并通过 `exit_plan` 请求审批。`ask_handler` 同时处理智能体的问题和权限审批；实际应用应根据请求展示选项并返回用户的选择。

### 组协调

```python
runner = Runner.create(
    base_dir=".",
    runner_config="group",
    permission_mode="default"
)

# 根协调专家智能体
runner.run("研究竞争对手并撰写市场分析报告")
# 根生成：研究员、分析师、撰稿人
```

### 团队协作

```python
runner = Runner.create(
    base_dir=".",
    runner_config="team",
    permission_mode="default"
)

# 创建持久团队看板
runner.run("为网页爬取项目创建团队")
runner.run("添加任务：设计模式、实现爬虫、测试")

# 成员认领和处理任务
# 任务在会话间持久化
```

## 智能体模式

| 模式 | 执行模式 | 适用场景 |
| --- | --- | --- |
| `agent` | 单智能体，可选委派 | 直接任务执行、问答 |
| `plan` | 调研 → 规划 → 审批 → 执行 | 大型重构、多文件更改 |
| `group` | 根协调专家智能体 | 并行研究、领域专长 |
| `team` | 持久看板、成员任务认领 | 长期项目、异步工作 |

所有模式使用相同的 `Runner` 执行路径并共享工具、权限和持久化基础设施。

## 工具

工具事件使用 `tool_start` / `tool_end`，通过相同 `call_id` 关联。结束事件保留
已有执行状态、返回结果及捕获的异常，不根据文本判断失败。CodeAct 同时保存公开
Python wrapper 与内部审计记录，供历史还原实时工具展示。
详见 [Agent 事件约定](src/juice_agents/core/agent/README.zh-CN.md)。

智能体可使用多种内置工具，例如：

- 文件系统：`read`、`write`、`edit`、`glob`、`grep`
- 执行：`shell`、`python`
- Web：`web_search`、`browser_open_url`、`browser_screenshot`
- 记忆：`memory_read`、`memory_write`、`memory_search`、`memory_forget`
- 协作：`agent_tool`、`team_task_create`、`send_message`
- 工作流和交互：`workflow_run`、`submit_output`、`ask`

工具根据智能体能力和 Runner 策略动态可用。

## 工作流

创建 Python 函数式工作流：

```python
# .juice/workflows/research.py
WORKFLOW_METADATA = {
    "name": "research",
    "description": "多步研究工作流",
    "input_schema": {"query": "string"},
}

def run(ctx, args):
    """工作流执行入口。"""
    query = args["query"]
    
    # 调用内部智能体
    search_result = ctx.agent(
        task=f"搜索：{query}",
        instructions="查找相关来源并总结发现。",
        phase="search"
    )
    
    analysis = ctx.agent(
        task=f"分析：{search_result}",
        instructions="分析发现并撰写报告。",
        phase="analyze"
    )
    
    # 保存产物
    ctx.artifact("analysis.md", analysis)
    
    return {"report": analysis, "sources": search_result}
```

通过 Runner 调用工作流（也可在智能体提示中使用 `$workflow:research`）：

```python
result = runner.workflow_manager.call("research", {"query": "AI trends 2024"})
```

## 权限模式

| 模式 | 行为 |
| --- | --- |
| `default` | 需要审批的工具通过用户交互请求批准 |
| `accept` | 自动批准需要审批的工具，仍受权限规则和 Runner 策略约束 |

在 runner 创建时配置：

```python
runner = Runner.create(
    base_dir=".",
    runner_config="agent",
    permission_mode="accept"
)
```

## Cron 和调度

调度周期任务：

```python
from juice_agents.core.cron import create_cron_task, execute_due_cron_tasks

create_cron_task(".", "0 9 * * *", "生成日报")
# 宿主进程管理时钟，需要执行到期任务时调用。
results = execute_due_cron_tasks(".", runner_config="agent")
```

## API 参考

### Runner 创建

直接调用 `Runner.create/resume`，用 `base_dir` 指定工作目录。
`runtime_config_path` 选择基础配置，`runtime_overrides` 添加本次启动覆盖。
创建或恢复后通过 `runner.set_ask_handler(handler)` 绑定交互回调；回调仅存在于当前进程，
不参与持久化。使用结束后在 `finally` 中调用 `runner.close()`。
定时任务 API 见 [Cron](src/juice_agents/core/cron/README.zh-CN.md)。

```python
runner = Runner.create(
    base_dir=".",
    runner_config="agent",  # 或 "plan", "group", "team", 或自定义配置
    permission_mode="default"
)
```

### Runner 恢复

```python
runner = Runner.resume(base_dir=".", runner_id="run_abc123")
```

### 执行

```python
# 流式
for event in runner.stream("任务描述"):
    print(event)  # 事件是字典

# 阻塞（内部消费流）
result = runner.run("任务描述")
```

### Runner 状态

```python
runner.runner_id  # 用于恢复的唯一 ID
runner.config     # 不可变 RunnerConfig
runner.stop()     # 协作取消
runner.close()    # 释放资源
```

## 包边界

SDK wheel 包含：
- `juice_agents` 包
- 类型存根
- 提示词和只读默认配置 `juice_agents/core/config/defaults.yaml`

**不包含：**
- `adapters/`（协议网关）
- `frontend/`（CLI 和 Web UI）
- 示例
- `.env` 或真实 `config.yaml`
- `.juice/` 运行时数据

配置和状态写入调用者的工作区。
SDK 不附带演示 Skill。可写技能放在 `.juice/skills`，只读来源通过 `.agents/skills`
或配置的外部目录加载。

## 架构

```text
Runner.create(base_dir=..., runner_config=...) / Runner.resume(...)
  └─ AgentManager, ToolManager, WorkflowRunManager
     └─ 智能体通过 RunnerContext 执行
        └─ 工具、权限、审计
```

**关键边界：**
- Registry：静态声明，无活动状态
- Manager：生命周期、持久化、取消
- Runner：请求编排、管理器组合
- Adapter：协议映射（SDK 外）

详细架构见 [Core](src/juice_agents/core/README.zh-CN.md)。

## 开发

运行测试：

```bash
conda activate juice-agents
python -m pip install -e ./sdk
python -m pytest tests/sdk -q
```

构建包：

```bash
python -m build --sdist --wheel --outdir sdk/dist sdk
```

产物：`sdk/build/`、`sdk/dist/`、`sdk/src/*.egg-info/`

## 模块文档

- [核心运行时](src/juice_agents/core/README.zh-CN.md)
- [智能体](src/juice_agents/core/agent/README.zh-CN.md)
- [Runner](src/juice_agents/core/runner/README.zh-CN.md)
- [工作流](src/juice_agents/core/workflow/README.zh-CN.md)
- [注册表](src/juice_agents/core/registry/README.zh-CN.md)
- [团队](src/juice_agents/core/team/README.zh-CN.md)

## 贡献

SDK 开发遵循核心约定：
- Registry 无活动状态
- Manager 拥有生命周期和持久化
- Runner 组合管理器，不实例化域对象
- 原子写入，恢复安全的持久化
- 测试在 `tests/sdk/` 和 `tests/core/`

项目指南见 [AGENTS.md](../AGENTS.md)。


### 回合结果与事件契约

```python
from juice_agents import RoundFailedError, RoundCancelledError

result = runner.run_round("检查变更")  # RoundResult，保留失败/取消的部分输出
print(result.status, result.reason, result.output)
# runner.run(...) 成功返回 output，否则抛出上述异常，异常的 .result 是同一结果契约。
```

| 公开事件 | data 中的主要字段 |
| --- | --- |
| `agent_state` | `status: idle/running/released`、生命周期和轻量回合摘要 |
| `round_start / round_end` | 输入消息身份 / `result: {round_id,status,output,reason,error}` |
| `step_start / step_end` | 步骤身份 / 已保存的 `step` 快照 |
| `model_start / model_end` | 相同 `model_call_id`，`purpose: generation/scoring/compression` |
| `thinking`、`tool_start / tool_end` | 思考内容 / 相同框架 `call_id` |
| `workflow_progress / team_update / message_deferred` | 调用 ID、业务进度及消息重新分配 |

所有事件包含 `event_id`、秒级 `timestamp`、Runner/Agent 身份及适用的 `round_id/step_num`。
`subscribe_events()` 可观察子 Agent；主对话仅消费 root。`describe_agent_sessions(agent_id=...)`
按需读取带回合、步骤和工具身份的历史。模型提供原生异步接口与 `aclose()`；同步 Agent/Runner 仅桥接一次完整调用，
取消覆盖请求建立、流读取和重试等待，见[模型约定](src/juice_agents/core/models/README.zh-CN.md)。
关闭 `stream()` 只退订，显式取消先保存意图、
停止模型/未开始工具，并等待已开始的调用收尾；取消不覆盖已保存的终态。
步数预算之后保留一次提交机会；仍未提交返回 `failed/max_steps_exceeded` 和部分输出。
Runner v6、Agent v2、Context v3 是当前格式；不迁移旧会话且不改写旧文件。

异步宿主使用 `await Runner.acreate/aresume(...)`、`await runner.arun/arun_round(...)`、
`async for event in runner.astream(...)` 及 `await runner.aclose()`（或 `async with`）。
同步入口共用同一协程执行链。ModelManager 负责请求、用量和连接，TeamManager
负责成员调度及结果对账。
