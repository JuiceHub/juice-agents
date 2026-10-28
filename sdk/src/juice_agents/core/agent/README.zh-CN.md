> [English](README.md)

# Agent

## 项目工作区

根 Agent、子 Agent 和恢复会话中的 Agent 复用 Runner 的 `ConfigurationContext`；
通过 `tool_manage` 运行时新增的工具也使用同一上下文。文件工具以项目为根，Shell
默认在项目运行，相对 `workdir` 相对项目解析。Skills 从项目 `.juice/skills`、
项目 `.agents/skills` 和用户 `~/.agents/skills` 发现，自定义 Skills 存储不会改变项目根目录。

```text
ConfigurationContext → Agent / 运行时工具 → 项目文件与 .juice 状态
```

CodeAct 和 `python` 工具把普通 `open`、具体 `pathlib.Path`、标准 `os` 文件调用和
子进程工作目录绑定到 workspace，不修改 SDK 进程目录。第三方 API 请传入项目绝对路径，
或通过 `shell` 运行项目脚本。机器相关路径只放在构造上下文中，不写入 Agent YAML。

ReAct 和 CodeAct 实现单步协议；`AgentManager` 管理生命周期、上下文检查点和取消。
工具统一经过 Runner 的权限边界执行。

## 内置声明

`build_general_agent()` 构造名称为 `root` 的根 Agent；`general` 仅作为子 Agent。
SDK 构建函数和 Runner 均由 AgentRegistry 按本地 YAML 优先、统一内置目录兜底解析。
查看、校验与构造不保存默认声明。Agent list/view 工具对内置资源返回
`source: builtin`、`path: null`，对本地声明返回 `source: local` 及实际路径。

```text
读取 / 校验 / 构造 → 本地覆盖 → 内置兜底
成功编辑           → 校验 → 原子保存完整本地覆盖
删除本地覆盖       → 恢复内置定义
```

运行路径、协议临时覆盖与模式限制仅作用于内存副本。首次成功编辑才生成
`.juice/agents/<name>.yaml`；删除覆盖即恢复内置声明。Team 私有成员的自身编辑
保留在私有目录，缺失时不回退到同名全局 Agent。

## 动态发现子 Agent

绑定 Runner 的 Agent 在调用时经 `RunnerContext` 查询当前可用子 Agent，与
`agents_list`、Runner 调度复用同一 Registry 投影。root 动态发现符合条件的声明，
普通子 Agent 仍限于显式托管关系；启动时的名单快照不作为调用授权依据。
每次模型请求前比较可见名称和描述，变化时刷新系统提示词，不重建 Agent。

```text
agent_manage 保存 / Registry 编辑 → 当前可用性查询 → agent_tool
                                             └──→ 下一步系统提示词
```

`allowed_modes`、Plan 策略、Team 成员范围和当前 Runner／模式的卸载状态继续生效。
Team 查询还可展示待选共享 Agent，查看候选不代表加入 Team 前就可调度。
目标配置在 fresh acquire 时生效，已有目标会话遵守原生命周期；未绑定 Runner 的
SDK Agent 继续使用显式引用。

## 协议

```xml
<!-- ReAct：执行前完成整个 actions 列表的协议校验。 -->
<thought>阅读项目说明。</thought>
<actions>[{"name":"read_file","args":{"path":"README.md"}}]</actions>
```

```xml
<!-- CodeAct：只在选中后才将代码交给智能体的 Python 运行时。 -->
<thought>计算结果。</thought>
<code>submit_output(output=6 * 7)</code>
```

`submit_output` 结束本轮；空 ReAct actions 写入观测并继续下一步；协议错误反馈给
下一次模型调用修正。`max_steps` 控制执行预算，保留现有最后一次提交的兜底流程。

## 工具创建契约

`tool_manage` 的配置规范、完整 `save_args` 示例和验证要求，统一维护在它自身的
`description`、`inputs`、`outputs` 中，由 ReAct 和 CodeAct 现有渲染流程共用。
固定的工具规范放在工具定义中，不散落到公共系统段落或用户任务消息。

新建工具必须提供配置对象，Python 源码定义 `def forward(self, ...)`，参数与返回值
分别对应 `inputs` 和 `outputs`，可选参数提供默认值。完整字段和打印当前时间示例
见运行时渲染的 `tool_manage` 说明。

```text
保存声明 → 检查 runtime_refresh → 工具出现在可用列表 → 调用并核验
```

`tool_manage save/unload` 通过 AgentRegistry 持久化工具引用，`root` 同样保存到
`.juice/agents/root.yaml`。`scheduled` / `next_step` 表示当前 Agent 在下一次模型请求前，
复用已有运行时引用表刷新工具提示词和 CodeAct 函数，不重建 Agent。恢复会话时读取
最新声明并刷新快照中的旧提示词。只有声明可更新时返回 `fresh_acquire`；`unsupported`
不代表已激活。保存报错时工具 YAML 可能已经写入，重试前应先检查声明。分别报告
保存、登记和调用结果，临时执行 Python 不等于创建工具。测试直接执行渲染示例及
两种 Agent 协议，无需模型请求。

## 每步候选选择

```python
from juice_agents.core.agent.context.strategies import GreedyStrategy, TreeOfThoughtsStrategy

agent.set_exploration_strategy(TreeOfThoughtsStrategy(breadth=3, reward_model=scorer))
# 恢复每步一次模型请求：
agent.set_exploration_strategy(GreedyStrategy())
```

向 `GenerativeRewardModel`（GRM）传入原生 SDK client，生成 JSON 评分，无需配置文件：

```python
from openai import OpenAI

from juice_agents.core.models.reward_models import GenerativeRewardModel

# OpenAI 从环境变量读取 OPENAI_API_KEY。
scorer = GenerativeRewardModel(model_name="your-model", client=OpenAI())
agent.set_exploration_strategy(TreeOfThoughtsStrategy(breadth=3, reward_model=scorer))
```

[模型层](../models/README.zh-CN.md#奖励模型)提供完整直接构造示例，以及可选的
`GenerativeRewardModel.from_config(...)` 目录解析入口，负责评分提示词、provider
调用和分数解析。策略使用注入的评分器选择候选。无效评分和模型异常在执行候选前
明确报错。

自定义评分器继承普通基类 `BaseRewardModel`，覆盖同步 `evaluate_batch` 方法。
与 `BaseChatModel` 一样，调用未实现的基类方法时抛出 `NotImplementedError`：

```python
from juice_agents.core.models.reward_models import BaseRewardModel

class MyRewardModel(BaseRewardModel):
    def evaluate_batch(self, *, context: list[dict], actions: list[dict]) -> dict:
        # context 与生成候选时的消息一致；actions 仅包含协议有效的候选：
        # {"role": "assistant", "content": raw_output}
        # 本地规则无 SDK 请求；用户实现评分函数，高分更优。
        return {"scores": score_candidates(context, actions), "usage": None, "usage_calls": []}
```

```text
当前选中叶子
  ├─ 候选 0 [淘汰；原始输出 + usage + 评分]
  ├─ 候选 1 [选中] → 工具/代码执行一次 → 下一步候选
  └─ 候选 2 [淘汰；原始输出 + usage + 评分]
```

| 配置或结果 | 行为 |
| --- | --- |
| 默认、`breadth=1` 或未配置评分器 | 每步一次生成，不评分 |
| `breadth=N > 1` 且配置评分器 | 相同上下文生成 N 次，只执行一个输出 |
| 无效协议候选 | 保留协议错误，不参与评分 |
| 全部无效 | 选择首个候选，走现有协议恢复流程 |
| 同分 | 选择先生成的有效候选 |
| 评分失败、数量不符或非有限/非数值评分 | 执行前明确报错并结束本轮 |

`breadth` 必须为正整数。策略不含深度、保留路径数或温度配置；采样仍由模型管理。
本功能不执行多条路径、不隔离环境、不回溯。CLI 通过 `/strategy breadth=N` 控制
候选数；`/rm` 打开模型选择页，标记当前奖励模型并提供清除选项；
`/rm judge` 直接选择目录模型，`/rm reset` 清除奖励模型。选择和清除均保留候选数
及生成模型。`/strategy reset` 将候选数恢复为 1 并保留奖励模型。
配置仅在当前 CLI 进程内生效；未配置评分器时显示“线性执行”。

## 上下文与持久化

`ContextTree` 保存任务、动作和摘要节点。Manager 在执行前插入全部候选，仅推进一个
活跃叶子，然后原位更新选中节点的观测结果，不重复追加节点。ReAct 的流式事件和
持久化观测共用同一次工具执行结果。

普通 `steps` / `to_messages()` 沿活跃选中分支工作。淘汰节点保留在 `tree.nodes`
及序列化快照中。Remain 只投影较早的观测；compact 创建摘要续写分支，同时保留
原节点用于检查。多轮任务接在活跃分支后；`reset_session=True` 清空上下文树和历史。

Agent 收集生成、摘要及策略评分返回值或异常中的 `usage_calls`，显式转交
`AgentManager` 持久化及汇总，写入 Runner 根下的
`agents/model_usage.jsonl`。淘汰候选、空响应重试、评分失败及取消前已知用量均计入，
批量评分只记一次；functional 或 ephemeral 调用即使不保存 session，也保存 token 元数据。

用量文件不包含 prompt 或 transcript，压缩与恢复均保留。`ContextTree` 只管理对话状态。
`ActionStep.usage` 保留最后生成响应的用量，用于追踪与窗口校准，不能重复累计入账。
通过 `runner.agent_manager.model_usage_records()` 查看明细，
[`runner.token_usage()`](../runner/README.zh-CN.md#token-统计) 查看汇总。

快照保存在 `.juice/runners/<runner_id>/agents/<agent_id>/session.json`。
恢复时创建全新的 Agent 并加载上下文。向 `AgentManager.acquire(session_state=...)`
传入 `ContextTree` 或序列化映射，或通过 `deserialize_snapshot` 查看快照时，均生成
独立上下文；嵌套输出、工具参数、附件、用量和元数据不会与调用方共享。评分器是
运行时依赖，不进入快照；恢复后需重新注入才能启用多候选选择，否则保持线性执行。

## 开发约定

| 模块 | 职责 |
| --- | --- |
| `agents.py` | 生成、解析、选择，然后执行 ReAct/CodeAct 输出 |
| `context/strategies/` | 候选数量与评分选择，不修改树 |
| `../models/reward_models.py` | BaseRewardModel 基类与 GRM 子类，不依赖策略或上下文树 |
| `manager.py` | Agent 生命周期与候选检查点；持久化及汇总 Agent 转交的用量 |
| `context/` | 上下文树、选中分支消息、压缩和序列化 |
| `tools/`、`prompts/` | 工具声明/运行时绑定与固定协议提示词 |

Persistent 智能体复用活跃实例；functional 智能体在请求结束后释放。禁止原地重建
活跃 Agent 或创建 Agent 私有 ToolManager。用户提示词仅包含动态任务输入，固定
流程放在系统提示词。功能测试放在 `tests/core/agent/`，日志注明 Runner/Agent ID。

```bash
conda activate juice-agents
python -m pytest tests/core/agent -q
```
