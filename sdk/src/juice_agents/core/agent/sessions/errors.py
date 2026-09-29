"""会话层可复用的协议/运行错误类型。"""


class AgentError(Exception):
    """智能体运行过程中可回传给后续轮次的基类异常。"""


class ModelOutputProtocolError(AgentError):
    """模型输出不符合约定协议时抛出的异常。"""


class ActionValidationError(AgentError):
    """ReAct actions 块解析或结构校验失败时抛出的异常。"""


class ToolResolutionError(AgentError):
    """模型引用了未注册工具或托管智能体时抛出的异常。"""


class SubmitOutputValidationError(AgentError):
    """submit_output 结构、解析或 schema 校验失败时抛出的异常。"""


class ContextWindowExceededError(AgentError):
    """remain 投影后请求仍超过 Agent context window 时抛出的本地错误。"""


def is_recoverable_agent_error(error: Exception) -> bool:
    """判断一个 step 错误能否通过下一次模型调用自行修正。

    协议、action、工具解析和最终输出校验错误都属于模型反馈：它们会写入
    ``ActionStep.error``，并在下一 step 作为 ``<error>`` 回灌给模型。
    上下文已经超过硬上限时，下一次模型调用本身无法发出，因此必须结束
    当前 round。未知异常也不在这里被宽松认定为可恢复，避免 Provider、
    持久化或框架内部故障触发无意义的重复调用。
    """

    return isinstance(
        error,
        (
            ModelOutputProtocolError,
            ActionValidationError,
            ToolResolutionError,
            SubmitOutputValidationError,
        ),
    )


__all__ = [
    "AgentError",
    "ModelOutputProtocolError",
    "ActionValidationError",
    "ToolResolutionError",
    "SubmitOutputValidationError",
    "ContextWindowExceededError",
    "is_recoverable_agent_error",
]
