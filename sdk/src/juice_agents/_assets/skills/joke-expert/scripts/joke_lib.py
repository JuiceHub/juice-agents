from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)


DEFAULT_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "jokes.json"


@dataclass(frozen=True)
class Joke:
    """单条笑话结构。"""

    id: str
    category: str
    setup: str
    punchline: str

    def to_text(self) -> str:
        return f"{self.setup} {self.punchline}"


class JokeRepository:
    """加载与查询笑话的简单仓库。"""

    def __init__(self, data_path: Path | None = None) -> None:
        self.data_path = data_path or DEFAULT_DATA_PATH
        self._jokes: List[Joke] = []
        self._load()

    def _load(self) -> None:
        if not self.data_path.exists():
            raise FileNotFoundError(f"找不到笑话库文件: {self.data_path}")

        with self.data_path.open("r", encoding="utf-8") as f:
            raw = json.load(f)

        jokes: List[Joke] = []
        for item in raw:
            try:
                jokes.append(
                    Joke(
                        id=str(item["id"]),
                        category=str(item["category"]),
                        setup=str(item["setup"]),
                        punchline=str(item["punchline"]),
                    )
                )
            except Exception as exc:  # pragma: no cover - 防御性保护
                logger.warning("忽略无效笑话数据 %s: %s", item, exc)

        if not jokes:
            raise ValueError("笑话库为空，请提供至少一条笑话数据")

        self._jokes = jokes
        logger.info("加载笑话库成功，数量=%d，路径=%s", len(self._jokes), self.data_path)

    def random_joke(self, category: str | None = None) -> Joke:
        if category:
            filtered = [j for j in self._jokes if j.category == category]
            if not filtered:
                raise ValueError(f"未找到分类为 {category} 的笑话")
            return random.choice(filtered)
        return random.choice(self._jokes)

    def list_categories(self) -> List[str]:
        return sorted({j.category for j in self._jokes})


def list_categories(data_path: str | Path | None = None) -> List[str]:
    repo = JokeRepository(Path(data_path) if data_path else None)
    return repo.list_categories()


def get_random_joke(category: str | None = None, data_path: str | Path | None = None) -> Joke:
    repo = JokeRepository(Path(data_path) if data_path else None)
    return repo.random_joke(category)


__all__ = ["Joke", "JokeRepository", "list_categories", "get_random_joke"]
