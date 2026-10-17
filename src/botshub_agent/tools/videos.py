"""Find demo videos for a product on YouTube, then prove they are about it before using them."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

import httpx
from aaa.tools import tool
from aaa.tools.builtin import SearXNGSearch

from botshub_agent.enrich.grounding import norm

OEMBED = "https://www.youtube.com/oembed"
GENERIC = {"agent", "agents", "ai", "app", "tool", "tools", "open", "source", "mcp", "server", "cli", "the"}


def youtube_id(url: str) -> str | None:
    u = urlsplit(url)
    host = (u.hostname or "").removeprefix("www.")
    if host == "youtu.be":
        return u.path.strip("/") or None
    if host in ("youtube.com", "m.youtube.com"):
        if u.path == "/watch":
            return (parse_qs(u.query).get("v") or [None])[0]
        m = re.match(r"/(?:embed|shorts)/([\w-]{6,})", u.path)
        return m.group(1) if m else None
    return None


def title_is_about(title: str, author: str, name: str) -> bool:
    """The product name (squashed) must appear in the video title or channel name. Names made only of generic words never match."""
    key = re.sub(r"[^a-z0-9]", "", norm(name))
    words = [w for w in re.findall(r"[a-z0-9]+", norm(name)) if w not in GENERIC]
    if len(key) < 4 or not words:
        return False
    hay = re.sub(r"[^a-z0-9]", "", norm(f"{title} {author}"))
    return key in hay


def make_video_tool(provider: SearXNGSearch, transport: httpx.AsyncBaseTransport | None = None):
    @tool(timeout_s=60, pool="searxng")
    async def find_product_videos(name: str, limit: int = 2) -> list[dict]:
        """Search for demo/overview videos of a product; keep only YouTube videos whose title or channel names it (checked via oEmbed)."""
        res = await provider.search(f'"{name}" demo OR overview OR tutorial', 15)
        out, seen = [], set()
        async with httpx.AsyncClient(timeout=10, transport=transport) as c:
            for r in res:
                vid = youtube_id(r.url)
                if not vid or vid in seen:
                    continue
                seen.add(vid)
                try:
                    j = (await c.get(OEMBED, params={"url": f"https://www.youtube.com/watch?v={vid}", "format": "json"})).json()
                except (httpx.HTTPError, ValueError):
                    continue
                if title_is_about(j.get("title", ""), j.get("author_name", ""), name):
                    out.append({"url": f"https://www.youtube.com/embed/{vid}", "source_url": f"https://www.youtube.com/watch?v={vid}",
                                "title": j["title"][:150], "author": j.get("author_name", "")})
                if len(out) >= limit:
                    break
        return out

    return find_product_videos
