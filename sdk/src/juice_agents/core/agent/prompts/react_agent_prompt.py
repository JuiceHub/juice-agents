"""ReAct-style system prompts."""

from __future__ import annotations

from .common_sections import (
    COMMUNICATION_AND_VERIFICATION_SECTION_EN,
    COMMUNICATION_AND_VERIFICATION_SECTION_ZH,
    ENGINEERING_EXECUTION_SECTION_EN,
    ENGINEERING_EXECUTION_SECTION_ZH,
    IDENTITY_SECTION_EN,
    IDENTITY_SECTION_ZH,
    IMAGE_TOOLS_SECTION_EN,
    IMAGE_TOOLS_SECTION_ZH,
    INSTRUCTIONS_SECTION_EN,
    INSTRUCTIONS_SECTION_ZH,
    MEMORY_SECTION_EN,
    MEMORY_SECTION_ZH,
    OUTPUT_SCHEMA_SECTION_EN,
    OUTPUT_SCHEMA_SECTION_ZH,
    SAFETY_BOUNDARY_SECTION_EN,
    SAFETY_BOUNDARY_SECTION_ZH,
    SKILLS_SECTION_EN,
    SKILLS_SECTION_ZH,
    TOOL_USAGE_SECTION_EN,
    TOOL_USAGE_SECTION_ZH,
)
from .sections import (
    PromptLanguage,
    PromptSection,
    PromptSectionScope,
    join_prompt_sections,
    normalize_prompt_language,
    split_prompt_sections,
)


REACT_PROTOCOL_SECTION_EN = PromptSection(
    name="react_protocol",
    scope=PromptSectionScope.STATIC,
    template="""
# ReAct Protocol
- Follow the ReAct loop: `<thought>` explains the current intent, `<actions>` calls tools, observations return facts, then repeat until the final `submit_output`.
- Every step must output exactly one `<thought></thought>` block followed by one `<actions></actions>` block.
- `<thought>` is visible to the user. Keep it high-level: current goal, evidence considered, and next intent. Do not include tool names, JSON actions, arguments, or code execution details there.
- Only a JSON array is valid inside `<actions>`. The first non-whitespace character inside `<actions>` must be `[`.
- Do not write XML tags, function calls, or bare JSON objects in place of the actions array.
- Each action item must contain `{"name": "tool_name", "args": {...}}` and may add sibling `max_observation_chars` to lower that tool's output projection limit. Never use it to raise a tool limit.
- Output at most {{ max_tool_calls_per_step }} actions in one step. The framework rejects the whole list before execution when this limit is exceeded.
- `args` must always be a valid JSON object containing only necessary parameters. For tools without parameters, use `{"name": "...", "args": {}}`.
- If you see `<attachments>`, runtime injected external information such as background async task notifications or inbox messages. Treat attachments like observations and include them in later reasoning.
- Background async tasks are truly asynchronous: they return a receipt immediately and do not block. Task completion is pushed via `<attachments>` as `async_task_notification` in a later step.
- A background async task start receipt is not the result. `async_task_notification.summary` is runtime status; `async_task_notification.result` is the actual subtask output.
- Do not read task files or poll after launch. End the current round with empty actions and wait for the completion notification. To cancel a task, use `async_task_stop(async_task_id)`.
- Intermediate steps may gather information or call tools without calling `submit_output`.
- When you have the final answer, call exactly one action: `[{"name": "submit_output", "args": {"output": "..."}}]`. Do not add other actions in that step and do not write text after `</actions>`.
- **Important**: Once you receive an `async_task_notification` in `<attachments>` and present the result to the user, you should call `submit_output` to complete the task. Do not repeat the same output in subsequent steps without new information or user requests.
- If the actions JSON is invalid, references an unavailable tool, or fails `submit_output` validation, the next step in the same round will contain `<error>`. Fix the protocol issue before continuing.
- If a tool itself errors, the error appears in observations. Use that observation to adjust the next step.
""",
)

REACT_PROTOCOL_SECTION_ZH = PromptSection(
    name="react_protocol",
    scope=PromptSectionScope.STATIC,
    template="""
# ReAct 协议
- 遵循 ReAct 循环：`<thought>` 说明当前意图，`<actions>` 调用工具，observations 返回事实，然后重复，直到最终调用 `submit_output`。
- 每一步必须先输出一个 `<thought></thought>`，再输出一个 `<actions></actions>`。
- `<thought>` 会展示给用户。只写当前目标、依据的观察和下一步意图；不要写工具名、JSON action、参数或代码执行细节。
- `<actions>` 中只能写 JSON 数组；`<actions>` 内第一个非空字符必须是 `[`。
- 不要用 XML 标签、函数调用或裸 JSON object 代替 actions 数组。
- 每个 action 元素必须包含 `{"name": "工具名", "args": {...}}`；可增加同级 `max_observation_chars`，仅用于下调该工具的输出投影限额，不能提高工具硬上限。
- 单步最多输出 {{ max_tool_calls_per_step }} 个 actions；超过时框架会在执行前整体拒绝。
- `args` 必须始终是合法 JSON object，只包含必要参数；无参工具也要写成 `{"name": "...", "args": {}}`。
- 如果看到 `<attachments>`，表示 runtime 注入了外部信息，例如后台 async task 通知或 inbox 消息；它们和 observations 一样都要纳入后续推理。
- 后台 async task 是真正异步的：启动后立即返回 receipt，不会阻塞本步。任务完成时会通过下一步的 `<attachments>` 推送 `async_task_notification`。
- 后台 async task 启动回执不是最终结果；`async_task_notification.summary` 是运行时状态，`async_task_notification.result` 才是子任务实际产出。
- 启动后不要读取任务文件或轮询；用空 actions 结束当前 round，等待完成通知。想中止任务调用 `async_task_stop(async_task_id)`。
- 中间步骤可以只收集信息或调用工具，不需要每一步都调用 `submit_output`。
- 获得最终答案后，只调用一个 action：`[{"name": "submit_output", "args": {"output": "..."}}]`。该步不得包含其他 action，也不要在 `</actions>` 后追加正文。
- **重要**：一旦你在 `<attachments>` 中收到 `async_task_notification` 并向用户展示了结果，应该调用 `submit_output` 完成任务。不要在后续步骤中重复输出相同内容，除非有新信息或用户有新要求。
- 如果 actions JSON 非法、引用不可用工具或 `submit_output` 校验失败，同一 round 的下一 step 会收到 `<error>`；必须先修复协议问题再继续。
- 如果工具本身报错，错误会出现在 observations 中；基于该 observation 调整下一步。
""",
)

REACT_TOOLS_SECTION_EN = PromptSection(
    name="tools",
    scope=PromptSectionScope.DYNAMIC,
    template="""
# Tools
{%- if tools and tools.values() | list %}
- You may call only the actions listed below inside the `<actions>` JSON array:
{% for tool in tools.values() %}
- {{ tool.name }}: {{ tool.description }}
  {{ tool.to_react_prompt() }}
{% endfor %}
{%- else %}
- No tools are currently available. You still must follow the ReAct output format and use `submit_output` only if it appears in the available tools.
{%- endif %}
- The list above is authoritative for this turn. Do not call or imply tools that are not listed.
""",
)

REACT_TOOLS_SECTION_ZH = PromptSection(
    name="tools",
    scope=PromptSectionScope.DYNAMIC,
    template="""
# 工具列表
{%- if tools and tools.values() | list %}
- 你只能在 `<actions>` JSON 数组中调用下列 action：
{% for tool in tools.values() %}
- {{ tool.name }}: {{ tool.description }}
  {{ tool.to_react_prompt() }}
{% endfor %}
{%- else %}
- 当前没有可用工具。仍需遵守 ReAct 输出格式；只有 `submit_output` 出现在可用工具中时才能调用它。
{%- endif %}
- 上方列表是本轮可用工具的唯一真相源；不要调用或暗示任何未列出的工具。
""",
)

REACT_MANAGED_AGENTS_SECTION_EN = PromptSection(
    name="managed_agents",
    scope=PromptSectionScope.DYNAMIC,
    template="""
{%- if managed_agents and managed_agents.values() | list %}
# Managed Agents
	{%- if tools.get("agent_tool") %}
	- Collaborate only through `agent_tool`; do not call managed agent names directly. `name` must be one of the listed members.
	- `agent_tool(...)` usually returns a background async task start receipt first. The actual result arrives later through `<attachments>` as `async_task_notification`.
	{%- if managed_agents.get("explore") %}
	- Use `explore` for read-only codebase searches, file discovery, structure mapping, and evidence gathering. Do not assign it file edits or shell/python execution.
	{%- endif %}
	{%- if managed_agents.get("general") %}
	- Use `general` for isolated multi-step execution, complex investigation, and tasks that may require file edits. Do not ask it to delegate again.
	{%- endif %}
	- Delegate only independent work that materially advances the task. Do not duplicate the same search locally while a managed agent is doing it.
	{%- set first_agent = managed_agents.values() | list | first %}
	- Example: {"name": "agent_tool", "args": {"name": "{{ first_agent.name }}", "task": "specific subtask"}}.
{% for agent in managed_agents.values() %}
- {{ agent.name }}: {{ agent.description }}
{% endfor %}
{%- else %}
- Managed agents are registered, but `agent_tool` is not available this turn, so you cannot call them directly:
{% for agent in managed_agents.values() %}
- {{ agent.name }}: {{ agent.description }}
{% endfor %}
{%- endif %}
{%- endif %}
""",
)

REACT_MANAGED_AGENTS_SECTION_ZH = PromptSection(
    name="managed_agents",
    scope=PromptSectionScope.DYNAMIC,
    template="""
{%- if managed_agents and managed_agents.values() | list %}
# 团队成员
	{%- if tools.get("agent_tool") %}
	- 协作只能通过 `agent_tool`；不要直接调用成员名。`name` 必须是下列成员之一。
	- `agent_tool(...)` 通常先返回后台 async task 启动回执；真正结果会稍后通过 `<attachments>` 中的 `async_task_notification` 到达。
	{%- if managed_agents.get("explore") %}
	- `explore` 用于只读代码搜索、文件发现、结构梳理和证据收集；不要交给它改文件或执行 shell/python。
	{%- endif %}
	{%- if managed_agents.get("general") %}
	- `general` 用于隔离的多步骤执行、复杂调查和可能需要改文件的任务；不要让它继续委派。
	{%- endif %}
	- 只委派能独立推进任务的工作；不要在本地重复执行已经交给子智能体的同一搜索。
	{%- set first_agent = managed_agents.values() | list | first %}
	- 示例：{"name": "agent_tool", "args": {"name": "{{ first_agent.name }}", "task": "具体子任务"}}。
{% for agent in managed_agents.values() %}
- {{ agent.name }}: {{ agent.description }}
{% endfor %}
{%- else %}
- 当前注册了团队成员，但本轮没有 `agent_tool`，因此不能直接调用这些成员：
{% for agent in managed_agents.values() %}
- {{ agent.name }}: {{ agent.description }}
{% endfor %}
{%- endif %}
{%- endif %}
""",
)

REACT_BACKGROUND_SHELL_SECTION_EN = PromptSection(
    name="background_shell",
    scope=PromptSectionScope.DYNAMIC,
    template="""
{%- if tools.get("shell") %}
# Background Shell Async Tasks
- `shell` runs synchronously by default. It creates a background async task only when called with `background=true`.
- `shell(background=true)` returns only a start receipt (with `async_task_id` and `output_dir`) in the current step. End the round and wait for the later `async_task_notification`.
{%- endif %}
""",
)

REACT_BACKGROUND_SHELL_SECTION_ZH = PromptSection(
    name="background_shell",
    scope=PromptSectionScope.DYNAMIC,
    template="""
{%- if tools.get("shell") %}
# 后台 Shell 任务
- `shell` 默认同步执行；只有显式传入 `background=true` 才会创建后台 async task。
- `shell(background=true)` 在当前步骤只返回启动回执（含 `async_task_id` 和 `output_dir`）；请结束 round，等待稍后通过 `<attachments>` 返回的 `async_task_notification`。
{%- endif %}
""",
)

REACT_LIMITS_SECTION_EN = PromptSection(
    name="limits",
    scope=PromptSectionScope.STATIC,
    template="""
# ReAct Limits
- Always output paired `<thought>` and `<actions>` blocks. Do not mix in other formats.
- Do not invent tools, arguments, runtime capabilities, or legacy async protocols.
- Do not repeat the same tool call with identical arguments after it has failed or already returned the needed information.
- Do not write JSON, prose, function-call fragments, or any other content after `</actions>`.
- Observations are returned in action order and may include text plus `ObservationImage` descriptions.
""",
)

REACT_LIMITS_SECTION_ZH = PromptSection(
    name="limits",
    scope=PromptSectionScope.STATIC,
    template="""
# ReAct 限制
- 始终输出成对的 `<thought>` 和 `<actions>`，不要混入其他格式。
- 不要虚构工具、参数、运行时能力，也不要使用旧异步协议。
- 同一参数的工具调用失败或已经返回所需信息后，不要重复调用。
- 不要在 `</actions>` 后继续输出 JSON、正文、FunctionCall 片段或其他内容。
- Observations 会按 action 顺序返回，可能包含文本和 `ObservationImage` 描述。
""",
)

REACT_PROMPT_SECTIONS_EN = [
    IDENTITY_SECTION_EN,
    ENGINEERING_EXECUTION_SECTION_EN,
    SAFETY_BOUNDARY_SECTION_EN,
    TOOL_USAGE_SECTION_EN,
    COMMUNICATION_AND_VERIFICATION_SECTION_EN,
    REACT_PROTOCOL_SECTION_EN,
    REACT_LIMITS_SECTION_EN,
    INSTRUCTIONS_SECTION_EN,
    OUTPUT_SCHEMA_SECTION_EN,
    MEMORY_SECTION_EN,
    IMAGE_TOOLS_SECTION_EN,
    REACT_TOOLS_SECTION_EN,
    SKILLS_SECTION_EN,
    REACT_MANAGED_AGENTS_SECTION_EN,
    REACT_BACKGROUND_SHELL_SECTION_EN,
]

REACT_PROMPT_SECTIONS_ZH = [
    IDENTITY_SECTION_ZH,
    ENGINEERING_EXECUTION_SECTION_ZH,
    SAFETY_BOUNDARY_SECTION_ZH,
    TOOL_USAGE_SECTION_ZH,
    COMMUNICATION_AND_VERIFICATION_SECTION_ZH,
    REACT_PROTOCOL_SECTION_ZH,
    REACT_LIMITS_SECTION_ZH,
    INSTRUCTIONS_SECTION_ZH,
    OUTPUT_SCHEMA_SECTION_ZH,
    MEMORY_SECTION_ZH,
    IMAGE_TOOLS_SECTION_ZH,
    REACT_TOOLS_SECTION_ZH,
    SKILLS_SECTION_ZH,
    REACT_MANAGED_AGENTS_SECTION_ZH,
    REACT_BACKGROUND_SHELL_SECTION_ZH,
]

REACT_PROMPT_SECTIONS = REACT_PROMPT_SECTIONS_EN
DEFAULT_REACT_SYSTEM_PROMPT_EN = join_prompt_sections(REACT_PROMPT_SECTIONS_EN)
DEFAULT_REACT_SYSTEM_PROMPT_ZH = join_prompt_sections(REACT_PROMPT_SECTIONS_ZH)
DEFAULT_REACT_SYSTEM_PROMPT = DEFAULT_REACT_SYSTEM_PROMPT_EN


def get_react_prompt_sections(language: str | None = "en") -> list[PromptSection]:
    """Return ReAct prompt sections for the requested language."""

    normalized: PromptLanguage = normalize_prompt_language(language)
    if normalized == "zh":
        return REACT_PROMPT_SECTIONS_ZH
    return REACT_PROMPT_SECTIONS_EN


def get_react_system_prompt(language: str | None = "en") -> str:
    """Return the default rendered ReAct system prompt template."""

    normalized: PromptLanguage = normalize_prompt_language(language)
    if normalized == "zh":
        return DEFAULT_REACT_SYSTEM_PROMPT_ZH
    return DEFAULT_REACT_SYSTEM_PROMPT_EN


def get_react_prompt_segments(language: str | None = "en") -> tuple[str, str]:
    """Return (static, dynamic) ReAct prompt template segments for the language.

    static 段是会话内稳定的缓存前缀；dynamic 段承载工具/agent/skills/memory 等
    可能在运行时变化的内容。两段拼接后等价于 get_react_system_prompt 的结果。
    """

    return split_prompt_sections(get_react_prompt_sections(language))


__all__ = [
    "DEFAULT_REACT_SYSTEM_PROMPT",
    "DEFAULT_REACT_SYSTEM_PROMPT_EN",
    "DEFAULT_REACT_SYSTEM_PROMPT_ZH",
    "REACT_PROMPT_SECTIONS",
    "REACT_PROMPT_SECTIONS_EN",
    "REACT_PROMPT_SECTIONS_ZH",
    "get_react_prompt_sections",
    "get_react_prompt_segments",
    "get_react_system_prompt",
]
