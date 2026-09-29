"""当前 managed agent 绑定到工具层的运行时上下文。"""

from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import TYPE_CHECKING, Any

from ..types.ask import AskRequest, AskResponse
from .cancellation import raise_if_cancelled

if TYPE_CHECKING:  # pragma: no cover
    from ..runner import Runner


@dataclass(slots=True)
class RunnerContext:
    """Tools access manager capabilities through this narrow request context."""

    runner: "Runner"
    agent_name: str
    agent_id: str
    agent_role: str
    agent: Any

    @property
    def runner_id(self) -> str:
        return self.runner.runner_id

    @property
    def layout(self):
        return self.runner.layout

    def execute_tools(self, actions: Any, *, context: Any) -> list[Any]:
        """Ask the Runner-owned ToolManager to execute resolved actions.

        Agents only express actions.  Tool binding, permission context,
        concurrency, audit and cancellation remain manager-owned.
        """

        return self.runner.tool_manager.execute_for_agent(
            agent_id=self.agent_id,
            actions=actions,
            context=context,
        )

    def execute_tool_action(self, action: Any, *, order: int, context: Any) -> Any:
        """Single-action companion used by CodeAct's callable bridge."""

        return self.runner.tool_manager.execute_action_for_agent(
            agent_id=self.agent_id,
            action=action,
            order=order,
            context=context,
        )

    def launch_local_agent(
        self,
        *,
        target_name: str,
        task: str,
        task_images: list[Any] | None = None,
        inherit_session: bool | None = None,
        max_steps: int | None = None,
        agent_config_dir: str | None = None,
        tool_config_dir: str | None = None,
        model: Any | None = None,
        isolation: str | None = None,
        max_observation_chars: int | None = None,
    ) -> dict[str, Any]:
        result = self.runner.invoke_agent(
            {
                "agent_ref": target_name,
                "task": task,
                "owner_agent_name": self.agent_name,
                "execution": "background",
                "task_images": list(task_images or []),
                "inherit_session": inherit_session,
                "max_steps": max_steps,
                "agent_config_dir": agent_config_dir,
                "tool_config_dir": tool_config_dir,
                "model": model,
                "isolation": isolation,
                "max_observation_chars": max_observation_chars,
            }
        )
        return {
            **dict(result),
            "kind": "local_agent",
            "summary": f"local_agent 已启动：{target_name}",
        }

    def launch_local_bash(
        self,
        *,
        command: str,
        cwd: str,
        timeout_seconds: float | None,
        max_observation_chars: int | None = None,
    ) -> dict[str, Any]:
        return self.runner.launch_local_bash_async_task(
            owner_agent_name=self.agent_name,
            command=command,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            max_observation_chars=max_observation_chars,
        )

    def launch_local_graph(
        self,
        *,
        graph_name: str,
        payload: dict[str, Any],
        config: dict[str, Any] | None = None,
        max_observation_chars: int | None = None,
    ) -> dict[str, Any]:
        return self.runner.launch_local_graph_async_task(
            owner_agent_name=self.agent_name,
            graph_name=graph_name,
            payload=dict(payload),
            config=None if config is None else dict(config),
            max_observation_chars=max_observation_chars,
        )

    def list_active_async_tasks(self) -> list[dict[str, Any]]:
        return [
            dict(item)
            for item in self.runner.async_task_manager.list_tasks(
                owner_agent_id=self.agent_id,
                statuses={"pending", "running"},
            )
        ]

    def cancel_async_task(self, async_task_id: str, *, reason: str = "agent_requested") -> dict[str, Any] | None:
        """Cancel a task through the Runner-owned ``AsyncTaskManager``."""

        return self.runner.async_task_manager.cancel(async_task_id, reason=reason)

    def ask_user(self, request: AskRequest) -> AskResponse:
        """把工具层用户提问交给 Runner 当前绑定的交互 provider。"""

        return self.runner.ask_user(request)

    def get_plan_file_path(self) -> str:
        """Return the one Runner-owned formal plan path."""

        return str(self.runner.plan_file_path())

    def record_approved_plan_exit(self, *, agent_name: str, plan: str, plan_file: str) -> None:
        """记录已批准计划，供 adapter 在当前流结束后退出 plan mode。"""

        self.runner.record_approved_plan_exit(
            agent_name=agent_name or self.agent_name,
            plan=plan,
            plan_file=plan_file,
        )

    def config_dirs(self) -> tuple[str, str]:
        return str(self.runner.config_context.agents_dir), str(self.runner.config_context.tools_dir)

    def observation_images_dir(self) -> str:
        """返回当前 runner 级图片观测目录，供 owner-aware 工具写入运行产物。"""

        return str(self.layout.observation_images_dir)

    def browser_screenshots_dir(self) -> str:
        """返回当前 runner 级浏览器截图目录，供 owner-aware 工具写入运行产物。"""

        return str(self.layout.browser_screenshots_dir)

    @property
    def cancel_event(self) -> threading.Event:
        """Return the current stream cancellation token."""

        return self.runner.cancel_event

    def check_cancelled(self) -> None:
        """Raise when the current stream has been interrupted by the user."""

        raise_if_cancelled(self.cancel_event, self.runner.stop_reason)

__all__ = ["RunnerContext"]
