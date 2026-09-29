"""Shell 与 Python 执行工具的默认安全策略。"""

from __future__ import annotations

import re
from typing import Any

DEFAULT_BLOCKED_SHELL_PATTERNS: list[tuple[str, str]] = [
    (r"(^|\s)sudo(\s|$)", "禁止提权命令 sudo"),
    (r"(^|\s)su(\s|$)", "禁止切换用户命令 su"),
    (r"(^|\s)(shutdown|reboot|poweroff|halt)(\s|$)", "禁止关机或重启命令"),
    (r"rm\s+-rf\s+/", "禁止破坏性删除根目录"),
    (r"(^|\s)mkfs(\.[a-z0-9_+-]+)?(\s|$)", "禁止格式化磁盘命令"),
    (r"dd\s+if=.*\s+of=/dev/", "禁止对块设备执行 dd"),
    (r"(curl|wget)[^|;\n]*\|\s*(sh|bash)\b", "禁止下载后直接执行脚本"),
    (r"/dev/tcp/", "禁止疑似反弹 shell"),
    (r"\bnc\b[^|;\n]*\s-e\s", "禁止 netcat 反弹 shell"),
]

DEFAULT_BLOCKED_PYTHON_IMPORTS = ["ctypes", "pty"]
DEFAULT_BLOCKED_PYTHON_CALL_PREFIXES = [
    "os.system",
    "os.popen",
    "os.exec",
    "os.spawn",
]
SUBPROCESS_SHELL_CALLS = {
    "run",
    "Popen",
    "call",
    "check_call",
    "check_output",
    "getoutput",
    "getstatusoutput",
}


def _normalize_blocked_shell_patterns(
    blocked_patterns: list[str] | list[tuple[str, str]] | None,
) -> list[tuple[str, str]]:
    if not blocked_patterns:
        return list(DEFAULT_BLOCKED_SHELL_PATTERNS)
    normalized: list[tuple[str, str]] = []
    for item in blocked_patterns:
        if isinstance(item, tuple) and len(item) == 2:
            normalized.append((str(item[0]), str(item[1])))
        else:
            normalized.append((str(item), f"命中黑名单模式: {item}"))
    return normalized


def ensure_shell_command_allowed(
    command: str,
    *,
    blocked_patterns: list[str] | list[tuple[str, str]] | None = None,
) -> None:
    text = str(command or "").strip()
    if not text:
        raise ValueError("command 必须为非空字符串")
    for pattern, reason in _normalize_blocked_shell_patterns(blocked_patterns):
        if re.search(pattern, text, flags=re.IGNORECASE):
            raise ValueError(f"{reason}: {text}")


def is_blocked_python_import(module_name: str, blocked_imports: list[str] | None = None) -> bool:
    normalized = str(module_name or "").strip()
    if not normalized:
        return False
    for blocked in list(blocked_imports or DEFAULT_BLOCKED_PYTHON_IMPORTS):
        blocked_name = str(blocked or "").strip()
        if not blocked_name:
            continue
        if normalized == blocked_name or normalized.startswith(f"{blocked_name}."):
            return True
    return False


def is_blocked_python_call(
    *,
    module_name: str,
    func_name: str,
    blocked_call_prefixes: list[str] | None = None,
) -> bool:
    qualified = f"{str(module_name or '').strip()}.{str(func_name or '').strip()}".strip(".")
    for prefix in list(blocked_call_prefixes or DEFAULT_BLOCKED_PYTHON_CALL_PREFIXES):
        normalized = str(prefix or "").strip()
        if not normalized:
            continue
        if normalized.endswith("."):
            if qualified.startswith(normalized):
                return True
            continue
        if qualified == normalized or qualified.startswith(f"{normalized}."):
            return True
    return False


def guard_subprocess_call(
    *,
    func_name: str,
    args: list[Any],
    kwargs: dict[str, Any],
    blocked_shell_patterns: list[str] | list[tuple[str, str]] | None = None,
) -> None:
    if func_name not in SUBPROCESS_SHELL_CALLS:
        return
    shell = bool(kwargs.get("shell"))
    command = None
    if args:
        command = args[0]
    elif "args" in kwargs:
        command = kwargs["args"]
    if func_name in {"getoutput", "getstatusoutput"} and args:
        command = args[0]
        shell = True
    if shell:
        if isinstance(command, str):
            ensure_shell_command_allowed(command, blocked_patterns=blocked_shell_patterns)
            return
        if isinstance(command, (list, tuple)):
            ensure_shell_command_allowed(
                " ".join(str(item) for item in command),
                blocked_patterns=blocked_shell_patterns,
            )
            return
    if isinstance(command, str):
        ensure_shell_command_allowed(command, blocked_patterns=blocked_shell_patterns)


__all__ = [
    "DEFAULT_BLOCKED_SHELL_PATTERNS",
    "DEFAULT_BLOCKED_PYTHON_IMPORTS",
    "DEFAULT_BLOCKED_PYTHON_CALL_PREFIXES",
    "ensure_shell_command_allowed",
    "is_blocked_python_import",
    "is_blocked_python_call",
    "guard_subprocess_call",
]
