# Agent

> [简体中文](README.zh-CN.md)

ReAct and CodeAct provide step protocols. `AgentManager` owns lifecycle, context
checkpoints, and cancellation; tools execute through the Runner permission boundary.

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
<thought>Read the project instructions.</thought>
<actions>[{"name":"read_file","args":{"path":"README.md"}}]</actions>
```

```xml
<!-- CodeAct: execute the selected code block in the agent's Python runtime. -->
<thought>Calculate the result.</thought>
<code>submit_output(output=6 * 7)</code>
```

`submit_output` ends a round. Empty ReAct actions continue with an observation;
protocol errors return to the next model step for correction. `max_steps` controls
the step budget with the existing final submission fallback.

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
from openai import OpenAI

from juice_agents.core.models.reward_models import GenerativeRewardModel

# OpenAI reads OPENAI_API_KEY from the environment.
scorer = GenerativeRewardModel(model_name="your-model", client=OpenAI())
agent.set_exploration_strategy(TreeOfThoughtsStrategy(breadth=3, reward_model=scorer))
```

The [model layer](../models/README.md#reward-model) shows complete direct
construction and the optional `GenerativeRewardModel.from_config(...)` entry
point for catalog resolution. It owns evaluation prompts, provider calls, and
score parsing. The strategy consumes the
injected scorer and selects a candidate. Invalid scores and provider failures
propagate before any candidate executes.

Custom injected scorers subclass the regular `BaseRewardModel` base class and
override its synchronous `evaluate_batch` method. As with `BaseChatModel`, calling
the unimplemented base method raises `NotImplementedError`:

```python
from juice_agents.core.models.reward_models import BaseRewardModel

class MyRewardModel(BaseRewardModel):
    def evaluate_batch(self, *, context: list[dict], actions: list[dict]) -> dict:
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

Agents collect `usage_calls` from generation, summaries, and strategy scoring
results or exceptions, then explicitly forward them to `AgentManager` for
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

Snapshots live in `.juice/runners/<runner_id>/agents/<agent_id>/session.json`.
Restoration creates a fresh Agent and restores its context. Passing a `ContextTree`
or serialized mapping to `AgentManager.acquire(session_state=...)`, or decoding a
snapshot with `deserialize_snapshot`, creates an independent context, including
nested outputs, tool arguments, attachments, usage and metadata. Scorers are runtime
dependencies and never enter snapshots: inject one again after restoration to
enable candidate selection. Otherwise execution stays linear.

## Development

| Module | Responsibility |
| --- | --- |
| `agents.py` | Generate, parse, select, then execute ReAct/CodeAct outputs |
| `context/strategies/` | Candidate count and scoring selection; no tree mutation |
| `../models/reward_models.py` | BaseRewardModel base class and GRM subclass; no strategy or tree dependency |
| `manager.py` | Agent lifecycle, candidate checkpoints, and persistence/totals for usage forwarded by Agents |
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
