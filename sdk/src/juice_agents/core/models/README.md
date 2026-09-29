# Models 模块说明

`sdk/src/juice_agents/core/models/` 是 Juice 的统一模型层，负责 provider 适配和模型级组合策略。

## 能力

| 模型 | 用途 |
| --- | --- |
| `DoubaoModel` | 火山 Ark / Doubao Chat Completions |
| `OpenAIModel` | OpenAI SDK 兼容 Chat Completions |
| `AnthropicModel` | Anthropic Messages API |
| `CompositeModel` | 聚合多个逻辑模型输出 |

## 返回契约

所有内置模型的 `generate()` 返回 `{"role", "content", "reasoning_content", "usage"}`。`usage` 只记录 provider/模型引擎原始返回的 token 统计，不做本地估算；缺失时为 `None`。

```python
{
    "content": "...",
    "usage": {
        "input_tokens": 12,
        "output_tokens": 34,
        "total_tokens": 46,
        "provider_usage": {"prompt_tokens": 12, "completion_tokens": 34},
    },
}
```

`CompositeModel` 的 `usage` 会汇总所有成功子调用，并在 `calls` 中保留每次调用的 `phase/name/usage` 明细。

`CompositeModel` 支持三种策略：

| strategy | 行为 |
| --- | --- |
| `judge_select` | 候选模型生成回复，裁判模型返回 JSON winner，选择最佳候选 |
| `synthesize` | 候选模型生成回复，最终回复模型综合成一个答案 |
| `iterative_refine` | 生成器先答，评审模型逐个验收；拒绝时带 critique 修正 |

## 配置示例

```yaml
models:
  composite_best:
    backend: composite
    strategy: judge_select
    candidates:
      - model: doubao_lite
      - model: gpt4o_mini
        model_effort: medium
    judge:
      model: doubao_pro
      model_effort: high
    fail_fast: false
```

`backend: composite` 只引用 `models.*` 中已有逻辑模型，不直接配置密钥。

## 开发约束

- `generate(messages, stop_sequence=None, *, cancel_event=None)` 的 provider 实现集中在模型层。`BaseChatModel` 负责校验、调用、解析、重试和取消；provider 子类只处理各自 API 边界。
- `reasoning_content` 与 `content` 分开返回。`normalize_token_usage()` 只归一化引擎实际返回的统计并保留 `provider_usage`；复合模型汇总成功子调用，失败且无结果的调用不计入。
- 复合模型不读 YAML 或环境变量，解析与递归引用检查交给 model catalog。裁判和评审结果经 `parse_model_json` 解析；候选失败可降级，裁判、综合、评审或修正失败则抛错。
- 固定策略流程放 system message，原始对话、候选回答和 critique 等动态数据放 user message。
