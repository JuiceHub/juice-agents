from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from joke_lib import list_categories  # noqa: E402


logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="列出笑话库支持的分类")
    parser.add_argument(
        "--data-path",
        type=str,
        default=None,
        help="可选：自定义笑话数据路径，默认使用内置 data/jokes.json",
    )
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )
    args = parse_args()
    try:
        categories = list_categories(args.data_path)
        result = {"categories": categories, "count": len(categories)}
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:  # pragma: no cover - CLI 异常打印
        logger.exception("列出笑话分类失败")
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
