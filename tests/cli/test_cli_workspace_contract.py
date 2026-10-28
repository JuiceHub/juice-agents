"""The source launcher preserves the caller's cwd and project executable PATH."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

ROOT_DIR = Path(__file__).resolve().parents[2]


def test_juice_wrapper_preserves_workspace_and_user_path(tmp_path: Path) -> None:
    """Record what reaches Node without depending on CLI rendering details."""
    workspace = tmp_path / "项目 workspace"
    workspace.mkdir()
    python = tmp_path / "python-probe"
    python.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    python.chmod(0o755)
    node = tmp_path / "node-probe"
    node.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "if '--import' in sys.argv:\n"
        "    print(json.dumps({'cwd': os.getcwd(), 'path': os.environ['PATH'], "
        "'workspace': os.environ.get('JUICE_WORKSPACE_DIR'), 'args': sys.argv[1:]}))\n",
        encoding="utf-8",
    )
    node.chmod(0o755)
    caller_path = f"{workspace / '.venv' / 'bin'}:/usr/bin:/bin"
    env = {**os.environ, "PATH": caller_path, "JUICE_PYTHON_PATH": str(python),
           "JUICE_NODE_PATH": str(node), "JUICE_WORKSPACE_DIR": str(workspace)}
    result = subprocess.run([str(ROOT_DIR / "juice"), "--workspace", str(workspace)],
                            cwd=workspace, env=env, text=True, capture_output=True, check=True)
    launched = json.loads(result.stdout)
    assert launched["cwd"] == str(workspace)
    assert launched["path"] == caller_path
    assert launched["workspace"] == str(workspace)
    assert launched["args"][-2:] == ["--workspace", str(workspace)]
