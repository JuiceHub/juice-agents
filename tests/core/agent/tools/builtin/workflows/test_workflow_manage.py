"""workflow_manage 工具层契约：只写不删、写前校验、create 默认不覆盖。

registry 级别的 lint 细节由 tests/core/workflow/test_workflow.py 覆盖；这里只验证
工具入口的动作分派、返回结构，以及「校验失败时磁盘不被改动」这一写入保证。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from juice_agents.core.agent.tools.builtin.evolution.workflow_manage import WorkflowManageTool
from juice_agents.core.config.context import ConfigurationContext
from juice_agents.core.registry.workflows import WorkflowRegistry

# 最小合法 workflow：字面量 WORKFLOW_METADATA + 同步 run(ctx, args)。
SOURCE = '''
WORKFLOW_METADATA = {"name": "counter", "description": "counter", "read_only": True, "phases": ["Count"]}

def run(ctx, args):
    return {"value": args.get("value", 0) + 1}
'''


def _tool(workspace: Path) -> WorkflowManageTool:
    """构造 workspace-scoped 工具，避免落盘到仓库根。"""

    registry = WorkflowRegistry(config_context=ConfigurationContext.from_workspace(workspace))
    return WorkflowManageTool(workflow_registry=registry)


def _path(workspace: Path, name: str = "counter") -> Path:
    return workspace / ".juice" / "workflows" / f"{name}.py"


# -- create / edit ---------------------------------------------------------------------


def test_create_then_edit_writes_local_workflow(tmp_path: Path) -> None:
    tool = _tool(tmp_path)

    created = tool.forward("create", "counter", content=SOURCE)
    assert created["success"], created
    assert created["action"] == "create"
    assert Path(created["path"]) == _path(tmp_path)
    # 返回的 metadata 来自写入前的 AST 解析，而不是 import 后的模块。
    assert created["metadata"]["name"] == "counter"
    assert created["metadata"]["read_only"] is True
    assert created["metadata"]["phases"] == ["Count"]

    edited = tool.forward("edit", "counter", content=SOURCE.replace("+ 1", "+ 2"))
    assert edited["success"], edited
    assert edited["action"] == "edit"
    assert "+ 2" in _path(tmp_path).read_text(encoding="utf-8")


def test_action_is_case_and_whitespace_insensitive(tmp_path: Path) -> None:
    result = _tool(tmp_path).forward("  CREATE ", "counter", content=SOURCE)
    assert result["success"], result
    assert result["action"] == "create"


def test_edit_requires_existing_workflow(tmp_path: Path) -> None:
    result = _tool(tmp_path).forward("edit", "counter", content=SOURCE)
    assert result == {"success": False, "action": "edit", "error": result["error"]}
    assert "不存在" in result["error"]
    assert not _path(tmp_path).exists()


@pytest.mark.parametrize("action", ["delete", "pause", "resume", "restart", "stop", "run"])
def test_only_create_and_edit_are_manage_actions(tmp_path: Path, action: str) -> None:
    """物理删除与运行控制都不属于 manage；文件必须原样保留。"""

    tool = _tool(tmp_path)
    assert tool.forward("create", "counter", content=SOURCE)["success"]

    result = tool.forward(action, "counter")
    assert not result["success"]
    assert "create/edit" in result["error"]
    assert _path(tmp_path).read_text(encoding="utf-8") == SOURCE


# -- overwrite -------------------------------------------------------------------------


def test_create_refuses_existing_without_overwrite(tmp_path: Path) -> None:
    tool = _tool(tmp_path)
    assert tool.forward("create", "counter", content=SOURCE)["success"]

    changed = SOURCE.replace("+ 1", "+ 2")
    refused = tool.forward("create", "counter", content=changed)
    assert not refused["success"]
    assert "已存在" in refused["error"]
    assert _path(tmp_path).read_text(encoding="utf-8") == SOURCE

    replaced = tool.forward("create", "counter", content=changed, overwrite=True)
    assert replaced["success"], replaced
    assert _path(tmp_path).read_text(encoding="utf-8") == changed


# -- 写前校验：metadata 结构 + AST lint ------------------------------------------------

_RUN = "\ndef run(ctx, args):\n    return {}\n"
_META = 'WORKFLOW_METADATA = {"name": "counter"}\n'


@pytest.mark.parametrize(
    ("content", "message"),
    [
        # metadata 结构
        (_RUN, "缺少 WORKFLOW_METADATA"),
        (_META, "缺少 run(ctx, args)"),
        ("WORKFLOW_METADATA = dict(name='counter')\n" + _RUN, "必须是字面量 object"),
        ("WORKFLOW_METADATA = ['counter']\n" + _RUN, "必须是 object"),
        ('WORKFLOW_METADATA = {"name": "counter", "tools": "read"}\n' + _RUN, "WORKFLOW_METADATA.tools"),
        ('WORKFLOW_METADATA = {"name": "other"}\n' + _RUN, "必须与文件名一致"),
        (_META + "\nasync def run(ctx, args):\n    return {}\n", "必须是同步函数"),
        ("def run(ctx, args:\n", "语法错误"),
        # AST lint：副作用必须经 ctx.agent / ctx.artifact
        ("import os\n" + _META + _RUN, "禁止直接导入副作用模块"),
        ("from subprocess import run as sh\n" + _META + _RUN, "禁止直接导入副作用模块"),
        (_META + "\ndef run(ctx, args):\n    return open('x').read()\n", "禁止直接调用 open"),
        (_META + "\ndef run(ctx, args):\n    return ctx.__class__\n", "禁止访问 dunder 属性"),
    ],
)
def test_invalid_source_is_rejected_before_write(tmp_path: Path, content: str, message: str) -> None:
    result = _tool(tmp_path).forward("create", "counter", content=content)
    assert not result["success"]
    assert message in result["error"]
    assert not _path(tmp_path).exists()


def test_invalid_edit_keeps_previous_source(tmp_path: Path) -> None:
    """edit 校验失败时不能留下半写入的文件。"""

    tool = _tool(tmp_path)
    assert tool.forward("create", "counter", content=SOURCE)["success"]

    result = tool.forward("edit", "counter", content="import os\n" + SOURCE)
    assert not result["success"]
    assert _path(tmp_path).read_text(encoding="utf-8") == SOURCE
    # 原子写入的临时文件也不应残留。
    assert sorted(p.name for p in _path(tmp_path).parent.iterdir()) == ["counter.py"]


@pytest.mark.parametrize("name", ["Bad-Name", "1counter", "../escape", ""])
def test_invalid_name_is_rejected(tmp_path: Path, name: str) -> None:
    result = _tool(tmp_path).forward("create", name, content=SOURCE)
    assert not result["success"]
    assert not (tmp_path / ".juice").exists()
