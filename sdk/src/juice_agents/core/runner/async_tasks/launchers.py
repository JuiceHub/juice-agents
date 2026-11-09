"""Launchers mixin: launch_async_task, local_bash."""

from __future__ import annotations

import os
import asyncio

from ..execution.async_io import blocking
import signal
import subprocess
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING

from ..types.definitions import AsyncTaskType
from .kinds import PUBLIC_TO_ASYNC_TASK_KIND

if TYPE_CHECKING:
    from .internal_types import AsyncTaskState as InternalAsyncTaskState


class _AsyncTaskLaunchersMixin:
    """Mixin providing async task launch entry points."""

    def launch_async_task(
        self,
        *,
        async_task_type: AsyncTaskType,
        owner_agent_id: str,
        owner_agent_name: str,
        description: str,
        extra_fields: dict[str, Any] | None,
        runner: Callable[[InternalAsyncTaskState, Path], Any],
        summary_builder: Callable[[Any], str],
    ) -> dict[str, Any]:
        # 直接索引而非用 to_internal_kind()：这里的 async_task_type 由框架内部传入，
        # 未知值属于编程错误，应当 KeyError 立刻暴露，不能静默退化成 agent_dispatch。
        kind = PUBLIC_TO_ASYNC_TASK_KIND[async_task_type]
        async_task = self._build_async_task(
            kind=kind,
            owner_agent_id=owner_agent_id,
            owner_agent_name=owner_agent_name,
            description=description,
            extra_fields=extra_fields,
            status="pending",
        )
        async_task_id = str(async_task.get("async_task_id") or "")
        output_dir = Path(str(async_task.get("output_dir") or ""))
        with self._lock:
            self._store_async_task_locked(async_task)
            self._persist_locked()
        future = self._submit_async_task(async_task_id, runner, summary_builder)
        with self._lock:
            self._futures[async_task_id] = future
        return {
            "status": "launched",
            "async_task_id": async_task_id,
            "kind": async_task_type,
            "summary": f"{async_task_type} 已启动，结果将稍后通过 attachments 返回。",
            "output_dir": str(output_dir),
        }

    def launch_local_bash_async_task(
        self,
        *,
        owner_agent_id: str,
        owner_agent_name: str,
        command: str,
        cwd: str,
        timeout_seconds: float | None,
        max_observation_chars: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async def _runner(async_task: InternalAsyncTaskState, output_dir: Path) -> dict[str, Any]:
            async_task_id = str(async_task.get("async_task_id") or output_dir.name)
            creation = asyncio.create_task(asyncio.create_subprocess_shell(
                command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                cwd=cwd or None, start_new_session=os.name == "posix",
            ))
            cancelled = False
            # 创建进程的 await 也可能被取消；先拿到句柄，finally 才有能力回收它。
            while True:
                try:
                    process = await asyncio.shield(creation)
                    break
                except asyncio.CancelledError:
                    cancelled = True
            communication = asyncio.create_task(process.communicate())

            def signal_group(force: bool) -> None:
                try:
                    if os.name == "posix":
                        os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
                    elif process.returncode is None:
                        process.kill() if force else process.terminate()
                except ProcessLookupError:
                    pass

            try:
                if cancelled:
                    raise asyncio.CancelledError
                try:
                    stdout, stderr = await asyncio.wait_for(asyncio.shield(communication), timeout_seconds)
                except TimeoutError as exc:
                    raise subprocess.TimeoutExpired(command, timeout_seconds) from exc
            finally:
                if not communication.done():
                    signal_group(False)
                    try:
                        await asyncio.wait_for(asyncio.shield(communication), 1.0)
                    except TimeoutError:
                        signal_group(True)
                        await communication
                    finally:
                        # shell 可能先退出；仍需回收忽略 TERM 的后代。
                        signal_group(True)
            stdout, stderr = stdout.decode(errors="replace"), stderr.decode(errors="replace")
            payload = {
                "stdout": (stdout or "").rstrip("\n"),
                "stderr": (stderr or "").rstrip("\n"),
                "returncode": process.returncode,
            }
            await blocking(self._write_stream_log, output_dir, "stdout.log", payload["stdout"])
            await blocking(self._write_stream_log, output_dir, "stderr.log", payload["stderr"])
            await blocking(self._append_async_task_output,
                output_dir,
                {
                    "async_task_id": async_task_id,
                    "event": "shell_result",
                    "payload": payload,
                },
            )
            return payload

        return self.launch_async_task(
            async_task_type="local_bash",
            owner_agent_id=owner_agent_id,
            owner_agent_name=owner_agent_name,
            description=f"shell background command: {command}",
            extra_fields={
                "metadata": {
                    **dict(metadata or {}),
                    "command": command,
                    "cwd": cwd,
                    "timeout_seconds": timeout_seconds,
                    "cancel_requested": False,
                    "max_observation_chars": max_observation_chars or dict(metadata or {}).get("max_observation_chars"),
                }
            },
            runner=_runner,
            summary_builder=lambda _result: "后台 shell 命令已完成",
        )
