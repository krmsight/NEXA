"""
Session context — lightweight state tracked across turns for conversational coherence.

Tracks:
- Intent history (conversation | action | multi_step)
- Last tool used and its result
- Active multi-step task state
- Turn count and timestamps

Intentionally simple. No persistence. Resets on process restart.
"""

import time
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class SessionContext:
    # ── Turn metadata ──────────────────────────────────────────────────
    turn_count: int = 0
    session_start: float = field(default_factory=time.monotonic)
    last_turn_ts: float = field(default_factory=time.monotonic)

    # ── Intent tracking ────────────────────────────────────────────────
    last_intent: Optional[str] = None        # "conversation" | "action" | "multi_step"
    intent_history: list[str] = field(default_factory=list)

    # ── Tool tracking ──────────────────────────────────────────────────
    last_tool: Optional[str] = None
    last_tool_result_preview: Optional[str] = None
    last_tool_success: bool = True
    tool_call_count: int = 0
    tool_failure_count: int = 0

    # ── Response tracking ──────────────────────────────────────────────
    last_response_preview: Optional[str] = None

    # ── Multi-step task state ──────────────────────────────────────────
    active_task: Optional[str] = None
    task_steps: list[str] = field(default_factory=list)
    completed_steps: list[str] = field(default_factory=list)
    current_step_index: int = 0

    # ── Safety ────────────────────────────────────────────────────────
    pending_confirmation: Optional[str] = None   # action awaiting user yes/no

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def new_turn(self) -> None:
        """Call at the start of each user turn."""
        self.turn_count += 1
        self.last_turn_ts = time.monotonic()
        # Clear transient per-turn state
        self.active_task = None
        self.task_steps = []
        self.completed_steps = []
        self.current_step_index = 0
        self.pending_confirmation = None

    def record_intent(self, intent: str) -> None:
        self.last_intent = intent
        self.intent_history.append(intent)
        if len(self.intent_history) > 20:
            self.intent_history.pop(0)

    def record_tool(self, name: str, result: Any, success: bool) -> None:
        self.last_tool = name
        self.last_tool_result_preview = str(result)[:300] if result is not None else None
        self.last_tool_success = success
        self.tool_call_count += 1
        if not success:
            self.tool_failure_count += 1

    def record_response(self, preview: str) -> None:
        self.last_response_preview = preview[:200]

    def start_task(self, description: str, steps: list[str]) -> None:
        self.active_task = description
        self.task_steps = steps
        self.completed_steps = []
        self.current_step_index = 0

    def complete_step(self, result_preview: str = "") -> None:
        if self.current_step_index < len(self.task_steps):
            step_name = self.task_steps[self.current_step_index]
            self.completed_steps.append(
                f"{step_name}" + (f" → {result_preview[:60]}" if result_preview else "")
            )
            self.current_step_index += 1

    def task_is_complete(self) -> bool:
        return self.current_step_index >= len(self.task_steps)

    def set_pending_confirmation(self, action_description: str) -> None:
        self.pending_confirmation = action_description

    def clear_pending_confirmation(self) -> None:
        self.pending_confirmation = None

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    def uptime_seconds(self) -> float:
        return time.monotonic() - self.session_start

    def to_summary(self) -> str:
        """Compact state string for prompt injection or logging."""
        parts = [f"turn={self.turn_count}"]
        if self.last_intent:
            parts.append(f"intent={self.last_intent}")
        if self.last_tool:
            status = "ok" if self.last_tool_success else "failed"
            parts.append(f"last_tool={self.last_tool}({status})")
        if self.active_task:
            parts.append(
                f"task={self.active_task!r} "
                f"step={self.current_step_index}/{len(self.task_steps)}"
            )
        if self.pending_confirmation:
            parts.append(f"awaiting_confirm={self.pending_confirmation!r}")
        return " | ".join(parts)

    def to_dict(self) -> dict:
        return {
            "turn_count": self.turn_count,
            "uptime_seconds": round(self.uptime_seconds(), 1),
            "last_intent": self.last_intent,
            "last_tool": self.last_tool,
            "last_tool_success": self.last_tool_success,
            "tool_call_count": self.tool_call_count,
            "tool_failure_count": self.tool_failure_count,
            "last_response_preview": self.last_response_preview,
            "active_task": self.active_task,
            "pending_confirmation": self.pending_confirmation,
        }


# ---------------------------------------------------------------------------
# Module-level singleton — one per agent process
# ---------------------------------------------------------------------------
context = SessionContext()
