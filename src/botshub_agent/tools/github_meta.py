"""GitHub repo metadata + README stored as a page, so claims can be quoted from it."""

from __future__ import annotations

import asyncio

import httpx
from aaa.tools import ToolError, tool

from botshub_agent.db.repo import Repo
from botshub_agent.tools.sources import UA


def make_github_readme_tool(repo: Repo, token: str = "", transport: httpx.AsyncBaseTransport | None = None):
    base = {"user-agent": UA, **({"authorization": f"Bearer {token}"} if token else {})}

    @tool(timeout_s=40, pool="github")
    async def github_readme(candidate_id: int, owner_repo: str) -> dict:
        """Fetch a GitHub repo's README (raw markdown) and its metadata, and store the README as a page."""
        async with httpx.AsyncClient(timeout=25, headers=base, transport=transport) as c:
            m = await c.get(f"https://api.github.com/repos/{owner_repo}", headers={"accept": "application/vnd.github+json"})
            if m.status_code >= 400:
                raise ToolError(f"GitHub HTTP {m.status_code}")
            r = await c.get(f"https://api.github.com/repos/{owner_repo}/readme", headers={"accept": "application/vnd.github.raw+json"})
        meta = m.json()
        info = {k: meta.get(k) for k in ("html_url", "description", "homepage", "license", "topics", "stargazers_count", "language")}
        if info["license"]:
            info["license"] = info["license"].get("spdx_id")
        changed = False
        if r.status_code < 400 and r.text.strip():
            changed = await asyncio.to_thread(
                repo.save_page, candidate_id, f"https://github.com/{owner_repo}", "readme", 200, r.text, r.text
            )
        # Repo metadata is also quotable: store as its own small page.
        facts = "\n".join(f"{k}: {v}" for k, v in info.items() if v)
        await asyncio.to_thread(repo.save_page, candidate_id, f"https://api.github.com/repos/{owner_repo}", "github_meta", 200, facts)
        return {"readme": r.status_code < 400, "changed": changed, "homepage": info["homepage"] or "", "topics": info["topics"] or []}

    return github_readme
