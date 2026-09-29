"""`juice` 启动脚本的 workspace 交接契约。

CLI 包装在仓库内，启动脚本必须 `cd` 进包目录才能跑 tsx；一旦先 `cd`，
用户真实的工作目录就丢了。resolver 侧的优先级由
`frontend/cli/src/lib/workspace.test.ts` 覆盖，这里只锁 shell 脚本的顺序
——它是唯一无法用 TS 测试表达的一环。
"""

from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]


def test_juice_wrapper_exports_original_workspace_before_entering_cli_package():
    script = (ROOT_DIR / "juice").read_text(encoding="utf-8")

    workspace_line = 'WORKSPACE_DIR="$(pwd -P)"'
    export_line = 'export JUICE_WORKSPACE_DIR="$WORKSPACE_DIR"'
    cd_line = 'cd "$CLI_DIR"'

    assert workspace_line in script
    assert export_line in script
    assert script.index(workspace_line) < script.index(cd_line)
    assert script.index(export_line) < script.index(cd_line)
