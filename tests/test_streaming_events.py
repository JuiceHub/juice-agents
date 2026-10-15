"""测试流式事件功能"""

import pytest
from sdk.src.juice_agents.core.agent.context.steps import ActionEvent, ActionStep


def test_action_event_creation():
    """测试 ActionEvent 的创建"""
    # 测试 thinking 事件
    thinking_event = ActionEvent(
        step_num=1,
        event_type="thinking",
        data={
            "thought": "我需要读取文件",
            "model_output": "<thought>我需要读取文件</thought>",
            "reasoning_content": "",
        },
    )
    assert thinking_event.step_num == 1
    assert thinking_event.event_type == "thinking"
    assert thinking_event.data["thought"] == "我需要读取文件"

    # 测试 tool_start 事件
    tool_start_event = ActionEvent(
        step_num=1,
        event_type="tool_start",
        data={
            "tool_name": "Shell",
            "tool_args": {"command": "ls -la"},
            "tool_index": 0,
        },
    )
    assert tool_start_event.event_type == "tool_start"
    assert tool_start_event.data["tool_name"] == "Shell"

    # 测试 tool_end 事件
    tool_end_event = ActionEvent(
        step_num=1,
        event_type="tool_end",
        data={
            "tool_name": "Shell",
            "tool_index": 0,
            "observation": "file1.txt\nfile2.txt",
            "error": None,
            "execution_time": 0.5,
        },
    )
    assert tool_end_event.event_type == "tool_end"
    assert tool_end_event.data["observation"] == "file1.txt\nfile2.txt"

    # 测试 step_complete 事件
    step = ActionStep(step_num=1, model_output="test")
    step_complete_event = ActionEvent(
        step_num=1,
        event_type="step_complete",
        data={"step": step},
    )
    assert step_complete_event.event_type == "step_complete"
    assert step_complete_event.data["step"] == step


def test_action_event_data_structure():
    """测试 ActionEvent 的数据结构"""
    event = ActionEvent(
        step_num=2,
        event_type="tool_start",
        data={"tool_name": "Read", "tool_args": {"path": "/tmp/test.txt"}},
    )

    # 验证可以访问 data 字段
    assert "tool_name" in event.data
    assert event.data["tool_name"] == "Read"
    assert isinstance(event.data["tool_args"], dict)


if __name__ == "__main__":
    test_action_event_creation()
    test_action_event_data_structure()
    print("✓ 所有流式事件测试通过")
