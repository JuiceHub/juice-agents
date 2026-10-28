# Models

> [简体中文](README.zh-CN.md)

`sdk/src/juice_agents/core/models/` separates chat generation from reward scoring.

## Model types

| Module | Contract | Implementations |
| --- | --- | --- |
| `chat_models.py` | `BaseChatModel.generate(...) -> dict` | `ChatModel(backend=...)` |
| `reward_models.py` | `BaseRewardModel.evaluate_batch(...) -> dict` | `GenerativeRewardModel` |

```python
from juice_agents.core.models.chat_models import BaseChatModel, ChatModel
from juice_agents.core.models.reward_models import GenerativeRewardModel, BaseRewardModel
```

The package root also exports these names. Internal consumers use the explicit
module for their role. `GenerativeRewardModel` (GRM) calls the provider SDK
directly to generate JSON scores and parses them into numbers.

```text
Agent ── ChatModel(backend).generate() ──> provider SDK ──> candidate outputs
Strategy ── GRM.evaluate_batch() ──> provider SDK ──> JSON scores ──> parse/select
```

## Chat model

`ChatModel` is the concrete subclass of `BaseChatModel`. Select `openai` (default),
`doubao`, or `anthropic` with `backend`; SDK differences stay inside the class:

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
    {"role": "system", "content": "Answer concisely."},
    {"role": "user", "content": "Compute 6 * 7"},
])
print(response["content"])
```

`api_base` and `api_key` are optional SDK settings. To inject a native client,
use `ChatModel(model_name="your-model", client=OpenAI())` after
`from openai import OpenAI`; the client must match `backend`.
`thinking=None` defaults to disabled; `max_retry_times=3` and `wait_time=1`
control retries for empty responses, connection/timeouts, HTTP 408/409/429, and
5xx failures. Other HTTP errors and unclassified exceptions fail immediately;
error-message keywords do not determine retries. Anthropic accepts `max_tokens=4096`
and `effort_config=None`. Output budgets above 8192 tokens use its streaming API
when available, including calls without a cancellation token, so high effort
and summary requests respect the SDK's long-request limits. Lower budgets retain
the blocking API unless cancellation is requested; cancellation watchers are
created only for calls with a token. `generate(messages, stop_sequence=None, *, cancel_event=None)`
preserves response normalization, cancellation, and provider cache handling.

## Reward model

Construct GRM with SDK connection parameters; no configuration file is required:

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
    context=[{"role": "user", "content": "Compute 6 * 7"}],
    actions=[{"role": "assistant", "content": "42"}],
)
scores = result["scores"]
```

The SDK client is created on the first nonempty batch and then reused.
`backend` accepts `openai` (default), `doubao`, or `anthropic`; `api_base` and
`api_key` are optional SDK settings. `max_tokens=4096` limits Anthropic output.
Alternatively, inject a native SDK client matching the backend:

```python
import os

from openai import OpenAI
from juice_agents.core.models.reward_models import GenerativeRewardModel

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
scorer = GenerativeRewardModel(model_name="your-model", client=client)
```

`client` must expose the native SDK API, rather than a `generate()` wrapper.

To reuse an existing catalog and its connection and credential configuration:

```python
from juice_agents.core.config.context import ConfigurationContext

scorer = GenerativeRewardModel.from_config(
    "judge", ConfigurationContext.from_workspace("."),
)
```

`judge` remains the scorer's logical `model_name`. On the first nonempty batch,
`from_config` uses `load_runtime_model_config` to read the backend, credentials,
endpoint, and provider model name, then creates and reuses its own SDK client.
Construction and empty batches do not resolve configuration or create clients.

Catalog-backed chat and reward models share the [configuration layers](../config/README.md): base → user → project → transient overrides; credentials resolve from process environment, project `.env`, then `~/.juice/.env`. Passing a context keeps model and tool credentials tied to the same workspace without exporting secrets into the process environment.

One request generates `{"scores": [number, ...]}` for the entire batch; empty
batches make no request. Fixed evaluation rules stay in its system prompt,
while context and candidates are untrusted JSON data in
the user message. The shared JSON parser repairs structured output and requires
one finite score per candidate in input order. Higher is better; invalid responses
and provider failures propagate to the caller.

The SDK handles network retries; empty or malformed scoring responses fail
explicitly. GRM does not send vendor thinking settings or share the Agent's effort.

Inject the scorer into `TreeOfThoughtsStrategy(reward_model=scorer, breadth=3)`
for per-step selection. The scorer is a runtime dependency and is not serialized.
`BaseRewardModel` is a regular base class, following `BaseChatModel`'s convention.
`GenerativeRewardModel` subclasses it; custom scorers also subclass it and override
`evaluate_batch`. Calling the unimplemented base method raises `NotImplementedError`.
The strategy uses this scoring interface, allowing dedicated reward providers or
local scoring rules to be supplied independently.

## Return contract

Models return usage for the current invocation; they do not collect, accumulate,
or persist it. Instances have no mutable usage history.

| Method | Result fields |
| --- | --- |
| `ChatModel.generate(...)` | `role`, `content`, `reasoning_content`, `usage`, `usage_calls` |
| `GenerativeRewardModel.evaluate_batch(...)` | `scores`, `usage`, `usage_calls` |

```python
result = scorer.evaluate_batch(context=[], actions=[{"content": "42"}])
print(result["scores"])       # One finite score per candidate, in input order.
print(result["usage"])        # Last response's normalized usage, or None.
print(result["usage_calls"])  # This invocation's observable SDK attempts.
```

Each `usage_calls` item contains `backend`, `model_name`, and normalized `usage`
(`input_tokens`, `output_tokens`, `total_tokens`, and original `provider_usage`
where reported). Missing usage stays `None`; Anthropic streams preserve reported
usage. Empty-response retries remain separate attempts. Failed calls, including
invalid scores or cancellation, expose the same list on the exception's
`usage_calls` attribute so known usage can still be reported.

An empty scoring batch returns `{"scores": [], "usage": None, "usage_calls": []}`.
One batch request yields one attempt, regardless of candidate count. Agents
forward generation and scoring usage to `AgentManager` for persistence and
aggregation; see [Runner](../runner/README.md#token-usage).
SDK-internal transparent retries and server usage never reported to the SDK
cannot be reconstructed accurately. Unknown usage is not a zero bill; no token
estimates or cost calculations are made.

## Development conventions

- `BaseChatModel` owns the shared generation lifecycle. Keep SDK construction and backend differences inside `ChatModel` in `chat_models.py`; callers select `backend` through this single concrete class. Preserve response, retry, cancellation, cache, and thinking behavior across backends.
- Return `reasoning_content` separately from `content`. ChatModel and GRM reuse the pure `normalize_token_usage()` conversion in `core/utils.py`, also exported from the model package; it preserves `provider_usage` without holding recording state.
- Return per-invocation `usage_calls`, including every observable attempt; attach it to failures before raising. Agents forward usage to `AgentManager` for persistence and totals; Models retain no instance-level usage history.
- Keep reward prompts, provider calls, and score parsing in `models/reward_models.py`; strategies consume the injected scorer and own selection rules. Import the catalog configuration type only under `TYPE_CHECKING` to avoid a model/catalog initialization cycle.
- Functional chat and scoring tests live in `tests/core/models/`; agent selection and gateway configuration tests remain with their consumers.
