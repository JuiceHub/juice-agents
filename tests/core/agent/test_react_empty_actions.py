"""ReAct 空 actions 语义：不执行动作并进入下一步，只有 submit_output 结束本轮。

模型替换为按调用顺序返回 ReAct 输出的桩，Runner / AgentManager / ReActAgent 均为真实组件。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Iterator

import pytest

from juice_agents.core.prebuilt.factory import seed_prebuilt_declarations
from juice_agents.core.registry.agents import registry as agent_registry_module
from juice_agents.core.runner import Runner

EMPTY = "<thought>wait</thought><actions>[]</actions>"


def _submit(output: Any) -> str:
    payload = json.dumps([{"name": "submit_output", "args": {"output": output}}], ensure_ascii=False)
    return f"<thought>done</thought><actions>{payload}</actions>"


class _ScriptedModel:
    """按调用顺序吐出预设输出，耗尽即报错；calls 记录真实调用次数。"""

    def __init__(self) -> None:
        self.outputs: list[str] = []
        self.calls = 0
        self._lock = threading.Lock()

    def generate(self, messages: Any, stop_sequence: Any = None, **_kwargs: Any) -> dict[str, Any]:
        del messages, stop_sequence
        with self._lock:
            if not self.outputs:
                raise AssertionError("model outputs exhausted")
            self.calls += 1
            return {"role": "assistant", "content": self.outputs.pop(0)}


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Runner, _ScriptedModel]]:
    model = _ScriptedModel()
    monkeypatch.setattr(agent_registry_module, "resolve_runtime_model", lambda **_kwargs: model)
    seed_prebuilt_declarations(base_dir=tmp_path, runner_config="agent")
    runner = Runner.create(runner_config="agent", base_dir=tmp_path)
    try:
        yield runner, model
    finally:
        runner.close()


def _round(runner: Runner, message: str) -> tuple[list[Any], dict[str, Any]]:
    """跑完一轮，返回 (action steps, round_end 事件)。"""

    events = list(runner.stream(message))
    steps = [event["action_step"] for event in events if event["kind"] == "action_step"]
    end = next(event for event in events if event["kind"] == "round_end")
    return steps, end


def test_empty_actions_continue_to_next_step_until_submit(harness) -> None:
    runner, model = harness
    model.outputs[:] = [EMPTY, _submit("done")]

    steps, end = _round(runner, "hi")

    assert [step.round_outcome for step in steps] == ["continue", "submitted"]
    assert end["outcome"] == "submitted"
    assert end["output"] == "done"
    # 空步与普通步一致：带回一条 observation，下一次请求才不会以 assistant 消息结尾（prefill）。
    assert steps[0].observations == ["本步未执行任何动作。"]
    assert steps[0].error is None


def test_consecutive_empty_actions_keep_continuing(harness) -> None:
    """连续空步不做特殊处理，和普通步一样继续，直到 submit_output。"""

    runner, model = harness
    model.outputs[:] = [EMPTY, EMPTY, _submit("done")]

    steps, end = _round(runner, "hi")

    assert [step.round_outcome for step in steps] == ["continue", "continue", "submitted"]
    assert end["outcome"] == "submitted"
    assert model.calls == 3
