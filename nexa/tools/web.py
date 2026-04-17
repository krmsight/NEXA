"""
Web tools — news briefing, web search, URL fetching, world monitor.

All tools:
  - Use run_with_retry for resilient network calls (2 retries, exponential backoff)
  - Never raise exceptions to the LLM — always return a safe string fallback
  - Log every outcome through the shared NexaTrace
"""

import asyncio
import logging
import platform
import re
import subprocess
import xml.etree.ElementTree as ET

import httpx

from nexa.execution.engine import run_with_retry
from nexa.execution.logger import trace

logger = logging.getLogger("nexa.tools.web")

# ---------------------------------------------------------------------------
# News feed configuration
# ---------------------------------------------------------------------------

SEED_FEEDS = [
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://www.cnbc.com/id/100727362/device/rss/rss.html",
    "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
    "https://www.aljazeera.com/xml/rss/all.xml",
]

ITEMS_PER_FEED     = 5
MAX_BRIEFING_ITEMS = 12

_FEED_TIMEOUT  = 5.0    # per-feed HTTP timeout (seconds)
_TOTAL_TIMEOUT = 12.0   # hard ceiling for gathering all feeds


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _fetch_and_parse_feed(client: httpx.AsyncClient, url: str) -> list[dict]:
    """Fetch a single RSS feed; returns [] on any failure (never raises)."""
    try:
        response = await client.get(
            url,
            headers={"User-Agent": "NEXA-Agent/1.0"},
            timeout=_FEED_TIMEOUT,
        )
        if response.status_code != 200:
            logger.warning("Feed %s → HTTP %s", url, response.status_code)
            return []

        root        = ET.fromstring(response.content)
        source_name = url.split(".")[1].upper()

        items: list[dict] = []
        for item in root.findall(".//item")[:ITEMS_PER_FEED]:
            title       = item.findtext("title") or "(no title)"
            description = item.findtext("description") or ""
            link        = item.findtext("link") or ""

            if description:
                description = re.sub(r"<[^<]+?>", "", description).strip()

            items.append({
                "source":  source_name,
                "title":   title,
                "summary": (description[:200] + "…") if description else "",
                "link":    link,
            })

        return items

    except ET.ParseError as exc:
        logger.error("XML parse error [%s]: %s", url, exc)
        return []
    except httpx.TimeoutException:
        logger.warning("Timeout fetching feed: %s", url)
        return []
    except httpx.RequestError as exc:
        logger.error("Network error [%s]: %s", url, exc)
        return []


async def _gather_all_feeds() -> list[dict]:
    """Fetch all seed feeds in parallel; returns up to MAX_BRIEFING_ITEMS articles."""
    async with httpx.AsyncClient(follow_redirects=True) as client:
        tasks   = [_fetch_and_parse_feed(client, url) for url in SEED_FEEDS]
        results = await asyncio.gather(*tasks, return_exceptions=False)

    articles = [item for sublist in results for item in sublist]
    return articles[:MAX_BRIEFING_ITEMS]


async def _fetch_url_content(url: str) -> str:
    """Fetch URL text; raises on HTTP error so the retry wrapper can catch it."""
    async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
        response = await client.get(url, headers={"User-Agent": "NEXA-Agent/1.0"})
        response.raise_for_status()
        return response.text[:4000]


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def register(mcp):

    @mcp.tool()
    async def get_world_news() -> str:
        """
        Fetch current global headlines from major outlets simultaneously.
        Use when the user asks about news, current events, or world updates.
        Returns a structured briefing ready for spoken delivery.
        """
        trace.intent("action", "get_world_news")

        articles, success = await run_with_retry(
            _gather_all_feeds,
            max_retries=2,
            timeout_seconds=_TOTAL_TIMEOUT,
            backoff_seconds=0.5,
            fallback=[],
            tool_name="get_world_news",
        )

        if not articles:
            trace.error("get_world_news", "all feeds unreachable", "returning fallback message")
            return (
                "Global news feeds are currently unreachable. "
                "Try again in a moment or ask me to search for a specific topic."
            )

        lines = ["### GLOBAL NEWS BRIEFING\n"]
        for entry in articles:
            lines.append(f"**[{entry['source']}]** {entry['title']}")
            if entry["summary"]:
                lines.append(entry["summary"])
            if entry["link"]:
                lines.append(f"Link: {entry['link']}")
            lines.append("")

        trace.step(1, f"assembled {len(articles)} articles from {len(SEED_FEEDS)} feeds", "ok")
        return "\n".join(lines)

    @mcp.tool()
    async def search_web(query: str) -> str:
        """
        Search the web for a given query and return a summary of results.
        Use when the user asks to find, look up, or search for something specific.
        """
        trace.intent("action", f"search_web: {query}")

        if not query or not query.strip():
            return "No search query provided. What would you like me to look up?"

        # TODO: wire in a real search provider (SerpAPI, Brave Search, Google CSE)
        logger.info("search_web stub called for query: %r", query)
        return (
            f"[Search stub] Real-time web search for '{query}' is not yet connected. "
            "You can fetch a specific URL directly or ask me to check a known source."
        )

    @mcp.tool()
    async def fetch_url(url: str) -> str:
        """
        Fetch the text content of a URL (first 4 000 characters).
        Use when the user asks to visit, read, or retrieve a specific web page.
        """
        trace.intent("action", f"fetch_url: {url}")

        if not url or not url.strip():
            return "No URL provided. Which page would you like me to fetch?"

        content, success = await run_with_retry(
            _fetch_url_content,
            url,
            max_retries=1,
            timeout_seconds=10.0,
            backoff_seconds=1.0,
            fallback=None,
            tool_name="fetch_url",
        )

        if not success or content is None:
            return (
                f"Couldn't reach {url}. "
                "The page may be down or blocking automated requests."
            )

        trace.step(1, f"fetched {len(content)} chars from {url}", "ok")
        return content

    @mcp.tool()
    async def open_world_monitor() -> str:
        """
        Open the World Monitor dashboard in the system's default browser.
        Always call this after delivering a world news brief.
        """
        trace.intent("action", "open_world_monitor")

        url    = "https://worldmonitor.app/"
        system = platform.system()

        try:
            if system == "Linux":
                # WSL — delegate browser launch to Windows host via cmd.exe
                subprocess.run(
                    ["/mnt/c/Windows/System32/cmd.exe", "/c", "start", "", url],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
            elif system == "Windows":
                subprocess.run(
                    ["cmd", "/c", "start", "", url],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
            else:
                subprocess.run(
                    ["xdg-open", url],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )

            trace.step(1, f"browser launched on {system}", "ok")
            return "Opening World Monitor on your screen."

        except subprocess.TimeoutExpired:
            logger.warning("open_world_monitor: browser launch timed out")
            trace.error("open_world_monitor", "browser launch timed out", "returning manual URL")
            return "The monitor took too long to open — visit worldmonitor.app directly."

        except subprocess.CalledProcessError as exc:
            logger.error("open_world_monitor: %s", exc)
            trace.error("open_world_monitor", str(exc), "returning manual URL")
            return "Couldn't launch the browser automatically. Visit worldmonitor.app to open it manually."
