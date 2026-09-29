"""用户主动交互工具。"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from juice_agents.core.runner.types.ask import (
    AskRequest,
    normalize_ask_options,
    normalize_ask_response,
)

from ...runtime.base_tools import ASK_OBSERVATION_CHARS, Tool

logger = logging.getLogger(__name__)


class AskTool(Tool):
    """向用户提问并等待回答。

    该工具只负责构造请求和回填结构化 observation；实际 UI、SDK 或测试中的
    回答通道由 Runner 的 ask handler 提供。
    """

    name = "ask"
    max_observation_chars = ASK_OBSERVATION_CHARS
    is_read_only = True
    description = "向用户提问以确认任务细节，支持单选、多选和选项外自由回复"
    inputs = {
        "question": {"type": "string", "description": "要向用户确认的问题"},
        "options": {
            "type": "list",
            "description": "可选项列表；元素可以是字符串或 {label,value,description}",
            "required": False,
        },
        "multiple": {"type": "boolean", "description": "是否允许多选，默认 false", "required": False},
        "allow_custom": {
            "type": "boolean",
            "description": "是否允许用户在选项外自由回复，默认 true",
            "required": False,
        },
        "timeout_seconds": {
            "type": "number",
            "description": "可选等待超时；不填由交互通道决定",
            "required": False,
        },
    }
    outputs = {
        "status": {"type": "string", "description": "answered、cancelled 或 error"},
        "request_id": {"type": "string", "description": "本次提问 ID"},
        "question": {"type": "string", "description": "原始问题"},
        "selected": {"type": "list", "description": "用户选择的规范化选项"},
        "custom_response": {"type": "string", "description": "用户输入的选项外回复"},
        "error": {"type": "string", "description": "错误信息，成功时为空"},
    }

    def __init__(self) -> None:
        super().__init__()
        self.owner_agent: Any | None = None

    def bind_owner_agent(self, agent: Any) -> None:
        self.owner_agent = agent

    def forward(
        self,
        question: str,
        options: list[Any] | None = None,
        multiple: bool = False,
        allow_custom: bool = True,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        normalized_question = str(question or "").strip()
        if not normalized_question:
            raise ValueError("question 必须为非空字符串")

        request: AskRequest = {
            "request_id": f"ask-{uuid4().hex[:12]}",
            "question": normalized_question,
            "options": normalize_ask_options(options),
            "multiple": bool(multiple),
            "allow_custom": bool(allow_custom),
            "timeout_seconds": timeout_seconds,
        }
        runner_context = getattr(self.owner_agent, "runner_context", None)
        ask_user = getattr(runner_context, "ask_user", None)
        if not callable(ask_user):
            logger.warning("AskTool 没有可用用户交互通道: question=%s", normalized_question)
            return {
                "status": "error",
                "request_id": request["request_id"],
                "question": normalized_question,
                "selected": [],
                "custom_response": "",
                "error": "没有可用的用户交互通道",
            }

        logger.info("AskTool 发起用户提问: request_id=%s question=%s", request["request_id"], normalized_question)
        response = ask_user(request)
        return dict(normalize_ask_response(response, request=request))


__all__ = ["AskTool"]
