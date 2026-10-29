> [English](README.md)

# Juice Agents SDK

用于创建具有持久会话、workflow 编排和多智能体协作的智能体的独立构建 Python SDK。

**包名：** `juice-agents` | **导入：** `juice_agents` | **Python：** 3.11+

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

模型条目可选 `api_base_env`：其指向的变量按上述凭据优先级覆盖 YAML 中的 `api_base`。内置 Claude 模型使用 `ANTHROPIC_BASE_URL`。参见[配置开发约定](src/juice_agents/core/config/README.zh-CN.md)。

## 快速开始

### 单智能体

```python
from juice_agents import Juice

juice = Juice(workspace=".")
runner = juice.runners.create(
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
runner = juice.runners.create(runner_config="agent")
runner.run("开始分析代码库")
runner_id = runner.runner_id

# 重启后（甚至不同进程）
runner = juice.runners.resume(runner_id=runner_id)
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

juice = Juice(workspace=".", ask_handler=ask_handler)
runner = juice.runners.create(runner_config="plan")
runner.run("重构身份验证模块")
```

智能体将计划写入 Runner 状态目录的 `plans/root.md`，并通过 `exit_plan` 请求审批。`ask_handler` 同时处理智能体的问题和权限审批；实际应用应根据请求展示选项并返回用户的选择。

### 组协调

```python
runner = juice.runners.create(
    runner_config="group",
    permission_mode="default"
)

# 根协调专家智能体
runner.run("研究竞争对手并撰写市场分析报告")
# 根生成：研究员、分析师、撰稿人
```

### 团队协作

```python
runner = juice.runners.create(
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
runner = juice.runners.create(
    runner_config="agent",
    permission_mode="accept"
)
```

## Cron 和调度

调度周期任务：

```python
# UI 拥有时钟；SDK 提供 tick 原语
juice.cron.tick()

# 用 juice.cron.create(cron="0 9 * * *", prompt="生成日报") 创建作业
# 作业保存在工作区 .juice/scheduled_tasks.json
```

## API 参考

### Juice 客户端

```python
juice = Juice(workspace=".", runtime_config_path=None)
```

- `workspace`：`.juice/` 状态的工作目录
- `runtime_config_path`：可选的运行时配置文件路径

### Runner 创建

```python
runner = juice.runners.create(
    runner_config="agent",  # 或 "plan", "group", "team", 或自定义配置
    permission_mode="default"
)
```

### Runner 恢复

```python
runner = juice.runners.resume(runner_id="run_abc123")
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
Juice 客户端
  └─ runners.create(runner_config) → Runner
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
