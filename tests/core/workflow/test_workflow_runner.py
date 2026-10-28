"""Workflow 在真实 Runner 下的端到端路径。

用假 dispatcher 测不出的东西都在这里：临时 AgentConfig 能否通过 ``invoke_agent``、
内部 agent 是否真的不落盘、是否以 accept 运行、非只读 workflow 的启动审批。
模型被替换为按调用顺序返回 ReAct 输出的桩，其余全部是真实组件。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from juice_agents.core.config.runtime_config import update_workspace_config
from juice_agents.core.registry.agents import registry as agent_registry_module
from juice_agents.core.registry.agents.types import AgentConfig
from juice_agents.core.runner import Runner


def _submit(output: Any) -> str:
    payload = json.dumps([{"name": "submit_output", "args": {"output": output}}], ensure_ascii=False)
    return f"<thought>done</thought><actions>{payload}</actions>"


def _call(name: str, args: dict[str, Any]) -> str:
    payload = json.dumps([{"name": name, "args": args}], ensure_ascii=False)
    return f"<thought>act</thought><actions>{payload}</actions>"


class _ScriptedModel:
    """所有 agent 共用；按全局调用顺序吐出预设输出，耗尽即报错。"""

    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)
        self._lock = threading.Lock()

    def generate(self, messages: Any, stop_sequence: Any = None, **_kwargs: Any) -> dict[str, Any]:
        del messages, stop_sequence
        with self._lock:
            if not self.outputs:
                raise AssertionError("model outputs exhausted")
            return {"role": "assistant", "content": self.outputs.pop(0)}


@pytest.fixture
def scripted(monkeypatch: pytest.MonkeyPatch):
    holder: dict[str, _ScriptedModel] = {}

    def install(outputs: list[str]) -> _ScriptedModel:
        holder["model"] = _ScriptedModel(outputs)
        return holder["model"]

    monkeypatch.setattr(agent_registry_module, "resolve_runtime_model", lambda **_kwargs: holder["model"])
    return install


def _write_workflow(tmp_path: Path, name: str, body: str, *, metadata: dict[str, Any]) -> None:
    folder = tmp_path / ".juice" / "workflows"
    folder.mkdir(parents=True, exist_ok=True)
    meta = {"name": name, **metadata}
    (folder / f"{name}.py").write_text(f"WORKFLOW_METADATA = {meta!r}\n\n{body}", encoding="utf-8")


def _runner(tmp_path: Path) -> Runner:
    # 与真实启动一致：Registry 按需解析内置声明，创建 Runner 不复制资源文件。
    runner = Runner.create(runner_config="agent", base_dir=tmp_path)
    runner._root()  # 内部 agent 继承 root 的模型与协议
    return runner


def test_inline_agent_config_runs_through_invoke_agent_without_persisting(tmp_path: Path, scripted) -> None:
    """回归：旧实现把脚本内联的 AgentConfig 交给 invoke_agent 时被按名字拒绝（PermissionError）。"""

    scripted([_submit({"answer": "42"})])
    _write_workflow(
        tmp_path,
        "ask_one",
        "def run(ctx, args):\n"
        "    return ctx.agent(args['q'], instructions='Answer.', tools=[], schema={'type': 'object'})\n",
        metadata={"read_only": True, "tools": []},
    )
    runner = _runner(tmp_path)
    agents_dir = runner.layout.agents_dir
    before = sorted(p.name for p in agents_dir.iterdir() if p.is_dir()) if agents_dir.exists() else []

    called = runner.workflow_manager.call("ask_one", {"q": "life?"}, owner_agent_name="root")

    assert called["result"] == {"answer": "42"}
    # ephemeral 不新增 snapshot / 日志目录；独立用量账本仍需保留请求元数据。
    after = sorted(p.name for p in agents_dir.iterdir() if p.is_dir()) if agents_dir.exists() else []
    assert after == before
    assert [m.agent_name for m in runner.agent_manager.live_agents.values()] == ["root"]


def test_inner_agent_empty_actions_continue_instead_of_leaking_protocol_text(tmp_path: Path, scripted) -> None:
    """回归：researcher 首步输出空 actions 时，旧实现结束本轮并把 ``<actions>[]</actions>``
    当作输出，schema 解析报 "expect=dict, actual=list"。"""

    scripted(["<thought>plan</thought><actions>[]</actions>", _submit({"answer": "42"})])
    _write_workflow(
        tmp_path,
        "ask_one",
        "def run(ctx, args):\n"
        "    return ctx.agent(args['q'], instructions='Answer.', tools=[], schema={'type': 'object'})\n",
        metadata={"read_only": True, "tools": []},
    )
    runner = _runner(tmp_path)

    called = runner.workflow_manager.call("ask_one", {"q": "life?"}, owner_agent_name="root")

    assert called["result"] == {"answer": "42"}


def test_caller_cancel_interrupts_inner_agent_at_step_boundary(tmp_path: Path, scripted) -> None:
    from juice_agents.core.agent import AgentExecutionInterrupted

    cancel = threading.Event()
    calls: list[int] = []

    class _CancellingModel:
        """第一步返回空动作（未提交），并在这一步期间收到调用方取消。"""

        def generate(self, messages: Any, stop_sequence: Any = None, **_kwargs: Any) -> dict[str, Any]:
            del messages, stop_sequence
            calls.append(1)
            cancel.set()
            return {"role": "assistant", "content": "<thought>wait</thought><actions>[]</actions>"}

    scripted([]).generate = _CancellingModel().generate  # type: ignore[method-assign]
    _write_workflow(
        tmp_path,
        "slow",
        "def run(ctx, args):\n    return ctx.agent('go', instructions='Work.', tools=[], max_steps=5)\n",
        metadata={"read_only": True, "tools": []},
    )
    runner = _runner(tmp_path)
    with pytest.raises(AgentExecutionInterrupted):
        runner.workflow_manager.call("slow", {}, owner_agent_name="root", cancel_event=cancel)
    assert len(calls) == 1  # 在第一个 step 边界就停下


def test_inner_agent_runs_writable_tool_in_accept_mode_without_asking(tmp_path: Path, scripted) -> None:
    target = tmp_path / "note.txt"
    scripted([_call("write", {"path": str(target), "content": "hello"}), _submit("written")])
    _write_workflow(
        tmp_path,
        "writer",
        "def run(ctx, args):\n"
        "    return ctx.agent('write it', instructions='Write the file.', tools=['write'])\n",
        metadata={"read_only": False, "tools": ["write"]},
    )
    runner = _runner(tmp_path)
    assert runner.permission_mode == "default"
    asked: list[Any] = []
    runner.set_ask_handler(lambda request: asked.append(request) or {"answer": "deny"})

    called = runner.workflow_manager.call("writer", {}, owner_agent_name="root")

    assert called["result"] == "written"
    assert target.read_text(encoding="utf-8") == "hello"
    assert asked == []


def test_registered_agent_can_be_used_and_unavailable_one_is_rejected(tmp_path: Path, scripted) -> None:
    scripted([_submit("explored")])
    _write_workflow(
        tmp_path,
        "use_explore",
        "def run(ctx, args):\n    return ctx.agent('look', agent=args['agent'])\n",
        metadata={"read_only": True, "tools": ["read", "glob", "grep"]},
    )
    runner = _runner(tmp_path)
    assert runner.workflow_manager.call("use_explore", {"agent": "explore"}, owner_agent_name="root")["result"] == "explored"
    with pytest.raises(PermissionError):
        runner.workflow_manager.call("use_explore", {"agent": "no_such_agent"}, owner_agent_name="root")


def test_invoke_agent_skips_name_availability_for_inline_config(tmp_path: Path, scripted) -> None:
    """临时 AgentConfig 已由调用方组装校验，不按名字查可用性；同名字符串引用仍被拒绝。"""

    scripted([_submit("inline ok")])
    runner = _runner(tmp_path)
    name = "not_registered_anywhere"
    assert not runner.is_agent_available(name)
    config = AgentConfig.from_dict({"name": name, "instructions": "Answer.", "tools": []})

    result = runner.invoke_agent({"agent_ref": config, "task": "t", "execution": "sync", "ephemeral": True})
    assert result["output"] == "inline ok"
    with pytest.raises(PermissionError):
        runner.invoke_agent({"agent_ref": name, "task": "t", "execution": "sync"})


def test_invoke_agent_rejects_persistent_inline_config(tmp_path: Path, scripted) -> None:
    scripted([])
    runner = _runner(tmp_path)
    config = AgentConfig.from_dict({"name": "tmp", "instructions": "x"})
    with pytest.raises(ValueError, match="functional"):
        runner.invoke_agent({"agent_ref": config, "task": "t", "execution": "sync", "lifecycle": "persistent"})


def _run_tool(tmp_path: Path, *, workflows_enabled: bool):
    from juice_agents.core.agent.tools.builtin.workflows.workflows_tools import WorkflowRunTool
    from juice_agents.core.permissions.policy import PermissionEngine

    _write_workflow(tmp_path, "ro", "def run(ctx, args):\n    return 1\n", metadata={"read_only": True, "allowed_modes": ["agent"]})
    _write_workflow(tmp_path, "rw", "def run(ctx, args):\n    return 1\n", metadata={"read_only": False})
    # 自动能力开关在 agent 构造时生效（网关改配置后会重建 Runner），所以先写配置再建 Runner。
    update_workspace_config(tmp_path, lambda current: {**current, "workflows": {"enabled": workflows_enabled}})
    runner = _runner(tmp_path)
    root = runner.agent_manager.get_by_name("root").instance
    tool = WorkflowRunTool()
    tool.bind_owner_agent(root)
    engine = PermissionEngine(root)

    def decide(name: str) -> str:
        args = {"name": name}
        return engine.decide(engine.action(tool, args), tool=tool, args=args)

    return tool, decide


def test_workflow_run_permissions_follow_metadata(tmp_path: Path, scripted) -> None:
    scripted([])
    tool, decide = _run_tool(tmp_path, workflows_enabled=True)
    assert tool.check_permissions({"name": "ro"}, agent_mode="agent").behavior == "allow"
    assert tool.check_permissions({"name": "ro"}, agent_mode="plan").behavior == "deny"  # allowed_modes
    assert tool.check_permissions({"name": "rw"}, agent_mode="plan").behavior == "deny"  # 非只读
    assert decide("ro") == "allow"
    assert decide("rw") == "ask"  # 非只读：启动时审批一次


def test_automatic_workflow_run_is_denied_when_workflows_disabled(tmp_path: Path, scripted) -> None:
    scripted([])
    _tool, decide = _run_tool(tmp_path, workflows_enabled=False)
    assert decide("ro") == "deny"
