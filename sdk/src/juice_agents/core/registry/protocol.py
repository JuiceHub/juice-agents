"""Registry 统一协议。"""

from __future__ import annotations

from typing import Any, Protocol, TypeVar

T = TypeVar("T")
D = TypeVar("D")


class BaseRegistry(Protocol[T, D]):
    """静态定义 Registry 的统一三段式协议。

    ``resolve`` only turns a public reference into a declaration.  ``validate``
    returns the canonical, self-contained declaration and never creates a live
    runtime object.  ``instantiate`` is the sole fresh-construction boundary;
    Managers own every object returned from it afterwards.
    """

    def resolve(self, raw: Any) -> D:
        """把外部输入归一化为定义对象。"""

    def validate(self, raw: Any) -> D:
        """校验并返回可安全用于 fresh construction 的静态定义。"""

    def instantiate(self, raw: Any, **runtime_kwargs: Any) -> T:
        """根据静态定义创建一个 fresh 对象。"""

    def list(self) -> list[str]:
        """列出当前支持的命名项。"""


__all__ = ["BaseRegistry"]
