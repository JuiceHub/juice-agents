"""HTTP/WebSocket gateway for the local Juice web app."""

from .app import create_app

__all__ = ["create_app"]
