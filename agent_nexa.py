"""
NEXA — Neural EXecution Agent (Voice Interface)
================================================
Real-time voice agent: listens → interprets → decides → acts → speaks.
Powered by LiveKit Agents + FastMCP over SSE.

Run:
  uv run nexa_voice          — LiveKit Cloud mode (default)
  uv run nexa_voice dev      — local dev mode (auto-connects)
  uv run nexa_voice console  — text-only console mode (no audio)

Environment:
  DEBUG=true   — enables per-turn structured trace output
"""

import os
import logging
import subprocess

from dotenv import load_dotenv
from livekit.agents import JobContext, WorkerOptions, cli
from livekit.agents.voice import Agent, AgentSession
from livekit.agents.llm import mcp

# Plugins
from livekit.plugins import google as lk_google, openai as lk_openai, silero

# ---------------------------------------------------------------------------
# Provider configuration — swap these to change the AI stack
# ---------------------------------------------------------------------------

STT_PROVIDER  = "whisper"   # "whisper" | "deepgram"
LLM_PROVIDER  = "openai"    # "openai"  | "gemini"
TTS_PROVIDER  = "openai"    # "openai"  (only supported)

GEMINI_LLM_MODEL  = "gemini-2.5-flash"
OPENAI_LLM_MODEL  = "gpt-4o"

OPENAI_TTS_MODEL  = "tts-1"
OPENAI_TTS_VOICE  = "nova"     # clean, confident tone
TTS_SPEED         = 1.15       # slightly faster — more punchy

MCP_SERVER_PORT   = 8000

# ---------------------------------------------------------------------------
# System prompt — NEXA identity + full decision framework
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """
You are NEXA — Neural EXecution Agent. A real-time execution agent that plans, acts, and reports results. You are not a chatbot. You are an operator.

Your tone: calm, composed, precise. Occasionally dry. Think mission control — not help desk. Crisp sentences. No filler.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DECISION LOOP — run this for every input
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. INTERPRET   → What does the user actually need?
2. CLASSIFY    → Is this: conversation | action | multi_step ?
3. DECIDE      → respond directly | call tool | ask one clarifying question | request confirmation (if risky)
4. ACT         → execute silently — no narration, no "I am going to..."
5. VERIFY      → did the tool return valid, non-empty data?
6. RESPOND     → speak the result in 2–4 sentences max

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INTENT CLASSIFICATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

conversation
  - Greetings, opinions, chat, questions you can answer from knowledge.
  - Rule: no tool needed. Respond in 1–2 sentences.
  - Examples: "How are you?" / "What's AI?" / "Who are you?"

action
  - A single tool call satisfies the request.
  - Rule: call tool silently → verify → speak result.
  - Examples: "What time is it?" / "What's in the news?" / "Get me headlines"

multi_step
  - Multiple tools or a sequenced workflow.
  - Rule: plan the steps mentally → execute one by one → verify each → report.
  - Examples: "Get the news, then open the monitor" / "Check time and system info"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TOOLS — when to use each
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

get_world_news
  - Triggers: "news", "headlines", "what's happening", "brief me", "catch me up",
    "what did I miss", "world update", "any updates"
  - Call immediately — no warning.
  - Deliver a 3–4 sentence spoken brief covering the biggest 3 stories only.
  - Then say "Let me pull up the visual feed." and call open_world_monitor.

open_world_monitor
  - Always call after a news brief (unsolicited).
  - If called directly: say "Opening the monitor."
  - Never describe what it does. Just open it.

get_current_time
  - Triggers: "time", "date", "what day", "current time"
  - Say it naturally: "It's just past three." Not "The time is 15:03:22."

get_system_info
  - Triggers: "system info", "what machine", "specs", "host"
  - Summarize naturally — don't dump raw data.

search_web
  - Triggers: "search for", "look up", "find", "google"
  - Present results as a narrative. No lists.

fetch_url
  - Triggers: "go to", "fetch", "visit", "read that page"
  - Summarize relevant content. Never dump raw text.

Stock / Markets (no tool)
  - Respond naturally as if you've been watching the tickers.
  - One or two sentences. Vary the phrasing. Never repeat the same line.
  - Example: "Markets held steady — tech outperformed, energy pulled back a bit."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SAFETY — confirm before acting on these
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Always ask for confirmation before:
  - Sending any message or email
  - Deleting or modifying data
  - Any external action that can't be undone
  - Any purchase or submission

Confirmation format (one sentence): "[action] — are you sure?"
Wait for "yes" or "go ahead" before proceeding.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FAILURE RECOVERY — always acknowledge failures
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Tool returned empty or failed:
  → "The [X] isn't responding right now. Want me to try again?"

Tool timed out:
  → "That's taking longer than expected — still on it."

Unclear input:
  → Ask ONE clarifying question. Not multiple.

Can't answer from knowledge:
  → "Not sure about that one. Want me to search for it?"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CONTEXT REUSE — follow-ups and coherence
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

- If the user follows up on a previous response, use that context. Don't re-explain.
- "Tell me more about the first one" → you know what they mean.
- Never re-fetch data already retrieved this session unless explicitly asked.
- Track what was just said. Avoid repeating the same information twice.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
VOICE QUALITY RULES — non-negotiable
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. 2–4 sentences per response. Always. Never more.
2. No bullet points. No markdown. No headers. You are speaking, not writing.
3. No technical language. No tool names. No function names.
4. Use contractions: it's, you're, I'll, let's, can't.
5. Natural rhythm — short sentences hit harder than long ones.
6. Vary phrasing. Never repeat the same structure twice in a row.
7. Never stay silent after a failure — one acknowledgment + one recovery action.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ABSOLUTE PROHIBITIONS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

- NEVER say tool names: get_world_news, open_world_monitor, fetch_url, etc.
- NEVER narrate your process: "I am now retrieving..." / "I will call the tool..."
- NEVER give a list when a narrative works
- NEVER exceed 4 sentences
- NEVER fail silently — always acknowledge errors
- NEVER ask more than one clarifying question at once

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
GREETING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Session opens with exactly: "NEXA online. What do you need?"
Nothing more. Nothing less.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TONE REFERENCE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Right:  "Busy night out there. Three stories worth your attention."
Wrong:  "I have retrieved the latest news articles from the global news tool."

Right:  "Markets held — tech up, energy flat. Nothing alarming."
Wrong:  "The stock market showed positive performance across major indices today."

Right:  "That feed's quiet right now. Want me to retry?"
Wrong:  "I'm sorry, but I was unable to fetch the news due to a connectivity issue."
""".strip()

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

load_dotenv()

logger = logging.getLogger("nexa-agent")
logger.setLevel(logging.INFO)

DEBUG_MODE = os.getenv("DEBUG", "false").lower() == "true"

# ---------------------------------------------------------------------------
# Session context import (in-agent-process state tracker)
# ---------------------------------------------------------------------------

from nexa.execution.context import context as _session_ctx

# ---------------------------------------------------------------------------
# Resolve Windows host IP from WSL
# ---------------------------------------------------------------------------

def _get_windows_host_ip() -> str:
    """Resolve Windows host IP when running inside WSL."""
    try:
        result = subprocess.run(
            "ip route show default | awk '{print $3}'",
            shell=True, capture_output=True, text=True, timeout=2,
        )
        ip = result.stdout.strip()
        if ip:
            logger.info("Resolved Windows host IP via gateway: %s", ip)
            return ip
    except Exception as exc:
        logger.warning("Gateway resolution failed: %s — trying fallback", exc)

    try:
        with open("/etc/resolv.conf") as f:
            for line in f:
                if "nameserver" in line:
                    ip = line.split()[1]
                    logger.info("Resolved Windows host IP via nameserver: %s", ip)
                    return ip
    except Exception:
        pass

    return "127.0.0.1"


def _mcp_server_url() -> str:
    url = f"http://127.0.0.1:{MCP_SERVER_PORT}/sse"
    logger.info("MCP Server URL: %s", url)
    return url


# ---------------------------------------------------------------------------
# Provider builders
# ---------------------------------------------------------------------------

def _build_stt():
    if STT_PROVIDER == "whisper":
        logger.info("STT → OpenAI Whisper")
        return lk_openai.STT(model="whisper-1")
    raise ValueError(f"Unknown STT_PROVIDER: {STT_PROVIDER!r}")


def _build_llm():
    if LLM_PROVIDER == "openai":
        logger.info("LLM → OpenAI (%s)", OPENAI_LLM_MODEL)
        return lk_openai.LLM(model=OPENAI_LLM_MODEL)
    if LLM_PROVIDER == "gemini":
        logger.info("LLM → Google Gemini (%s)", GEMINI_LLM_MODEL)
        return lk_google.LLM(model=GEMINI_LLM_MODEL, api_key=os.getenv("GOOGLE_API_KEY"))
    raise ValueError(f"Unknown LLM_PROVIDER: {LLM_PROVIDER!r}")


def _build_tts():
    if TTS_PROVIDER == "openai":
        logger.info("TTS → OpenAI TTS (%s / %s)", OPENAI_TTS_MODEL, OPENAI_TTS_VOICE)
        return lk_openai.TTS(model=OPENAI_TTS_MODEL, voice=OPENAI_TTS_VOICE, speed=TTS_SPEED)
    raise ValueError(f"Unknown TTS_PROVIDER: {TTS_PROVIDER!r}")


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class NexaAgent(Agent):
    """
    NEXA — Neural EXecution Agent.

    Extends LiveKit's Agent with:
    - Full decision-loop system prompt (interpret → decide → act → verify → respond)
    - Session context tracking (intent, tool state, turn count)
    - Debug trace output when DEBUG=true
    - MCP tool orchestration via FastMCP/SSE
    """

    def __init__(self, stt, llm, tts) -> None:
        super().__init__(
            instructions=SYSTEM_PROMPT,
            stt=stt,
            llm=llm,
            tts=tts,
            vad=silero.VAD.load(),
            mcp_servers=[
                mcp.MCPServerHTTP(
                    url=_mcp_server_url(),
                    transport_type="sse",
                    client_session_timeout_seconds=30,
                ),
            ],
        )
        self._ctx = _session_ctx
        logger.info("NexaAgent initialized | debug=%s", DEBUG_MODE)

    async def on_enter(self) -> None:
        """Session open — track turn and deliver greeting."""
        self._ctx.new_turn()
        logger.info(
            "Session started | turn=%d | uptime=%.1fs",
            self._ctx.turn_count,
            self._ctx.uptime_seconds(),
        )
        await self.session.generate_reply(
            instructions=(
                "Greet with exactly: 'NEXA online. What do you need?' "
                "Crisp. No additions."
            )
        )


# ---------------------------------------------------------------------------
# LiveKit pipeline helpers
# ---------------------------------------------------------------------------

def _turn_detection() -> str:
    return "vad"


def _endpointing_delay() -> float:
    # Whisper needs a bit more silence before committing to a transcript
    return {"whisper": 0.3}.get(STT_PROVIDER, 0.1)


# ---------------------------------------------------------------------------
# LiveKit entry point
# ---------------------------------------------------------------------------

async def entrypoint(ctx: JobContext) -> None:
    logger.info(
        "NEXA online — room: %s | STT=%s | LLM=%s | TTS=%s | debug=%s",
        ctx.room.name, STT_PROVIDER, LLM_PROVIDER, TTS_PROVIDER, DEBUG_MODE,
    )

    stt = _build_stt()
    llm = _build_llm()
    tts = _build_tts()

    session = AgentSession(
        turn_detection=_turn_detection(),
        min_endpointing_delay=_endpointing_delay(),
    )

    await session.start(
        agent=NexaAgent(stt=stt, llm=llm, tts=tts),
        room=ctx.room,
    )


# ---------------------------------------------------------------------------
# CLI entry points
# ---------------------------------------------------------------------------

def main():
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))


def dev():
    """Run in dev mode (auto-injects 'dev' CLI flag)."""
    import sys
    if len(sys.argv) == 1:
        sys.argv.append("dev")
    main()


if __name__ == "__main__":
    main()
