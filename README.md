# NEXA — Neural EXecution Agent

> *A real-time AI agent capable of executing actions through natural language using MCP.*

NEXA is split into two cooperating processes:

| Component | What it is |
|-----------|-----------|
| **MCP Server** (`uv run nexa`) | A [FastMCP](https://github.com/jlowin/fastmcp) server that exposes tools (news, web fetch, system info, …) over SSE. The execution backend — it does the actual work. |
| **Voice Agent** (`uv run nexa_voice`) | A [LiveKit Agents](https://github.com/livekit/agents) voice pipeline that listens to your microphone, reasons with an LLM (Gemini 2.5 Flash by default), and speaks back with OpenAI TTS — while pulling tools from the MCP server in real time. |

---

## How it works

```
Microphone ──► STT (Sarvam Saaras v3 / Whisper)
                    │
                    ▼
             LLM (Gemini 2.5 Flash / GPT-4o)  ◄──────► MCP Server (FastMCP / SSE)
                    │                                        ├─ get_world_news
                    ▼                                        ├─ open_world_monitor
             TTS (OpenAI nova / Sarvam)                      ├─ search_web
                    │                                        ├─ fetch_url
                    ▼                                        └─ …more tools
             Speaker / LiveKit room
```

The voice agent connects to the MCP server via SSE at `http://127.0.0.1:8000/sse`
(auto-resolved to the Windows host IP when running inside WSL).

---

## Project structure

```
nexa/
├── server.py           # uv run nexa       → starts the MCP server (SSE on :8000)
├── agent_nexa.py       # uv run nexa_voice → starts the LiveKit voice agent
├── pyproject.toml
├── .env.example        # copy → .env and fill in your keys
│
└── nexa/               # MCP server package
    ├── config.py       # env-var loading & app-wide settings
    ├── tools/          # MCP tools (callable by the LLM)
    │   ├── web.py      # get_world_news, open_world_monitor, search_web, fetch_url
    │   ├── system.py   # get_current_time, get_system_info
    │   └── utils.py    # format_json, word_count
    ├── prompts/        # MCP prompt templates (summarize, explain_code, …)
    └── resources/      # MCP resources exposed to clients (nexa://info)
```

---

## Quick start

### 1. Prerequisites

- Python ≥ 3.11
- [`uv`](https://github.com/astral-sh/uv) — `pip install uv` or `curl -Lsf https://astral.sh/uv/install.sh | sh`
- A [LiveKit Cloud](https://cloud.livekit.io) project (free tier works)

### 2. Clone & install

```bash
git clone <your-repo-url>
cd nexa
uv sync          # creates .venv and installs all dependencies
```

### 3. Set up environment

```bash
cp .env.example .env
# Open .env and fill in your API keys (see the table below)
```

### 4. Run — two terminals

**Terminal 1 — MCP server** (must start first)

```bash
uv run nexa
```

Starts the FastMCP server on `http://127.0.0.1:8000/sse`.
The voice agent connects here to fetch its tools.

**Terminal 2 — Voice agent**

```bash
uv run nexa_voice
```

Starts the LiveKit voice agent in **dev mode** — it joins a LiveKit room and begins listening.
Open the [LiveKit Agents Playground](https://agents-playground.livekit.io) and connect to your room to talk to NEXA.

---

## Commands reference

| Command | Entry point | What it does |
|---------|------------|--------------|
| `uv run nexa` | `server.py → main()` | Launches the **FastMCP MCP server** over SSE on port 8000. Registers all tools, prompts, and resources the LLM can call. |
| `uv run nexa_voice` | `agent_nexa.py → dev()` | Launches the **LiveKit voice agent**. Builds the STT / LLM / TTS pipeline, connects to your LiveKit room, and wires the MCP server as a tool source. The `dev()` wrapper auto-injects the `dev` CLI flag. |

> Both processes must run **simultaneously**. The voice agent calls the MCP server in real time whenever it needs a tool.

---

## Environment variables

Copy `.env.example` → `.env` and fill in the values below.

| Variable | Required | Where to get it |
|----------|----------|----------------|
| `LIVEKIT_URL` | ✅ | [LiveKit Cloud dashboard](https://cloud.livekit.io) → your project URL |
| `LIVEKIT_API_KEY` | ✅ | LiveKit Cloud → API Keys |
| `LIVEKIT_API_SECRET` | ✅ | LiveKit Cloud → API Keys |
| `OPENAI_API_KEY` | ✅ | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) — for TTS (nova) and Whisper STT |
| `GOOGLE_API_KEY` | ✅ (default LLM) | [aistudio.google.com](https://aistudio.google.com/projects) |
| `GROQ_API_KEY` | optional | [console.groq.com](https://console.groq.com) — only if `LLM_PROVIDER = "groq"` |
| `DEEPGRAM_API_KEY` | optional | [console.deepgram.com](https://console.deepgram.com) |
| `GOOGLE_APPLICATION_CREDENTIALS` | optional | GCP service-account JSON — only for `STT_PROVIDER = "google"` |
| `SUPABASE_URL` | optional | [supabase.com](https://supabase.com) — for the ticketing tool |
| `SUPABASE_API_KEY` | optional | Supabase project → API settings |
| `SERVER_NAME` | optional | Defaults to `"NEXA"` |
| `DEBUG` | optional | Set `"true"` for verbose logging |

---

## Switching providers

Open [agent_nexa.py](agent_nexa.py) and change the provider constants at the top:

```python
STT_PROVIDER = "whisper"   # "whisper" | "deepgram"
LLM_PROVIDER = "openai"    # "openai"  | "gemini"
TTS_PROVIDER = "openai"    # "openai"
```

---

## Adding a new tool

1. Create or open a file in `nexa/tools/`
2. Define a `register(mcp)` function and decorate tools with `@mcp.tool()`
3. Import and call `register(mcp)` inside `nexa/tools/__init__.py`

The MCP server will pick it up on next restart.

```python
# nexa/tools/my_tool.py
import logging
logger = logging.getLogger("nexa.tools.my_tool")

def register(mcp):
    @mcp.tool()
    def my_action(param: str) -> str:
        """What this tool does — shown to the LLM."""
        return f"Result: {param}"
```

---

## Tech stack

- **[FastMCP](https://github.com/jlowin/fastmcp)** — MCP server framework
- **[LiveKit Agents](https://github.com/livekit/agents)** — real-time voice pipeline
- **Sarvam Saaras v3** — STT (Indian-English optimised)
- **Google Gemini 2.5 Flash** — LLM (default)
- **OpenAI GPT-4o** — LLM (alternative)
- **OpenAI TTS** (`nova` voice) — TTS (default)
- **[uv](https://github.com/astral-sh/uv)** — fast Python package manager

---

## License

MIT
