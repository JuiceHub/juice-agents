"""Group 领域内置 manager / worker 强类型配置。"""

from __future__ import annotations

from typing import Any

from juice_agents.core.registry.agents.types import AgentConfig, normalize_agent_type_policy
from juice_agents.core.registry.tools.types import ToolRef

DEFAULT_GROUP_MODEL_SECTION = "doubao_lite"
DEFAULT_GROUP_WORKER_NAMES = (
    "general_worker",
    "research_worker",
    "implement_worker",
    "review_worker",
    "evolution_worker",
)
DEFAULT_WORKER_TOOL_NAMES = (
    "shell",
    "python",
    "read",
    "write",
    "edit",
    "api_web_search",
)

DEFAULT_MANAGER_INSTRUCTIONS = (
    "你是 group mode 的 manager。"
    "你负责根据任务目标自主选择合适的 worker 与工具推进任务。"
    "你可以维护自己的 todos，但不要把完整待办重复写进用户提示词。"
    "当需要扩展团队时，可以使用运行时注入的 managed-agent CRUD 工具。"
    "当已有 worker 足够时，优先复用现有 worker，而不是无意义地新增重复角色。"
    "派发 worker 后，如果当前没有别的独立工作，不要继续空转；等待后续 async_task_notification 再继续决策。"
    "收到 async_task_notification 后，summary 是 runtime 生成的状态摘要，result 才是 worker 的实际产出。"
    "\n\n## 错误恢复策略\n"
    "- 如果 worker 返回的 result 为空或包含 [runtime] 错误信息，说明该 worker 未能完成任务。"
    "- 同一类型的 worker 对同一子任务最多重试 2 次；超过后必须换一种策略（换 worker 类型、拆分子任务、或用自身知识直接完成）。"
    "- 当所有 worker 均无法获取有效数据时，应使用自身知识直接完成任务，而不是无限重试。"
    "- 数据收集完成后，必须及时派发 implement_worker 进入实现阶段，不要停留在收集阶段。"
    "- 任务完成前，你必须调用 submit_output 提交最终结果。"
)
DEFAULT_GENERAL_WORKER_INSTRUCTIONS = (
    "你是 general_worker。"
    "你负责通用兜底类任务：当任务暂时无法明确归类到 research、implement、review 时接手处理。"
    "请先澄清目标，再执行必要的整理、汇总、转述或轻量操作。"
    "最终只需通过 submit_output.output 返回业务结果，不要额外包装顶层 summary。"
    "优先返回简洁、可复用、可直接消费的结果；如果明显需要专门研究、实现或审核，应在结果中指出。"
    "\n重要：即使任务无法完全完成，也必须在最后调用 submit_output 返回已有的部分结果，绝不能不提交就结束。"
    "\n\nsubmit_output 格式要求：output 必须是 JSON 对象，必填字段 result（字符串），"
    "可选字段 notes（字符串）。不允许额外字段。"
)
DEFAULT_RESEARCH_WORKER_INSTRUCTIONS = (
    "你是 research_worker。"
    "你负责检索事实、整理证据、归纳信息。"
    "最终只需通过 submit_output.output 返回业务结果，不要额外包装顶层 summary。"
    "请优先查清关键事实、来源依据、可验证结论，并在结果中明确不确定性、假设或缺失信息。"
    "\n重要：搜索或工具调用返回了有用信息后，应立即整理并调用 submit_output 提交。"
    "即使信息不完整，也必须提交已有的部分结果，绝不能不提交就结束。"
    "\n\nsubmit_output 格式要求：output 必须是 JSON 对象，必填字段 findings（字符串），"
    "可选字段 sources（字符串数组）和 confidence（字符串）。不允许额外字段。"
)
DEFAULT_IMPLEMENT_WORKER_INSTRUCTIONS = (
    "你是 implement_worker。"
    "你负责实现、修改与验证任务。"
    "最终只需通过 submit_output.output 返回业务结果，不要额外包装顶层 summary。"
    "请尽量直接产出可复用的实现结果，并说明关键改动、验证结论或未完成项。"
    "\n重要：即使实现不完整，也必须在最后调用 submit_output 返回已有的部分结果，绝不能不提交就结束。"
    "\n\nsubmit_output 格式要求：output 必须是 JSON 对象，必填字段 result（字符串），"
    "可选字段 files_changed（字符串数组）和 verification（字符串）。不允许额外字段。"
)
DEFAULT_REVIEW_WORKER_INSTRUCTIONS = (
    "你是 review_worker。"
    "你负责根据目标与验收标准做审核。"
    "最终只需通过 submit_output.output 返回业务结果，不要额外包装顶层 summary。"
    "submit_output.output 必须至少包含 approved 和 feedback。"
    "请明确给出通过/不通过结论，并指出主要问题、风险或仍需补充验证的地方。"
    "\n重要：即使审核未完成，也必须在最后调用 submit_output 返回已有的部分结果，绝不能不提交就结束。"
)
DEFAULT_EVOLUTION_WORKER_INSTRUCTIONS = (
    "你是 group mode 的专职 evolution_worker。"
    "你只根据 manager 传入的动态优化目标和证据演进当前 Group 的 Agent、Tool、Skill、Graph 配置。"
    "开始修改前必须先读取目标的现有配置，只做实现目标所需的最小变更，禁止触碰 Group manager/root。"
    "Agent 配置必须先调用 agent_manage(action='validate')，校验通过后才能调用 agent_manage(action='save')；"
    "保存后必须重新读取目标并核对下一 step 是否已生效。"
    "不得声称或通过调用参数提升自己的 mode、role、agent 或 target scope 权限。"
    "最终必须通过 submit_output 返回结构化报告，列出修改对象、验证结果和残余风险；"
    "本角色不启动后台循环，也不自行持续优化。"
)


def _output_schema(worker_name: str) -> dict[str, Any]:
    """每次返回独立 schema，避免共享可变声明。"""

    if worker_name == "research_worker":
        return {
            "type": "object",
            "description": "research worker 的业务结果。",
            "properties": {
                "findings": {"type": "string", "description": "研究发现的核心内容"},
                "sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "信息来源列表",
                },
                "confidence": {
                    "type": "string",
                    "description": "置信度说明（高/中/低及理由）",
                },
            },
            "required": ["findings"],
            "additionalProperties": False,
        }
    if worker_name == "implement_worker":
        return {
            "type": "object",
            "description": "implement worker 的业务结果。",
            "properties": {
                "result": {"type": "string", "description": "实现结果描述"},
                "files_changed": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "修改的文件路径列表",
                },
                "verification": {"type": "string", "description": "验证结论"},
            },
            "required": ["result"],
            "additionalProperties": False,
        }
    if worker_name == "review_worker":
        return {
            "type": "object",
            "description": "review worker 的业务结果。",
            "properties": {
                "approved": {"type": "boolean", "description": "是否通过"},
                "feedback": {"type": "string", "description": "评审反馈"},
            },
            "required": ["approved", "feedback"],
            "additionalProperties": False,
        }
    if worker_name == "evolution_worker":
        return {
            "type": "object",
            "description": "受控配置演进的结构化结果。",
            "properties": {
                "modified_targets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "实际修改的配置对象；未修改时为空列表",
                },
                "validation": {
                    "type": "string",
                    "description": "validate、保存后回读及生效核对结论",
                },
                "risks": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "残余风险或 deferred 字段；无风险时为空列表",
                },
            },
            "required": ["modified_targets", "validation", "risks"],
            "additionalProperties": False,
        }
    return {
        "type": "object",
        "description": "general worker 的业务结果。",
        "properties": {
            "result": {"type": "string", "description": "任务结果"},
            "notes": {"type": "string", "description": "补充说明"},
        },
        "required": ["result"],
        "additionalProperties": False,
    }


def get_default_group_worker_output_schema(worker_name: str) -> dict[str, Any]:
    return _output_schema(str(worker_name or "").strip())


def _worker_tools() -> tuple[ToolRef, ...]:
    return tuple(ToolRef(name=name) for name in DEFAULT_WORKER_TOOL_NAMES)


def build_default_group_manager_config(
    *,
    name: str = "manager",
    model_config_name: str = DEFAULT_GROUP_MODEL_SECTION,
    max_steps: int = 20,
    agent_type: str = "default",
) -> AgentConfig:
    return AgentConfig(
        name=str(name),
        agent_type=normalize_agent_type_policy(agent_type),
        model_config_name=str(model_config_name),
        # Group-only worker declarations must never leak into direct, plan or
        # team dispatch merely because they share the global Agent Registry.
        allowed_modes=("group",),
        description="Group manager",
        instructions=DEFAULT_MANAGER_INSTRUCTIONS,
        max_steps=int(max_steps),
        tools=(ToolRef(name="todos"), ToolRef(name="agent_tool")),
        managed_agent_names=DEFAULT_GROUP_WORKER_NAMES,
    )


def build_default_group_worker_configs(
    *,
    model_config_name: str = DEFAULT_GROUP_MODEL_SECTION,
    worker_max_steps: int = 16,
    agent_type: str = "default",
) -> tuple[AgentConfig, ...]:
    """构建彼此独立的五个内置 worker 声明。"""

    normalized_type = normalize_agent_type_policy(agent_type)
    shared = {
        "agent_type": normalized_type,
        "model_config_name": str(model_config_name),
        # Each worker is globally registered but only selectable by a Group
        # Runner. The Registry keeps this declaration portable and applies it
        # consistently to `/agents`, prompts and dispatch.
        "allowed_modes": ("group",),
        "max_steps": int(worker_max_steps),
        "lifecycle": "persistent",
    }
    return (
        AgentConfig(
            name="general_worker",
            description="通用兜底 worker，适合无法明确归类的整理、汇总和轻量执行任务；若明显需要专门研究、实现或审核，应交给对应 worker。",
            instructions=DEFAULT_GENERAL_WORKER_INSTRUCTIONS,
            output_schema=_output_schema("general_worker"),
            tools=_worker_tools(),
            **shared,
        ),
        AgentConfig(
            name="research_worker",
            description="研究检索 worker，适合查事实、找证据、做信息归纳；不负责直接实现代码或给出最终审核结论。",
            instructions=DEFAULT_RESEARCH_WORKER_INSTRUCTIONS,
            output_schema=_output_schema("research_worker"),
            tools=_worker_tools(),
            **shared,
        ),
        AgentConfig(
            name="implement_worker",
            description="实现执行 worker，适合修改文件、落地方案、运行必要验证；不负责独立做事实研究或最终审核结论。",
            instructions=DEFAULT_IMPLEMENT_WORKER_INSTRUCTIONS,
            output_schema=_output_schema("implement_worker"),
            tools=_worker_tools(),
            **shared,
        ),
        AgentConfig(
            name="review_worker",
            description="审核评审 worker，适合根据目标和验收标准判断通过/不通过并指出风险；不负责直接实现修改。",
            instructions=DEFAULT_REVIEW_WORKER_INSTRUCTIONS,
            output_schema=_output_schema("review_worker"),
            tools=(),
            **shared,
        ),
        AgentConfig(
            name="evolution_worker",
            description=(
                "受控配置演进 worker；根据明确目标读取、校验并最小化修改 Group workers "
                "及共享 Tool/Skill/Graph，禁止修改 manager/root。"
            ),
            instructions=DEFAULT_EVOLUTION_WORKER_INSTRUCTIONS,
            output_schema=_output_schema("evolution_worker"),
            # 管理工具由 RunnerConfig capability 与可信 Agent role 注入并绑定 owner；声明中不放
            # shell/python/通用文件写工具，避免绕过领域 Registry 的校验和原子写入。
            tools=(),
            **shared,
        ),
    )


__all__ = [
    "DEFAULT_GROUP_MODEL_SECTION",
    "DEFAULT_GROUP_WORKER_NAMES",
    "DEFAULT_MANAGER_INSTRUCTIONS",
    "DEFAULT_EVOLUTION_WORKER_INSTRUCTIONS",
    "DEFAULT_WORKER_TOOL_NAMES",
    "build_default_group_manager_config",
    "build_default_group_worker_configs",
    "get_default_group_worker_output_schema",
]
