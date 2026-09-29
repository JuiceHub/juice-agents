"""Entry point for the stdio JSON-RPC adapter."""

from __future__ import annotations

import logging
import sys

from .stdio_server import StdioJsonRpcServer

# Keep logs on stderr so stdout remains a clean JSON-RPC channel.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)

logger = logging.getLogger(__name__)


def main() -> int:
    """Run the stdio JSON-RPC server until stdin closes or a fatal error occurs."""
    try:
        server = StdioJsonRpcServer()
        server.run()
        return 0
    except Exception as exc:
        logger.exception("Fatal error: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
