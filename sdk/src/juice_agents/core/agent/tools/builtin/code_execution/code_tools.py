"""
默认 tools，为智能体提供常用能力。
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:  # pragma: no cover - 类型提示用
    from juice_agents.core.agent.local_python_executor import LocalPythonExecutor

from ...runtime.base_tools import CONTENT_OBSERVATION_CHARS, Tool
from juice_agents.core.runner.execution.cancellation import StreamCancelled, raise_if_cancelled
from .security import (
    DEFAULT_BLOCKED_PYTHON_CALL_PREFIXES,
    DEFAULT_BLOCKED_PYTHON_IMPORTS,
    DEFAULT_BLOCKED_SHELL_PATTERNS,
    ensure_shell_command_allowed,
)

logger = logging.getLogger(__name__)


class ShellTool(Tool):
    _background_capable = True

    def execution_policy(self, args: dict[str, Any], context: Any) -> Any:
        del context
        from ...runtime.executor import ToolExecutionMode, ToolExecutionPolicy

        mode = ToolExecutionMode.BACKGROUND if bool(args.get("background")) else ToolExecutionMode.SERIAL
        cwd = str(args.get("workdir") or "").strip()
        return ToolExecutionPolicy(mode=mode, resource_keys=(f"cwd:{cwd}",) if cwd else ())
    """
    在单条命令行中执行命令，返回 stdout/stderr/returncode。
    """

    name = "shell"
    max_observation_chars = CONTENT_OBSERVATION_CHARS
    description = "执行单条 Shell 命令并返回结果"
    inputs = {
        "command": {"type": "string", "description": "要执行的命令"},
        "timeout": {
            "type": "number",
            "description": "超时时间（秒），默认 20s",
            "required": False,
        },
        "workdir": {
            "type": "string",
            "description": "可选工作目录，不填使用默认工作目录或当前目录",
            "required": False,
        },
        "background": {
            "type": "boolean",
            "description": "是否以后台 async task 方式执行，默认 false",
            "required": False,
        },
    }
    outputs = {
        "stdout": {"type": "string", "description": "标准输出"},
        "stderr": {"type": "string", "description": "标准错误"},
        "returncode": {"type": "integer", "description": "进程退出码"},
        "status": {"type": "string", "description": "同步执行时为空；后台执行时为 launched 或 failed"},
        "async_task_id": {"type": "string", "description": "后台 async task id"},
        "kind": {"type": "string", "description": "后台 async task 类型"},
        "summary": {"type": "string", "description": "后台执行摘要"},
        "output_dir": {"type": "string", "description": "后台 async task 结果目录"},
        "error": {"type": "string", "description": "后台执行失败原因"},
    }

    def __init__(
        self,
        default_timeout: float = 20.0,
        default_workdir: str | Path | None = None,
        blocked_patterns: list[str] | list[tuple[str, str]] | None = None,
    ) -> None:
        super().__init__()
        self.owner_agent: Any | None = None
        self.default_timeout = default_timeout
        self.blocked_patterns = blocked_patterns or list(DEFAULT_BLOCKED_SHELL_PATTERNS)
        self.default_workdir = None
        if default_workdir:
            resolved = Path(default_workdir).expanduser().resolve()
            if not resolved.exists():
                raise ValueError(f"default_workdir 不存在: {resolved}")
            if not resolved.is_dir():
                raise ValueError(f"default_workdir 必须是目录: {resolved}")
            self.default_workdir = resolved

    def bind_owner_agent(self, agent: Any) -> None:
        self.owner_agent = agent

    def _runner_context(self) -> Any | None:
        if self.owner_agent is None:
            return None
        return getattr(self.owner_agent, "runner_context", None)

    @staticmethod
    def _terminate_process(process: subprocess.Popen[str], *, command: str, reason: str) -> None:
        if process.poll() is not None:
            return
        logger.info("ShellTool 终止命令: %s reason=%s", command, reason)
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except Exception:
            process.terminate()
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            logger.warning("ShellTool terminate 超时，kill 命令: %s", command)
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except Exception:
                process.kill()
            process.wait(timeout=1.0)

    def forward(
        self,
        command: str,
        timeout: float | None = None,
        workdir: str | None = None,
        background: bool = False,
    ) -> Dict[str, Any]:
        if not isinstance(command, str) or not command.strip():
            raise ValueError("command 必须为非空字符串")

        # 智能 timeout 选择：
        # - 后台任务：默认无限（None），允许长时间运行（用户可显式指定）
        # - 同步任务：默认使用 self.default_timeout（20秒），避免阻塞
        if timeout is not None:
            run_timeout = timeout
        elif background:
            run_timeout = None  # 后台任务默认无限 timeout
        else:
            run_timeout = self.default_timeout
        cwd = Path(workdir).expanduser().resolve() if workdir else self.default_workdir
        if cwd and not cwd.exists():
            raise ValueError(f"指定的工作目录不存在: {cwd}")
        if cwd and not cwd.is_dir():
            raise ValueError(f"指定的工作目录不是目录: {cwd}")

        try:
            ensure_shell_command_allowed(command, blocked_patterns=self.blocked_patterns)
            if background:
                if self.owner_agent is None:
                    raise ValueError("shell(background=True) 需要先绑定 owner_agent")
                runner_context = getattr(self.owner_agent, "runner_context", None)
                if runner_context is not None:
                    receipt = runner_context.launch_local_bash(
                        command=command,
                        cwd=str(cwd) if cwd else "",
                        timeout_seconds=run_timeout,
                        max_observation_chars=self.current_max_observation_chars,
                    )
                else:
                    # Background lifetime belongs to Runner. Do not create a
                    # private registry here: direct calls without Runner must
                    # fail explicitly like every manager-owned tool call.
                    raise RuntimeError("shell(background=True) requires a Runner context")
                receipt["summary"] = f"后台 shell 命令已启动：{command}"
                return dict(receipt)
            logger.info("ShellTool 执行命令: %s (cwd=%s, timeout=%ss)", command, cwd or ".", run_timeout)
            runner_context = self._runner_context()
            cancel_event = getattr(runner_context, "cancel_event", None)
            raise_if_cancelled(cancel_event)
            process = subprocess.Popen(
                command,
                shell=True,
                cwd=str(cwd) if cwd else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            unregister = None
            if runner_context is not None and callable(getattr(runner_context, "register_cancel_callback", None)):
                unregister = runner_context.register_cancel_callback(
                    lambda reason: self._terminate_process(process, command=command, reason=reason)
                )
            deadline = time.monotonic() + float(run_timeout)
            try:
                while process.poll() is None:
                    raise_if_cancelled(cancel_event)
                    if time.monotonic() >= deadline:
                        self._terminate_process(process, command=command, reason="timeout")
                        stdout, _stderr = process.communicate(timeout=1.0)
                        logger.warning("ShellTool 执行超时 (%ss): %s", run_timeout, command)
                        return {
                            "stdout": (stdout or "").rstrip("\n"),
                            "stderr": f"Timeout after {run_timeout} seconds",
                            "returncode": -1,
                        }
                    time.sleep(0.05)
                raise_if_cancelled(cancel_event)
                stdout, stderr = process.communicate()
            except StreamCancelled:
                self._terminate_process(process, command=command, reason="user_cancelled")
                raise
            finally:
                if callable(unregister):
                    unregister()
            stdout = (stdout or "").rstrip("\n")
            stderr = (stderr or "").rstrip("\n")
            logger.info(
                "ShellTool 执行完成，returncode=%s, stdout_len=%d, stderr_len=%d",
                process.returncode,
                len(stdout),
                len(stderr),
            )
            return {"stdout": stdout, "stderr": stderr, "returncode": process.returncode}
        except subprocess.TimeoutExpired as exc:
            stdout = (exc.stdout or "").rstrip("\n")
            logger.warning("ShellTool 执行超时 (%ss): %s", run_timeout, command)
            return {"stdout": stdout, "stderr": f"Timeout after {run_timeout} seconds", "returncode": -1}
        except StreamCancelled:
            raise
        except Exception as exc:  # pragma: no cover - 防御性保护
            logger.exception("ShellTool 执行失败: %s", exc)
            return {"stdout": "", "stderr": str(exc), "returncode": -1}


class PythonTool(Tool):
    _execution_mode = "serial"
    """
    使用本地安全执行器运行 Python 代码。

    - 允许复用执行状态，支持可选重置
    - 返回执行结果、日志、错误信息与终止输出标记
    """

    name = "python"
    max_observation_chars = CONTENT_OBSERVATION_CHARS
    description = "执行受控 Python 代码块并返回结果"
    inputs = {
        "code": {"type": "string", "description": "要执行的 Python 代码"},
        "variables": {"type": "object", "description": "执行前注入的变量", "required": False},
        "reset_state": {"type": "boolean", "description": "是否在执行前重置执行器", "required": False},
    }
    outputs = {
        "result": {"type": "any", "description": "代码执行结果"},
        "logs": {"type": "string", "description": "print 输出日志"},
        "error": {"type": "string", "description": "错误信息，成功时为 None"},
        "observation_images": {"type": "list", "description": "执行过程中收集的图片"},
    }

    def __init__(
        self,
        additional_authorized_imports: list[str] | None = None,
        initial_variables: Dict[str, Any] | None = None,
        blocked_imports: list[str] | None = None,
        blocked_call_prefixes: list[str] | None = None,
        blocked_shell_patterns: list[str] | list[tuple[str, str]] | None = None,
    ) -> None:
        super().__init__()
        self.additional_authorized_imports = additional_authorized_imports or []
        self._base_variables = dict(initial_variables or {})
        self.blocked_imports = list(blocked_imports or DEFAULT_BLOCKED_PYTHON_IMPORTS)
        self.blocked_call_prefixes = list(blocked_call_prefixes or DEFAULT_BLOCKED_PYTHON_CALL_PREFIXES)
        self.blocked_shell_patterns = list(blocked_shell_patterns or DEFAULT_BLOCKED_SHELL_PATTERNS)
        self.executor: "LocalPythonExecutor | None" = None

    def _init_executor(self) -> None:
        from juice_agents.core.agent.local_python_executor import LocalPythonExecutor

        self.executor: LocalPythonExecutor = LocalPythonExecutor(
            additional_authorized_imports=self.additional_authorized_imports,
            allow_all_imports=True,
            blocked_imports=self.blocked_imports,
            blocked_call_prefixes=self.blocked_call_prefixes,
            blocked_shell_patterns=self.blocked_shell_patterns,
        )
        self.executor.send_tools({})
        if self._base_variables:
            self.executor.send_variables(self._base_variables)

    def _ensure_executor(self) -> None:
        if self.executor is None:
            logger.info("PythonTool 初始化执行器")
            self._init_executor()

    def forward(self, code: str, variables: Dict[str, Any] | None = None, reset_state: bool = False) -> Dict[str, Any]:
        if not isinstance(code, str) or not code.strip():
            raise ValueError("code 必须为非空字符串")

        self._ensure_executor()
        assert self.executor is not None  # for type checkers

        if reset_state:
            logger.info("PythonTool 重置执行器状态")
            self._init_executor()

        if variables:
            logger.debug("PythonTool 注入变量: %s", list(variables.keys()))
            self.executor.send_variables(variables)

        logger.info("PythonTool 执行代码，长度=%d", len(code))
        code_output = self.executor(code)
        return {
            "result": code_output.output,
            "logs": code_output.logs,
            "error": code_output.error,
            "observation_images": code_output.observation_images,
        }

__all__ = ["ShellTool", "PythonTool"]
