"""仓库忽略规则测试。"""

from pathlib import Path
import subprocess
import unittest


class GitignoreRulesTests(unittest.TestCase):
    def test_agent_instructions_use_a_single_file(self) -> None:
        """代码智能体约定只保存在根目录的 AGENTS.md。"""

        repo_root = Path(__file__).resolve().parents[2]
        self.assertTrue((repo_root / "AGENTS.md").is_file())
        self.assertFalse((repo_root / "CLAUDE.md").exists())

    def test_local_credentials_and_runtime_state_are_ignored(self) -> None:
        """旧版配置和当前运行数据都不能意外进入后续公开提交。"""

        repo_root = Path(__file__).resolve().parents[2]
        for relative_path in (
            ".env",
            "config.ini",
            ".juice/runners/example/manifest.json",
        ):
            with self.subTest(path=relative_path):
                # --no-index 让断言不依赖文件是否存在或曾经被 Git 跟踪。
                result = subprocess.run(
                    ["git", "check-ignore", "--no-index", relative_path],
                    cwd=repo_root,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_frontend_cli_node_modules_is_ignored(self) -> None:
        """确保前端 CLI 的依赖目录不会被误提交到版本库。"""

        repo_root = Path(__file__).resolve().parents[2]
        ignored_path = "frontend/cli/node_modules"

        # 使用 git 自身的 ignore 判定逻辑做断言，避免测试与实际提交行为脱节。
        result = subprocess.run(
            ["git", "check-ignore", ignored_path],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(ignored_path, result.stdout)

    def test_removed_benchmark_assets_are_absent(self) -> None:
        """旧 backend 目录和评测数据不能残留在工作区。"""

        repo_root = Path(__file__).resolve().parents[2]
        removed_paths = (
            "backend",
            "evaluations",
            "juice-eval",
        )

        for relative_path in removed_paths:
            with self.subTest(path=relative_path):
                self.assertFalse((repo_root / relative_path).exists())

        # 不再忽略评测数据目录，避免以后残留的数据再次从 git status 隐身。
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "evaluations/datasets"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1, result.stderr)


if __name__ == "__main__":
    unittest.main()
