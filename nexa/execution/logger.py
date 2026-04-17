"""
Structured observability for NEXA.
Provides a per-turn trace: intent → decision → action → result → final.

Usage:
    from nexa.execution.logger import trace

    trace.reset()
    trace.intent("action", "news request")
    t0 = trace.tool_call("get_world_news")
    ...
    trace.tool_result("get_world_news", success=True, start_time=t0, preview="12 articles")
    trace.final("delivered 3-sentence brief")
    print(trace.get_debug_trace())
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

_log = logging.getLogger("nexa.trace")


@dataclass
class TraceEntry:
    stage: str
    detail: str
    ts: float = field(default_factory=time.monotonic)
    duration_ms: Optional[float] = None
    status: str = "ok"   # "ok" | "error" | "retry"


class NexaTrace:
    """Per-turn trace buffer. Call reset() at the start of each new turn."""

    def __init__(self) -> None:
        self._entries: list[TraceEntry] = []
        self._turn_start: float = time.monotonic()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def reset(self) -> None:
        self._entries.clear()
        self._turn_start = time.monotonic()

    # ------------------------------------------------------------------
    # Stage recorders
    # ------------------------------------------------------------------

    def intent(self, intent_type: str, detail: str = "") -> None:
        full = intent_type + (f" | {detail}" if detail else "")
        self._entries.append(TraceEntry("INTENT", full))
        _log.info("[INTENT]   %s", full)

    def decision(self, reason: str) -> None:
        self._entries.append(TraceEntry("DECISION", reason))
        _log.info("[DECISION] %s", reason)

    def tool_call(self, name: str, args: Optional[dict] = None) -> float:
        """Record a tool dispatch. Returns a start timestamp for duration tracking."""
        detail = name + (f"({args})" if args else "()")
        self._entries.append(TraceEntry("TOOL_CALL", detail))
        _log.info("[TOOL →]   %s", detail)
        return time.monotonic()

    def tool_result(
        self,
        name: str,
        success: bool,
        start_time: float,
        preview: str = "",
    ) -> None:
        duration_ms = (time.monotonic() - start_time) * 1000
        status = "ok" if success else "error"
        label = "OK" if success else "FAIL"
        detail = f"{name} [{label}]" + (f" — {preview[:60]}" if preview else "")
        self._entries.append(
            TraceEntry("TOOL_RESULT", detail, duration_ms=duration_ms, status=status)
        )
        _log.info("[TOOL ←]   %s  %.0fms", detail, duration_ms)

    def step(self, num: int, description: str, status: str = "ok") -> None:
        detail = f"Step {num}: {description}"
        self._entries.append(TraceEntry("STEP", detail, status=status))
        _log.info("[STEP %d]   %s  [%s]", num, description, status.upper())

    def error(self, component: str, error: str, recovery: str = "") -> None:
        detail = f"{component}: {error}" + (f"  → {recovery}" if recovery else "")
        self._entries.append(TraceEntry("ERROR", detail, status="error"))
        _log.error("[ERROR]    %s", detail)

    def retry(self, tool_name: str, attempt: int, reason: str) -> None:
        detail = f"{tool_name} attempt {attempt}: {reason}"
        self._entries.append(TraceEntry("RETRY", detail, status="retry"))
        _log.warning("[RETRY]    %s", detail)

    def final(self, preview: str) -> None:
        total_ms = (time.monotonic() - self._turn_start) * 1000
        detail = f"{preview[:80]}  ({total_ms:.0f}ms total)"
        self._entries.append(TraceEntry("FINAL", detail))
        _log.info("[FINAL]    %s", detail)

    # ------------------------------------------------------------------
    # Debug output
    # ------------------------------------------------------------------

    def get_debug_trace(self) -> str:
        """Return a concise, human-readable trace suitable for console output."""
        STAGE_PREFIX = {
            "INTENT":      "→ Intent   ",
            "DECISION":    "  Decision ",
            "TOOL_CALL":   "  ⚡ Call   ",
            "TOOL_RESULT": "  ✓ Result ",
            "STEP":        "  Step     ",
            "RETRY":       "  ↺ Retry  ",
            "ERROR":       "  ✗ Error  ",
            "FINAL":       "← Final    ",
        }
        lines = ["╔═══════════════════ NEXA TRACE ═══════════════════╗"]
        for e in self._entries:
            prefix = STAGE_PREFIX.get(e.stage, f"  {e.stage:<10}")
            dur_str = f"  ({e.duration_ms:.0f}ms)" if e.duration_ms is not None else ""
            status_str = f"  [{e.status.upper()}]" if e.status != "ok" else ""
            lines.append(f"║  {prefix}  {e.detail}{dur_str}{status_str}")
        lines.append("╚══════════════════════════════════════════════════╝")
        return "\n".join(lines)

    def to_dict(self) -> list[dict]:
        return [
            {
                "stage": e.stage,
                "detail": e.detail,
                "status": e.status,
                "duration_ms": round(e.duration_ms, 1) if e.duration_ms else None,
            }
            for e in self._entries
        ]


# ---------------------------------------------------------------------------
# Module-level singleton — shared within a single process
# ---------------------------------------------------------------------------
trace = NexaTrace()
