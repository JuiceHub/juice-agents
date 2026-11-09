# Agent

> [简体中文](README.zh-CN.md)

ReAct and CodeAct provide step protocols. `AgentManager` owns lifecycle, context
checkpoints, and cancellation; tools execute through the Runner permission boundary.

## Lifecycle and committed results

```text
acquire → idle → running → persistent: idle / functional: released
                        └→ RoundResult(completed / failed / cancelled)
```

Manager alone starts rounds, commits steps/results and releases instances.
`AgentManager.run()` returns `RoundResult`, including partial output and a reason.
The step budget retains a final submission opportunity; exhaustion then fails
with `max_steps_exceeded`. Cancellation saves intent and waits for started model,
tool and synchronous child calls to unwind before committing once. Ephemeral
workflow Agents use this lifecycle without public events or saved transcript bodies.

`AgentManager.post()` emits `message_received.data.attachments` only after the
inbox write succeeds, before waiting for a model/tool boundary. Stable message
IDs suppress retry notifications. Acceptance does not acknowledge consumption:
the Manager removes the queue entry only after saving it in the receiving
session and acknowledging its producer. Read history through
`describe_agent_sessions`, not `inbox.json`; its `pending_messages` projection
also restores unconsumed input when reconnecting during a slow call.

`round_start.data.input` contains the saved initial task and attachments,
including functional child calls that do not use an inbox. Frontends render
these events and saved attachments without scheduling rounds or inferring
completion; only the backend's `round_end` commits a round result.
Per-Agent `log.txt` records accepted messages as `Message received` (awaiting
context commit), and functional initial input as `Task`. Logging failures never
invalidate an accepted message or stop scheduling.

Public `step_end.data.step` snapshots are committed before publication. Logical model
calls share a request-scoped wrapper with generation/scoring/compression purposes,
without binding Agent identity on a shared model. Keep fixed process instructions
in system prompts and dynamic inputs in user messages. Functional tests live in `tests/`.

## Project workspace

Root, child and restored Agents share the Runner's `ConfigurationContext`.
Runtime tools added with `tool_manage` reuse that same context. File tools use
the project root; Shell defaults to it and resolves relative `workdir` from it.
Skills use `<workspace>/.juice/skills`, `<workspace>/.agents/skills` and
`~/.agents/skills`; custom Skill storage does not change the project root.

```text
ConfigurationContext → Agent / runtime tool → project files and .juice state
```

CodeAct and the `python` tool resolve ordinary `open`, concrete `pathlib.Path`,
standard `os` filesystem calls and subprocess working directories against the
workspace, without changing the SDK process directory. Pass absolute project
paths to third-party APIs, or run project scripts through `shell`.
Keep workspace paths in construction context rather than saved Agent YAML.

The public `FILE_TOOLS` bundle is created on first explicit access, using that
caller's cwd. Importing the SDK or tool classes does not create filesystem tools.
For an explicit workspace, construct tools with `root_dir` so the directory is
validated immediately and the sandbox remains bound to that project.

## Built-in declarations

`build_general_agent()` constructs `root`; `general` is exclusively a child
Agent. Both this SDK helper and Runner resolve declarations through
AgentRegistry, using local YAML before the unified built-in catalog. Viewing,
validation and construction do not save defaults. Agent list/view tools report
`source: builtin` with `path: null`, or `source: local` with its actual path.

```text
read / validate / construct → local override → builtin fallback
successful edit            → validate → atomic local override
remove local override      → builtin fallback
```

Runtime paths, protocol overrides and mode restrictions apply to detached
copies. Only successful edits create `.juice/agents/<name>.yaml`; deleting an
override restores the built-in declaration. Team-private self edits stay in
that Team's private directory and never fall back to same-name global Agents.

## Dynamic child discovery

Runner-bound Agents query child availability through `RunnerContext` at call
time, sharing the Registry projection used by `agents_list` and Runner dispatch.
Root discovers eligible declarations dynamically; child Agents retain their
explicit managed relationships. A startup name snapshot never authorizes a call.
Before each model request, compare visible names and descriptions and refresh
the system prompt when they change, without reconstructing the Agent.

```text
agent_manage save / Registry edit → current availability → agent_tool
                                           └───────────→ next-step system prompt
```

`allowed_modes`, Plan policy, Team membership and Runner/mode-local unloads
remain authoritative. Team inspection may additionally show shared candidates;
listing a candidate does not authorize dispatch before it joins the Team.
Saved target configuration applies to fresh acquisition; existing target
sessions keep their lifecycle. Detached SDK Agents use explicit references.

## Protocols

```xml
<!-- ReAct: validate the entire actions list before executing tools. -->
<thought>Read the independent project instructions and configuration.</thought>
<actions>[{"name":"read","args":{"path":"README.md"}},{"name":"read","args":{"path":"pyproject.toml"}}]</actions>
```

```xml
<!-- CodeAct: execute the selected code block in the agent's Python runtime. -->
<thought>Calculate the result.</thought>
<code>submit_output(output=6 * 7)</code>
```

`submit_output` ends a round. Empty ReAct actions continue with an observation;
protocol errors return to the next model step for correction. `max_steps` controls
the step budget with the existing final submission fallback.
ReAct safely removes repeated closing `</actions>` tags at the end of an output
before replay; other trailing content remains a protocol error. Tags inside JSON
strings and thought blocks are preserved.

ReAct submits the complete validated action list to `ToolManager`. Actions in one
step run concurrently by default; dependent or conflicting operations belong in
different steps. For example, read files together, then use their observations
to edit a file in the next step and validate that edit in a later step:

```text
step 1: [read A, read B] → wait for both
step 2: [edit C using A and B] → wait
step 3: [check C]
```

Each foreground step waits for all its actions. `tool_start` / `tool_end` events
stream during execution, with `tool_end` following actual completion order;
observations and persisted results retain action order. Background actions return
a start receipt and deliver their result through a later notification. Tool
resource locks and main-thread barriers can constrain execution; see
[ToolManager conventions](../managers/README.md). CodeAct executes ordinary
Python calls in Python statement order; this ReAct scheduling rule does not
automatically parallelize a code block. Set `tool_executor_max_workers` when
constructing an Agent to change its foreground concurrency limit (default: 8).

Tool display uses only `tool_start` / `tool_end`, joined by a framework-owned
`call_id`; `step_end.data.step.tool_calls` preserves the same identity and result for
history. CodeAct records its visible Python wrapper with its own ID, separate
from the inner tool calls. Tool output and caught execution errors stay within
that call's observation/error; they do not produce an additional step error.
Status comes from the execution result, never from matching output text.
Submission schema errors remain step errors; an already completed Python call
keeps its successful result and observations in history.

```text
tool_start(call_id) → running record → tool_end(same call_id) updates record
                                   └→ saved tool_calls preserves final result
```

## Web search

The built-in `web_search` uses Bing by default and returns titles, URLs and
snippets. Supply `query` and optionally `max_results` (default 5, capped at 20).
To use DuckDuckGo in SDK code, select it when constructing the tool:

```python
from juice_agents.core.agent.tools import WebSearchTool

search = WebSearchTool(engine="duckduckgo")  # "ddg" is also supported
results = search.forward(query="Python asyncio", max_results=5)
```

The engine is fixed at construction, not supplied in `forward`; there is no
automatic fallback between engines. Recreate existing tool instances or restart
the process to pick up a changed default. Search availability depends on network
access to the selected engine.

## Image generation and editing

Both `build_general_agent()` and Runner expose `generate_edit_image` only when
`image.enabled: true`; it defaults off and is always hidden in Plan mode.
`image.model_name → image_models → providers` selects its independent Ark
connection. Choosing a chat model or GRM does not select an image model.

```yaml
image:
  enabled: true
  model_name: seedream_4
```

The tool accepts paths, HTTP(S) URLs, bytes, PIL images and `ObservationImage`.
Generation and editing both use Ark `images.generate()`; editing sends either
the original remote URL or a MIME-correct base64 data URL. Results use the
Runner's observation-image directory. Functional tests use the real Ark SDK
with mock HTTP transport; they never call a live image service.

Task images, attachments, and image observations keep local paths or HTTP(S)
URLs in session state. OpenAI/Ark chat requests encode local files only when
sending; both chat and editing reuse `image_to_api_source()` in `agent_type.py`.
See [image input](../models/README.md#image-input) for the transport contract.

## Browser setup

Browser tools use Playwright and its managed Chromium by default; install the
browser with `python -m playwright install chromium`. To reuse a local Chromium
executable, set an absolute path in the workspace's `.juice/config.yaml`:

```yaml
browser:
  executable_path: /path/to/chromium
```

The CLI passes its current Node.js executable to the gateway as
`PLAYWRIGHT_NODEJS_PATH` when that variable is unset; an explicit value takes
precedence. For standalone SDK use or other entry points, set it before starting
the process if Playwright's bundled driver Node.js is incompatible with the host:

```bash
export PLAYWRIGHT_NODEJS_PATH=/path/to/node
```

Use Chromium and Node.js versions compatible with the installed Playwright and
the host system. A local executable does not remove Playwright's Python package
or system library requirements. Restart the process after changing the settings
so existing browser sessions do not retain the old configuration.

## Tool Creation Contract

`tool_manage` keeps its configuration rules, complete `save_args` example, and
verification requirements in its own `description`, `inputs`, and `outputs`.
Both protocols render those definitions; fixed tool rules belong there rather
than in generic system sections or user task messages.

New tools require a configuration object with Python source defining
`def forward(self, ...)`; its parameters and return value match `inputs` and
`outputs`. Optional parameters have defaults. See the rendered `tool_manage`
definition for all supported fields and the current-time example.

```text
save declaration → inspect runtime_refresh → available tool → invoke and verify
```

`tool_manage save/unload` persists the Agent's tool references through
AgentRegistry, including `.juice/agents/root.yaml`. `scheduled` / `next_step`
refreshes the current Agent's tool prompt and CodeAct functions before its next
model request, using the existing runtime reference table without rebuilding the
Agent. Resume reads the saved declaration and refreshes the restored prompt.
`fresh_acquire` applies when only a static declaration can be updated;
`unsupported` does not activate the tool. A save error can occur after the tool
YAML was written, so inspect the declaration before retrying. Report saved,
registered, and executed outcomes separately. One-off Python execution does not
create a tool. Tests execute the rendered example and both Agent protocols locally.

## Per-step Candidate Selection

```python
from juice_agents.core.agent.context.strategies import GreedyStrategy, TreeOfThoughtsStrategy

agent.set_exploration_strategy(TreeOfThoughtsStrategy(breadth=3, reward_model=scorer))
# Restore one model request per step:
agent.set_exploration_strategy(GreedyStrategy())
```

Inject `GenerativeRewardModel` (GRM) with a native SDK client to generate JSON
scores without configuration files:

```python
from juice_agents.core.models.reward_models import GenerativeRewardModel

# OpenAI reads OPENAI_API_KEY from the environment.
scorer = GenerativeRewardModel(model_name="your-model")
agent.set_exploration_strategy(TreeOfThoughtsStrategy(breadth=3, reward_model=scorer))
```

The [model layer](../models/README.md#reward-model) shows complete direct
construction and the optional `GenerativeRewardModel.from_config(...)` entry
point for catalog resolution. It owns evaluation prompts, provider calls, and
score parsing. The strategy consumes the
injected scorer and selects a candidate. Invalid scores and provider failures
propagate before any candidate executes.

Custom injected scorers subclass the regular `BaseRewardModel` base class and
override its async `evaluate_batch` method. As with `BaseChatModel`, calling
the unimplemented base method raises `NotImplementedError`:

```python
from juice_agents.core.models.reward_models import BaseRewardModel

class MyRewardModel(BaseRewardModel):
    async def evaluate_batch(self, *, context: list[dict], actions: list[dict]) -> dict:
        # context is the generation message snapshot; actions contain
        # {"role": "assistant", "content": raw_output} for valid candidates only.
        # A local rule makes no SDK requests; higher scores are better.
        return {"scores": score_candidates(context, actions), "usage": None, "usage_calls": []}
```

```text
Current selected leaf
  ├─ candidate 0 [rejected; raw output + usage + score]
  ├─ candidate 1 [selected] → tools/code once → next candidates
  └─ candidate 2 [rejected; raw output + usage + score]
```

| Setting or result | Behavior |
| --- | --- |
| Default, `breadth=1`, or missing scorer | One generation, no evaluation |
| `breadth=N > 1` with scorer | N generations from identical context; execute one |
| Invalid protocol candidate | Retain its error; exclude it from scoring |
| All candidates invalid | Select first and use normal protocol recovery |
| Equal scores | Select the first generated valid candidate |
| Scorer failure, wrong count, nonfinite/nonnumeric score | Fail the round explicitly before execution |

`breadth` is a positive integer. The strategy has no depth, beam width, or
temperature settings; model sampling configuration stays on the model. There is
no multi-path execution, environment isolation, or backtracking. CLI controls breadth
with `/strategy breadth=N` and the catalog reward model independently with `/rm`;
`/rm` opens a model picker with the current reward model marked and a clear
option; `/rm judge` selects directly, and `/rm reset` clears it. Selection and
clearing retain breadth and the generation model. `/strategy reset` restores breadth to 1 while
retaining the reward model. Settings apply only in the current CLI process;
without a scorer it reports linear execution.

## Context and Persistence

`ContextTree` stores task, action, and summary nodes. Manager inserts every
candidate before execution, advances one active leaf, then updates the selected
node with observations instead of appending a duplicate. ReAct streaming events
and persisted observations use the same tool execution results.

Normal `steps` / `to_messages()` follow the active selected branch. Rejected
nodes remain available in `tree.nodes` and serialized snapshots. Remain compression
only projects older observations; compact creates a summary continuation while
retaining the original nodes for inspection. Subsequent tasks extend the active
branch; `reset_session=True` clears its tree and history.

UI history uses `serialize_context_transcript(tree)` to export the selected leaf's
complete ancestry, including original nodes before compression and excluding rejected
candidates. Reuse this read-only export for session inspection and first-request previews.

Agents collect `usage_calls` from generation, summaries, and strategy scoring
results or exceptions; `AgentManager` binds identity and forwards them to `ModelManager` for
persistence and totals in `agents/model_usage.jsonl` under the Runner root.
Rejected candidates, empty-response retries, failed scoring, and usage known
before cancellation count; a batch reward request counts once. Functional or
ephemeral calls retain token metadata even when their sessions are not saved.

The usage file contains no prompts or transcripts and survives compression and
resume. `ContextTree` owns conversation state only. `ActionStep.usage` retains
the last generation response's usage for tracing and window calibration; do not
add it to the usage file again. Query records with
`runner.agent_manager.model_usage_records()` and totals with
[`runner.token_usage()`](../runner/README.md#token-usage).

Snapshots live in `.juice/sessions/<runner_id>/agents/<agent_id>/session.json`.
Restoration creates a fresh Agent and restores its context. Passing a `ContextTree`
or serialized mapping to `AgentManager.acquire(session_state=...)`, or decoding a
snapshot with `deserialize_snapshot`, creates an independent context, including
nested outputs, tool arguments, attachments, usage and metadata. Scorers are runtime
dependencies and never enter snapshots: inject one again after restoration to
enable candidate selection. Otherwise execution stays linear.

## Durable message scheduling

`AgentManager.post(agent_id, attachment)` accepts a `RuntimeAttachment` with a
stable `message_id` and returns a receipt only after `inbox.json` is written.
Acquire/restore identities with scheduling disabled, bind callbacks, then call
`activate()`; repeating activation is safe. A persistent Agent consumes incoming
attachments at step boundaries or starts a new round when idle.

```text
agents/<agent_id>/
  manifest.json   identity and observable status
  session.json    context + agent_status + round_checkpoint + round_results + committed message IDs
  inbox.json      messages awaiting durable handoff/producer acknowledgement
```

Input node, round ID, status and remaining step budget share the context write.
Final actions and terminal state are committed together. Recovery resumes an
active round without creating another input node, retries acknowledgements, and
never appends a committed message twice. A message arriving during a final step
moves to a fresh round with a `message_deferred` receipt event. Broken targets or
inboxes remain visible as delivery failures instead of looping wakeups.

The session file is the commit boundary: write the manifest summary first and
the authoritative session last. Every saved round result remains in history;
inspection never infers success from output text or submission intent.

`cancel_round(round_id)` persists its intent before signalling synchronous work;
a queued cancellation remains in inbox until the preceding round has ended,
without replacing that round's checkpoint. Persistent instances return to idle
and new messages can wake them; functional instances are released. Independent async
tasks have their own cancellation API. Functional Agents only run within their
calling invocation. `close()` shuts down scheduling resources. These JSON files
have one Runner owner; message handoff does not make tool side effects exactly once.

## Development

| Module | Responsibility |
| --- | --- |
| `agents.py` | Generate, parse, select, then execute ReAct/CodeAct outputs |
| `context/strategies/` | Candidate count and scoring selection; no tree mutation |
| `../models/reward_models.py` | BaseRewardModel base class and GRM subclass; no strategy or tree dependency |
| [AgentManager](../managers/agents/README.md) | Instance lifecycle, candidate checkpoints, and identity binding for ModelManager |
| `context/` | Tree, selected-path messages, compression, serialization |
| `tools/`, `prompts/` | Tool declarations/runtime binding and fixed protocol instructions |

Persistent agents reuse live instances; functional agents release after a request.
Do not reconstruct a live Agent or create an Agent-owned ToolManager. Keep dynamic
task input in user prompts and fixed workflow instructions in system prompts.
Add functional tests under `tests/core/agent/` and keep runtime logs scoped to
Runner/Agent IDs.

```bash
conda activate juice-agents
python -m pytest tests/core/agent -q
```

Built-in `astep()` directly awaits generation, scoring and compression through
`ModelManager.acall()`. `step()` adapts the complete async stream. AgentManager
persists candidates before advancing into tool side effects. Sync custom Agents
and tools run through bounded blocking adapters; cancellation waits for cleanup.

Custom strategies override `aselect()`; synchronous `select()` adapts it. Native
ReAct scheduling awaits tool futures without holding a stream worker. Async
barrier tools and CodeAct use the same stable per-Agent affinity thread; ordinary
sync tool bodies retain their bounded worker pool.
