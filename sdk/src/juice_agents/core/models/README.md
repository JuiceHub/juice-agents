# Models

> [简体中文](README.zh-CN.md)

`sdk/src/juice_agents/core/models/` separates chat generation from reward scoring.

## Model types

| Module | Contract | Implementations |
| --- | --- | --- |
| `chat_models.py` | `await BaseChatModel.generate(...) -> dict` | `ChatModel(backend=...)` |
| `reward_models.py` | `await BaseRewardModel.evaluate_batch(...) -> dict` | `GenerativeRewardModel` |

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
import asyncio
import os

from juice_agents.core.models.chat_models import ChatModel

async def main():
    async with ChatModel(
        model_name="your-model", backend="openai",
        api_key=os.environ["OPENAI_API_KEY"],
    ) as model:
        response = await model.generate([
            {"role": "system", "content": "Answer concisely."},
            {"role": "user", "content": "Compute 6 * 7"},
        ])
        print(response["content"])

asyncio.run(main())
```

`api_base` and `api_key` are optional SDK settings. All built-in model methods
are native coroutines; the examples below with `await` run inside an async function.
Use `async with model` or `await model.aclose()` to release framework-created
clients. Inject only native **async** SDK clients matching the backend, such as
`AsyncOpenAI`; injected clients remain caller-owned. Create, use and close a model
on the same event loop. A fresh `asyncio.run()` for each call invalidates pooled
connections and is rejected; use it once at the application's async entry point.

`generate(messages, stop_sequence=None, *, stream=False)` returns one complete
normalized result. `stream=True` selects SDK streaming and retains usage received
before cancellation; it does not turn the method into an iterator. Anthropic output
budgets above 8192 tokens always use streaming. `thinking=None` disables thinking;
Anthropic also accepts `max_tokens=4096` and `effort_config=None`.

`max_retry_times=3` is the maximum number of attempts for **empty responses**;
`wait_time=1` is the async delay between them. Connection, timeout, rate-limit and
HTTP failures are retried only by the provider SDK, preventing nested network
retry loops. Unknown exceptions propagate immediately.

Cancel the task running `generate()` or `evaluate_batch()` and await its completion.
Cancellation covers response headers, stream reads, SDK backoff and empty-response
retry sleeps. Streams close before `asyncio.CancelledError` propagates, with known
`usage_calls` attached. Concurrent calls keep separate usage and cancellation state.
Real TCP/SSE regression coverage lives in `tests/core/models/test_request_cancellation.py`.

Models contain no SDK proxies or dedicated event loop. Runtime Agents await
`ModelManager.acall()` directly for generation, scoring and compression. The
manager owns events, usage and framework-created clients; injected SDK clients
remain caller-owned. Runner sync/async entry points share one persistent execution
loop. Direct model users keep calls and cleanup on their own loop.

### Image input

For OpenAI/Ark chat requests, `image_url.url` accepts a local file path, an
HTTP(S) URL, or an existing base64 data URL:

```python
response = await model.generate([{
    "role": "user",
    "content": [
        {"type": "text", "text": "Describe this image."},
        {"type": "image_url", "image_url": {"url": "/path/to/photo.jpg"}},
    ],
}])
```

At send time, local files become `data:<actual MIME>;base64,...`; HTTP(S)
links remain remote without a local download, and existing data URLs are kept.
The MIME type comes from image contents, even when the filename has a different
extension. Invalid or missing local files fail before the provider request.
The conversion does not mutate messages or session state, so user images,
tool observations, attachments, and resumed sessions keep their original paths.
Image editing reuses `image_to_api_source()` from `agent/agent_type.py`; keep
transport encoding there and provider request conversion in the model layer.
Functional coverage lives in `tests/core/models/test_image_transport.py` and
uses real SDK clients with mock HTTP transport.

## Reward model

Construct GRM with SDK connection parameters; no configuration file is required:

```python
import os

from juice_agents.core.models.reward_models import GenerativeRewardModel

async with GenerativeRewardModel(
    model_name="your-model",
    backend="openai",
    api_base="https://api.openai.com/v1",
    api_key=os.environ["OPENAI_API_KEY"],
) as scorer:
    result = await scorer.evaluate_batch(
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

from openai import AsyncOpenAI
from juice_agents.core.models.reward_models import GenerativeRewardModel

async with AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"]) as client:
    scorer = GenerativeRewardModel(model_name="your-model", client=client)
    result = await scorer.evaluate_batch(context=[], actions=[{"content": "42"}])
```

`client` must expose the native asynchronous SDK API. The caller awaits its
`close()` on the owning loop after use; model `aclose()` leaves injected clients open.

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

Chat and GRM both select entries from `models`, which reference shared
`providers`; image entries live only in `image_models`. Their selections and
SDK clients remain independent. At an idle boundary,
`await scorer.reload_config(context)` closes and discards only a catalog-owned connection and
defers recreation until the next nonempty batch. It retains the logical model
choice and leaves directly supplied clients untouched. Runner's explicit config
reload applies this to attached GRMs without changing exploration breadth.

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

`await evaluate_batch(..., stream=False)` follows the same task cancellation and
cleanup contract as generation. `stream=True` preserves reported partial usage.

Managed generation, scoring and compression share a request-scoped invocation
wrapper. Public `model_start` / `model_end` events use one `model_call_id` per
logical call and distinguish `purpose="generation"`, `"scoring"`, or
`"compression"`. Observable attempts stay in `usage_calls`; SDK-internal network
retries are transparent. Callbacks and
Agent identity are never installed on a shared model instance.

## Return contract

Models return usage for the current invocation; they do not collect, accumulate,
or persist it. Instances have no mutable usage history.

| Method | Result fields |
| --- | --- |
| `ChatModel.generate(...)` | `role`, `content`, `reasoning_content`, `usage`, `usage_calls` |
| `GenerativeRewardModel.evaluate_batch(...)` | `scores`, `usage`, `usage_calls` |

```python
result = await scorer.evaluate_batch(context=[], actions=[{"content": "42"}])
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
forward generation and scoring usage to `ModelManager` for persistence and
aggregation; see [Runner](../runner/README.md#token-usage).
SDK-internal transparent retries and server usage never reported to the SDK
cannot be reconstructed accurately. Unknown usage is not a zero bill; no token
estimates or cost calculations are made.

## Development conventions

- `BaseChatModel` owns the shared generation lifecycle. Keep SDK construction and backend differences inside `ChatModel` in `chat_models.py`; callers select `backend` through this single concrete class. Preserve response, retry, cancellation, cache, and thinking behavior across backends.
- Return `reasoning_content` separately from `content`. ChatModel and GRM reuse the pure `normalize_token_usage()` conversion in `core/utils.py`, also exported from the model package; it preserves `provider_usage` without holding recording state.
- Return per-invocation `usage_calls`, including every observable attempt; attach it to failures before raising. Agents forward usage to `ModelManager` for persistence and totals; Models retain no instance-level usage history.
- Keep reward prompts, provider calls, and score parsing in `models/reward_models.py`; strategies consume the injected scorer and own selection rules. Import the catalog configuration type only under `TYPE_CHECKING` to avoid a model/catalog initialization cycle.
- Functional chat and scoring tests live in `tests/core/models/`; agent selection and gateway configuration tests remain with their consumers.
