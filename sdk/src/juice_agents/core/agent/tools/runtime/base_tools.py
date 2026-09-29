"""
内置工具共享的 Tool 基类定义。

每个工具描述自身的输入输出信息，并通过 `forward` 实现具体逻辑。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import contextmanager
import json
import threading
from typing import Any, Dict, TYPE_CHECKING

import logging

if TYPE_CHECKING:
    from juice_agents.core.permissions.policy import PermissionResult

logger = logging.getLogger(__name__)

SHORT_OBSERVATION_CHARS = 4_000
LIST_OBSERVATION_CHARS = 8_000
CONTENT_OBSERVATION_CHARS = 12_000
ORCHESTRATION_OBSERVATION_CHARS = 16_000
ASK_OBSERVATION_CHARS = 32_000


class Tool(ABC):
    """
    工具基类。

    `inputs` 和 `outputs` 使用描述字典，形如：
    {
        "image": {"type": "image", "description": "图片或路径"}
    }
    """

    name: str = ""
    description: str = ""
    inputs: Dict[str, Dict[str, Any]] = {}
    outputs: Dict[str, Dict[str, Any]] = {}
    # 若为 True，调用该工具后 agent 循环立即终止（与 submit_output 行为一致）
    is_terminal: bool = False
    # 若为 True，工具不会修改项目文件、执行任意代码或启动子 agent。
    # plan mode 默认只允许只读工具，少数流程工具由 plan mode 显式特许。
    is_read_only: bool = False
    # Conservative defaults. Built-in/domain classes are marked as audited in
    # __init_subclass__; user-defined Tools must override execution_policy
    # before strict ToolManager scheduling.
    _execution_mode: Any = "serial"
    _execution_thread_affinity: str = "any"
    _execution_resource_keys: tuple[str, ...] = ()
    _execution_policy_declared: bool = False
    # 每个 Tool 显式拥有 observation 硬上限。内置短状态默认 4K；具体正文、
    # 列表、编排和用户回答工具在各自类上覆盖为对应平衡档。
    max_observation_chars: int = SHORT_OBSERVATION_CHARS

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        module = str(getattr(cls, "__module__", ""))
        if "execution_policy" in cls.__dict__:
            cls._execution_policy_declared = True
        elif (
            module.startswith("juice_agents.core.agent.tools.builtin")
            or module.startswith("juice_agents.core.team")
            or module.startswith("juice_agents.core.registry.tools")
        ):
            # These modules are framework-owned and audited below through
            # conservative defaults when a class does not need a dynamic rule.
            cls._execution_policy_declared = True
        if module.startswith("juice_agents.core.team") or module.startswith(
            "juice_agents.core.agent.tools.builtin.evolution"
        ) or module.startswith("juice_agents.core.agent.tools.builtin.web.browser") or module.startswith(
            "juice_agents.core.agent.tools.builtin.planning"
        ) or module.startswith("juice_agents.core.agent.tools.builtin.user_interaction"):
            cls._execution_mode = "barrier"
            cls._execution_thread_affinity = "main"

    def __init__(self) -> None:
        # 拷贝输入输出描述，避免类属性被实例共享修改
        self.inputs = dict(self.inputs)
        self.outputs = dict(self.outputs)
        hard_limit = getattr(type(self), "max_observation_chars", SHORT_OBSERVATION_CHARS)
        if isinstance(hard_limit, bool) or not isinstance(hard_limit, int) or hard_limit <= 0:
            raise ValueError(f"工具 {self.name!r} 的 max_observation_chars 必须为正整数")
        self.max_observation_chars = hard_limit
        # action 可逐次下调上限。thread-local 使 ReAct 并发工具互不污染，也让
        # 后台发起工具能把本次有效值传给 Runner manifest。
        self._observation_limit_local = threading.local()
        # Tool 实例可以在同一 action batch 中被多个 worker 复用。执行上下文同样
        # 必须是 thread-local，不能把一个 action 的取消 token 泄漏给相邻调用。
        self._execution_context_local = threading.local()

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        logger.debug("调用工具 %s，args=%s kwargs=%s", self.name, args, kwargs)
        return self.forward(*args, **kwargs)

    def execution_policy(
        self,
        args: dict[str, Any],
        context: Any,
    ) -> Any:
        """Return the tool's scheduling policy.

        Runtime-owned tools should override this method with an explicit
        ``ToolExecutionPolicy``.  The conservative serial fallback keeps old
        third-party tools usable during the migration; callers that register
        tools in strict mode can use :meth:`validate_execution_policy` to
        reject classes that did not declare a policy.
        """

        del context
        # Import lazily to avoid a base-module import cycle.
        from .executor import ToolExecutionMode, ToolExecutionPolicy

        mode = self._execution_mode
        if not isinstance(mode, ToolExecutionMode):
            mode = ToolExecutionMode(str(mode).strip().lower())
        if bool(args.get("background")) and bool(getattr(self, "_background_capable", False)):
            mode = ToolExecutionMode.BACKGROUND
        return ToolExecutionPolicy(
            mode=mode,
            thread_affinity=self._execution_thread_affinity,  # type: ignore[arg-type]
            resource_keys=tuple(self._execution_resource_keys),
        )

    @classmethod
    def has_explicit_execution_policy(cls) -> bool:
        """Whether ``cls`` overrides the conservative base policy."""

        # Inspect the class itself instead of an inherited marker. A custom
        # subclass must make its own scheduling choice; inheriting a policy
        # from an unrelated parent must not satisfy strict registration.
        if "execution_policy" in cls.__dict__:
            return True
        module = str(getattr(cls, "__module__", ""))
        return bool(
            getattr(cls, "_execution_policy_declared", False)
            and (
                module.startswith("juice_agents.core.agent.tools.builtin")
                or module.startswith("juice_agents.core.team")
                or module.startswith("juice_agents.core.registry.tools")
            )
        )

    def validate_execution_policy(
        self,
        *,
        strict: bool = False,
        args: dict[str, Any] | None = None,
        context: Any = None,
    ) -> Any:
        """Validate and return this tool's policy.

        ``strict=True`` is intended for registry/initialisation boundaries;
        direct ``Tool`` calls remain backwards compatible until all external
        tools have migrated to an explicit declaration.
        """

        if strict and not type(self).has_explicit_execution_policy():
            raise TypeError(
                f"工具 {self.name!r} 未声明 execution_policy；"
                "请显式实现 execution_policy(args, context)"
            )
        return self.execution_policy(dict(args or {}), context)

    @property
    def current_max_observation_chars(self) -> int:
        """返回当前调用的有效上限；直接调用工具时退回工具硬上限。"""

        value = getattr(self._observation_limit_local, "value", None)
        return value if isinstance(value, int) and value > 0 else self.max_observation_chars

    @contextmanager
    def observation_limit(self, value: int):
        """在单次调用期间绑定有效 observation 上限，退出后恢复原状态。"""

        previous = getattr(self._observation_limit_local, "value", None)
        self._observation_limit_local.value = value
        try:
            yield
        finally:
            if previous is None:
                try:
                    delattr(self._observation_limit_local, "value")
                except AttributeError:
                    pass
            else:
                self._observation_limit_local.value = previous

    @property
    def current_execution_context(self) -> Any | None:
        """Return the context bound to the current Tool invocation.

        A tool that performs a long-running operation can call
        :meth:`check_execution_cancelled` periodically.  The executor binds
        this value only around ``forward()`` and uses thread-local storage, so
        concurrent read-only calls on one Tool instance remain isolated.
        """

        local = getattr(self, "_execution_context_local", None)
        return None if local is None else getattr(local, "value", None)

    @contextmanager
    def execution_context_scope(self, context: Any):
        """Bind framework execution context for one ``forward()`` invocation."""

        # A small number of third-party Tools predate ``Tool.__init__`` and do
        # not call ``super()``.  Lazily creating the local preserves their
        # synchronous behaviour while still giving them the new cancellation
        # contract when executed through ToolManager.
        local = getattr(self, "_execution_context_local", None)
        if local is None:
            local = threading.local()
            self._execution_context_local = local
        previous = getattr(local, "value", None)
        local.value = context
        try:
            yield
        finally:
            if previous is None:
                try:
                    delattr(local, "value")
                except AttributeError:
                    pass
            else:
                local.value = previous

    def check_execution_cancelled(self) -> None:
        """Raise the framework cancellation error when the current call stops.

        This is intentionally optional for short-lived tools.  Tools that own
        a subprocess or polling loop should invoke it between blocking units
        of work so an in-flight invocation observes the Runner cancellation
        token rather than only being marked cancelled after it returns.
        """

        context = self.current_execution_context
        check = getattr(context, "check_cancelled", None)
        if callable(check):
            check()

    def bind_owner_agent(self, agent: Any) -> None:
        """
        将当前工具绑定到所属 agent。

        大多数无状态工具无需感知 owner；需要运行时上下文的工具
        （如 agent_tool 或配置管理工具）可覆写该钩子。
        """
        del agent

    def reset_runtime_state(self) -> None:
        """
        重置工具的临时运行时状态。

        默认工具大多是无状态的，因此基类提供空实现；像文件工具这类需要跨 action
        共享短期状态的工具，可以覆写该方法并在 agent 开启 `reset_session=True`
        的新任务前统一清空。
        """
        return None

    def should_terminal(self, result: Any) -> bool:
        """返回本次工具结果是否应终止 agent 循环。"""

        del result
        return bool(self.is_terminal)

    @abstractmethod
    def forward(self, *args: Any, **kwargs: Any) -> Any:
        """工具的核心逻辑。由子类实现。"""
        raise NotImplementedError(f"工具 {self.name} 未实现 forward 方法")

    def check_permissions(
        self,
        args: dict[str, Any],
        *,
        permission_mode: str = "default",
        agent_mode: str = "agent",
        **context: Any,
    ) -> "PermissionResult":
        """
        工具自决权限，返回 allow/deny/ask/passthrough。

        两个正交维度：
        - ``agent_mode``：执行模式（agent/plan/team/group），决定工具面约束。
          plan 是只读探索模式，因此非只读工具在此直接 deny。
        - ``permission_mode``：审批策略（default/accept），由 PermissionEngine
          在后续步骤消费；工具自决层不在这里放行 accept，避免绕过硬开关。

        默认逻辑：只读工具 → allow；非只读在 plan mode → deny；其余 → passthrough。
        复杂工具（如 agent_tool）覆写此方法做细粒度判定。
        """
        from juice_agents.core.permissions.policy import PermissionResult

        del permission_mode
        if self.is_read_only:
            return PermissionResult("allow")
        if agent_mode == "plan":
            return PermissionResult("deny", reason=f"Plan mode 不允许非只读工具 {self.name!r}")
        return PermissionResult("passthrough")

    def _iter_inputs(self) -> list[tuple[str, Dict[str, Any]]]:
        """返回稳定顺序的输入描述，便于不同 prompt 渲染复用。"""
        return list(self.inputs.items())

    def _iter_outputs(self) -> list[tuple[str, Dict[str, Any]]]:
        """返回稳定顺序的输出描述，便于不同 prompt 渲染复用。"""
        return list(self.outputs.items())

    def _render_param_label(self, name: str, meta: Dict[str, Any]) -> str:
        param_type = meta.get("type", "any")
        required = meta.get("required", True)
        suffix = "required" if required else "optional"
        return f"{name}: {param_type} ({suffix})"

    def to_react_prompt(self) -> str:
        """
        返回适合 ReAct `<actions>` 协议的动作说明。

        ReAct 不直接暴露 Python 函数签名，避免让模型误以为需要输出函数调用。
        """
        example_args = {
            name: f"<{meta.get('type', 'any')}>"
            for name, meta in self._iter_inputs()
        }
        example = json.dumps(
            {"name": self.name, "args": example_args or {}},
            ensure_ascii=False,
        )
        lines = [f"- action: {self.name}"]
        lines.append(f"  purpose: {self.description}")
        if self.inputs:
            lines.append("  args:")
            for name, meta in self._iter_inputs():
                desc = meta.get("description", "无描述")
                lines.append(f"    - {self._render_param_label(name, meta)}: {desc}")
        else:
            lines.append("  args: 必须传空对象 {}")
        lines.append(
            "  observation_limit: "
            f"hard max {self.max_observation_chars} chars; optional sibling "
            "max_observation_chars may only lower it"
        )
        if self.outputs:
            lines.append("  returns:")
            for name, meta in self._iter_outputs():
                output_type = meta.get("type", "any")
                desc = meta.get("description", "无描述")
                lines.append(f"    - {name}: {output_type} - {desc}")
        lines.append(f"  example: {example}")
        return "\n".join(lines)

    def to_code_prompt(self) -> str:
        """
        返回工具的结构化调用说明，供提示词展示。

        格式类似 Python 函数签名 + docstring 风格。
        """
        params = []
        for param, meta in self._iter_inputs():
            param_type = meta.get("type", "Any")
            required = meta.get("required", True)
            if required:
                params.append(f"{param}: {param_type}")
            else:
                params.append(f"{param}: {param_type} = None")

        if self.outputs:
            first_output = next(iter(self.outputs.values()))
            return_type = first_output.get("type", "Any")
        else:
            return_type = "Any"

        signature = f"def {self.name}({', '.join(params)}) -> {return_type}:"
        lines = [signature]
        lines.append(f'    """{self.description}')

        if self.inputs:
            lines.append("")
            lines.append("    Args:")
            for param, meta in self._iter_inputs():
                desc = meta.get("description", "无描述")
                required = meta.get("required", True)
                optional_tag = "" if required else "（可选）"
                lines.append(f"        {param}: {desc}{optional_tag}")

        if self.outputs:
            lines.append("")
            lines.append("    Returns:")
            for name, meta in self._iter_outputs():
                desc = meta.get("description", "无描述")
                output_type = meta.get("type", "Any")
                lines.append(f"        {name} ({output_type}): {desc}")
            if len(self.outputs) > 1:
                lines.append("")
                lines.append("    Important:")
                lines.append("        返回值包含多个字段时，可直接按字段读取结构化结果。")

        lines.append('    """')
        return "\n".join(lines)


__all__ = [
    "Tool",
    "SHORT_OBSERVATION_CHARS",
    "LIST_OBSERVATION_CHARS",
    "CONTENT_OBSERVATION_CHARS",
    "ORCHESTRATION_OBSERVATION_CHARS",
    "ASK_OBSERVATION_CHARS",
]
