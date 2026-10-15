"""
测试 /model 命令不会重建 runner，保持 runner_id 不变。

这个测试验证了修复 issue: "/model会切换runners，历史消息还会弹出来"
"""

import tempfile
import shutil
from pathlib import Path
import sys

# 添加路径
sys.path.insert(0, str(Path(__file__).parent.parent / "sdk" / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent / "adapters"))

from stdio_gateway.runtime import DirectRunnerRuntime


def test_model_switch_preserves_runner_id():
    """测试切换模型时 runner_id 保持不变"""
    tmpdir = tempfile.mkdtemp()

    try:
        # 创建 runtime
        runtime = DirectRunnerRuntime(base_dir=tmpdir)

        # 启动会话
        runtime.start_session()
        status_before = runtime.describe_session()
        runner_id_before = status_before.runner_id

        print(f"初始 runner_id: {runner_id_before}")
        print(f"初始模型: {status_before.model_name}")

        # 切换模型（使用实际存在的模型名称）
        # 从初始模型名称切换到另一个名称来测试
        new_model_name = "doubao_pro" if status_before.model_name != "doubao_pro" else "doubao_lite"
        status_after = runtime.switch_model(new_model_name)
        runner_id_after = status_after.runner_id

        print(f"切换后 runner_id: {runner_id_after}")
        print(f"切换后模型: {status_after.model_name}")

        # 断言：runner_id 应该保持不变
        assert runner_id_before == runner_id_after, (
            f"runner_id 不应该改变！"
            f"修复前会创建新 runner，修复后应该保持同一个 runner。"
            f"before: {runner_id_before}, after: {runner_id_after}"
        )

        # 断言：模型名称应该已更新
        assert status_after.model_name == new_model_name, (
            f"模型名称应该更新为 {new_model_name}，实际: {status_after.model_name}"
        )

        print("✅ 测试通过：/model 不会重建 runner，runner_id 保持不变")

    finally:
        # 清理临时目录
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_model_switch_multiple_times():
    """测试多次切换模型，runner_id 始终保持不变"""
    tmpdir = tempfile.mkdtemp()

    try:
        runtime = DirectRunnerRuntime(base_dir=tmpdir)
        runtime.start_session()

        initial_status = runtime.describe_session()
        initial_runner_id = initial_status.runner_id

        print(f"\n初始 runner_id: {initial_runner_id}")
        print(f"初始模型: {initial_status.model_name}")

        # 多次切换模型（使用实际存在的模型）
        models = ["doubao_pro", "doubao_lite", "doubao_pro"]
        for model_name in models:
            status = runtime.switch_model(model_name)
            print(f"切换到 {model_name}, runner_id: {status.runner_id}")

            assert status.runner_id == initial_runner_id, (
                f"第 {models.index(model_name) + 1} 次切换后 runner_id 改变了！"
            )
            assert status.model_name == model_name, (
                f"模型名称未正确更新"
            )

        print("✅ 测试通过：多次切换模型，runner_id 始终不变")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    print("=" * 60)
    print("测试：/model 命令不会重建 runner")
    print("=" * 60)

    test_model_switch_preserves_runner_id()
    test_model_switch_multiple_times()

    print("\n" + "=" * 60)
    print("所有测试通过！")
    print("=" * 60)
