"""
NEXA Execution Engine
=====================
Provides:
  - Intent classification (conversation | action | multi_step)
  - Risk detection (actions requiring confirmation)
  - run_with_retry()   — retries + exponential backoff + timeout
  - run_with_timeout() — simple timeout wrapper

These utilities are used by MCP tool implementations to guarantee
reliability without cluttering business logic.
"""

import asyncio
import logging
import time
from typing import Any, Callable, Optional

from .logger import trace
from .context import context

_log = logging.getLogger("nexa.engine")

# ---------------------------------------------------------------------------
# Intent classification
# ---------------------------------------------------------------------------

# Keyword sets for lightweight heuristic classification.
# Order matters: multi_step is checked before action.
_MULTI_STEP_MARKERS = {
    "then", "after that", "and then", "followed by", "next", "finally",
    "step by step", "first.*then", "second", "third",
}

_ACTION_MARKERS = {
    "open", "send", "fetch", "get", "show", "find", "search", "check",
    "look up", "pull", "run", "launch", "start", "tell me", "what's",
    "what is", "give me", "read", "load", "fetch",
}

_RISKY_MARKERS = {
    "send email", "send message", "send a message", "send an email",
    "delete", "remove", "modify", "update record", "post to",
    "publish", "submit", "buy", "purchase", "confirm order",
}


def classify_intent(user_text: str) -> str:
    """
    Lightweight heuristic intent classifier.
    Returns: "multi_step" | "action" | "conversation"
    """
    text = user_text.lower()

    # Count multi-step signal words
    multi_hits = sum(1 for kw in _MULTI_STEP_MARKERS if kw in text)
    if multi_hits >= 2:
        return "multi_step"

    action_hits = sum(1 for kw in _ACTION_MARKERS if kw in text)
    if action_hits >= 1:
        return "action"

    return "conversation"


def is_risky(user_text: str) -> bool:
    """Returns True if the request contains a potentially destructive action."""
    text = user_text.lower()
    return any(kw in text for kw in _RISKY_MARKERS)


# ---------------------------------------------------------------------------
# Execution wrappers
# ---------------------------------------------------------------------------

async def run_with_timeout(
    coro,
    timeout_seconds: float = 10.0,
    fallback: Any = None,
    tool_name: str = "unknown",
) -> Any:
    """
    Await a coroutine with a hard timeout.
    Returns fallback if it times out; never raises.
    """
    try:
        return await asyncio.wait_for(coro, timeout=timeout_seconds)
    except asyncio.TimeoutError:
        trace.error(tool_name, f"timed out after {timeout_seconds:.1f}s", "returning fallback")
        _log.warning("[%s] timeout after %.1fs", tool_name, timeout_seconds)
        return fallback
    except Exception as exc:
        trace.error(tool_name, str(exc), "returning fallback")
        _log.error("[%s] unexpected error: %s", tool_name, exc)
        return fallback


async def run_with_retry(
    fn: Callable,
    *args,
    max_retries: int = 2,
    timeout_seconds: float = 10.0,
    backoff_seconds: float = 0.5,
    fallback: Any = None,
    tool_name: str = "unknown",
    **kwargs,
) -> tuple[Any, bool]:
    """
    Execute an async callable with retries, exponential backoff, and per-attempt timeout.

    Returns:
        (result, success: bool)

    Guarantees:
        - Never raises — always returns (value, bool).
        - Logs every attempt and outcome to the trace.
        - Updates the shared session context.
    """
    start_time = trace.tool_call(tool_name, kwargs if kwargs else None)
    last_error: Optional[str] = None

    for attempt in range(max_retries + 1):
        try:
            if asyncio.iscoroutinefunction(fn):
                result = await asyncio.wait_for(fn(*args, **kwargs), timeout=timeout_seconds)
            else:
                result = fn(*args, **kwargs)

            trace.tool_result(tool_name, success=True, start_time=start_time, preview=str(result)[:80])
            context.record_tool(tool_name, result, success=True)
            return result, True

        except asyncio.TimeoutError:
            last_error = f"timeout after {timeout_seconds:.1f}s"
        except Exception as exc:
            last_error = str(exc)

        if attempt < max_retries:
            delay = backoff_seconds * (2 ** attempt)   # 0.5s → 1s → ...
            trace.retry(tool_name, attempt + 1, last_error or "unknown")
            _log.warning("[%s] attempt %d/%d failed: %s — retrying in %.1fs",
                         tool_name, attempt + 1, max_retries + 1, last_error, delay)
            await asyncio.sleep(delay)
        else:
            trace.error(tool_name, last_error or "unknown", "using fallback")
            _log.error("[%s] all %d attempts failed: %s", tool_name, max_retries + 1, last_error)

    trace.tool_result(tool_name, success=False, start_time=start_time,
                      preview=f"fallback: {str(fallback)[:40]}")
    context.record_tool(tool_name, fallback, success=False)
    return fallback, False


# ---------------------------------------------------------------------------
# Safety guard
# ---------------------------------------------------------------------------

def build_confirmation_prompt(action_description: str) -> str:
    """Return the spoken confirmation question for a risky action."""
    return f"{action_description} — confirm?"
