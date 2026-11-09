> [English](README.md)

# Agent


## 生命周期与提交结果

```text
acquire → idle → running → persistent: idle / functional: released
                        └→ RoundResult(completed / failed / cancelled)
```

Manager 唯一负责开始、步骤和终态保存、实例释放。`AgentManager.run()` 返回
`RoundResult`，保留部分输出与原因。步数预算耗尽后仍有一次提交机会；仍未提交则为
`failed/max_steps_exceeded`。取消先保存意图，等待模型、工具和同步子调用收尾，再提交一次终态。
工作流内部 Agent 复用同一生命周期，但不公开事件、不保存会话正文。

`AgentManager.post()` 在 inbox 原子写入成功后发布 `message_received.data.attachments`，
无需等待模型或工具调用结束；重试按稳定消息 ID 去重。该事件仅表示接受输入，
会话保存并确认生产方后才清理 inbox；消息历史从 `describe_agent_sessions` 读取。
快照的 `pending_messages` 提供尚未写入上下文的输入，不会唤醒 Agent 或确认消费。
`round_start.data.input` 提供已保存的初始 task 和附件，也覆盖不经过 inbox 的 functional
子调用。前端展示事件和已保存附件；回合调度、消费确认以及 `round_end` 的结果提交均由后端负责。
每个 Agent 的 `log.txt` 用 `Message received` 记录已接受、等待写入上下文的消息，
functional 初始输入记录为 `Task`。日志失败不影响已接受消息和后续调度。

步骤保存后才发布 `step_end.data.step`。模型逻辑调用复用请求级包装与用量回调，
区分 generation/scoring/compression；共享模型上不绑定 Agent 身份。固定流程放系统提示词，
动态输入放用户消息；功能测试放在 `tests/`。

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

公开的 `FILE_TOOLS` 工具组在首次显式访问时创建，以当时的 cwd 为根目录。
导入 SDK 或工具类不创建文件工具。指定工作区时传入 `root_dir` 构造工具，
立即校验目录并将沙箱固定到该项目。

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

## 图片生成与编辑

`build_general_agent()` 和 Runner 仅在 `image.enabled: true` 时挂载
`generate_edit_image`；默认关闭，Plan 模式始终禁用。
通过 `image.model_name → image_models → providers` 独立选择 Ark 连接，
切换聊天模型或 GRM 不改变图片模型。

```yaml
image:
  enabled: true
  model_name: seedream_4
```

原图接受路径、HTTP(S) URL、bytes、PIL 图片和 `ObservationImage`。
生成与编辑统一调用 Ark `images.generate()`；编辑传原始网络 URL 或带正确 MIME
的 base64 data URL。结果存入 Runner 的观测图片目录。功能测试使用真实 Ark SDK
和模拟 HTTP，不访问真实生图服务。

任务图片、附件与图片观测在会话中保存本地路径或 HTTP(S) URL；OpenAI/Ark
聊天请求只在发送时编码本地文件，聊天与编辑共用 `agent_type.py` 的
`image_to_api_source()`，传输约定见[图片输入](../models/README.zh-CN.md#图片输入)。

## 协议

```xml
<!-- ReAct：执行前完成整个 actions 列表的协议校验。 -->
<thought>读取彼此独立的项目说明与配置。</thought>
<actions>[{"name":"read","args":{"path":"README.md"}},{"name":"read","args":{"path":"pyproject.toml"}}]</actions>
```

```xml
<!-- CodeAct：只在选中后才将代码交给智能体的 Python 运行时。 -->
<thought>计算结果。</thought>
<code>submit_output(output=6 * 7)</code>
```

`submit_output` 结束本轮；空 ReAct actions 写入观测并继续下一步；协议错误反馈给
下一次模型调用修正。`max_steps` 控制执行预算，保留现有最后一次提交的兜底流程。
ReAct 会在回放前修复输出末尾连续重复的 `</actions>`；其他尾随内容仍作为协议错误
拒绝。JSON 字符串和 thought 中的标签保持原样。

ReAct 将校验通过的完整 action 列表交给 `ToolManager`。同一 step 的 actions 默认
并发；有依赖或操作冲突的动作必须拆到不同 step。例如先同时读取文件，在下一步
利用读取结果编辑文件，再用后续 step 检查修改：

```text
step 1: [读取 A, 读取 B] → 等待两者完成
step 2: [根据 A、B 编辑 C] → 等待完成
step 3: [检查 C]
```

每步等待全部前台 action 完成。执行期间持续输出 `tool_start` / `tool_end`，
`tool_end` 按实际完成顺序到达；observations 与持久化结果仍按原 action 顺序排列。
后台 action 返回启动回执，结果由后续通知送达。资源锁和主线程屏障可能约束工具
执行，见 [ToolManager 约定](../managers/README.zh-CN.md)。CodeAct 的普通 Python
调用仍按语句顺序执行；上述 ReAct 调度规则不会自动将代码块并行化。

工具展示只消费 `tool_start` / `tool_end`，用框架分配的 `call_id` 关联，
`step_end.data.step.tool_calls` 保存同一身份和结果供历史回放。CodeAct 的可见 Python
代码块记录使用独立于内部工具调用的 ID。工具返回内容与执行时捕获的异常留在
该调用的 observation/error 中，不额外生成 step 错误；状态取自执行结果，
不根据输出文本猜测失败。
提交值的 schema 校验错误仍属于 step 错误；已经完成的 Python 调用在历史中
保留成功状态与观测结果。

```text
tool_start(call_id) → 运行中记录 → tool_end(同一 call_id) 更新原记录
                               └→ 保存 tool_calls 中的最终结果
```
构造 Agent 时可通过 `tool_executor_max_workers` 调整前台并发上限，默认值为 8。

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
from juice_agents.core.models.reward_models import GenerativeRewardModel

# OpenAI 从环境变量读取 OPENAI_API_KEY。
scorer = GenerativeRewardModel(model_name="your-model")
agent.set_exploration_strategy(TreeOfThoughtsStrategy(breadth=3, reward_model=scorer))
```

[模型层](../models/README.zh-CN.md#奖励模型)提供完整直接构造示例，以及可选的
`GenerativeRewardModel.from_config(...)` 目录解析入口，负责评分提示词、provider
调用和分数解析。策略使用注入的评分器选择候选。无效评分和模型异常在执行候选前
明确报错。

自定义评分器继承普通基类 `BaseRewardModel`，覆盖异步 `evaluate_batch` 方法。
与 `BaseChatModel` 一样，调用未实现的基类方法时抛出 `NotImplementedError`：

```python
from juice_agents.core.models.reward_models import BaseRewardModel

class MyRewardModel(BaseRewardModel):
    async def evaluate_batch(self, *, context: list[dict], actions: list[dict]) -> dict:
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

界面历史通过 `serialize_context_transcript(tree)` 导出选中叶子的完整祖先链，保留压缩前
原始记录并排除淘汰候选；会话检查与首条请求预览共用此只读导出。

Agent 收集生成、摘要及策略评分返回值或异常中的 `usage_calls`，显式转交
`ModelManager` 持久化及汇总，写入 Runner 根下的
`agents/model_usage.jsonl`。淘汰候选、空响应重试、评分失败及取消前已知用量均计入，
批量评分只记一次；functional 或 ephemeral 调用即使不保存 session，也保存 token 元数据。

用量文件不包含 prompt 或 transcript，压缩与恢复均保留。`ContextTree` 只管理对话状态。
`ActionStep.usage` 保留最后生成响应的用量，用于追踪与窗口校准，不能重复累计入账。
通过 `runner.agent_manager.model_usage_records()` 查看明细，
[`runner.token_usage()`](../runner/README.zh-CN.md#token-统计) 查看汇总。

快照保存在 `.juice/sessions/<runner_id>/agents/<agent_id>/session.json`。
恢复时创建全新的 Agent 并加载上下文。向 `AgentManager.acquire(session_state=...)`
传入 `ContextTree` 或序列化映射，或通过 `deserialize_snapshot` 查看快照时，均生成
独立上下文；嵌套输出、工具参数、附件、用量和元数据不会与调用方共享。评分器是
运行时依赖，不进入快照；恢复后需重新注入才能启用多候选选择，否则保持线性执行。

## 持久消息调度

`AgentManager.post(agent_id, attachment)` 接收带稳定 `message_id` 的
`RuntimeAttachment`，在 `inbox.json` 原子落盘后返回回执。加载身份与会话时调度关闭，
绑定回调后调用幂等 `activate()`；运行中消息在安全步骤接收，idle 时自动启动 round。

```text
agents/<agent_id>/
  manifest.json   身份与可见状态
  session.json    上下文 + agent_status + round_checkpoint + round_results + 已提交消息 ID
  inbox.json      尚未完成上下文交接或生产方确认的消息
```

输入节点、round ID、状态和剩余步骤预算随上下文同次保存；最终步骤与终结状态同次
提交。恢复 active round 不重复创建输入节点；确认失败保留 inbox 并重试，已提交的
消息不重复追加。最终步骤期间到达的消息转入新 round，通过 `message_deferred`
事件通知等待方。损坏或失效目标保留可见失败，不反复唤醒。

会话文件是提交边界：先写 manifest 摘要，最后写权威 session。历史保存每一轮的
真实结果，不从输出文本或提交意图推断成功。

`cancel_round(round_id)` 先保存取消意图再中断同步调用。persistent 实例结束后回到 idle，
新消息仍可唤醒；functional 实例结束后释放。尚未开始的 round 将取消意图保存在 inbox，
等待前一轮退出后交接，不覆盖旧轮
检查点。独立异步任务单独取消，functional Agent 必须在调用内完成工作。`close()`
关闭调度资源。会话仅由单个 Runner 持有调度权；工具副作用不保证仅执行一次。

## 开发约定

内置 `astep()` 通过 `ModelManager.acall()` 直接 await 生成、评分和压缩；
同步 `step()` 适配完整异步流。AgentManager 先持久化候选再进入工具执行。
同步自定义 Agent/工具通过有界阻塞适配器执行，取消等待清理完成。
独立同步扩展通过 `ModelManager.invoke_sync`
仅桥接一次完整调用，保持请求级用量、取消和事件身份；不再包装 SDK 的各个入口。

| 模块 | 职责 |
| --- | --- |
| `agents.py` | 生成、解析、选择，然后执行 ReAct/CodeAct 输出 |
| `context/strategies/` | 候选数量与评分选择，不修改树 |
| `../models/reward_models.py` | BaseRewardModel 基类与 GRM 子类，不依赖策略或上下文树 |
| [AgentManager](../managers/agents/README.zh-CN.md) | 实例生命周期、候选检查点和 ModelManager 身份绑定 |
| `context/` | 上下文树、选中分支消息、压缩和序列化 |
| `tools/`、`prompts/` | 工具声明/运行时绑定与固定协议提示词 |

Persistent 智能体复用活跃实例；functional 智能体在请求结束后释放。禁止原地重建
活跃 Agent 或创建 Agent 私有 ToolManager。用户提示词仅包含动态任务输入，固定
流程放在系统提示词。功能测试放在 `tests/core/agent/`，日志注明 Runner/Agent ID。

```bash
conda activate juice-agents
python -m pytest tests/core/agent -q
```

自定义策略重写 `aselect()`，同步 `select()` 适配它。ReAct 调度通过协程等待工具
结果，不占用流等待线程；异步屏障工具和 CodeAct 共用每个 Agent 的稳定亲和线程，
普通同步工具本体仍在有界工作池执行。
