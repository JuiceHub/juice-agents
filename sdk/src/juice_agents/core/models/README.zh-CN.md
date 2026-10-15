> [English](README.md)

# Models 模块说明

`sdk/src/juice_agents/core/models/` 是 Juice 的统一模型层，负责 provider 适配。

## 能力

| 模型 | 用途 |
| --- | --- |
| `DoubaoModel` | 火山 Ark / Doubao Chat Completions |
| `OpenAIModel` | OpenAI SDK 兼容 Chat Completions |
| `AnthropicModel` | Anthropic Messages API |

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

## 开发约束

- `generate(messages, stop_sequence=None, *, cancel_event=None)` 的 provider 实现集中在模型层。`BaseChatModel` 负责校验、调用、解析、重试和取消；provider 子类只处理各自 API 边界。
- `reasoning_content` 与 `content` 分开返回。`normalize_token_usage()` 只归一化引擎实际返回的统计并保留 `provider_usage`。
