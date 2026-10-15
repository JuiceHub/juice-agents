# Models

> [简体中文](README.zh-CN.md)

`sdk/src/juice_agents/core/models/` is Juice's unified model layer for provider adapters.

## Models

| Model | Use |
| --- | --- |
| `DoubaoModel` | Volcengine Ark / Doubao Chat Completions |
| `OpenAIModel` | OpenAI SDK-compatible Chat Completions |
| `AnthropicModel` | Anthropic Messages API |

## Return contract

Every built-in model's `generate()` returns `{"role", "content", "reasoning_content", "usage"}`. `usage` records token counts returned by the provider/model engine without local estimation; it is `None` when the provider omits statistics.

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

## Development conventions

- Provider implementations of `generate(messages, stop_sequence=None, *, cancel_event=None)` stay in the model layer. `BaseChatModel` handles validation, calls, parsing, retries, and cancellation; provider subclasses handle only their API boundary.
- Return `reasoning_content` separately from `content`. `normalize_token_usage()` normalizes engine statistics and preserves `provider_usage`.
