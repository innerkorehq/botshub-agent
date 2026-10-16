"""Tools for the gap-filler loop. The model may only read pages on the candidate's OWN site or repo:
the allowed hosts come from our database (run metadata), never from the model."""

from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

import httpx
from aaa.tools import ToolContext, ToolError, tool
from aaa.tools.builtin import SearXNGSearch
from aaa.tools.builtin.net import FetchBlocked

from botshub_agent.db.repo import Repo
from botshub_agent.discovery.resolve import host_of
from botshub_agent.enrich.extract import clean_page
from botshub_agent.tools.fetch_html import page_text


def allowed(url: str, cand: dict) -> bool:
    u = urlsplit(url)
    if u.scheme != "https":
        return False
    h = host_of(url)
    site = host_of(cand.get("website") or "")
    if site and (h == site or h.endswith("." + site)):
        return True
    key = cand.get("key", "")
    return key.startswith("gh:") and h == "github.com" and u.path.lower().startswith(f"/{key[3:]}")


def make_research_tools(repo: Repo, fetcher, searx: SearXNGSearch):
    def cand_of(ctx: ToolContext) -> dict:
        c = repo.candidate(int(ctx.meta.get("candidate_id", 0)))
        if not c:
            raise ToolError("no candidate bound to this run")
        return c

    @tool(timeout_s=30, pool="searxng")
    async def site_search(query: str, ctx: ToolContext) -> list[dict]:
        """Search the product's own website/repo for a topic (e.g. 'pricing plans'). Returns url/title/snippet."""
        c = cand_of(ctx)
        host = host_of(c.get("website") or "")
        q = f"site:{host} {query}" if host else f"{c['name']} {query}"
        res = await searx.search(q, 10)
        return [{"url": r.url, "title": r.title, "snippet": clean_page(r.snippet)}
                for r in res if allowed(r.url, c)][:6]

    @tool(timeout_s=45, pool="fetch")
    async def read_page(url: str, ctx: ToolContext, kind: str = "docs") -> dict:
        """Fetch a page on the product's own site or repo and store it as a source. Returns a short preview only."""
        c = cand_of(ctx)
        if not allowed(url, c):
            raise ToolError("only pages on the product's own website or GitHub repo may be read")
        try:
            r = await fetcher.fetch(url)
        except FetchBlocked as e:
            raise ToolError(str(e)) from e
        except httpx.HTTPError as e:
            raise ToolError(f"fetch failed: {type(e).__name__}") from e
        if not allowed(r.url, c):
            raise ToolError("redirected off the product's own site")
        text = page_text(r.text, r.content_type)
        if r.status >= 400 or not text.strip():
            raise ToolError(f"no usable content (HTTP {r.status})")
        kind = kind if kind in ("pricing", "features", "integrations", "docs") else "docs"
        await asyncio.to_thread(repo.save_page, c["id"], r.url, kind, r.status, text,
                                r.text if r.content_type.startswith("text/html") else "")
        return {"url": r.url, "chars": len(text), "preview": clean_page(text)[:400]}

    return [site_search, read_page]
