"""Raw-HTML fetch built on AAA's SSRF-hardened SafeFetcher.

SafeFetcher turns HTML into text before returning, but we also need the markup (images, links, embeds).
We reuse its `_target` (scheme/port/host/DNS vetting + pinned address) and redirect/size rules."""

from __future__ import annotations

import asyncio
from urllib.parse import urljoin

import httpx
from aaa.ingest.loaders import html_to_text
from aaa.tools import ToolError, tool
from aaa.tools.builtin.net import FetchBlocked, FetchPolicy, FetchResult, SafeFetcher
from bs4 import BeautifulSoup

from botshub_agent.db.repo import Repo

TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain", "text/markdown", "application/json")


class RawFetcher(SafeFetcher):
    async def _fetch(self, url, extra_headers):  # type: ignore[override]
        p = self.policy
        hops: list[str] = []
        timeout = httpx.Timeout(p.timeout_s, connect=p.connect_timeout_s)
        async with httpx.AsyncClient(
            transport=self._transport, follow_redirects=False, trust_env=False,
            timeout=timeout, verify=self._verify,
        ) as client:
            current = url
            for _ in range(p.max_redirects + 1):
                connect, host, host_header = await self._target(current)
                hdrs = {"user-agent": p.user_agent, "accept": "text/html,text/plain;q=0.9,*/*;q=0.1",
                        **(extra_headers or {}), "host": host_header}
                ext = {"sni_hostname": host} if connect.scheme == "https" else {}
                async with client.stream("GET", connect, headers=hdrs, extensions=ext) as resp:
                    if resp.is_redirect and (loc := resp.headers.get("location")):
                        nxt = urljoin(current, loc)
                        if httpx.URL(current).scheme == "https" and httpx.URL(nxt).scheme == "http":
                            raise FetchBlocked("a redirect from https to http was refused")
                        hops.append(nxt)
                        current = nxt
                        continue
                    ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                    if resp.status_code < 400 and ctype and not any(ctype.startswith(t) for t in TEXT_TYPES):
                        raise FetchBlocked(f"content type {ctype!r} is not allowed")
                    body, truncated = bytearray(), False
                    async for chunk in resp.aiter_bytes():
                        body += chunk
                        if len(body) > p.max_bytes:
                            del body[p.max_bytes:]
                            truncated = True
                            break
                    try:
                        raw = bytes(body).decode(resp.charset_encoding or "utf-8", errors="replace")
                    except LookupError:
                        raw = bytes(body).decode("utf-8", errors="replace")
                    return FetchResult(current, resp.status_code, ctype, raw, truncated, hops)
        raise FetchBlocked(f"more than {p.max_redirects} redirects")


def page_text(raw: str, ctype: str) -> str:
    return html_to_text(raw)[0] if ctype in ("text/html", "application/xhtml+xml") else raw


def extract_links(raw: str, base: str) -> list[dict]:
    soup = BeautifulSoup(raw, "html.parser")
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = urljoin(base, a["href"]).split("#")[0]
        if href.startswith("http") and href not in seen:
            seen.add(href)
            out.append({"url": href, "text": a.get_text(" ", strip=True)[:80]})
    return out


def make_fetch_tool(repo: Repo, fetcher: SafeFetcher | None = None):
    fetcher = fetcher or RawFetcher(FetchPolicy())

    @tool(timeout_s=45, pool="fetch")
    async def fetch_page(candidate_id: int, url: str, kind: str = "page") -> dict:
        """Fetch a public web page for a candidate and store it (text + raw HTML). Returns only metadata."""
        try:
            r = await fetcher.fetch(url)
        except FetchBlocked as e:
            raise ToolError(str(e)) from e
        except httpx.HTTPError as e:
            raise ToolError(f"fetch failed: {type(e).__name__}") from e
        text = page_text(r.text, r.content_type)
        changed = False
        if r.status < 400 and text.strip():
            changed = await asyncio.to_thread(
                repo.save_page, candidate_id, r.url, kind, r.status, text,
                r.text if r.content_type.startswith(("text/html", "application/xhtml")) else "",
            )
        return {"url": r.url, "status": r.status, "chars": len(text), "changed": changed}

    return fetch_page
