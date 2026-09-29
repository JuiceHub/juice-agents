"""Command-line entrypoint for the local Juice web gateway."""

from __future__ import annotations

import argparse
import logging

import uvicorn

from .app import create_app, is_loopback_host


def _loopback_host(value: str) -> str:
    """Keep the unauthenticated gateway reachable only from the local machine."""
    host = value.strip().lower()
    if is_loopback_host(host):
        return host
    raise argparse.ArgumentTypeError("web gateway host must be a loopback address or localhost")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Juice local web gateway")
    parser.add_argument("--host", type=_loopback_host, default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8003)
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO))
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level=args.log_level)


if __name__ == "__main__":
    main()
