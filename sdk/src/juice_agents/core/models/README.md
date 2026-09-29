# Models

> [简体中文](README.zh-CN.md)

`sdk/src/juice_agents/core/models/` is Juice's unified model layer for provider adapters and model-composition strategies.

> [Chinese](README.zh-CN.md)

## Models

| Model | Use |
| --- | --- |
| `DoubaoModel` | Volcengine Ark / Doubao Chat Completions |
| `OpenAIModel` | OpenAI SDK-compatible Chat Completions |
| `AnthropicModel` | Anthropic Messages API |
| `CompositeModel` | Aggregate outputs from multiple logical models |

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

`CompositeModel.usage` aggregates successful subcalls and keeps each call's `phase`, `name`, and `usage` details in `calls`.

`CompositeModel` supports three strategies:

| Strategy | Behavior |
| --- | --- |
| `judge_select` | Candidate models answer; a judge returns a JSON winner and the best candidate is selected |
| `synthesize` | Candidate models answer; a final model synthesizes one response |
| `iterative_refine` | A generator answers, a reviewer checks each response, and rejected responses are revised with critique |

## Configuration example

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

`backend: composite` references existing logical models under `models.*` and does not configure credentials directly.

## Development conventions

- Provider implementations of `generate(messages, stop_sequence=None, *, cancel_event=None)` stay in the model layer. `BaseChatModel` handles validation, calls, parsing, retries, and cancellation; provider subclasses handle only their API boundary.
- Return `reasoning_content` separately from `content`. `normalize_token_usage()` normalizes engine statistics and preserves `provider_usage`; the composite model counts successful subcalls only.
- Composite models do not read YAML or environment variables. Model catalog handles parsing and recursive-reference checks. Judge and reviewer results go through `parse_model_json`; candidate failures may degrade, while judge, synthesis, review, or revision failures raise.
- Put fixed strategy instructions in the system message and dynamic conversations, candidate answers, and critiques in the user message.
