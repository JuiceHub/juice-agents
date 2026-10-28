> [English](README.md)

# juice-agents

轻量级 Python 智能体框架，支持 **会话恢复**、**工作流编排** 和 **多智能体协作**，构建 ReAct 和 CodeAct 智能体。

**当前版本：** `0.1.0`（开发版）

## 核心功能

| 功能 | 说明 |
| --- | --- |
| 🔄 **会话恢复** | 进程重启后恢复智能体、工作流和任务 |
| 🎯 **多种智能体模式** | `agent`、`plan`、`group`、`team` — 一个运行时，四种协作模式 |
| 🌐 **工作流编排** | 分支、并行、循环和检查点支持复杂工作流 |
| 🛠️ **丰富工具集** | Shell、Python、文件、Web、浏览器、图像、MCP 及自定义工具 |
| 💾 **持久化状态** | 会话、工作流、后台任务和团队看板在重启后保留 |
| 🚀 **多种接口** | Python SDK、终端 CLI 和本地 Web 工作台 |

```text
用户请求 → Runner → Agent / Workflow / Team → 工具
              │                            │
              └──── 持久化、审批、审计 ─────┘
```

## 快速开始

### 安装

需要 Conda。在仓库根目录执行：

```bash
conda env create -f environment.yml
conda activate juice-agents
python -m pip install -e ./sdk
python -m pip install -r adapters/requirements.txt
npm ci --prefix frontend
./install-juice
```

安装器将当前源码仓库以及当前 Python、Node 的绝对路径绑定到 `~/.local/bin/juice`，
请把该目录加入 Shell 的 `PATH`。完成一次安装后，日常无需进入框架仓库或激活 Conda；
移动仓库或替换解释器后重新执行安装器。正常启动只检查依赖，缺失时给出修复命令，
不会自动安装包或修改锁文件。

配置共用设置及在线模型的 API 凭证：

```bash
mkdir -p ~/.juice
cp .env.example ~/.juice/.env
# 在 ~/.juice/.env 中填写 API 密钥
# 可选：在 ~/.juice/config.yaml 中填写共用模型与运行参数覆盖
```

浏览器工具需要：`python -m playwright install chromium`

### 第一个程序

```python
from juice_agents import Juice

juice = Juice(workspace=".")
runner = juice.runners.create(runner_config="agent", permission_mode="default")

# 流式获取执行事件
for event in runner.stream("分析这个项目的模块边界"):
    print(event)

# 稍后恢复同一会话（即使进程重启后）
runner = juice.runners.resume(runner_id=runner.runner_id)
print(runner.run("把建议整理成实施顺序"))
print(runner.token_usage())  # generation / reward / total
```

Agent 收集并转交模型用量，由 `AgentManager` 持久化及汇总，写入 `agents/model_usage.jsonl`，
Runner 仅代理。淘汰候选也计入，恢复后继续累计；已知用量与 `missing_usage_requests`
分开保留，不估算 token 或费用，见 [Runner 文档](sdk/src/juice_agents/core/runner/README.zh-CN.md#token-统计)。

### 智能体模式

| 模式 | 适用场景 |
| --- | --- |
| `agent` | 单智能体执行，可选委派 |
| `plan` | 先调研和规划，审批后执行 |
| `group` | 根智能体协调专职智能体 |
| `team` | 持久任务板，成员认领和消息协作 |

切换模式时保留当前 ReAct/CodeAct 协议，只有显式传入新的 `agent_type` 才修改。

### CLI 和 Web

```bash
cd /path/to/project
juice                         # 使用当前目录作为 workspace
juice --workspace /other/project
# 在框架仓库中：
./juice-web   # 本地 Web 工作台（仅本机访问）
```

```text
配置：SDK 默认配置（或 runtime_config_path）
        → ~/.juice/config.yaml → <workspace>/.juice/config.yaml → 启动参数
凭据：进程环境变量 > <workspace>/.env > ~/.juice/.env
规则：~/.juice/AGENTS.md → Git 项目根 → 启动目录
```

SDK 默认配置随包保存在 `juice_agents/core/config/defaults.yaml`，修改配置请使用用户级或
项目级覆盖。SDK 不附带演示 Skill；通过 `.juice/skills`、`.agents/skills` 或配置的外部目录提供技能。

`--workspace` 优先于 `JUICE_WORKSPACE_DIR` 和启动目录，目标必须存在。
根 Agent、子 Agent、Team、Workflow 的工具、Skills 和 `.juice` 会话状态均使用该目录。
全局配置通过文件维护；`/config`、`/model` 仅保存用户显式修改的项目覆盖。
缺少凭据时在使用对应模型或工具时提示，仍可打开界面和查看帮助。

规则链中每层优先读取非空 `AGENTS.override.md`，否则读取 `AGENTS.md`；非 Git 项目只读取
workspace 本层项目规则。规则附带来源与作用目录，合并上限 32 KiB，截断会给出诊断，
恢复会话会重新读取并替换旧规则。Agent 修改更深目录前须先查阅该目录的规则。
已有 Git worktree 可直接作为 workspace 启动；CLI 内置 worktree 管理尚未实现，会明确提示。

### 直接运行示例

每个示例内置默认任务，直接运行主文件即可展示功能，也支持 IDE 运行及绝对路径。
先在 `examples/<name>/.juice/config.yaml` 配置该示例的模型；provider 密钥可统一保存在
仓库根目录 `.env`，无需逐个示例复制，
详见[示例文档](examples/README.zh-CN.md)。

```bash
python examples/agent_tot/__main__.py       # 每步生成三个候选，奖励模型评分，执行胜出候选
python examples/team_delivery/__main__.py  # 用内置交付任务演示团队协作
python examples/agent_tot/__main__.py "设计缓存方案" --reward-model <模型名>
```

凭证按进程环境、示例目录 `.env`、仓库根目录 `.env` 的顺序取值，与启动目录无关。
模型配置仍由各示例独立管理，可用 `--model <模型名>` 选择已配置的模型。
工作区始终为示例所在目录；从仓库根目录仍可使用
`python -m examples.<name>`。运行时显示任务、Runner ID、工具调用和结果，ToT
还显示候选评分及选中项。示例调用真实模型，需要先配置可用凭证。

### 每步候选选择

```text
当前上下文 → 生成 breadth 个候选 → 协议校验 → 评分 → 执行一个 → 下一步
```

```python
from openai import OpenAI

from juice_agents.core.agent.context.strategies import TreeOfThoughtsStrategy
from juice_agents.core.models.reward_models import GenerativeRewardModel

# 原生 SDK client；OpenAI 从环境变量读取 OPENAI_API_KEY。
scorer = GenerativeRewardModel(model_name="your-model", client=OpenAI())
# evaluate_batch(...) 返回 {"scores": [...], "usage": ..., "usage_calls": [...]}。
# scores 每个有效候选对应一个有限数值；高分胜出，同分选先生成的候选。
agent.set_exploration_strategy(TreeOfThoughtsStrategy(breadth=3, reward_model=scorer))
```

[模型层](sdk/src/juice_agents/core/models/README.zh-CN.md)提供统一聊天入口 `ChatModel`，
通过 `backend="openai"`、`"doubao"` 或 `"anthropic"` 选择实现；聊天生成与奖励评分
保持独立，完整用法见模块示例。
聊天调用仅重试暂时性故障和空响应；请求参数、凭据及无法归类的错误直接抛出。
Anthropic 的大输出预算请求即使没有取消令牌也使用流式接口。
`GenerativeRewardModel`（生成式奖励模型，GRM）直接调用 provider SDK 生成 JSON 评分并解析为
数值，无需配置文件；需要使用目录中的逻辑模型名时，可选用
`GenerativeRewardModel.from_config("judge", config_context)`。探索策略使用 `BaseRewardModel`；
自定义评分器继承该基类并覆盖 `evaluate_batch`，与聊天模型的基类约定保持一致。

CLI 用 `/rm` 独立配置奖励模型，用 `/strategy` 控制候选数：

```text
/models                 # 查看目录中的逻辑模型名
/rm                     # 打开奖励模型选择页
/strategy breadth=3     # 生成三个候选，择优执行一个
/rm <model_name>         # 也可按名称直接选择目录模型
/rm reset               # 清除奖励模型，保留候选数
/strategy reset         # 恢复 breadth=1，保留 /rm 设置
```

`/rm` 像 `/model` 一样打开选择页，标记当前奖励模型，并提供保留候选数的清除选项。
它复用 `/models` 的目录、连接配置和密钥解析，不改变生成模型。
只有配置了评分器且 `breadth>1` 时，才额外请求一次奖励模型批量评分有效候选；
默认、`breadth=1` 或没有评分器时，每步仍只请求一次生成模型。配置操作不请求模型。
`breadth` 必须是正整数，执行预算仍由 `max_steps` 控制。

Manager 保存全部候选、usage、评分和淘汰状态；后续消息及压缩沿选中分支工作。
评分器不序列化；`/rm` 设置仅在当前 CLI 进程内有效，同工作区启动/恢复前的设置会转交。
重启 CLI 后需重新设置 `/rm` 或通过 SDK 注入。本功能不执行多条路径或回溯。
完整用法见 [Agent](sdk/src/juice_agents/core/agent/README.zh-CN.md) 与
[探索示例](examples/README.zh-CN.md)。

## 架构

```text
Registry（静态配置） → Manager（运行时状态） ← Runner（执行调度）
```

- **Registry**：解析、验证和实例化对象
- **Manager**：管理生命周期、持久化和取消
- **Runner**：组合管理器并调度请求
- **Adapter**：映射 stdio/HTTP/WebSocket 协议

所有智能体模式通过 Runner 共享同一执行路径。状态持久化到工作区的 `.juice/`。

恢复或继承的会话拥有独立的嵌套状态，修改其输出或元数据不会污染源树或快照。

Registry 统一按“本地覆盖优先 → 只读内置兜底”解析。查看、校验、构造和运行
预置资源均不保存默认 Agent YAML 或 Team manifest；首次成功编辑才原子保存
完整的本地覆盖，删除覆盖后恢复内置，纯内置资源拒绝删除。`root` 是根入口，
`general` 仅作为子 Agent；模式限制与路径只作用于内存副本。会话、任务和运行记录
继续按原方式保存。新增工具写入 ToolRegistry 并登记到当前 Agent 声明，下一步即可调用。
root 通过与 `agents_list`、调度共用的可用性查询动态发现子 Agent。新增声明符合
当前模式条件后即可调用，名称和描述在下一次模型请求前更新；仍遵守 Plan 策略、
Team 成员范围与当前 Runner／模式的卸载状态。

```text
查询 / 运行 → Registry → 本地覆盖 → 内置兜底
编辑        → 校验 → 原子保存到 .juice
```

使用 `/agents [name]` 与 `/teams [name]` 查询；`/resources` 及其
`list_mode_resources` 网关接口已移除。查询返回 `local` / `builtin` 来源，
内置 Team 的 `manifest_path` 为 `null`。

## 开发

运行测试：

```bash
conda activate juice-agents
python -m pytest tests -q
npm test --prefix frontend --workspaces --if-present
npm run typecheck --prefix frontend --workspaces --if-present
npm run build --prefix frontend --workspaces --if-present
```

贡献代码应包含测试、文档更新和验证结果。开发指南见 [AGENTS.md](AGENTS.md)。

工具专属的提示词规范与示例维护在工具自身定义中，供 ReAct 和 CodeAct 共用；
详见 [工具创建契约](sdk/src/juice_agents/core/agent/README.zh-CN.md#工具创建契约)。

## 模块文档

- [SDK](sdk/README.zh-CN.md) · [适配层](adapters/README.zh-CN.md) · [示例](examples/README.zh-CN.md)
- [Core](sdk/src/juice_agents/core/README.zh-CN.md) · [Workflow](sdk/src/juice_agents/core/workflow/README.zh-CN.md)
- [CLI](frontend/cli/README.zh-CN.md) · [Web](frontend/web/README.zh-CN.md) · [前端共享模块](frontend/shared/README.zh-CN.md)

## 许可证

[Apache License 2.0](LICENSE)
