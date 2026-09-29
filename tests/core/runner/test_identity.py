"""Runner 默认标识的时间排序与唯一性契约测试。"""

from __future__ import annotations

import re
import unittest
from datetime import timezone
from types import SimpleNamespace
from unittest.mock import patch

from juice_agents.core.runner.types.identity import new_runner_id


RUNNER_ID_PATTERN = re.compile(r"^\d{8}T\d{12}Z-[0-9a-f]{8}$")


class RunnerIdentityTests(unittest.TestCase):
    def test_new_runner_id_prefixes_original_short_uuid_with_utc_timestamp(self) -> None:
        """时间前缀固定宽度，且 UUID 后缀保持原有 8 位小写 hex。"""

        with (
            patch("juice_agents.core.runner.types.identity.datetime") as datetime_mock,
            patch(
                "juice_agents.core.runner.types.identity.uuid4",
                return_value=SimpleNamespace(hex="a1b2c3d4" * 4),
            ),
        ):
            datetime_mock.now.return_value.strftime.return_value = "20260726T143205123456Z"
            runner_id = new_runner_id("default", "root")

        datetime_mock.now.assert_called_once_with(timezone.utc)
        self.assertEqual(runner_id, "20260726T143205123456Z-a1b2c3d4")
        self.assertRegex(runner_id, RUNNER_ID_PATTERN)

    def test_runner_ids_sort_lexically_by_creation_timestamp(self) -> None:
        """固定宽度时间字段应让普通字符串排序等同于创建时间排序。"""

        with (
            patch("juice_agents.core.runner.types.identity.datetime") as datetime_mock,
            patch(
                "juice_agents.core.runner.types.identity.uuid4",
                side_effect=[
                    SimpleNamespace(hex="ffffffff" * 4),
                    SimpleNamespace(hex="00000000" * 4),
                ],
            ),
        ):
            datetime_mock.now.return_value.strftime.side_effect = [
                "20260726T143205123456Z",
                "20260726T143205123457Z",
            ]
            older = new_runner_id("default", "root")
            newer = new_runner_id("default", "root")

        # UUID 的逆序值刻意排除“随机后缀碰巧有序”，只验证时间前缀主导排序。
        self.assertEqual(sorted([newer, older]), [older, newer])

    def test_same_timestamp_keeps_uuid_suffix_uniqueness(self) -> None:
        """极端同微秒创建时仍由原随机后缀保证 runner_id 不重复。"""

        with (
            patch("juice_agents.core.runner.types.identity.datetime") as datetime_mock,
            patch(
                "juice_agents.core.runner.types.identity.uuid4",
                side_effect=[
                    SimpleNamespace(hex="11111111" * 4),
                    SimpleNamespace(hex="22222222" * 4),
                ],
            ),
        ):
            datetime_mock.now.return_value.strftime.return_value = "20260726T143205123456Z"
            first = new_runner_id("default", "root")
            second = new_runner_id("default", "root")

        self.assertNotEqual(first, second)
        self.assertRegex(first, RUNNER_ID_PATTERN)
        self.assertRegex(second, RUNNER_ID_PATTERN)


if __name__ == "__main__":
    unittest.main()
