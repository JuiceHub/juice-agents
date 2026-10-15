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

**配置：** 使用托管模型前，配置 `.juice/config.yaml` 并通过环境提供 API 密钥：

```bash
mkdir -p .juice
cp sdk/src/juice_agents/_assets/config.example.yaml .juice/config.yaml
cp .env.example .env
# 在 .env 中填写 API 密钥
```

模型条目可选 `api_base_env`：其指向的变量（进程环境 > `.env`）存在时覆盖 YAML 中的 `api_base`。内置 Claude 模型使用 `ANTHROPIC_BASE_URL`，接入代理/中转服务只需修改 `.env`。

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
runner = juice.runners.create(
    runner_config="plan",
    permission_mode="default"
)

# 调研和规划
runner.run("重构身份验证模块")
# 智能体创建 plans/root.md

# 审查和批准
plan = runner.get_plan()
print(plan)
runner.approve_plan()

# 执行已批准的计划
runner.run("执行计划")
```

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

智能体可访问 20+ 内置工具：

**文件系统：** `read_file`, `write_file`, `edit_file`, `list_directory`, `search_files`

**执行：** `shell`, `python_repl`, `python_script`

**Web：** `web_search`, `web_fetch`, `browser_navigate`, `browser_screenshot`

**记忆：** `memory_create`, `memory_update`, `memory_search`, `memory_list`

**协作：** `agent_spawn`, `agent_message`, `task_create`, `team_broadcast`

**工作流：** `workflow_call`（通过 `ctx.agent()` 调用内部智能体）

**输出：** `submit_output`, `ask_user`

工具根据智能体能力和 runner 策略动态可用。

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
        phase="search"
    )
    
    analysis = ctx.agent(
        task=f"分析：{search_result}",
        phase="analyze"
    )
    
    # 保存产物
    ctx.artifact("analysis.md", analysis)
    
    return {"report": analysis, "sources": search_result}
```

从智能体调用工作流：

```python
from juice_agents.core.managers.workflows import WorkflowRunManager

result = workflow_manager.call("research", {"query": "AI trends 2024"})
```

## 权限模式

| 模式 | 行为 |
| --- | --- |
| `default` | 提示破坏性/外部操作 |
| `auto` | 自动批准所有操作 |
| `read_only` | 阻止写操作 |
| `strict` | 提示每个工具调用 |

在 runner 创建时配置：

```python
runner = juice.runners.create(
    runner_config="agent",
    permission_mode="read_only"
)
```

## Cron 和调度

调度周期任务：

```python
# UI 拥有时钟；SDK 提供 tick 原语
juice.cron.tick()

# Cron 作业通过工作区 .juice/config.yaml 配置
```

## API 参考

### Juice 客户端

```python
juice = Juice(workspace=".", config_path=None)
```

- `workspace`：`.juice/` 状态的工作目录
- `config_path`：可选的 `config.yaml` 路径

### Runner 创建

```python
runner = juice.runners.create(
    runner_config="agent",  # 或 "plan", "group", "team", 或自定义配置
    permission_mode="default",
    model=None  # 可选模型覆盖
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
    if event.type == "agent_message":
        print(event.content)

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
- 内置资源（技能、提示、配置）

**不包含：**
- `adapters/`（协议网关）
- `frontend/`（CLI 和 Web UI）
- 示例
- `.env` 或真实 `config.yaml`
- `.juice/` 运行时数据

配置和状态写入调用者的工作区。

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
