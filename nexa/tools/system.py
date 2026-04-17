"""
System tools — time, host info, and session diagnostics.
"""

import datetime
import platform

from nexa.execution.logger import trace


def register(mcp):

    @mcp.tool()
    def get_current_time() -> str:
        """
        Return the current local date and time.
        Use when the user asks what time or date it is.
        """
        trace.intent("action", "get_current_time")
        now = datetime.datetime.now()

        # Human-friendly string for spoken delivery + ISO for precision
        try:
            spoken = now.strftime("%A, %B %-d at %-I:%M %p")   # Linux/Mac
        except ValueError:
            spoken = now.strftime("%A, %B %d at %I:%M %p")      # Windows fallback

        iso = now.isoformat(timespec="seconds")
        trace.step(1, f"time resolved: {iso}", "ok")
        return f"{spoken}  (ISO: {iso})"

    @mcp.tool()
    def get_system_info() -> dict:
        """
        Return a summary of the host system (OS, architecture, Python version).
        Use when the user asks about the machine, host, or system specs.
        """
        trace.intent("action", "get_system_info")

        info = {
            "os":             platform.system(),
            "os_version":     platform.version(),
            "machine":        platform.machine(),
            "processor":      platform.processor() or "unknown",
            "python_version": platform.python_version(),
            "hostname":       platform.node(),
        }

        trace.step(1, f"system info: {info['os']} / {info['machine']}", "ok")
        return info
