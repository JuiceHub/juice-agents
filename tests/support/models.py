"""测试共享的模型桩。

这里只收敛在多个测试文件中**逐字重复**的实现，避免同一个桩散落十几份。
带有文件特定行为的桩（记录 messages、注入竞态、自定义 usage 等）仍留在各自
测试文件里，不强行抽象成带开关的通用类。
"""

from __future__ import annotations

from typing import Any


class StaticModel:
    """固定返回同一段内容的最小模型桩。

    用于只需要"能构造出 agent"的场景，不参与多轮行为断言。
    """

    def __init__(self, content: str = "ok") -> None:
        self.content = content

    def generate(self, messages, stop_sequence=None) -> dict[str, Any]:
        del messages, stop_sequence
        return {"role": "assistant", "content": self.content}


class SequenceModel:
    """按调用顺序依次返回预设输出。

    输出耗尽后直接抛 ``AssertionError``：测试期望的模型调用次数是断言的一部分，
    多余的调用应当立即暴露，而不是静默返回空内容让失败点漂移到别处。
    ``calls`` 记录已发生的调用次数。
    """

    def __init__(self, outputs) -> None:
        self.outputs = list(outputs)
        self.calls = 0

    def generate(self, messages, stop_sequence=None) -> dict[str, Any]:
        del messages, stop_sequence
        self.calls += 1
        if not self.outputs:
            raise AssertionError("model outputs exhausted")
        return {"role": "assistant", "content": self.outputs.pop(0)}


class KwargsModel:
    """记录构造参数的模型桩，用于断言 registry 透传了哪些模型参数。"""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def generate(self, messages, stop_sequence=None) -> dict[str, Any]:
        del messages, stop_sequence
        return {"role": "assistant", "content": "ok"}


__all__ = ["KwargsModel", "SequenceModel", "StaticModel"]
