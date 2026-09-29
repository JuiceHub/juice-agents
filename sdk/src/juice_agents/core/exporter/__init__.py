"""运行轨迹导出层。

把持久化的 runner 会话导出为可直接用于 post-training 的数据集（当前支持 OpenAI
Chat Completions JSONL 格式）。核心入口是 ``TrajectoryExporter``，配置通过
``ExportConfig`` 控制「保留哪些会话」与「保留哪些内容」。
"""

from .exporters import ExportResult, TrajectoryExporter
from .filters import ExportConfig, should_export_session, should_keep_step
from .formats import session_to_messages, session_to_sample

__all__ = [
    "TrajectoryExporter",
    "ExportResult",
    "ExportConfig",
    "session_to_messages",
    "session_to_sample",
    "should_export_session",
    "should_keep_step",
]
