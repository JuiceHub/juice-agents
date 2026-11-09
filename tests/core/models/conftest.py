"""官方异步 SDK 使用 asyncio；测试与生产采用同一事件循环后端。"""

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"
