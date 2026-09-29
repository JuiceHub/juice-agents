"""Launchers mixin: launch_async_task, local_bash, local_graph."""

from __future__ import annotations

import subprocess
import time
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
        future = self._executor.submit(self._run_async_task, async_task_id, runner, summary_builder)
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
    ) -> dict[str, Any]:
        def _runner(async_task: InternalAsyncTaskState, output_dir: Path) -> dict[str, Any]:
            async_task_id = str(async_task.get("async_task_id") or output_dir.name)
            timeout_at = (
                time.time() + float(timeout_seconds)
                if timeout_seconds is not None and float(timeout_seconds) > 0
                else None
            )
            process = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=cwd or None,
            )
            with self._lock:
                self._processes[async_task_id] = process
            try:
                while process.poll() is None:
                    if self.is_async_task_cancelled(async_task_id):
                        process.terminate()
                        try:
                            process.wait(timeout=1.0)
                        except subprocess.TimeoutExpired:
                            process.kill()
                        break
                    if timeout_at is not None and time.time() >= timeout_at:
                        process.kill()
                        raise subprocess.TimeoutExpired(command, timeout_seconds)
                    time.sleep(0.05)
                stdout, stderr = process.communicate(timeout=1.0)
            finally:
                with self._lock:
                    self._processes.pop(async_task_id, None)
            payload = {
                "stdout": (stdout or "").rstrip("\n"),
                "stderr": (stderr or "").rstrip("\n"),
                "returncode": process.returncode,
            }
            self._write_stream_log(output_dir, "stdout.log", payload["stdout"])
            self._write_stream_log(output_dir, "stderr.log", payload["stderr"])
            self._append_async_task_output(
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
                    "command": command,
                    "cwd": cwd,
                    "timeout_seconds": timeout_seconds,
                    "cancel_requested": False,
                    "max_observation_chars": max_observation_chars,
                }
            },
            runner=_runner,
            summary_builder=lambda _result: "后台 shell 命令已完成",
        )

    def launch_local_graph_async_task(
        self,
        *,
        owner_agent_id: str,
        owner_agent_name: str,
        graph_name: str,
        runner: Callable[[InternalAsyncTaskState, Path], Any],
        max_observation_chars: int | None = None,
    ) -> dict[str, Any]:
        return self.launch_async_task(
            async_task_type="local_graph",
            owner_agent_id=owner_agent_id,
            owner_agent_name=owner_agent_name,
            description=f"graph background run: {graph_name}",
            extra_fields={
                "metadata": {
                    "graph_name": graph_name,
                    "max_observation_chars": max_observation_chars,
                }
            },
            runner=runner,
            summary_builder=lambda _result: f"local_graph 已完成：{graph_name}",
        )
