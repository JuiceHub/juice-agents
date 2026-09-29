"""stdin/stdout JSON-RPC gateway for the Juice TypeScript + Ink CLI."""

from .entry import main
from .stdio_server import StdioJsonRpcServer

__all__ = ["main", "StdioJsonRpcServer"]
