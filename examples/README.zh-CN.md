> [English](README.md)

# 业务示例（examples）

每个示例都有内置任务，无参数直接运行主文件即可用真实模型展示功能。
示例所在目录是固定工作区：配置与 Runner 状态保存在 `.juice/`，生成文件也留在该工作区。

| 场景 | 在仓库根目录运行 | 展示功能 |
| --- | --- | --- |
| 可追溯研究 | `python examples/agent_research/__main__.py` | 研究本地资料、建立论点与证据关联并输出报告 |
| 能力演化 | `python examples/agent_evolution/__main__.py` | 创建并调用可复用工具或专职智能体 |
| 反馈运营 | `python examples/group_operations/__main__.py` | 协调专职智能体完成反馈分类和行动优先级分析 |
| 团队交付 | `python examples/team_delivery/__main__.py` | 协作分配任务、实现有限需求并完成验证 |
| 每步候选选择 | `python examples/agent_tot/__main__.py` | 每步生成三个候选、评分并执行胜出候选 |

## 配置与运行

进入环境，为要运行的示例配置模型：

```bash
conda activate juice-agents
python -m pip install -e ./sdk
mkdir -p examples/agent_tot/.juice
cp sdk/src/juice_agents/core/config/defaults.yaml examples/agent_tot/.juice/config.yaml
# 编辑 config.yaml；provider API 密钥可填写在仓库根目录 .env
python examples/agent_tot/__main__.py
```

其他示例需要分别配置自己的 `.juice/config.yaml`，可用 `--model <模型名>` 选择该示例
目录中的模型。所有示例都可复用仓库根目录 `.env`，无需分别复制密钥。凭证按以下
优先级取值：进程环境 > 示例目录 `.env` > 仓库根目录 `.env` > `~/.juice/.env`。
示例和仓库的文件路径固定，不受启动目录或 IDE 工作目录影响。示例实际请求模型，需要可用的 provider
凭证。研究示例默认使用本地资料，不额外要求搜索 API 密钥。

主文件也支持 IDE 运行、绝对路径和任意启动目录。将下方路径替换为实际仓库路径：

```bash
python /path/to/juice-agents/examples/agent_tot/__main__.py
```

从仓库根目录仍可使用原有模块入口：

```bash
python -m examples.agent_tot
```

位置参数可覆盖默认任务。通用选项包括 `--model`、`--model-effort`、
`--agent-type react|codeact`、`--permission-mode default|accept` 和 `--resume RUNNER_ID`；
通过 `--help` 查看各场景参数。

默认在终端中确认需要审批的工具操作；非交互运行时，可显式添加
`--permission-mode accept` 允许工具操作。`--model`、`--model-effort` 和
`--agent-type` 覆盖当前 root Agent，协作成员保留各自的模型和协议配置。

```bash
python examples/agent_research/__main__.py "比较本地项目资料中的部署方案"
python examples/team_delivery/__main__.py --resume <runner_id> "继续未完成任务并完成验证"
```

运行时输出工作区、任务、Runner ID、步骤、工具调用和最终结果。不传 `--resume` 时
创建新 Runner；退出时释放 Runner，持久化状态仍可用于恢复。
恢复时保留会话原有的审批模式。

## ToT 候选选择

`agent_tot` 默认启用 `--strategy tot --breadth 3`。`GenerativeRewardModel`（GRM）
从 `juice_agents.core.models.reward_models` 导入，直接调用 provider SDK 生成 JSON
评分并解析为数值，默认使用 root Agent 的逻辑模型名，创建独立 SDK client。
入口使用 `GenerativeRewardModel.from_config(name, context)`，
复用同一示例工作区的目录、连接配置及上述凭证优先级，构造和空批次均不解析 provider。
通过 `--reward-model` 可独立选择目录中的奖励模型：

```bash
python examples/agent_tot/__main__.py
python examples/agent_tot/__main__.py "设计缓存方案" --model <生成模型名> --reward-model <奖励模型名>
python examples/agent_tot/__main__.py --strategy greedy
```

```text
相同上下文 → 生成 breadth 个候选 → 校验 → 评分 → 执行胜出候选 → 下一步
                                         └── 输出候选评分和选中项
```

只执行选中的候选，淘汰候选保留在持久化树中。`--strategy greedy` 或 `--breadth 1`
每步只请求一次生成模型，不评分；当前能力是每步择优，不执行多条路径或回溯。
通过 `main(..., reward_model=GenerativeRewardModel(model_name="your-model", client=sdk_client))`
可使用与 backend 对应的原生 SDK client，无需评分配置文件。
也可用 `main(..., reward_model=scorer)` 注入自定义评分器，要求
`evaluate_batch(context=..., actions=...)` 返回含 `scores`、`usage` 和 `usage_calls` 的 dict，
其中 `scores` 按输入顺序为每个有效候选提供一个有限数值。
评分器对象不持久化；入口在恢复时重新配置 GRM，自定义评分器需调用方再次注入。

## 开发约定

```text
默认/自定义任务 → 场景系统指令 → Juice(workspace=示例目录) → Runner stream
                                                           └── 工作区状态与产物
```

- 用户提示词只存放动态任务数据；流程、边界和验收标准写入场景系统指令。
- 每个 `__main__.py` 提供有用的默认任务，保留直接运行和模块运行。默认任务控制规模，无需用户事先准备外部输入文件。
- 使用公开的 `Juice(...).runners.create/resume` API，以自身目录作为 workspace，不增加 `--workspace`。通用参数、事件展示和 Runner 清理复用 `examples/_runtime.py`。
- SDK 的 `Runner.stream` 不解析网关的 `$workflow` 语法；研究入口通过现有 `ManualCapability.install` 显式授权 `deep_research`，无需开启自动工作流。
- 调用真实配置的模型，不加入 mock、假产物或静默降级。成功和异常路径都调用 `runner.stop()`。
- 功能测试放在 `tests/examples/`，结束后不遗留生成文件，并更新本 README 和[项目 README](../README.zh-CN.md)。
