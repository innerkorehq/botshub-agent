"""Discovery collectors. Each returns a list of plain signal dicts:
{source, url, name, description, metrics: {...}}. Network failures raise ToolError (the DAG continues)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
from aaa.tools import ToolError, tool
from aaa.tools.builtin import SearXNGSearch

UA = "botshub-agent/0.1 (+https://botshub.com)"


def github_tool(token: str = "", transport: httpx.AsyncBaseTransport | None = None):
    headers = {"accept": "application/vnd.github+json", "user-agent": UA}
    if token:
        headers["authorization"] = f"Bearer {token}"

    @tool(timeout_s=30, pool="github")
    async def gh_search(topic: str, pushed_within_days: int = 30, limit: int = 30) -> list[dict]:
        """Search GitHub repositories by topic, recently pushed, most starred first."""
        since = (datetime.now(UTC) - timedelta(days=pushed_within_days)).date().isoformat()
        q = f"topic:{topic} pushed:>{since} stars:>20 archived:false"
        async with httpx.AsyncClient(headers=headers, timeout=20, transport=transport) as c:
            r = await c.get(
                "https://api.github.com/search/repositories",
                params={"q": q, "sort": "stars", "order": "desc", "per_page": min(limit, 100)},
            )
        if r.status_code in (403, 429):
            raise ToolError("GitHub rate limit; set GITHUB_TOKEN")
        if r.status_code >= 400:
            raise ToolError(f"GitHub HTTP {r.status_code}")
        out = []
        for it in r.json().get("items", []):
            out.append(
                {
                    "source": "github",
                    "url": it["html_url"],
                    "name": it["name"],
                    "description": it.get("description") or "",
                    "website": it.get("homepage") or "",
                    "metrics": {"stars": it["stargazers_count"], "forks": it["forks_count"]},
                }
            )
        return out

    return gh_search


def hn_tool(transport: httpx.AsyncBaseTransport | None = None):
    @tool(timeout_s=30, pool="hn")
    async def hn_search(query: str, days: int = 14, limit: int = 30) -> list[dict]:
        """Hacker News stories matching a query from the last `days` days (Algolia API)."""
        since = int((datetime.now(UTC) - timedelta(days=days)).timestamp())
        async with httpx.AsyncClient(timeout=20, transport=transport) as c:
            r = await c.get(
                "https://hn.algolia.com/api/v1/search",
                params={"query": query, "tags": "story", "hitsPerPage": limit,
                        "numericFilters": f"created_at_i>{since},points>5"},
            )
        if r.status_code >= 400:
            raise ToolError(f"HN HTTP {r.status_code}")
        out = []
        for h in r.json().get("hits", []):
            if not h.get("url"):
                continue
            title = h.get("title") or ""
            out.append(
                {
                    "source": "hn",
                    "url": h["url"],
                    "name": title.removeprefix("Show HN: ").split(" – ")[0].split(" - ")[0][:80],
                    "description": title,
                    "metrics": {"hn_points": h.get("points") or 0, "hn_comments": h.get("num_comments") or 0},
                }
            )
        return out

    return hn_search


def searxng_tool(provider: SearXNGSearch):
    @tool(timeout_s=30, pool="searxng")
    async def searx_search(query: str, limit: int = 20) -> list[dict]:
        """Web search through our SearXNG instance; results are candidate pages."""
        res = await provider.search(query, limit)
        return [
            {"source": "searxng", "url": r.url, "name": r.title[:80], "description": r.snippet, "metrics": {}}
            for r in res
        ]

    return searx_search


def corroboration_tool(provider: SearXNGSearch):
    from botshub_agent.discovery.resolve import host_of

    @tool(timeout_s=30, pool="searxng")
    async def count_mentions(name: str, own_host: str = "") -> int:
        """Number of distinct other domains that surface when searching for the product name."""
        res = await provider.search(f'"{name}" AI agent', 20)
        hosts = {host_of(r.url) for r in res} - {own_host, ""}
        return len(hosts)

    return count_mentions
