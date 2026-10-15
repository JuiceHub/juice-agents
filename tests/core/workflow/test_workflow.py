"""Workflow 功能测试：编排原语、声明组装与工具约束、registry 校验、deep_research 编排。

内部 agent 通过假 dispatcher 执行，真实 Runner 路径见 test_workflow_runner.py。
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.managers.workflows import WorkflowRunManager
from juice_agents.core.registry.agents.types import AgentConfig
from juice_agents.core.registry.workflows import WorkflowRegistry, validate_workflow_source
from juice_agents.core.workflow.context import WorkflowContext
from juice_agents.core.workflow.types import (
    WorkflowBuildContext,
    WorkflowCancelledError,
    WorkflowConfig,
    WorkflowTimeoutError,
)

_READ_ONLY_TOOLS = {"read", "glob", "grep", "web_search", "api_web_search", "browser_open_url", "browser_extract"}


class FakeDispatcher:
    """记录每次调用的最终 AgentConfig；输出由 responder(config, task) 决定。"""

    runner_id = "test-runner"

    def __init__(self, responder: Any = None, registered: dict[str, AgentConfig] | None = None) -> None:
        self.responder = responder or (lambda config, task: f"out:{task}")
        self.registered = dict(registered or {})
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def resolve_agent(self, agent_name: str) -> AgentConfig:
        if agent_name not in self.registered:
            raise PermissionError(f"unavailable: {agent_name}")
        return self.registered[agent_name]

    def tool_is_read_only(self, tool_name: str) -> bool:
        return tool_name in _READ_ONLY_TOOLS

    def invoke_agent(self, spec: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.calls.append(spec)
        return {"output": self.responder(spec["agent_ref"], spec["task"])}


def _ctx(
    tmp_path: Path,
    dispatcher: FakeDispatcher,
    *,
    tools: tuple[str, ...] = ("read", "grep", "shell"),
    read_only: bool = False,
    cancel_event: threading.Event | None = None,
    config: WorkflowConfig | None = None,
    events: list[dict[str, Any]] | None = None,
) -> WorkflowContext:
    build = WorkflowBuildContext(
        config_context=ConfigurationContext.from_workspace(tmp_path),
        workspace_dir=tmp_path,
        artifacts_dir=tmp_path / "artifacts",
        agent_dispatcher=dispatcher,
        owner_agent_name="root",
        cancel_event=cancel_event or threading.Event(),
    )
    return WorkflowContext(
        build,
        name="demo",
        declared_tools=tools,
        read_only=read_only,
        config=config,
        lifecycle_callback=None if events is None else events.append,
    )


def _tool_names(config: AgentConfig) -> list[str]:
    return [tool.name for tool in config.tools]


# -- ctx.agent：三种用法 ---------------------------------------------------------------


def test_inline_agent_uses_general_subagent_base_with_overrides(tmp_path: Path) -> None:
    dispatcher = FakeDispatcher()
    ctx = _ctx(tmp_path, dispatcher)
    assert ctx.agent("hi", instructions="ROLE", tools=["read"]) == "out:hi"
    config = dispatcher.calls[0]["agent_ref"]
    assert isinstance(config, AgentConfig)
    assert config.instructions == "ROLE"
    assert _tool_names(config) == ["read"]
    assert config.lifecycle == "functional"
    assert config.enable_workflow_tools is False


def test_inline_agent_requires_instructions(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="instructions"):
        _ctx(tmp_path, FakeDispatcher()).agent("hi")


def test_registered_agent_is_used_as_is_and_can_be_overridden(tmp_path: Path) -> None:
    explore = AgentConfig.from_dict(
        {"name": "explore", "instructions": "EXPLORE", "tools": ["read", "grep"], "max_steps": 12}
    )
    dispatcher = FakeDispatcher(registered={"explore": explore})
    ctx = _ctx(tmp_path, dispatcher)

    ctx.agent("plain", agent="explore")
    ctx.agent("override", agent="explore", instructions="CUSTOM", tools=["read"], max_steps=3)

    plain, override = (call["agent_ref"] for call in dispatcher.calls)
    assert (plain.name, plain.instructions, _tool_names(plain), plain.max_steps) == ("explore", "EXPLORE", ["read", "grep"], 12)
    assert (override.instructions, _tool_names(override), override.max_steps) == ("CUSTOM", ["read"], 3)


def test_unavailable_registered_agent_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(PermissionError, match="unavailable"):
        _ctx(tmp_path, FakeDispatcher()).agent("x", agent="missing")


def test_schema_output_is_parsed_and_submit_instruction_appended(tmp_path: Path) -> None:
    dispatcher = FakeDispatcher(responder=lambda config, task: '{"answer": 42}')
    ctx = _ctx(tmp_path, dispatcher)
    result = ctx.agent({"q": 1}, instructions="ROLE", tools=[], schema={"type": "object"})
    assert result == {"answer": 42}
    sent = dispatcher.calls[0]
    assert "submit_output" in sent["agent_ref"].instructions
    # dict task 以 JSON 文本发给 agent
    assert sent["task"] == '{"q": 1}'


# -- 工具约束 ---------------------------------------------------------------------------


def test_forbidden_tools_are_stripped_from_registered_agent(tmp_path: Path) -> None:
    general = AgentConfig.from_dict(
        {"name": "general", "instructions": "G", "tools": ["read", "agent_tool", "workflow_run", "team_task_create", "skill_manage"]}
    )
    dispatcher = FakeDispatcher(registered={"general": general})
    _ctx(tmp_path, dispatcher).agent("x", agent="general")
    assert _tool_names(dispatcher.calls[0]["agent_ref"]) == ["read"]


def test_undeclared_tool_is_rejected(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, FakeDispatcher(), tools=("read",))
    with pytest.raises(PermissionError, match="未声明"):
        ctx.agent("x", instructions="R", tools=["read", "write"])


def test_read_only_workflow_rejects_writable_tools(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, FakeDispatcher(), tools=("read", "shell"), read_only=True)
    with pytest.raises(PermissionError, match="非只读"):
        ctx.agent("x", instructions="R", tools=["shell"])
    ctx.agent("x", instructions="R", tools=["read"])


def test_inline_default_base_tools_must_be_declared(tmp_path: Path) -> None:
    """不覆盖 tools 时继承 general subagent 的工具集，超出声明必须报错而不是静默放行。"""

    ctx = _ctx(tmp_path, FakeDispatcher(), tools=("read",))
    with pytest.raises(PermissionError, match="未声明"):
        ctx.agent("x", instructions="R")


# -- 并发与控制 -------------------------------------------------------------------------


def test_parallel_preserves_order_and_respects_worker_limit(tmp_path: Path) -> None:
    active = 0
    peak = 0
    lock = threading.Lock()

    def slow(value: int) -> int:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return value * 10

    ctx = _ctx(tmp_path, FakeDispatcher(), config=WorkflowConfig(max_workers=2))
    assert ctx.parallel([lambda v=v: slow(v) for v in range(5)]) == [0, 10, 20, 30, 40]
    assert peak == 2


def test_parallel_propagates_first_failure(tmp_path: Path) -> None:
    def boom() -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        _ctx(tmp_path, FakeDispatcher()).parallel([lambda: 1, boom])


def test_pipeline_runs_each_item_through_all_stages(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, FakeDispatcher())
    assert ctx.pipeline([1, 2, 3], lambda x: x + 1, lambda x: x * 2) == [4, 6, 8]


def test_cancel_stops_at_next_agent_boundary(tmp_path: Path) -> None:
    cancel = threading.Event()
    dispatcher = FakeDispatcher(responder=lambda config, task: cancel.set() or "done")
    ctx = _ctx(tmp_path, dispatcher, cancel_event=cancel)
    ctx.agent("first", instructions="R", tools=[])
    with pytest.raises(WorkflowCancelledError):
        ctx.agent("second", instructions="R", tools=[])
    assert len(dispatcher.calls) == 1
    # 取消信号原样传给内部 agent，供 AgentManager 在 step 边界中断。
    assert dispatcher.calls[0]["cancel_event"] is cancel


def test_cancel_interrupts_parallel_wait(tmp_path: Path) -> None:
    cancel = threading.Event()
    ctx = _ctx(tmp_path, FakeDispatcher(), cancel_event=cancel)
    threading.Timer(0.1, cancel.set).start()
    started = time.monotonic()
    with pytest.raises(WorkflowCancelledError):
        ctx.parallel([lambda: time.sleep(1.0)])
    assert time.monotonic() - started < 0.9


def test_timeout_is_checked_at_boundaries(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, FakeDispatcher(), config=WorkflowConfig(timeout_seconds=0.05))
    time.sleep(0.1)
    with pytest.raises(WorkflowTimeoutError):
        ctx.agent("x", instructions="R", tools=[])


def test_progress_events_carry_phase_and_counts(tmp_path: Path) -> None:
    events: list[dict[str, Any]] = []
    ctx = _ctx(tmp_path, FakeDispatcher(), events=events)
    ctx.parallel([lambda i=i: ctx.agent(str(i), instructions="R", tools=[], phase="Research", label="researcher") for i in range(3)])
    completed = [e for e in events if e["event"] == "node_completed"]
    assert {e["phase"] for e in completed} == {"Research"}
    assert sorted(e["completed"] for e in completed) == [1, 2, 3]
    assert all(e["total"] == 3 for e in completed[-1:])
    assert events[0] == {"event": "phase_started", "phase": "Research", "completed": 0, "total": 0, "detail": "Research"}


# -- artifact --------------------------------------------------------------------------


def test_artifact_writes_text_json_and_rejects_paths(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, FakeDispatcher())
    assert ctx.artifact("a.md", "hello").read_text(encoding="utf-8") == "hello"
    assert '"k": 1' in ctx.artifact("b.json", {"k": 1}).read_text(encoding="utf-8")
    for bad in ("../x.md", "sub/x.md", ""):
        with pytest.raises(ValueError):
            ctx.artifact(bad, "x")


# -- registry --------------------------------------------------------------------------

_SCRIPT = '''
WORKFLOW_METADATA = {"name": "demo", "read_only": True, "tools": ["read"], "allowed_modes": ["agent"], "phases": ["A"]}

def run(ctx, args):
    return {"echo": ctx.agent(args["q"], instructions="R", tools=["read"])}
'''


def test_registry_parses_metadata_and_loads_run(tmp_path: Path) -> None:
    local = tmp_path / ".juice" / "workflows"
    local.mkdir(parents=True)
    (local / "demo.py").write_text(_SCRIPT, encoding="utf-8")
    registry = WorkflowRegistry(config_context=ConfigurationContext.from_workspace(tmp_path))
    meta = registry.get_metadata("demo")
    assert (meta.source, meta.read_only, meta.tools, meta.allowed_modes, meta.phases) == (
        "local", True, ("read",), ("agent",), ("A",)
    )
    assert meta.allows_mode("agent") and not meta.allows_mode("plan")
    assert callable(registry.instantiate("demo"))
    assert "deep_research" in registry.list()


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("WORKFLOW_METADATA = {'name': 'x'}\n", "run(ctx, args)"),
        ("def run(ctx, args):\n    return 1\n", "WORKFLOW_METADATA"),
        ("WORKFLOW_METADATA = {'name': 'x'}\nasync def run(ctx, args):\n    return 1\n", "同步"),
        ("import importlib\nWORKFLOW_METADATA = {'name': 'x'}\ndef run(ctx, args):\n    return 1\n", "importlib"),
        ("WORKFLOW_METADATA = {'name': 'x'}\ndef run(ctx, args):\n    return getattr(ctx, 'a')\n", "getattr"),
        ("WORKFLOW_METADATA = {'name': 'x'}\ndef run(ctx, args):\n    return ctx.__class__\n", "dunder"),
        ("WORKFLOW_METADATA = {'name': 'x', 'tools': 'read'}\ndef run(ctx, args):\n    return 1\n", "tools"),
    ],
)
def test_local_source_lint_rejects_unsupported_scripts(tmp_path: Path, source: str, message: str) -> None:
    with pytest.raises(ValueError, match=re.escape(message)):
        validate_workflow_source(source, path=tmp_path / "x.py", local=True)


def test_instantiate_rejects_source_changed_after_validation(tmp_path: Path) -> None:
    local = tmp_path / ".juice" / "workflows"
    local.mkdir(parents=True)
    path = local / "demo.py"
    path.write_text(_SCRIPT, encoding="utf-8")
    registry = WorkflowRegistry(config_context=ConfigurationContext.from_workspace(tmp_path))
    approved = registry.validate("demo")
    path.write_text(_SCRIPT.replace('"R"', '"CHANGED"'), encoding="utf-8")
    with pytest.raises(ValueError, match="被修改"):
        registry.instantiate(approved)


def test_manage_writes_only_valid_named_scripts(tmp_path: Path) -> None:
    registry = WorkflowRegistry(config_context=ConfigurationContext.from_workspace(tmp_path))
    assert registry.manage("create", name="demo", content=_SCRIPT)["success"]
    assert not registry.manage("create", name="demo", content=_SCRIPT)["success"]  # 已存在
    assert not registry.manage("create", name="other", content=_SCRIPT)["success"]  # 名称不一致
    assert not registry.manage("delete", name="demo")["success"]


# -- manager.call ----------------------------------------------------------------------


def test_manager_call_returns_result_and_writes_only_artifacts(tmp_path: Path) -> None:
    local = tmp_path / ".juice" / "workflows"
    local.mkdir(parents=True)
    (local / "demo.py").write_text(
        _SCRIPT.replace('return {"echo"', 'ctx.artifact("r.md", "x")\n    return {"echo"'), encoding="utf-8"
    )
    context = ConfigurationContext.from_workspace(tmp_path)
    events: list[dict[str, Any]] = []
    manager = WorkflowRunManager(
        runner_root=tmp_path / "runner",
        registry=WorkflowRegistry(config_context=context),
        config_context=context,
        agent_dispatcher=FakeDispatcher(),
        owner_agent_name="root",
        lifecycle_callback=events.append,
    )
    called = manager.call("demo", {"q": "hi"})
    assert called["result"] == {"echo": "out:hi"}
    assert called["stats"] == {"agent_calls": 1}
    artifacts = Path(called["artifacts_dir"])
    assert [p.name for p in artifacts.iterdir()] == ["r.md"]
    # 除产物外不落盘任何运行态。
    assert sorted(p.name for p in (tmp_path / "runner").rglob("*") if p.is_file()) == ["r.md"]
    assert [e["event"] for e in events if e["event"].startswith("workflow_")] == ["workflow_started", "workflow_completed"]
    assert all(e["scope"] == "workflow" for e in events)


def test_manager_call_propagates_failure(tmp_path: Path) -> None:
    local = tmp_path / ".juice" / "workflows"
    local.mkdir(parents=True)
    (local / "demo.py").write_text(
        "WORKFLOW_METADATA = {'name': 'demo'}\ndef run(ctx, args):\n    raise RuntimeError('bad input')\n",
        encoding="utf-8",
    )
    context = ConfigurationContext.from_workspace(tmp_path)
    manager = WorkflowRunManager(
        runner_root=tmp_path / "runner", registry=WorkflowRegistry(config_context=context), config_context=context
    )
    with pytest.raises(RuntimeError, match="bad input"):
        manager.call("demo", {})


# -- deep_research ---------------------------------------------------------------------


def _research_responder(rounds_with_gaps: int):
    """按 instructions 识别角色并返回结构化输出。

    每个 researcher 都支持同一个 claim；前 N 轮的来源共用一个 independence_key
    （算作一个来源 → unverified → gap），之后的来源各自独立，claim 得以 verified。
    """

    state = {"research_round": 0}
    lock = threading.Lock()

    def respond(config: AgentConfig, task: str) -> Any:
        role = str(config.instructions)
        if role.startswith("You are a research supervisor. Convert"):
            return {"objective": "o", "complexity": "multi_part", "assumptions": ["a1"]}
        if role.startswith("You are a research supervisor. Plan"):
            return {"topics": [{"id": "t1", "question": "q1"}, {"id": "t2", "question": "q2"}]}
        if role.startswith("You are a focused researcher"):
            with lock:
                state["research_round"] += 1
                index = state["research_round"]
            key = "same" if index <= rounds_with_gaps * 2 else f"k{index}"
            return {
                "sources": [
                    {"id": f"s{index}", "location": f"https://ex/{index}", "excerpt": f"e{index}", "independence_key": key},
                    {"id": f"d{index}", "location": "https://ex/1", "excerpt": "dup"},
                ],
                "claims": [{"text": "Fact A", "source_ids": [f"s{index}"]}],
            }
        if role.startswith("You are a report writer"):
            return {"report_markdown": "Report [s3] [bogus]"}
        raise AssertionError(role)

    return respond


def test_deep_research_runs_rounds_until_gaps_close(tmp_path: Path) -> None:
    from juice_agents.core.workflow.builtins import deep_research

    dispatcher = FakeDispatcher(responder=_research_responder(rounds_with_gaps=1))
    ctx = _ctx(tmp_path, dispatcher, tools=deep_research.WORKFLOW_METADATA["tools"], read_only=True)
    result = deep_research.run(ctx, {"question": "compare A and B", "source_mode": "web"})

    stats = result["stats"]
    # 第 1 轮全是单来源 claim → gaps → 第 2 轮两个独立来源支持 Fact A → 收敛。
    assert stats["rounds"] == 2 and stats["research_units"] == 4
    assert [c["text"] for c in result["verified_claims"]] == ["Fact A"]
    # 重复 location 的来源被合并。
    assert len({s["location"] for s in result["sources"]}) == len(result["sources"])
    # 不被 verified claim 支持的引用被删除并记入 limitations。
    assert "[bogus]" not in result["report_markdown"]
    assert any("bogus" in item for item in result["limitations"])
    assert result["assumptions"] == ["a1"]
    for key in ("report", "sources", "claims", "debug"):
        assert Path(result["artifacts"][key]).exists()
    # 研究员只拿到 web 工具；brief/plan/writer 没有工具。
    researcher_tools = {
        tuple(_tool_names(call["agent_ref"])) for call in dispatcher.calls if "researcher" in call["agent_ref"].instructions
    }
    assert researcher_tools == {("api_web_search", "web_search", "browser_open_url", "browser_extract")}
    assert all(
        _tool_names(call["agent_ref"]) == []
        for call in dispatcher.calls
        if "researcher" not in call["agent_ref"].instructions
    )


def test_deep_research_stops_at_round_limit(tmp_path: Path) -> None:
    from juice_agents.core.workflow.builtins import deep_research

    dispatcher = FakeDispatcher(responder=_research_responder(rounds_with_gaps=99))
    ctx = _ctx(tmp_path, dispatcher, tools=deep_research.WORKFLOW_METADATA["tools"], read_only=True)
    result = deep_research.run(
        ctx, {"question": "compare A and B", "source_mode": "web", "limits": {"max_research_rounds": 2}}
    )
    assert result["stats"]["rounds"] == 2
    assert result["verified_claims"] == []


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"question": ""}, "question"),
        ({"question": "q", "source_mode": "bad"}, "source_mode"),
        ({"question": "q", "source": "web"}, "source_mode"),
    ],
)
def test_deep_research_rejects_invalid_args(tmp_path: Path, args: dict[str, Any], message: str) -> None:
    from juice_agents.core.workflow.builtins import deep_research

    with pytest.raises(ValueError, match=message):
        deep_research.run(_ctx(tmp_path, FakeDispatcher()), args)
