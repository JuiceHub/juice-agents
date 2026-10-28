> [English](README.md)

# Models 模块说明

`sdk/src/juice_agents/core/models/` 按能力区分聊天生成与奖励评分。

## 模型类型

| 模块 | 接口契约 | 实现 |
| --- | --- | --- |
| `chat_models.py` | `BaseChatModel.generate(...) -> dict` | `ChatModel(backend=...)` |
| `reward_models.py` | `BaseRewardModel.evaluate_batch(...) -> dict` | `GenerativeRewardModel` |

```python
from juice_agents.core.models.chat_models import BaseChatModel, ChatModel
from juice_agents.core.models.reward_models import GenerativeRewardModel, BaseRewardModel
```

模型包根入口也导出上述名称；内部调用方按职责使用显式模块。
`GenerativeRewardModel`（生成式奖励模型，简称 GRM）直接调用 provider SDK
生成 JSON 评分，再解析为数值。

```text
Agent ── ChatModel(backend).generate() ──> provider SDK ──> 候选输出
策略 ── GRM.evaluate_batch() ──> provider SDK ──> JSON 评分 ──> 解析/选择
```

## 聊天模型

`ChatModel` 是 `BaseChatModel` 的具体子类，通过 `backend` 选择 `openai`（默认）、
`doubao` 或 `anthropic`，SDK 差异在类内部处理：

```python
import os

from juice_agents.core.models.chat_models import ChatModel

model = ChatModel(
    model_name="your-model",
    backend="openai",
    api_base="https://api.openai.com/v1",
    api_key=os.environ["OPENAI_API_KEY"],
)
response = model.generate([
    {"role": "system", "content": "简洁回答。"},
    {"role": "user", "content": "计算 6 * 7"},
])
print(response["content"])
```

`api_base`、`api_key` 为可选 SDK 配置。也可在 `from openai import OpenAI` 后使用
`ChatModel(model_name="your-model", client=OpenAI())` 注入与 backend 对应的原生 client。
`thinking=None` 默认关闭思考；`max_retry_times=3`、`wait_time=1` 控制空响应、连接/超时、
HTTP 408/409/429 及 5xx 错误的重试。其他 HTTP 错误和未知异常立即失败，不根据错误正文
关键词猜测是否重试。Anthropic 支持 `max_tokens=4096` 和 `effort_config=None`；输出预算
超过 8192 token 时优先使用可用的流式接口，无取消令牌的高强度推理和摘要调用也能遵守
SDK 长请求限制。较低预算默认沿用阻塞接口，传入取消令牌时改为流式；仅有取消令牌时
创建取消 watcher。
`generate(messages, stop_sequence=None, *, cancel_event=None)` 保持统一响应、取消和
provider 缓存处理。

## 奖励模型

直接传入 SDK 连接参数，无需配置文件：

```python
import os

from juice_agents.core.models.reward_models import GenerativeRewardModel

scorer = GenerativeRewardModel(
    model_name="your-model",
    backend="openai",
    api_base="https://api.openai.com/v1",
    api_key=os.environ["OPENAI_API_KEY"],
)
result = scorer.evaluate_batch(
    context=[{"role": "user", "content": "计算 6 * 7"}],
    actions=[{"role": "assistant", "content": "42"}],
)
scores = result["scores"]
```

首次非空批次才创建并复用 SDK client。`backend` 支持 `openai`（默认）、`doubao`
和 `anthropic`；`api_base`、`api_key` 为可选 SDK 配置，`max_tokens=4096` 限制
Anthropic 输出。也可直接注入与 backend 对应的原生 SDK client：

```python
import os

from openai import OpenAI
from juice_agents.core.models.reward_models import GenerativeRewardModel

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
scorer = GenerativeRewardModel(model_name="your-model", client=client)
```

`client` 必须提供原生 SDK API，不能传入仅提供 `generate()` 的包装对象。

需要复用现有模型目录、连接和凭证配置时，使用独立配置入口：

```python
from juice_agents.core.config.context import ConfigurationContext

scorer = GenerativeRewardModel.from_config(
    "judge", ConfigurationContext.from_workspace("."),
)
```

评分器的 `model_name` 保留逻辑名称 `judge`。`from_config` 在首个非空批次通过
`load_runtime_model_config` 读取 backend、凭证、地址和 provider 模型名，创建并
复用独立 SDK client；构造及空批次均不解析配置或创建 client。

通过模型目录创建的聊天模型和奖励模型复用[统一配置层级](../config/README.zh-CN.md)：基础配置 → 用户配置 → 项目配置 → 临时覆盖；凭据优先级为进程环境、项目 `.env`、`~/.juice/.env`。传递同一上下文可保证模型和工具使用目标 workspace 的凭据，且不向进程环境导出密钥。

一次请求为整个批次生成 `{"scores": [number, ...]}`，空批次不请求模型。
固定评分规则放在 system
提示词中，上下文及候选作为不可信 JSON 数据放在 user 消息中。复用统一 JSON
解析器修复结构化输出，要求按输入顺序为每个候选返回一个有限数值，高分更优；
非法响应和 provider 异常交由调用方处理。

网络重试由 SDK 负责，空响应或评分格式错误明确失败。GRM 不发送 provider 的
thinking 设置，也不共享 Agent 的推理强度。

将评分器注入 `TreeOfThoughtsStrategy(reward_model=scorer, breadth=3)` 即可启用
每步候选选择。评分器属于运行时依赖，不进入会话快照。
`BaseRewardModel` 与 `BaseChatModel` 一样使用普通基类写法。
`GenerativeRewardModel` 显式继承它；自定义评分器也继承该基类并覆盖
`evaluate_batch`，调用未实现的基类方法时抛出 `NotImplementedError`。
策略使用这一评分接口，专用奖励模型或本地评分规则也可独立接入。

## 返回契约

Models 只返回本次调用的用量，不收集、累计或持久化，模型实例不保存可变的用量历史。

| 方法 | 返回字段 |
| --- | --- |
| `ChatModel.generate(...)` | `role`、`content`、`reasoning_content`、`usage`、`usage_calls` |
| `GenerativeRewardModel.evaluate_batch(...)` | `scores`、`usage`、`usage_calls` |

```python
result = scorer.evaluate_batch(context=[], actions=[{"content": "42"}])
print(result["scores"])       # 按输入顺序为每个候选返回一个有限数值。
print(result["usage"])        # 最后响应的归一化用量，缺失为 None。
print(result["usage_calls"])  # 本次调用可观测的 SDK 尝试明细。
```

`usage_calls` 每项包含 `backend`、`model_name` 和归一化 `usage`，保留实际报告的
`input_tokens`、`output_tokens`、`total_tokens` 及原始 `provider_usage`。
缺失用量为 `None`，Anthropic 流式调用也保留已报告用量；空响应重试分别记录。
失败异常同样携带 `usage_calls` 属性，评分格式错误或取消时也可显式上报已知用量。

空评分批次返回 `{"scores": [], "usage": None, "usage_calls": []}`。
一次批量请求对应一次 SDK 尝试，不按候选数重复计算。Agent 收集生成及评分用量，
转交 `AgentManager` 持久化和汇总，见 [Runner](../runner/README.zh-CN.md#token-统计)。
SDK 内部透明重试或服务端未报告的用量无法准确还原；未知用量不是零账单，
不做 token 估算或费用计算。

## 开发约束

- `BaseChatModel` 承担公共生成生命周期；SDK 构造与 backend 差异集中在 `chat_models.py` 的 `ChatModel` 内，调用方通过唯一具体类的 `backend` 选择实现。保持各 backend 的响应、重试、取消、缓存及 thinking 行为。
- `reasoning_content` 与 `content` 分开返回。ChatModel 和 GRM 复用 `core/utils.py` 中的纯转换函数 `normalize_token_usage()`，模型包也导出它；保留 `provider_usage`，不保存记录状态。
- 返回本次调用所有可观测尝试的 `usage_calls`，抛异常前同样附上明细。Agent 收集后转交 `AgentManager` 持久化及汇总，Models 不保存实例级用量历史。
- 奖励模型提示词、provider 调用和分数解析集中在 `models/reward_models.py`；策略使用注入的评分器并负责选择规则。模型目录的配置类型仅在 `TYPE_CHECKING` 下导入，避免模型与目录初始化时循环依赖。
- 聊天生成与评分功能测试放在 `tests/core/models/`；Agent 选择和 Gateway 配置测试保留在各调用方模块。
