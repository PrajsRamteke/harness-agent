"""Browser remote control for a running Jarvis session."""

from .bridge import WebBridge
from .server import start_web_server

__all__ = ["WebBridge", "start_web_server"]
