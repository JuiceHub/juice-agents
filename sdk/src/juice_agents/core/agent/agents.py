"""
智能体实现。

包含多轮推理的基类 `MultiStepAgent`，及 ReAct、CodeAct 两种示例策略。
"""

from __future__ import annotations

import inspect
import json
import logging
import math
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from juice_agents.core.agent.attachments import RuntimeAttachment
from juice_agents.core.runner.types.identity import new_root_agent_id
from juice_agents.core.permissions import check_agent_tool_permission

try:  # 可选依赖，渲染 Jinja 模板
    from jinja2 import Template
except Exception:  # pragma: no cover - 运行时缺失 jinja2
    Template = None  # type: ignore

try:  # 可选依赖，标准 JSON Schema 校验
    from jsonschema import ValidationError as JsonSchemaValidationError
    from jsonschema import validate as jsonschema_validate
except Exception:  # pragma: no cover - 运行时缺失 jsonschema
    JsonSchemaValidationError = None  # type: ignore
    jsonschema_validate = None  # type: ignore

from juice_agents.core.agent.agent_type import DEFAULT_OBSERVATION_IMAGE_CACHE_DIR, ObservationImage
from .local_python_executor import BASE_BUILTIN_MODULES, LocalPythonExecutor
from juice_agents.core.models import _EmptyModelResponseError
from .prompts import (
    DEFAULT_CODEACT_SYSTEM_PROMPT,
    DEFAULT_REACT_SYSTEM_PROMPT,
    get_codeact_prompt_segments,
    get_codeact_system_prompt,
    get_react_prompt_segments,
    get_react_system_prompt,
)
from .prompts.sections import normalize_prompt_language
from juice_agents.core.registry.skills import SkillRegistry
from .sessions import (
    ActionStep,
    ActionValidationError,
    AgentError,
    AgentSession,
    CompactCompressStrategy,
    CompressStrategy,
    CompressibleAgentSession,
    ContextWindowExceededError,
    ModelOutputProtocolError,
    RemainCompressStrategy,
    SubmitOutputValidationError,
    TaskStep,
    ToolResolutionError,
    is_recoverable_agent_error,
    observations_to_text,
)
from .sessions.content import (
    CODEACT_OBSERVATION_PROJECTION_CHARS,
    DEFAULT_OBSERVATION_PROJECTION_CHARS,
)
from .tools.builtin.output.completion_tools import SubmitOutputTool
from .tools.builtin.graphs.constants import GRAPH_TOOL_NAMES
from .tools.builtin.plugins.plugins_tools import PluginViewTool, PluginsListTool
from .tools.builtin.skills.skills_tools import SkillViewTool, SkillsListTool
from .tools.builtin.evolution.skill_manage import SkillManageTool
from .tools.runtime.base_tools import Tool
from .tools.runtime.executor import (
    ResolvedToolAction,
    ToolExecutionContext,
    ToolExecutionResult,
)
from juice_agents.core.utils import parse_model_json
from juice_agents.core.runner.execution.cancellation import StreamCancelled, raise_if_cancelled

logger = logging.getLogger(__name__)
ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-9;]*m")
_MISSING = object()

# ANSI 颜色码，用于突出智能体专属的美观日志。
COLOR_RESET = "\033[0m"
COLOR_BOLD = "\033[1m"
COLOR_DIVIDER = "\033[90m"  # 灰色分隔线
COLOR_USER = "\033[92m"     # 绿色 (用户任务)
COLOR_ASSISTANT = "\033[94m" # 蓝色 (模型输出)
COLOR_OBS = "\033[96m"       # 青色 (观测)
COLOR_ERROR = "\033[91m"     # 红色 (错误)

LOG_TYPE_CONFIG = {
    "用户任务": {"color": COLOR_USER, "icon": "👤"},
    "模型输出": {"color": COLOR_ASSISTANT, "icon": "🤖"},
    "Attachments": {"color": COLOR_OBS, "icon": "📎"},
    "Observations": {"color": COLOR_OBS, "icon": "👁️"},
    "Error": {"color": COLOR_ERROR, "icon": "❌"},
}

_NO_SUBMIT_OUTPUT = object()


def _runner_tool_policy(agent: Any) -> Any | None:
    """Read the immutable policy bound by RunnerContext, if any.

    Agents never decide a mode.  They only project the serializable policy so
    the model is not shown a callable that ToolManager will later deny.
    """

    runner = getattr(getattr(agent, "runner_context", None), "runner", None)
    return getattr(getattr(runner, "config", None), "tool_policy", None)


def _is_read_only_policy(agent: Any) -> bool:
    return bool(getattr(_runner_tool_policy(agent), "read_only", False))


def _policy_allows_tool(agent: Any, tool: Tool) -> bool:
    policy = _runner_tool_policy(agent)
    if policy is None:
        return True
    name = str(getattr(tool, "name", "") or "").strip()
    allowed = frozenset(getattr(policy, "allowed_tools", ()) or ())
    if allowed and name not in allowed:
        return False
    if (
        bool(getattr(policy, "read_only", False))
        and not bool(getattr(tool, "is_read_only", False))
        and name not in frozenset(getattr(policy, "read_only_exceptions", ()) or ())
    ):
        return False
    return True


def _policy_denial_message(tool_name: str) -> str:
    return f"RunnerConfig policy blocked tool {tool_name!r}."


def _policy_allowed_agent_names(agent: Any) -> frozenset[str]:
    policy = _runner_tool_policy(agent)
    return frozenset(
        str(item).strip()
        for item in getattr(policy, "allowed_agent_names", ()) or ()
        if str(item).strip()
    )


@dataclass(frozen=True, slots=True)
class ManagedAgentDescriptor:
    """Prompt-facing registry agent descriptor."""

    name: str
    description: str = ""


class _RegistryLazyTool(Tool):
    """
    registry tool 的轻量运行时代理。

    配置工具只把 tool 名称加入当前 agent 的可调用表；这里在真正调用时再
    从 ToolRegistry 创建真实 Tool，避免“创建配置”阶段直接挂载运行时对象。
    """

    def __init__(
        self,
        *,
        tool_name: str,
        tool_config_dir: str | Path | None = None,
        params: dict[str, Any] | None = None,
    ) -> None:
        super().__init__()
        self.name = str(tool_name or "").strip()
        self.tool_config_dir = None if tool_config_dir is None else Path(tool_config_dir)
        self.params = dict(params or {})
        self.owner_agent: Any | None = None
        self.description = f"registry tool: {self.name}"
        self._load_prompt_metadata()

    def _load_prompt_metadata(self) -> None:
        if not self.name:
            return
        try:
            from juice_agents.core.registry import ToolRegistry

            config = ToolRegistry(self.tool_config_dir).load_config(self.name)
        except Exception:
            return
        self.description = config.description
        self.inputs = dict(config.inputs)
        self.outputs = dict(config.outputs)
        self.max_observation_chars = config.max_observation_chars

    def bind_owner_agent(self, agent: Any) -> None:
        self.owner_agent = agent

    def execution_policy(self, args: dict[str, Any], context: Any) -> Any:
        """Resolve the configured Tool's policy without allowing model control."""

        from juice_agents.core.registry import ToolRegistry

        tool = ToolRegistry(self.tool_config_dir).instantiate(self.name, params=self.params)
        policy_fn = getattr(tool, "execution_policy", None)
        if callable(policy_fn):
            return policy_fn(dict(args), context)
        from .tools.runtime.executor import ToolExecutionMode, ToolExecutionPolicy

        return ToolExecutionPolicy(mode=ToolExecutionMode.SERIAL)

    def forward(self, *args: Any, **kwargs: Any) -> Any:
        from juice_agents.core.registry import ToolRegistry

        tool = ToolRegistry(self.tool_config_dir).instantiate(self.name, params=self.params)
        if self.owner_agent is not None:
            tool.bind_owner_agent(self.owner_agent)
        with tool.observation_limit(self.current_max_observation_chars):
            return tool(*args, **kwargs)


def _merge_tools_with_submit_output(
    tools: List[Tool],
    *,
    skill_tool_params: dict[str, Any] | None = None,
    plugin_tool_params: dict[str, Any] | None = None,
    include_skill_tools: bool = True,
    include_plugin_tools: bool = True,
    include_graph_tools: bool = True,
    include_self_evolution: bool = True,
) -> dict[str, Tool]:
    """
    将传入的工具去重后自动补充 runtime 基础工具。

    `submit_output` 是终止协议工具；skills_* 是 Hermes 风格 progressive
    disclosure 所需的按需加载入口，声明态导出仍只保留用户显式配置的工具。
    """
    tools_dict = {
        tool.name: tool
        for tool in tools
        if include_graph_tools or str(getattr(tool, "name", "")) not in GRAPH_TOOL_NAMES
    }
    if "submit_output" not in tools_dict:
        output_tool = SubmitOutputTool()
        tools_dict[output_tool.name] = output_tool
        logger.info("自动注入 submit_output 工具")
    if include_skill_tools:
        params = dict(skill_tool_params or {})
        skill_tool_classes = (SkillsListTool, SkillViewTool, SkillManageTool) if include_self_evolution else (SkillsListTool, SkillViewTool)
        for tool_cls in skill_tool_classes:
            if tool_cls.name not in tools_dict:
                tools_dict[tool_cls.name] = tool_cls(**params)
                logger.info("自动注入 skill 工具: %s", tool_cls.name)
    if include_plugin_tools:
        params = dict(plugin_tool_params or {})
        for tool_cls in (PluginsListTool, PluginViewTool):
            if tool_cls.name not in tools_dict:
                tools_dict[tool_cls.name] = tool_cls(**params)
                logger.info("自动注入 plugin 工具: %s", tool_cls.name)
    return tools_dict


def _render_system_prompt(system_prompt: str, context: dict[str, Any]) -> str:
    """
    渲染支持 Jinja 语法的 system prompt。

    约束：必须安装 jinja2，否则直接抛出异常，避免静默退化。
    """
    if not system_prompt:
        return ""

    if Template is None:
        raise ImportError("渲染 system_prompt 需要 jinja2，请先安装 jinja2")

    return Template(system_prompt).render(**context)


def _get_pretty_logger() -> logging.Logger:
    """
    返回用于美观日志输出的专用 logger，格式仅保留消息内容。
    """
    pretty_logger = logging.getLogger("agents.pretty")
    if not pretty_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        pretty_logger.addHandler(handler)
        pretty_logger.propagate = False
        pretty_logger.setLevel(logging.INFO)
    return pretty_logger


def _strip_ansi(text: str) -> str:
    """移除 ANSI 转义序列，便于写入纯文本日志文件。"""
    return ANSI_ESCAPE_PATTERN.sub("", text)


def _should_use_color() -> bool:
    """
    判断是否应该使用ANSI颜色代码。

    注意：不检查 stderr.isatty()，因为测试可能使用 StringIO 捕获日志，
    仍然需要验证颜色输出功能。
    """
    return True


def _format_pretty_block(
    title: str,
    content: str,
    *,
    agent_name: str,
    timestamp: str,
) -> str:
    """
    生成带颜色的美观日志块，移除了侧边框以避免越界，并为内部文字着色。
    在非TTY环境（如测试）中自动禁用颜色代码。
    """
    config = LOG_TYPE_CONFIG.get(title, {"color": COLOR_RESET, "icon": "📝"})
    icon = config["icon"]

    safe_content = "" if content is None else str(content)

    # 根据环境决定是否使用颜色
    use_color = _should_use_color()

    if use_color:
        color = config["color"]
        # 标题行：左侧短线 + 图标 + 标题 + agent/time 元信息 + 右侧长线
        header = (
            f"{COLOR_DIVIDER}━━ {COLOR_RESET}{COLOR_BOLD}{color}{icon} {title}{COLOR_RESET} "
            f"{COLOR_DIVIDER}[agent={agent_name} time={timestamp}] "
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{COLOR_RESET}"
        )
        if not safe_content:
            return f"\n{header}\n"
        # 正文内容：直接着色，移除侧边框
        body = f"{color}{safe_content}{COLOR_RESET}"
    else:
        # 无颜色模式：纯文本输出
        header = f"━━ {icon} {title} [agent={agent_name} time={timestamp}] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        if not safe_content:
            return f"\n{header}\n"
        body = safe_content

    return f"\n{header}\n{body}\n"


def _extract_block(text: str, tag: str) -> str:
    """
    从模型输出中提取 <tag>...</tag> 或成对 <tag> ... <tag> 之间的内容。
    """
    patterns = [
        rf"<{tag}>\s*(.*?)\s*</{tag}>",
        rf"<{tag}>\s*(.*?)\s*<{tag}>",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


_THOUGHT_BLOCK_PATTERN = re.compile(r"<thought>.*?</thought>", re.DOTALL | re.IGNORECASE)
# 闭合符允许是 </actions> 或重复的 <actions>：模型常把闭标签误写成开标签，此形式
# 意图无歧义（沿用 _extract_block 的既有容错），不值得多花一轮往返让模型重写。
# 真正的重复块（两个完整闭合对）仍会被计为 2 个而报错，见 _validate_react_protocol。
_ACTIONS_BLOCK_PATTERN = re.compile(
    r"<actions>(.*?)(?:</actions>|<actions>)", re.DOTALL | re.IGNORECASE
)


def _strip_thought_blocks(text: str) -> str:
    """
    逐块删除所有 ``<thought>...</thought>``，返回仅剩协议外层的残余文本。

    模型经常在 thought 里引用协议名（写出字面量 ``<actions>`` / ``</actions>``），
    若直接全文定位 actions 会把这些字面量误当成真正的块边界。这里用【非贪婪】
    逐块删除而非“从首个 <thought> 删到末个 </thought>”的贪婪做法：贪婪会在模型
    尾部幻觉伪造第二轮（thought + actions）时，把中间真实的 actions 一起吞掉，
    只剩伪造的那个且数量恰好为 1，导致校验通过并静默执行伪造动作。逐块删除会让
    这种情况留下 2 个 actions，落到显式协议错误、由模型下一轮自行修复。
    """
    return _THOUGHT_BLOCK_PATTERN.sub("", text)


def _find_actions_blocks(text: str) -> list[re.Match[str]]:
    """在已剔除 thought 的文本上定位所有 ``<actions>...</actions>`` 块。"""
    return list(_ACTIONS_BLOCK_PATTERN.finditer(text))


def _build_react_empty_output_protocol_error() -> ModelOutputProtocolError:
    """统一生成 ReAct 的空响应协议提示，只约束外层格式。"""
    return ModelOutputProtocolError(
        "模型输出格式错误: Invalid output format. Required:\n"
        "<thought>...</thought>\n"
        "<actions>[...]</actions>"
    )


def _build_codeact_empty_output_protocol_error() -> ModelOutputProtocolError:
    """统一生成 CodeAct 的空响应协议提示，只约束外层格式。"""
    return ModelOutputProtocolError(
        "模型输出格式错误: Invalid output format. Required:\n"
        "<thought>...</thought>\n"
        "<code>...</code>"
    )


def _safe_json_loads(
    payload: str,
    *,
    field_name: str,
    error_cls: type[AgentError] = AgentError,
) -> Any:
    """
    使用统一解析工具，优先严格 JSON，失败时可走统一兜底逻辑。
    """
    try:
        return parse_model_json(payload, field_name=field_name)
    except ValueError as exc:
        raise error_cls(f"解析 {field_name} 失败: {exc}") from exc


def _schema_accepts_type(schema: dict[str, Any] | None, expected: str) -> bool:
    if not isinstance(schema, dict):
        return False
    schema_type = schema.get("type")
    if isinstance(schema_type, str):
        return schema_type == expected
    if isinstance(schema_type, list):
        return expected in schema_type
    return False


def _coerce_submit_output_payload(output: Any, schema: dict[str, Any] | None) -> Any:
    """
    仅在 schema 需要 object/array 且 output 为字符串时尝试 JSON 语法修复。

    这里不做字段别名、层级搬运或宽松兼容，只允许把字符串 JSON 解析回目标结构。
    """
    if not isinstance(output, str):
        return output
    if _schema_accepts_type(schema, "object"):
        return parse_model_json(output, field_name="submit_output.output", expected_type=dict)
    if _schema_accepts_type(schema, "array"):
        return parse_model_json(output, field_name="submit_output.output", expected_type=list)
    return output


def _validate_submit_output_payload(output: Any, schema: dict[str, Any] | None) -> Any:
    """
    对 submit_output 的 payload 做统一的解析与 schema 校验。

    这里将“协议错误”统一提升为 SubmitOutputValidationError，避免下游把
    结构/校验失败误判为普通 observation。
    """
    try:
        normalized_output = _coerce_submit_output_payload(output, schema)
    except ValueError as exc:
        raise SubmitOutputValidationError(
            "submit_output 协议错误：output 修复后仍无法通过解析。\n"
            f"{exc}\n"
            "请按 schema 重新输出。"
        ) from exc

    schema_error = _validate_submit_output_schema(normalized_output, schema)
    if schema_error:
        raise SubmitOutputValidationError(
            "submit_output 协议错误：output 未通过 output_schema 校验。\n"
            f"{schema_error}\n"
            "请修正后重试。"
        )
    return normalized_output


def _serialize_submit_output(output: Any) -> str:
    """保证终结输出在 observation 中保持稳定的文本形态。"""
    if isinstance(output, (dict, list)):
        return json.dumps(output, ensure_ascii=False)
    return str(output)


def _type_matches(value: Any, schema_type: str) -> bool:
    if schema_type == "null":
        return value is None
    if schema_type == "boolean":
        return isinstance(value, bool)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "number":
        return (isinstance(value, int) and not isinstance(value, bool)) or isinstance(value, float)
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "array":
        return isinstance(value, list)
    if schema_type == "object":
        return isinstance(value, dict)
    return True


def _validate_instance_simple(instance: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    errors: list[str] = []
    if not isinstance(schema, dict):
        return [f"{path}: schema 必须为 object"]

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        if not any(isinstance(item, str) and _type_matches(instance, item) for item in schema_type):
            errors.append(f"{path}: type 不匹配，期望 {schema_type}，实际 {type(instance).__name__}")
            return errors
    elif isinstance(schema_type, str):
        if not _type_matches(instance, schema_type):
            errors.append(f"{path}: type 不匹配，期望 {schema_type}，实际 {type(instance).__name__}")
            return errors

    if "enum" in schema and instance not in schema.get("enum", []):
        errors.append(f"{path}: 值不在 enum 中，实际 {instance!r}")
    if "const" in schema and instance != schema.get("const"):
        errors.append(f"{path}: 值不等于 const，期望 {schema.get('const')!r}，实际 {instance!r}")

    if isinstance(instance, dict):
        properties = schema.get("properties")
        required = schema.get("required")
        if isinstance(required, list):
            for req in required:
                if isinstance(req, str) and req not in instance:
                    errors.append(f"{path}: 缺少必填字段 {req!r}")
        if isinstance(properties, dict):
            for key, subschema in properties.items():
                if key in instance and isinstance(subschema, dict):
                    errors.extend(_validate_instance_simple(instance[key], subschema, f"{path}.{key}"))
        additional_allowed = schema.get("additionalProperties", True)
        if additional_allowed is False and isinstance(properties, dict):
            allowed_keys = set(properties.keys())
            extra_keys = [k for k in instance.keys() if k not in allowed_keys]
            for key in extra_keys:
                errors.append(f"{path}: 不允许额外字段 {key!r}")

    if isinstance(instance, list):
        items_schema = schema.get("items")
        if isinstance(items_schema, dict):
            for idx, item in enumerate(instance):
                errors.extend(_validate_instance_simple(item, items_schema, f"{path}[{idx}]"))

    return errors


def _validate_submit_output_schema(output: Any, schema: dict[str, Any] | None) -> str | None:
    if schema is None:
        return None
    if not isinstance(schema, dict):
        return "output_schema 必须为 object"

    if jsonschema_validate is not None:
        try:
            jsonschema_validate(instance=output, schema=schema)
            return None
        except Exception as exc:
            if JsonSchemaValidationError is not None and isinstance(exc, JsonSchemaValidationError):
                at_path = "$"
                if exc.path:
                    at_path = "$." + ".".join([str(p) for p in exc.path])
                return f"{at_path}: {exc.message}"
            return f"schema 校验失败: {exc}"

    errors = _validate_instance_simple(output, schema, "$")
    if not errors:
        return None
    return "; ".join(errors[:5])


class MultiStepAgent(ABC):
    """
    多轮推理智能体。

    子类实现 `step` 方法即可自定义每轮推理逻辑。
    """

    def __init__(
        self,
        tools: List[Tool],
        model: Any,
        system_prompt: str | None,
        max_steps: int = 5,
        context_window_tokens: int = 128_000,
        max_tool_calls_per_step: int = 8,
        session_compression: dict[str, Any] | None = None,
        compression_model: Any | None = None,
        name: str | None = None,
        description: str | None = None,
        skill_names: Optional[list[str]] = None,
        disabled_skill_names: Optional[list[str]] = None,
        skills_base_path: str | Path | None = None,
        local_skills_path: str | Path | None = None,
        skills_config_path: str | Path | None = None,
        instructions: Optional[str] = None,
        output_schema: Optional[Dict[str, Any]] = None,
        log_file_path: str | Path | None = None,
        tool_executor_max_workers: int = 8,
        prompt_language: str = "en",
        skills_enabled: bool = True,
        attachments_hook: Callable[[int, "MultiStepAgent"], list[RuntimeAttachment]] | None = None,
        agent_id: str | None = None,
    ):
        self.tools = {tool.name: tool for tool in tools}
        self._runtime_tool_refs: dict[str, dict[str, Any]] = {}
        self._runtime_managed_agent_refs: dict[str, dict[str, Any]] = {}
        self._runtime_disabled_tool_names: set[str] = set()
        self._runtime_disabled_managed_agent_names: set[str] = set()
        self.model = model
        self.system_prompt_template = system_prompt
        self.prompt_language = normalize_prompt_language(prompt_language)
        # 子智能体关系不再保存为对象列表；运行时只保留 registry 名称引用。
        self.managed_agents: list["MultiStepAgent"] = []
        self.name = name or self.__class__.__name__
        self.agent_id = str(agent_id or new_root_agent_id()).strip() or new_root_agent_id()
        self.description = description or ""
        self.instructions = instructions
        self.output_schema = output_schema
        if isinstance(tool_executor_max_workers, bool) or not isinstance(tool_executor_max_workers, int) or tool_executor_max_workers <= 0:
            raise ValueError("tool_executor_max_workers 必须为正整数")
        self.tool_executor_max_workers = tool_executor_max_workers
        if (
            isinstance(context_window_tokens, bool)
            or not isinstance(context_window_tokens, int)
            or context_window_tokens <= 0
        ):
            raise ValueError("context_window_tokens 必须为正整数")
        if (
            isinstance(max_tool_calls_per_step, bool)
            or not isinstance(max_tool_calls_per_step, int)
            or max_tool_calls_per_step <= 0
        ):
            raise ValueError("max_tool_calls_per_step 必须为正整数")
        self.context_window_tokens = context_window_tokens
        self.max_tool_calls_per_step = max_tool_calls_per_step
        self.log_file_path: Path | None = (
            Path(log_file_path) if log_file_path is not None else None
        )
        self.skill_registry = SkillRegistry(
            local_dir=Path(local_skills_path) if local_skills_path else None,
            builtin_dir=Path(skills_base_path) if skills_base_path else None,
            config_path=Path(skills_config_path) if skills_config_path else None,
            allow_names=skill_names or None,
            deny_names=disabled_skill_names or None,
            available_tools=list(self.tools.keys()),
        )
        self.skills_enabled = bool(skills_enabled) and self.skill_registry.enabled
        if not self.skills_enabled:
            for tool_name in ("skills_list", "skill_view", "skill_manage"):
                self.tools.pop(tool_name, None)
        self.skills_metadata = [
            meta.to_prompt_dict()
            for meta in (self.skill_registry.list() if self.skills_enabled else [])
        ]
        self.system_prompt_static, self.system_prompt_dynamic = self.init_system_prompt_segments()
        # 完整 system_prompt 保持为两段拼接，供 transcript / 摘要 / 既有调用方使用，
        # 与分段后实际发送给模型的内容保持一致。
        self.system_prompt = self._compose_full_system_prompt()
        self.max_steps = max_steps
        self.session_compression = self._normalize_session_compression(session_compression)
        self.compression_model = compression_model or model
        self._pending_request_bytes = 0
        self.session = AgentSession(
            system_prompt=self.system_prompt,
            system_prompt_static=self.system_prompt_static,
            system_prompt_dynamic=self.system_prompt_dynamic,
        )
        # Tool execution is deliberately not created here.  Fresh Agents are
        # pure step objects until AgentManager binds a RunnerContext; that
        # context forwards every action to the Runner-owned ToolManager.
        self._current_execution_records: list[ToolExecutionResult] = []
        self.session_compression_strategy: CompressStrategy | None = None
        self.attachments_hook = attachments_hook
        logger.info(
            "智能体 %s 初始化完成，agent_id=%s，工具：%s，托管智能体：%s",
            self.name,
            self.agent_id,
            list(self.tools.keys()),
            list(self.managed_agents_map.keys()),
        )

    def set_log_file_path(self, log_file_path: str | Path | None) -> None:
        """设置日志文件路径（None 表示关闭文件日志）。"""
        self.log_file_path = Path(log_file_path) if log_file_path is not None else None
        if self.log_file_path is not None:
            self.log_file_path.parent.mkdir(parents=True, exist_ok=True)

    def _runner_observation_image_cache_dir(self) -> str:
        """
        返回当前图片观测落盘目录。

        Runner 绑定后使用 runner 级目录；裸 agent / 单元测试 / 工具直调场景没有
        runner id，只能退回 workspace 级 `.juice/observation_images`。
        """

        context = getattr(self, "runner_context", None)
        layout = getattr(context, "layout", None)
        cache_dir = getattr(layout, "observation_images_dir", None)
        return DEFAULT_OBSERVATION_IMAGE_CACHE_DIR if cache_dir is None else str(cache_dir)

    def bind_manager_infra(
        self,
        *,
        agent_id: str | None = None,
        event_queue: Any | None = None,
        async_task_manager: Any | None = None,
        runtime_workspace: Any | None = None,
        runtime_base_dir: str | Path | None = None,
    ) -> None:
        """Attach Manager-owned facilities without giving Agent ownership.

        The fields are private references for domain tools that need to emit a
        notification.  Scheduling, persistence and cancellation still happen
        in the managers; all ordinary tool calls use ``RunnerContext``.
        """
        if agent_id is not None:
            normalized_agent_id = str(agent_id or "").strip()
            if normalized_agent_id:
                self.agent_id = normalized_agent_id
        if event_queue is not None:
            self._event_queue = event_queue
        if async_task_manager is not None:
            self._async_task_manager = async_task_manager
        if runtime_workspace is not None:
            self._runtime_workspace = runtime_workspace
        if runtime_base_dir is not None:
            self._runtime_base_dir = Path(runtime_base_dir).resolve()

    def _append_file_log(self, formatted: str) -> None:
        if self.log_file_path is None:
            return
        try:
            path = Path(self.log_file_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(formatted)
                if not formatted.endswith("\n"):
                    f.write("\n")
        except Exception as exc:  # pragma: no cover - 运行时兜底
            logger.warning("写入 agent 文件日志失败: %s", exc)

    @property
    def managed_agents_map(self) -> dict[str, ManagedAgentDescriptor]:
        """当前可调度 registry agent 的 prompt 描述视图。"""

        from juice_agents.core.registry import AgentRegistry

        refs = dict(getattr(self, "_runtime_managed_agent_refs", {}) or {})
        disabled = set(getattr(self, "_runtime_disabled_managed_agent_names", set()) or set())
        descriptors: dict[str, ManagedAgentDescriptor] = {}
        for agent_name, ref in refs.items():
            normalized = str(agent_name or "").strip()
            if not normalized or normalized in disabled:
                continue
            description = ""
            config_dir = dict(ref or {}).get("agent_config_dir")
            try:
                description = AgentRegistry(config_dir=config_dir).load_config(normalized).description
            except Exception:
                logger.debug("读取 managed agent 描述失败: agent=%s", normalized, exc_info=True)
            descriptors[normalized] = ManagedAgentDescriptor(
                name=normalized,
                description=description,
            )
        return descriptors

    @property
    def model_visible_managed_agents_map(self) -> dict[str, ManagedAgentDescriptor]:
        """按当前 mode 投影给模型的 managed agents，不改变完整声明集。"""
        descriptors = self.managed_agents_map
        allowed_agent_names = _policy_allowed_agent_names(self)
        if not allowed_agent_names:
            return descriptors
        return {
            name: descriptor
            for name, descriptor in descriptors.items()
            if name in allowed_agent_names
        }

    @property
    def available_managed_agent_names(self) -> list[str]:
        """当前 agent_tool 可调度的 registry agent 名称。"""
        names = set(str(name) for name in dict(getattr(self, "_runtime_managed_agent_refs", {}) or {}))
        names.difference_update(set(getattr(self, "_runtime_disabled_managed_agent_names", set()) or set()))
        return sorted(name for name in names if str(name).strip())

    @property
    def available_tool_names(self) -> list[str]:
        """当前 agent 可调用的 tool 名称，包括 live tools 和名称型 registry 更新。"""
        names = set(self.tools.keys())
        names.update(str(name) for name in dict(getattr(self, "_runtime_tool_refs", {}) or {}))
        names.difference_update(set(getattr(self, "_runtime_disabled_tool_names", set()) or set()))
        return sorted(name for name in names if str(name).strip())

    def _bind_owner_tools(self) -> None:
        """
        给当前 agent 挂载的工具补齐 owner 上下文。

        运行时经常会先实例化工具，再在后续步骤里把它挂到 agent 上；
        统一在这里做 bind，可以兼容初始化、runtime update 和手工注入三条路径。
        """
        for tool in self.tools.values():
            try:
                tool.bind_owner_agent(self)
            except Exception:  # pragma: no cover - 运行时保护
                logger.warning("绑定工具 owner 失败: agent=%s tool=%s", self.name, tool.name)

    def _build_callable_registry(self) -> dict[str, Any]:
        self._bind_owner_tools()
        # 运行时可调用表只暴露工具；子 agent 统一通过 agent_tool 按 registry 名称调度。
        disabled_tool_names = set(getattr(self, "_runtime_disabled_tool_names", set()) or set())
        callables = {
            name: tool
            for name, tool in self.tools.items()
            if name not in disabled_tool_names
        }
        for tool_name, ref in dict(getattr(self, "_runtime_tool_refs", {}) or {}).items():
            normalized = str(tool_name or "").strip()
            if not normalized or normalized in disabled_tool_names:
                continue
            ref = dict(ref or {})
            callables[normalized] = _RegistryLazyTool(
                tool_name=normalized,
                tool_config_dir=ref.get("tool_config_dir"),
                params=ref.get("params") if isinstance(ref.get("params"), dict) else {},
            )
        for tool in callables.values():
            try:
                tool.bind_owner_agent(self)
            except Exception:  # pragma: no cover - 运行时保护
                logger.warning("绑定 callable owner 失败: agent=%s tool=%s", self.name, getattr(tool, "name", ""))
        return callables

    @property
    def callables(self) -> dict[str, Any]:
        """由 concrete tools 与 registry name refs 派生出的当前可调用视图。"""
        return self._build_callable_registry()

    @property
    def model_visible_callables(self) -> dict[str, Any]:
        """模型工具面；Plan Mode 只暴露允许工具，runtime callables 保持完整。"""
        callables = self.callables
        return {
            name: target
            for name, target in callables.items()
            if not isinstance(target, Tool) or _policy_allows_tool(self, target)
        }

    def _build_executor_tool_registry(self) -> dict[str, Any]:
        """Return tools for CodeAct executors, with runtime permission guards."""

        callables = self.model_visible_callables
        guarded: dict[str, Any] = {}
        for name, target in callables.items():
            if isinstance(target, Tool) and not _policy_allows_tool(self, target):
                message = _policy_denial_message(name)

                def _blocked_tool(*args: Any, _message: str = message, **kwargs: Any) -> Any:
                    del args, kwargs
                    raise RuntimeError(_message)

                _blocked_tool.__name__ = str(name)
                guarded[name] = _blocked_tool
                continue

            if isinstance(target, Tool):
                def _guarded_tool(
                    *args: Any,
                    _target: Tool = target,
                    **kwargs: Any,
                ) -> Any:
                    # CodeAct may call a tool more than once or from a branch;
                    # assign action order at invocation time while reusing the
                    # same executor contract as ReAct.
                    if args:
                        bound = inspect.signature(_target.forward).bind(*args, **kwargs)
                        bound.apply_defaults()
                        values = dict(bound.arguments)
                        values.pop("self", None)
                    else:
                        values = dict(kwargs)
                    order = int(getattr(self, "_codeact_action_order", 0))
                    self._codeact_action_order = order + 1
                    result = self._execute_tool_action(
                        ResolvedToolAction(tool=_target, args=values, order=order),
                        order=order,
                    )
                    self._current_execution_records.append(result)
                    if not result.ok:
                        raise RuntimeError(result.error or f"tool {name!r} failed")
                    return result.observation

                _guarded_tool.__name__ = str(name)
                guarded[name] = _guarded_tool
                continue
            guarded[name] = target
        return guarded

    def refresh_executor_tools(self) -> None:
        """把当前派生 callable 视图同步到需要静态工具表的 executor。"""
        if hasattr(self, "executor") and hasattr(self.executor, "send_tools"):
            try:
                self.executor.send_tools(self._build_executor_tool_registry())
            except Exception:  # pragma: no cover - 运行时保护
                logger.warning("刷新 executor tools 失败: agent=%s", self.name)

    def __call__(
        self,
        task: str,
        task_images: Optional[List[Any]] = None,
        *,
        reset_session: bool = True,
        attachments_hook: Callable[[int, "MultiStepAgent"], list[RuntimeAttachment]] | None = None,
    ) -> Any:
        """
        直调协议已废弃，统一要求使用显式入口。
        """
        del task, task_images, reset_session, attachments_hook
        raise TypeError(
            "MultiStepAgent.__call__ 已禁用；直接执行 agent 请使用 "
            'Runner.create(runner_config="agent").run(...)，'
            "子智能体协作请通过 agent_tool(name=..., task=...) 调度。"
        )

    def _pretty_log(self, title: str, content: str) -> None:
        """统一的美观日志格式。"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        formatted = _format_pretty_block(
            title,
            content,
            agent_name=self.name,
            timestamp=timestamp,
        )
        _get_pretty_logger().info(formatted)
        self._append_file_log(_strip_ansi(formatted))

    @staticmethod
    def _format_observations(observations: Any) -> str:
        return observations_to_text(observations)

    def _build_prompt_context(self, **extra_context: Any) -> dict[str, Any]:
        """
        构造 system prompt 渲染上下文。

        这里集中维护 ReAct / CodeAct 共享的上下文字段，避免两个子类各自拼装时
        出现字段漂移；子类只补自己独有的上下文即可。
        """
        output_schema_str = None
        if self.output_schema is not None:
            output_schema_str = json.dumps(self.output_schema, indent=2, ensure_ascii=False)

        callables = self.model_visible_callables
        context = {
            "tools": callables,
            "tool_names": list(callables.keys()),
            "has_shell_tool": "shell" in callables,
            "browser_enabled": any(str(name).startswith("browser_") for name in callables),
            "managed_agents": self.model_visible_managed_agents_map,
            "skills_enabled": self.skills_enabled,
            "skills_metadata": self.skills_metadata,
            "instructions": self.instructions,
            "output_schema": self.output_schema,
            "output_schema_str": output_schema_str,
            "memory_enabled": False,
            "memory_dir": "",
            "memory_index": "",
            "max_tool_calls_per_step": self.max_tool_calls_per_step,
        }
        memory_context = getattr(self, "memory_context", None)
        if isinstance(memory_context, dict):
            context.update(memory_context)
        context.update(extra_context)
        return context

    def _render_system_prompt_with_context(
        self,
        default_template: str,
        **extra_context: Any,
    ) -> str:
        template = self.system_prompt_template or default_template
        return _render_system_prompt(template, self._build_prompt_context(**extra_context))

    def _render_segments_with_context(
        self,
        static_template: str,
        dynamic_template: str,
        **extra_context: Any,
    ) -> tuple[str, str]:
        """渲染 (static, dynamic) 两段模板。

        当用户传入自定义 system_prompt_template 时，无 section 边界可用，降级为
        “整段作为 static、dynamic 为空”，行为与单段渲染等价（仍可作缓存前缀）。
        """
        context = self._build_prompt_context(**extra_context)
        if self.system_prompt_template:
            return _render_system_prompt(self.system_prompt_template, context), ""
        static_text = _render_system_prompt(static_template, context) if static_template else ""
        dynamic_text = _render_system_prompt(dynamic_template, context) if dynamic_template else ""
        return static_text, dynamic_text

    def _compose_full_system_prompt(self) -> str:
        """把 static / dynamic 两段拼回完整 system prompt 字符串。"""
        parts = [
            part
            for part in (
                getattr(self, "system_prompt_static", "") or "",
                getattr(self, "system_prompt_dynamic", "") or "",
            )
            if part.strip()
        ]
        return "\n\n".join(parts)

    @staticmethod
    def _format_model_output_log_content(
        content: Any,
        reasoning_content: Any = "",
        *,
        include_reasoning_in_context: bool = False,
    ) -> str:
        """
        仅在日志展示层把 reasoning 渲染成 `<think>...</think>` 前缀。

        `ActionStep.model_output` 仍然只保存正文，避免污染协议解析与 session 持久化。
        默认 session 不会把 reasoning 回灌给模型，因此默认日志也不把 reasoning
        混入“模型输出”块；只有显式回灌时，日志才用 `<think>` 与上下文保持一致。
        """
        normalized_content = "" if content is None else str(content)
        normalized_reasoning = "" if reasoning_content is None else str(reasoning_content)
        if not normalized_reasoning.strip() or not include_reasoning_in_context:
            return normalized_content
        if not normalized_content:
            return f"<think>{normalized_reasoning}</think>"
        return f"<think>{normalized_reasoning}</think>\n{normalized_content}"

    def _record_model_output(self, step: ActionStep, content: Any) -> str:
        """统一写入模型原始输出，并同步到美观日志。"""
        normalized_content = "" if content is None else str(content)
        step.model_output = normalized_content
        include_reasoning = bool(self.session.include_reasoning_in_context)
        if step.reasoning_content and not include_reasoning:
            # 默认 reasoning 不会进入后续上下文，日志也保持为独立诊断块，
            # 避免把 `<think>` 误读成模型正文协议的一部分。
            self._pretty_log("Reasoning", step.reasoning_content)
        self._pretty_log(
            "模型输出",
            self._format_model_output_log_content(
                normalized_content,
                step.reasoning_content,
                include_reasoning_in_context=include_reasoning,
            ),
        )
        return normalized_content

    def _prepare_step_execution(self) -> None:
        """Prepare a step without rebuilding this managed Agent in place.

        Declaration changes are applied only by a later fresh acquire through
        AgentManager.  Keeping the live instance stable prevents a model step
        from bypassing Manager lifecycle ownership.
        """

    def _normalize_failed_action_step(self, step: ActionStep, exc: Exception) -> ActionStep:
        """
        把异常收敛成可回放的 ``ActionStep``，并区分 step 与 round 失败。

        模型能够修正的协议类错误保留在 ``step.error`` 中，并继续当前
        round；session 会把它作为 ``<error>`` 反馈给下一次模型调用。
        只有无法再次调用模型的错误才结束 round。
        """
        step.model_output = step.model_output or "异常终止"
        step.error = exc if isinstance(exc, AgentError) else AgentError(str(exc))
        step.observations = []
        step.observations_images = []
        step.round_outcome = "continue" if is_recoverable_agent_error(exc) else "failed"
        self._pretty_log("Error", str(step.error))
        return step

    def _finalize_step_artifacts(
        self,
        step: ActionStep,
        *,
        observations: list[str] | None = None,
        observation_images: list[ObservationImage] | None = None,
        observation_limits: list[int] | None = None,
    ) -> None:
        """
        统一收敛每一步的执行产物。

        两类 agent 都只产出 observations / images。配置管理工具只原子写入
        `.juice`；后续 AgentManager fresh acquire 才读取新声明，因此当前
        动作列表和当前 ManagedAgent 的工具绑定始终保持稳定。
        """
        step.observations = list(observations or [])
        step.observations_images = list(observation_images or [])
        step.observation_limits = list(observation_limits or [])

        if step.observations or step.observations_images:
            self._pretty_log("Observations", self._format_observations(step.observations))

    @staticmethod
    def _tool_call_records_for_display(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Project unified execution records into the session tool-call view."""

        tool_calls: list[dict[str, Any]] = []
        for record in records:
            name = str(record.get("name") or record.get("tool_name") or "").strip()
            if not name or name == "submit_output":
                continue
            args = record.get("args")
            tool_calls.append(
                {
                    "name": name,
                    "args": dict(args) if isinstance(args, dict) else {},
                    "mode": str(record.get("execution_mode") or record.get("mode") or "serial"),
                    "status": "success" if bool(record.get("succeeded")) else str(record.get("status") or "error"),
                    "call_id": str(record.get("call_id") or ""),
                    "task_id": str(record.get("task_id") or record.get("async_task_id") or ""),
                    "observation_index": record.get("observation_index"),
                    "is_terminal": bool(record.get("is_terminal")),
                    "max_observation_chars": record.get("max_observation_chars"),
                }
            )
        return tool_calls

    def refresh_system_prompt(self) -> None:
        """重渲染分段 system prompt 并同步到 session，消除双源不一致。

        这是配置全量变更（工具集替换、受限配置应用、托管 agent 安装等）后重建
        system prompt 的唯一入口，调用方不应再手动拼装 system_prompt/static/dynamic。
        generation 切换时会重算整段；static 不依赖运行时状态，重算后逐字节相同，
        因而仍能保持稳定的缓存前缀。
        """
        self.system_prompt_static, self.system_prompt_dynamic = self.init_system_prompt_segments()
        self.system_prompt = self._compose_full_system_prompt()
        session = getattr(self, "session", None)
        if session is not None:
            session.system_prompt = self.system_prompt
            session.system_prompt_static = self.system_prompt_static
            session.system_prompt_dynamic = self.system_prompt_dynamic

    def set_attachments_hook(
        self,
        hook: Callable[[int, "MultiStepAgent"], list[RuntimeAttachment]] | None,
    ) -> None:
        self.attachments_hook = hook

    def set_session_compression_strategy(self, strategy: CompressStrategy | None) -> None:
        """设置显式的旧式单策略覆盖；None 恢复默认 remain + compact 管线。"""

        self.session_compression_strategy = strategy

    @staticmethod
    def _normalize_session_compression(value: dict[str, Any] | None) -> dict[str, Any]:
        """把构造器输入规整为稳定的组合压缩运行时配置。"""

        raw = dict(value or {})
        remain = dict(raw.get("remain") or {})
        compact = dict(raw.get("compact") or {})
        keep_recent = remain.get("keep_recent_actions", 5)
        preserve_recent = compact.get("preserve_recent_actions", 5)
        trigger_ratio = compact.get("trigger_ratio", 0.8)
        if isinstance(keep_recent, bool) or not isinstance(keep_recent, int) or keep_recent < 0:
            raise ValueError("session_compression.remain.keep_recent_actions 必须为非负整数")
        if (
            isinstance(preserve_recent, bool)
            or not isinstance(preserve_recent, int)
            or preserve_recent < 0
        ):
            raise ValueError("session_compression.compact.preserve_recent_actions 必须为非负整数")
        if (
            isinstance(trigger_ratio, bool)
            or not isinstance(trigger_ratio, (int, float))
            or float(trigger_ratio) <= 0
            or float(trigger_ratio) > 1
        ):
            raise ValueError("session_compression.compact.trigger_ratio 必须位于 (0, 1]")
        normalized_compact: dict[str, Any] = {
            "trigger_ratio": float(trigger_ratio),
            "preserve_recent_actions": preserve_recent,
        }
        model_name = compact.get("compression_model_config_name")
        if model_name is not None:
            normalized_compact["compression_model_config_name"] = str(model_name)
        return {
            "remain": {"keep_recent_actions": keep_recent},
            "compact": normalized_compact,
        }

    @staticmethod
    def _messages_utf8_bytes(messages: list[dict[str, Any]]) -> int:
        """用稳定 JSON 编码计算真正传给 model.generate 的消息字节数。"""

        encoded = json.dumps(
            messages,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return len(encoded)

    def _token_per_byte_ratio(self) -> float:
        """用最近一轮 usage 校准；没有可靠 usage 时退回 bytes / 3。"""

        for step in reversed(self.session.steps):
            if not isinstance(step, ActionStep):
                continue
            request_bytes = int(step.request_bytes or 0)
            usage = step.usage if isinstance(step.usage, dict) else {}
            input_tokens = usage.get("input_tokens")
            if (
                request_bytes > 0
                and not isinstance(input_tokens, bool)
                and isinstance(input_tokens, (int, float))
                and input_tokens >= 0
            ):
                return max(float(input_tokens) / request_bytes * 1.1, 1.0 / 3.0)
            return 1.0 / 3.0
        return 1.0 / 3.0

    def _estimate_message_tokens(self, messages: list[dict[str, Any]]) -> int:
        # 上取整保证窗口边界偏保守，避免小数截断把刚超限的请求误判为可发送。
        return math.ceil(
            self._messages_utf8_bytes(messages) * self._token_per_byte_ratio()
        )

    def _record_request_metrics(self, step: ActionStep, response: dict[str, Any]) -> None:
        """把当前请求字节数和 provider usage 与产出该响应的 ActionStep 绑定。"""

        step.request_bytes = max(0, int(self._pending_request_bytes or 0))
        raw_usage = response.get("usage") if isinstance(response, dict) else None
        if isinstance(raw_usage, dict):
            step.usage = dict(raw_usage)
        else:
            step.usage = None

    def build_messages(self) -> list[dict[str, Any]]:
        # 显式单策略只用于已有集成的受控覆盖；remain 仍由 wrapper 保证不回写。
        if self.session_compression_strategy is not None:
            compressible = CompressibleAgentSession(self.session, self.session_compression_strategy)
            messages = compressible.to_messages()
            self.session = compressible.session
            self._pending_request_bytes = self._messages_utf8_bytes(messages)
            return messages

        # Compact 的触发和摘要输入都基于 canonical 的逐工具限额投影，且发生在
        # remain 之前，确保摘要模型仍能看到较老 observation 的真实投影。
        canonical_messages = self.session.to_messages()
        canonical_tokens = self._estimate_message_tokens(canonical_messages)
        compact_config = self.session_compression["compact"]
        trigger_tokens = int(
            self.context_window_tokens * float(compact_config["trigger_ratio"])
        )
        if canonical_tokens >= trigger_tokens:
            strategy = CompactCompressStrategy(
                step_threshold=1,
                compression_model=self.compression_model,
                preserve_recent_actions=int(compact_config["preserve_recent_actions"]),
            )
            try:
                self.session = strategy.compress(self.session)
                logger.info(
                    "会话 compact 成功: agent=%s estimated_tokens=%s trigger_tokens=%s",
                    self.name,
                    canonical_tokens,
                    trigger_tokens,
                )
            except Exception:
                # 摘要失败不破坏 canonical；当前轮继续走 remain 投影。
                logger.exception(
                    "会话 compact 失败，降级为 remain 投影: agent=%s",
                    self.name,
                )

        remain_config = self.session_compression["remain"]
        projected_session = RemainCompressStrategy(
            k=int(remain_config["keep_recent_actions"])
        ).compress(self.session)
        messages = projected_session.to_messages()
        estimated_tokens = self._estimate_message_tokens(messages)
        if estimated_tokens > self.context_window_tokens:
            raise ContextWindowExceededError(
                "模型请求上下文超限: "
                f"agent={self.name!r}, estimated_tokens={estimated_tokens}, "
                f"context_window_tokens={self.context_window_tokens}; "
                "compact 已失败或 remain 后仍无法容纳请求"
            )
        self._pending_request_bytes = self._messages_utf8_bytes(messages)
        return messages

    def _runner_cancel_event(self) -> Any | None:
        """Return the runner cancellation token when this agent is runner-bound."""

        context = getattr(self, "runner_context", None)
        return getattr(context, "cancel_event", None)

    def _check_cancelled(self) -> None:
        """Stop the current step immediately when the user interrupts the stream."""

        context = getattr(self, "runner_context", None)
        if context is not None and callable(getattr(context, "check_cancelled", None)):
            context.check_cancelled()
            return
        raise_if_cancelled(self._runner_cancel_event())

    def _tool_execution_context(self) -> ToolExecutionContext:
        """Build the per-step execution context used by ReAct and CodeAct.

        Permission and plan checks run before an action is submitted to a
        worker.  This keeps user interaction and fail-closed policy decisions
        on the manager-owned request thread while the private helper owns all
        actual scheduling.
        """

        runner_context = getattr(self, "runner_context", None)

        def _plan_check(tool: Tool, args: dict[str, Any]) -> Any:
            del args
            if not _policy_allows_tool(self, tool):
                return {
                    "allowed": False,
                    "reason": _policy_denial_message(getattr(tool, "name", "tool")),
                }
            return True

        return ToolExecutionContext(
            agent=self,
            runner_context=runner_context,
            permission_mode=str(getattr(getattr(runner_context, "runner", None), "permission_mode", "") or ""),
            agent_mode=str(getattr(getattr(runner_context, "runner", None), "agent_mode", "") or ""),
            permission_check=lambda tool, args: check_agent_tool_permission(self, tool, dict(args)),
            plan_check=_plan_check,
        )

    def _execute_tools(self, actions: Any, *, context: ToolExecutionContext | None = None) -> list[ToolExecutionResult]:
        """Delegate a batch to the Runner-owned ToolManager.

        This deliberately has no fallback executor.  A live Agent without a
        RunnerContext is not a valid execution host; silently creating a local
        pool would split permission, audit and cancellation ownership.
        """

        runner_context = getattr(self, "runner_context", None)
        execute = getattr(runner_context, "execute_tools", None)
        if not callable(execute):
            raise RuntimeError("Agent 执行工具需要 AgentManager 绑定的 RunnerContext")
        result = execute(actions, context=context or self._tool_execution_context())
        return list(result)

    def _execute_tool_action(self, action: ResolvedToolAction, *, order: int) -> ToolExecutionResult:
        """Delegate CodeAct's one callable invocation through RunnerContext."""

        runner_context = getattr(self, "runner_context", None)
        execute = getattr(runner_context, "execute_tool_action", None)
        if not callable(execute):
            raise RuntimeError("Agent 执行工具需要 AgentManager 绑定的 RunnerContext")
        return execute(action, order=order, context=self._tool_execution_context())

    @staticmethod
    def _generate_accepts_cancel_event(generate: Any) -> bool:
        """Best-effort signature check so old custom models keep working."""

        try:
            signature = inspect.signature(generate)
        except (TypeError, ValueError):
            return True
        return "cancel_event" in signature.parameters or any(
            param.kind == inspect.Parameter.VAR_KEYWORD
            for param in signature.parameters.values()
        )

    def _generate_model(
        self,
        messages: list[dict[str, Any]],
        *,
        stop_sequence: list[str] | None | object = _MISSING,
    ) -> dict[str, Any]:
        """Call the model with the runner cancellation token when supported."""

        self._check_cancelled()
        generate = self.model.generate
        kwargs: dict[str, Any] = {}
        if stop_sequence is not _MISSING:
            kwargs["stop_sequence"] = stop_sequence
        cancel_event = self._runner_cancel_event()
        if cancel_event is not None and self._generate_accepts_cancel_event(generate):
            kwargs["cancel_event"] = cancel_event
        response = generate(messages, **kwargs)
        self._check_cancelled()
        return response

    def _reset_tool_runtime_state(self) -> None:
        """
        在新任务开始前重置工具的临时运行时状态。

        典型场景是文件工具的 read-state：它应该在同一连续会话中可复用，但当上层
        显式要求 `reset_session=True` 启动新任务时，又必须被清掉，避免跨任务串味。
        """
        for tool in self.tools.values():
            try:
                tool.reset_runtime_state()
            except Exception:  # pragma: no cover - 运行时保护
                logger.warning("重置工具运行时状态失败: agent=%s tool=%s", self.name, tool.name)

    @abstractmethod
    def step(self, step: ActionStep) -> ActionStep:
        """执行单步推理。"""
        raise NotImplementedError

    @abstractmethod
    def init_system_prompt(self) -> str:
        """
        返回已经结合自身属性渲染完成的 system prompt。

        子类应利用 self.system_prompt_template（可能为 None）、工具列表和其他上下文生成最终提示词。
        """
        raise NotImplementedError

    def init_system_prompt_segments(self) -> tuple[str, str]:
        """返回渲染完成的 (static, dynamic) system prompt 两段。

        默认实现把 init_system_prompt() 的完整结果当作 static、dynamic 为空，
        保证未覆写该方法的子类仍可工作（无缓存分块收益但不破坏行为）。
        子类应覆写为真正的分段渲染。
        """
        return self.init_system_prompt(), ""


class ReActAgent(MultiStepAgent):
    """
    ReAct 风格智能体：思考 + 工具行动。

    模型输出结构：
        <thought>...</thought>
        <actions>[{"name": "...", "args": {...}}, ...]</actions>
    """

    def __init__(
        self,
        tools: List[Tool],
        model: Any,
        system_prompt: str | None = None,
        max_steps: int = 5,
        context_window_tokens: int = 128_000,
        max_tool_calls_per_step: int = 8,
        session_compression: dict[str, Any] | None = None,
        compression_model: Any | None = None,
        name: str | None = None,
        description: str | None = None,
        skill_names: Optional[list[str]] = None,
        disabled_skill_names: Optional[list[str]] = None,
        skills_base_path: str | Path | None = None,
        local_skills_path: str | Path | None = None,
        skills_config_path: str | Path | None = None,
        instructions: Optional[str] = None,
        output_schema: Optional[Dict[str, Any]] = None,
        log_file_path: str | Path | None = None,
        tool_executor_max_workers: int = 8,
        prompt_language: str = "en",
        enable_skill_tools: bool = True,
        enable_graph_tools: bool = True,
        enable_self_evolution: bool = True,
    ):
        if isinstance(tool_executor_max_workers, bool) or not isinstance(tool_executor_max_workers, int) or tool_executor_max_workers <= 0:
            raise ValueError("tool_executor_max_workers 必须为正整数")
        self.tool_executor_max_workers = tool_executor_max_workers
        # PermissionEngine also consults this marker to reject any out-of-band
        # GraphTool object that was not part of the declared capability set.
        self.graph_tools_enabled = bool(enable_graph_tools)
        prompt_language = normalize_prompt_language(prompt_language)
        system_prompt = system_prompt or get_react_system_prompt(prompt_language)
        tools_dict = _merge_tools_with_submit_output(
            tools,
            skill_tool_params={
                "local_dir": local_skills_path,
                "builtin_dir": skills_base_path,
                "config_path": skills_config_path,
                "allow_names": skill_names or [],
                "deny_names": disabled_skill_names or [],
            },
            plugin_tool_params={
                "workspace_dir": (
                    Path(local_skills_path).resolve().parent.parent
                    if local_skills_path is not None
                    else None
                ),
                "config_path": skills_config_path,
            },
            include_skill_tools=enable_skill_tools,
            include_plugin_tools=enable_skill_tools,
            include_graph_tools=enable_graph_tools,
            include_self_evolution=enable_self_evolution,
        )
        super().__init__(
            tools=list(tools_dict.values()),
            model=model,
            system_prompt=system_prompt,
            max_steps=max_steps,
            context_window_tokens=context_window_tokens,
            max_tool_calls_per_step=max_tool_calls_per_step,
            tool_executor_max_workers=tool_executor_max_workers,
            session_compression=session_compression,
            compression_model=compression_model,
            name=name,
            description=description,
            skill_names=skill_names,
            disabled_skill_names=disabled_skill_names,
            skills_base_path=skills_base_path,
            local_skills_path=local_skills_path,
            skills_config_path=skills_config_path,
            instructions=instructions,
            output_schema=output_schema,
            log_file_path=log_file_path,
            prompt_language=prompt_language,
            skills_enabled=enable_skill_tools,
        )

    def init_system_prompt(self) -> str:
        return self._render_system_prompt_with_context(
            get_react_system_prompt(self.prompt_language)
        )

    def init_system_prompt_segments(self) -> tuple[str, str]:
        static_template, dynamic_template = get_react_prompt_segments(self.prompt_language)
        return self._render_segments_with_context(static_template, dynamic_template)

    def _validate_react_protocol(self, content: str) -> str:
        """
        校验 ReAct 外层协议，并返回唯一 ``<actions>`` 块的原始内容。

        剔除 thought 后要求：actions 块恰好 1 个，且其后不得有非空残留。后者对齐
        提示词里 “不要在 </actions> 后继续输出内容” 的约束——只查数量挡不住模型在
        唯一 actions 之后追加伪造的 ``<attachments>`` / 工具观测，那些内容会随
        ``step.model_output`` 回放并被下一轮当成真实历史。
        """
        residual = _strip_thought_blocks(content)
        blocks = _find_actions_blocks(residual)
        if not blocks:
            raise self._build_action_validation_error(
                "模型输出缺少必需的 <actions> 块。即使当前步没有工具调用，"
                "也必须输出 <actions>[]</actions> 表示空动作列表。"
            )
        if len(blocks) > 1:
            raise self._build_action_validation_error(
                f"模型输出包含 {len(blocks)} 个 <actions> 块，每步只允许一个。"
                "请只保留本步真正要执行的动作，删除其余 <actions> 块。"
            )
        trailing = residual[blocks[0].end():].strip()
        if trailing:
            preview = trailing[:200]
            ellipsis = "..." if len(trailing) > 200 else ""
            raise self._build_action_validation_error(
                "</actions> 之后不得输出任何内容（包括 JSON、正文、attachments "
                "或伪造的工具观测）。请在 </actions> 处结束本步输出。"
                f"实际多余的内容为：{preview}{ellipsis}"
            )
        return blocks[0].group(1)

    def parse_actions(self, model_output: str) -> List[Dict[str, Any]]:
        actions_block = self._validate_react_protocol(model_output)
        stripped_actions = actions_block.strip()
        if not stripped_actions:
            return []
        if stripped_actions and not stripped_actions.startswith("["):
            # 附上实际解析到的内容，便于区分“真的写错了 JSON”与“thought 内字面量
            # 标签把提取带偏”这两类成因，避免模型只看到抽象规则却无从修复。
            preview = stripped_actions[:200]
            ellipsis = "..." if len(stripped_actions) > 200 else ""
            raise self._build_action_validation_error(
                "<actions> 内容必须是 JSON 数组，第一个非空字符必须是 '['；"
                "若本步无需调用工具，请输出 <actions>[]</actions>。"
                f"实际解析到的内容为：{preview}{ellipsis}"
            )
        try:
            actions = _safe_json_loads(
                stripped_actions,
                field_name="actions",
                error_cls=ActionValidationError,
            )
        except ActionValidationError as exc:
            raise self._build_action_validation_error(str(exc)) from exc
        if not isinstance(actions, list):
            raise self._build_action_validation_error("actions 解析结果必须为 list")
        normalized: list[dict[str, Any]] = []
        for idx, action in enumerate(actions):
            if not isinstance(action, dict):
                raise self._build_action_validation_error(f"actions[{idx}] 必须为 object")
            framework_fields = {
                "execution",
                "execution_mode",
                "async_label",
                "thread",
                "thread_affinity",
                "worker",
                "workers",
                "max_workers",
                "resource_keys",
                "resource_key",
                "thread_name",
                "worker_count",
                "call_id",
                "mode",
            }
            forbidden_fields = sorted(framework_fields.intersection(action))
            if forbidden_fields:
                legacy_execution_hint = ""
                if "execution" in forbidden_fields:
                    legacy_value = str(action.get("execution") or "").strip().lower()
                    if legacy_value:
                        legacy_execution_hint = f" (execution={legacy_value} 已移除)"
                raise self._build_action_validation_error(
                    f"actions[{idx}] 包含框架控制字段 {', '.join(forbidden_fields)}；"
                    "通用 execution=async 协议已移除；如需后台执行请使用工具业务参数 background=true；"
                    "模型只能提供 name、args 和 max_observation_chars"
                    f"{legacy_execution_hint}"
                )
            tool_name = action.get("name")
            if not isinstance(tool_name, str) or not tool_name.strip():
                raise self._build_action_validation_error(
                    f"actions[{idx}].name 必须为非空字符串"
                )
            unknown_fields = sorted(
                set(action).difference({"name", "args", "max_observation_chars"})
            )
            if unknown_fields:
                raise self._build_action_validation_error(
                    f"actions[{idx}] 包含未知字段 {', '.join(unknown_fields)}；"
                    "模型只能提供 name、args 和 max_observation_chars"
                )
            if "args" not in action:
                raise self._build_action_validation_error(f"actions[{idx}] 缺少 args")
            tool_args = action.get("args")
            if not isinstance(tool_args, dict):
                raise self._build_action_validation_error(
                    f"actions[{idx}].args 必须为 object"
                )
            forbidden_args = sorted(framework_fields.intersection(tool_args))
            if forbidden_args:
                raise self._build_action_validation_error(
                    f"actions[{idx}].args 包含框架控制字段 {', '.join(forbidden_args)}；"
                    "通用 execution=async 协议已移除；如需后台执行请使用工具业务参数 background=true；"
                    "模型只能提供工具公开的业务参数"
                )
            requested_limit = action.get("max_observation_chars")
            if requested_limit is not None and (
                isinstance(requested_limit, bool)
                or not isinstance(requested_limit, int)
                or requested_limit <= 0
            ):
                raise self._build_action_validation_error(
                    f"actions[{idx}].max_observation_chars 必须为正整数"
                )
            normalized.append(
                {
                    "name": tool_name.strip(),
                    "args": tool_args,
                    "max_observation_chars": requested_limit,
                }
            )
        return normalized

    @staticmethod
    def _build_action_validation_error(detail: str) -> ActionValidationError:
        return ActionValidationError(
            f"模型输出格式错误: Invalid action format: {detail}\n"
            "Required: <actions>[...]</actions>"
        )

    @staticmethod
    def _build_submit_output_validation_error(detail: str) -> SubmitOutputValidationError:
        return SubmitOutputValidationError(
            f"submit_output error: {detail}\n"
            'Required: {{"name":"submit_output","args":{{"output":...}}}}'
        )

    def _validate_submit_output_action(self, action: Dict[str, Any]) -> Any:
        # parse_actions 已保证 action 至少具备合法的 {"name", "args"} 结构；
        # 这里仅补 submit_output 独有的 output 约束，避免重复做同一层校验。
        tool_args = action["args"]
        if "output" not in tool_args:
            raise self._build_submit_output_validation_error("缺少 args.output。")
        return _validate_submit_output_payload(tool_args.get("output"), self.output_schema)

    def _resolve_action_target(self, action: Dict[str, Any], idx: int) -> dict[str, Any]:
        """
        为 action 解析唯一的执行目标。

        终止工具判定、主线程执行要求和 callables 查找都依赖同一个目标对象；
        在这里统一解析后，下游校验和执行都复用这一份结果，避免重复查表。
        """
        tool_name = action["name"]
        target = self.callables.get(tool_name)
        if target is None:
            if tool_name in self.managed_agents_map:
                if "agent_tool" in self.tools:
                    raise ToolResolutionError(
                        f"actions[{idx}].name={tool_name!r} 指向的是托管智能体，"
                        "当前已禁止直接调用；请改用 "
                        '{"name":"agent_tool","args":{"name":"%s","task":"<task>"}}。'
                        % tool_name
                    )
                raise ToolResolutionError(
                    f"actions[{idx}].name={tool_name!r} 指向的是托管智能体，"
                    "当前已禁止直接调用；如需协作，请先注入 agent_tool 后再分发任务。"
                )
            raise ToolResolutionError(
                f"actions[{idx}].name={tool_name!r} 未注册为可用工具，"
                "请从当前工具列表中选择。"
            )
        hard_limit = getattr(target, "max_observation_chars", DEFAULT_OBSERVATION_PROJECTION_CHARS)
        requested_limit = action.get("max_observation_chars")
        effective_limit = hard_limit
        if isinstance(requested_limit, int):
            if requested_limit > hard_limit:
                logger.warning(
                    "action observation 限额超过工具硬上限，已钳制: "
                    "agent=%s tool=%s requested=%s hard=%s",
                    self.name,
                    tool_name,
                    requested_limit,
                    hard_limit,
                )
            effective_limit = min(requested_limit, hard_limit)
        return {
            "order": idx,
            "name": tool_name,
            "args": action["args"],
            "target": target,
            "is_terminal": bool(getattr(target, "is_terminal", False)),
            "is_tool": isinstance(target, Tool),
            "max_observation_chars": effective_limit,
        }

    def _resolve_action_targets(self, actions: List[Dict[str, Any]]) -> list[dict[str, Any]]:
        return [self._resolve_action_target(action, idx) for idx, action in enumerate(actions)]

    def _validate_actions_protocol(self, action_specs: List[dict[str, Any]]) -> None:
        """
        在真正执行 action 前先做协议级校验，避免协议错误造成半成功状态。
        """
        if len(action_specs) > self.max_tool_calls_per_step:
            raise self._build_action_validation_error(
                "单步 actions 数量超过上限："
                f"{len(action_specs)} > {self.max_tool_calls_per_step}；"
                "本步没有执行任何工具。"
            )
        terminal_indexes = [
            spec["order"] for spec in action_specs if spec["is_terminal"]
        ]
        if terminal_indexes and len(action_specs) != 1:
            raise self._build_submit_output_validation_error(
                "终止工具必须独占当前 actions；不能与其他工具放在同一步。"
            )
        for spec in action_specs:
            if spec["is_terminal"] and spec["name"] == "submit_output":
                # submit_output 的最终值需要先走统一 schema 校验，再在执行后复用
                # 这份已校验结果，避免把 Python repr 混入最终输出。
                spec["validated_terminal_output"] = self._validate_submit_output_action(spec)

    def _split_result(self, result: Any) -> tuple[list[str], list[ObservationImage]]:
        if result is None:
            return [], []

        regular_parts = list(result) if isinstance(result, (list, tuple)) else [result]
        if ObservationImage is None:
            return [str(part) for part in regular_parts], []

        texts: list[str] = []
        images: list[ObservationImage] = []

        def _append_part(part: Any) -> None:
            img = self._wrap_image(part)
            if img is not None:
                images.append(img)
            elif part is not None:
                texts.append(str(part))

        for part in regular_parts:
            _append_part(part)
        return texts, images

    def _wrap_image(self, result: Any) -> ObservationImage | None:
        if ObservationImage is None:
            return None
        if isinstance(result, ObservationImage):
            return result
        cache_dir = self._runner_observation_image_cache_dir()
        if isinstance(result, (bytes, bytearray)):
            return ObservationImage.from_image(image=result, description="", cache_dir=cache_dir)
        # 宽松识别 PIL.Image.Image：避免对 Pillow 引入硬依赖
        if result.__class__.__module__.startswith("PIL."):
            return ObservationImage.from_image(image=result, description="", cache_dir=cache_dir)
        return None

    def process_actions(
        self, action_specs: List[dict[str, Any]]
    ) -> tuple[list[str], list[ObservationImage], list[dict[str, Any]], Any]:
        """Execute resolved actions through the Runner-owned ToolManager."""
        if not action_specs:
            return [], [], [], _NO_SUBMIT_OUTPUT
        observations: list[str] = []
        images: list[ObservationImage] = []
        execution_records: list[dict[str, Any]] = []
        terminal_output: Any = _NO_SUBMIT_OUTPUT
        resolved = [
            ResolvedToolAction(
                tool=spec["target"],
                args=dict(spec["args"]),
                order=int(spec["order"]),
                max_observation_chars=spec.get("max_observation_chars"),
            )
            for spec in action_specs
        ]
        context = self._tool_execution_context()
        results = self._execute_tools(resolved, context=context)
        self._current_execution_records = list(results)
        for spec, result in zip(action_specs, results):
            texts, imgs = self._split_result(result.observation)
            if result.status not in {"completed", "background"} and result.error:
                if result.status == "denied" and not _policy_allows_tool(self, spec["target"]):
                    texts = [_policy_denial_message(spec["name"])]
                elif result.status == "failed":
                    detail = str(result.error)
                    if ": " in detail:
                        detail = detail.split(": ", 1)[1]
                    texts = [f"工具 {spec['name']} 执行失败: {detail}"]
                else:
                    texts = [str(result.error)]
            if not texts and not imgs:
                texts = ["执行完成，工具无返回值"] if result.status in {"completed", "background"} else []
            imgs.extend(
                img
                for raw_img in list(result.observation_images or [])
                if (img := self._wrap_image(raw_img)) is not None
            )
            record = result.to_dict()
            record.update(
                {
                    "name": spec["name"],
                    "args": dict(spec["args"]),
                    "mode": result.status,
                    "execution_mode": result.policy.mode.value if result.policy else "serial",
                    "succeeded": result.ok,
                    "is_terminal": bool(spec["is_terminal"]),
                    "max_observation_chars": spec["max_observation_chars"],
                }
            )
            block_lines = list(texts)
            for img in imgs:
                placeholder = f'ObservationImage(image_url="{img.image_url}", description="{img.description}") # 图片内容参考下方observation_image'
                block_lines.append(placeholder)
                images.append(img)
            block = "\n".join(block_lines)
            record["observation_index"] = len(observations) if block else None
            execution_records.append(record)
            if block:
                observations.append(block)
            if result.succeeded and spec["is_terminal"]:
                target = spec["target"]
                should_terminal = not hasattr(target, "should_terminal") or bool(target.should_terminal(result.observation))
                if should_terminal:
                    terminal_output = spec.get("validated_terminal_output", result.observation)

        return observations, images, execution_records, terminal_output

    @staticmethod
    def _tool_call_records_for_display(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        tool_calls: list[dict[str, Any]] = []
        for record in records:
            name = str(record.get("name") or record.get("tool_name") or "").strip()
            if not name or name == "submit_output":
                continue
            args = record.get("args")
            tool_calls.append(
                {
                    "name": name,
                    "args": dict(args) if isinstance(args, dict) else {},
                    "mode": str(record.get("execution_mode") or record.get("mode") or "serial"),
                    "status": "success" if bool(record.get("succeeded")) else str(record.get("status") or "error"),
                    "call_id": str(record.get("call_id") or ""),
                    "task_id": str(record.get("task_id") or record.get("async_task_id") or ""),
                    "observation_index": record.get("observation_index"),
                    "is_terminal": bool(record.get("is_terminal")),
                    "max_observation_chars": record.get("max_observation_chars"),
                }
            )
        return tool_calls

    def step(self, step: ActionStep) -> ActionStep:
        self._prepare_step_execution()
        messages = self.build_messages()
        try:
            # 不下发 stop_sequence：让模型自己吐完闭合标签，输出即完整协议报文。
            # 多吐的尾部内容由 _validate_react_protocol 显式报错、交模型下一轮修复，
            # 而不是在此静默补齐或截断。
            response = self._generate_model(messages)
        except _EmptyModelResponseError as exc:
            # BaseChatModel 已经在同一次 generate() 内做过空响应重试；
            # 只有重试耗尽时才回到 agent 协议层，把它翻译成下一轮可修复的 <error>。
            raise _build_react_empty_output_protocol_error() from exc
        content = str(response.get("content", "") or "")
        self._record_request_metrics(step, response)
        step.reasoning_content = str(response.get("reasoning_content", "") or "")
        self._record_model_output(step, content)
        step.thought = _extract_block(content, "thought")

        # 对未继承 BaseChatModel 的自定义裸 generate(...) 模型，仍保留旧兜底，
        # 这样不要求所有外部模型立刻实现同样的空响应重试协议。
        if not content.strip():
            raise _build_react_empty_output_protocol_error()

        # 外层协议校验（actions 恰好 1 个 + 尾部无残留）在 parse_actions 内完成
        actions = self.parse_actions(content)
        action_specs = self._resolve_action_targets(actions)
        # 先完成协议级校验，再执行工具，避免“前几个工具已经生效，后面才发现
        # submit_output/未知工具 不合法”的半成功状态。
        self._validate_actions_protocol(action_specs)
        observations, observation_images, execution_records, terminal_output = self.process_actions(
            action_specs
        )
        step.tool_calls = self._tool_call_records_for_display(execution_records)
        observation_limits = [
            int(record.get("max_observation_chars") or DEFAULT_OBSERVATION_PROJECTION_CHARS)
            for record in execution_records
            if record.get("observation_index") is not None
        ]

        self._finalize_step_artifacts(
            step,
            observations=observations,
            observation_images=observation_images,
            observation_limits=observation_limits,
        )

        if terminal_output is _NO_SUBMIT_OUTPUT:
            # 空 actions 是显式 yield：当前 round 到此结束，不再无意义地调用模型。
            step.round_outcome = "yielded" if not action_specs else "continue"
        else:
            step.round_outcome = "submitted"
            step.output = terminal_output
            # 终止步的最终值由 runtime 单独维护；当当前 observation 为空，或
            # submit_output 需要稳定 JSON 文本时，再回填一份纯文本结果供日志
            # / session / 展示复用，避免把协议 tag 或 Python repr 泄漏出去。
            if (
                not step.observations
                or (action_specs and action_specs[0]["name"] == "submit_output")
            ):
                step.observations = [_serialize_submit_output(terminal_output)]

        return step


class CodeActAgent(MultiStepAgent):
    """
    CodeAct 风格智能体：思考 + 代码执行。

    模型输出结构：
        <thought>...</thought>
        <code>代码块</code>
    """

    def __init__(
        self,
        tools: List[Tool],
        model: Any,
        system_prompt: str | None = None,
        max_steps: int = 5,
        context_window_tokens: int = 128_000,
        max_tool_calls_per_step: int = 8,
        session_compression: dict[str, Any] | None = None,
        compression_model: Any | None = None,
        name: str | None = None,
        description: str | None = None,
        additional_authorized_imports: Optional[List[str]] = None,
        skill_names: Optional[list[str]] = None,
        disabled_skill_names: Optional[list[str]] = None,
        skills_base_path: str | Path | None = None,
        local_skills_path: str | Path | None = None,
        skills_config_path: str | Path | None = None,
        instructions: Optional[str] = None,
        output_schema: Optional[Dict[str, Any]] = None,
        log_file_path: str | Path | None = None,
        tool_executor_max_workers: int = 8,
        prompt_language: str = "en",
        enable_skill_tools: bool = True,
        enable_graph_tools: bool = True,
        enable_self_evolution: bool = True,
    ):
        prompt_language = normalize_prompt_language(prompt_language)
        if isinstance(tool_executor_max_workers, bool) or not isinstance(tool_executor_max_workers, int) or tool_executor_max_workers <= 0:
            raise ValueError("tool_executor_max_workers 必须为正整数")
        self.tool_executor_max_workers = tool_executor_max_workers
        self.graph_tools_enabled = bool(enable_graph_tools)
        tools_dict = _merge_tools_with_submit_output(
            tools,
            skill_tool_params={
                "local_dir": local_skills_path,
                "builtin_dir": skills_base_path,
                "config_path": skills_config_path,
                "allow_names": skill_names or [],
                "deny_names": disabled_skill_names or [],
            },
            plugin_tool_params={
                "workspace_dir": (
                    Path(local_skills_path).resolve().parent.parent
                    if local_skills_path is not None
                    else None
                ),
                "config_path": skills_config_path,
            },
            include_skill_tools=enable_skill_tools,
            include_plugin_tools=enable_skill_tools,
            include_graph_tools=enable_graph_tools,
            include_self_evolution=enable_self_evolution,
        )
        system_prompt = system_prompt or get_codeact_system_prompt(prompt_language)
        additional_authorized_imports = additional_authorized_imports or []
        authorized_imports = sorted(
            set(BASE_BUILTIN_MODULES) | set(additional_authorized_imports))
        self.authorized_imports = authorized_imports
        super().__init__(
            tools=list(tools_dict.values()),
            model=model,
            system_prompt=system_prompt,
            max_steps=max_steps,
            context_window_tokens=context_window_tokens,
            max_tool_calls_per_step=max_tool_calls_per_step,
            tool_executor_max_workers=tool_executor_max_workers,
            session_compression=session_compression,
            compression_model=compression_model,
            name=name,
            description=description,
            skill_names=skill_names,
            disabled_skill_names=disabled_skill_names,
            skills_base_path=skills_base_path,
            local_skills_path=local_skills_path,
            skills_config_path=skills_config_path,
            instructions=instructions,
            output_schema=output_schema,
            log_file_path=log_file_path,
            prompt_language=prompt_language,
            skills_enabled=enable_skill_tools,
        )
        self.executor = LocalPythonExecutor(
            additional_authorized_imports=additional_authorized_imports,
            additional_functions=None,
        )
        self.executor.send_tools(self._build_executor_tool_registry())
        self.executor.send_variables({"ObservationImage": ObservationImage})

    def parse_code(self, model_output: str) -> str:
        return _extract_block(model_output, "code")

    def _wrap_images(self, images: list[Any]) -> list[Any]:
        wrapped: list[Any] = []
        cache_dir = self._runner_observation_image_cache_dir()
        for img in images:
            if ObservationImage is not None and isinstance(img, ObservationImage):
                wrapped.append(img)
            else:
                wrapped.append(
                    ObservationImage.from_image(
                        image=img,
                        description="code generated image",
                        cache_dir=cache_dir,
                    )
                )
        return wrapped

    def step(self, step: ActionStep) -> ActionStep:
        self._prepare_step_execution()
        messages = self.build_messages()
        try:
            response = self._generate_model(messages)
        except _EmptyModelResponseError as exc:
            raise _build_codeact_empty_output_protocol_error() from exc
        step.reasoning_content = str(response.get("reasoning_content", "") or "")
        self._record_request_metrics(step, response)
        content = self._record_model_output(step, response.get("content", ""))
        step.thought = _extract_block(content, "thought")

        # 自定义裸 generate(...) 模型仍走这里的旧兜底；
        # 只有 BaseChatModel 体系才会在模型层先做空响应重试。
        if not str(content or "").strip():
            raise _build_codeact_empty_output_protocol_error()

        code_block = self.parse_code(content)
        if not code_block:
            raise ModelOutputProtocolError(
                "模型输出格式错误: Invalid output format. Required:\n"
                "<thought>...</thought>\n"
                "<code>...</code>"
            )
        step.code_action = code_block

        self._codeact_action_order = 0
        self._current_execution_records = []
        code_output = self.executor(code_block)
        # executor 会分别返回标准输出/错误、最终返回值和图片；这里把它们拆回
        # session 能消费的 observation 结构，保持日志和回放一致。
        result_parts = (
            list(code_output.output)
            if isinstance(code_output.output, (list, tuple))
            else ([] if code_output.output is None else [code_output.output])
        )
        code_result = result_parts[0] if len(result_parts) == 1 else result_parts

        log_parts: list[str] = []
        if code_output.logs:
            log_parts.append(f"[logs]\n{code_output.logs}")

        if code_output.error:
            log_parts.append(f"[error]\n{code_output.error}")
        elif result_parts:
            log_parts.append(f"[result]\n{code_result}")

        if code_output.submitted:
            _validate_submit_output_payload(code_result, self.output_schema)

        self._finalize_step_artifacts(
            step,
            # CodeAct 内部工具结果始终保持真实值；这里只把最终 logs/result 合为
            # 一个 canonical observation，并在后续模型投影时统一限制为 20K。
            observations=["\n\n".join(part for part in log_parts if part)] if log_parts else [],
            observation_images=self._wrap_images(code_output.observation_images),
            observation_limits=(
                [CODEACT_OBSERVATION_PROJECTION_CHARS] if log_parts else []
            ),
        )
        step.tool_calls = self._tool_call_records_for_display(
            [
                record.to_dict()
                for record in self._current_execution_records
                if str(record.tool_name or "") != "add_image"
            ]
        )
        if code_output.submitted:
            step.round_outcome = "submitted"
            step.output = code_result
        else:
            # Python/工具执行错误已经进入 canonical observations；下一次
            # 模型调用应读取错误并修正代码，而不是把一次 step 失败升级为
            # 整个 round 失败。
            step.round_outcome = "continue"
        return step

    def init_system_prompt(self) -> str:
        return self._render_system_prompt_with_context(
            get_codeact_system_prompt(self.prompt_language),
            authorized_imports=self.authorized_imports,
        )

    def init_system_prompt_segments(self) -> tuple[str, str]:
        static_template, dynamic_template = get_codeact_prompt_segments(self.prompt_language)
        return self._render_segments_with_context(
            static_template,
            dynamic_template,
            authorized_imports=self.authorized_imports,
        )


__all__ = ["MultiStepAgent", "ReActAgent", "CodeActAgent"]
