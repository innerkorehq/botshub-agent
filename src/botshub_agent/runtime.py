"""Wires the AAA engine: store, LLM, tools, validators, guards. One engine process per events DB."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass

from aaa.events import SQLiteEventStore
from aaa.llm import LLMConfig, LLMHandler, build_client
from aaa.runtime import Engine
from aaa.tools import ToolHandler, ToolRegistry
from aaa.tools.builtin import SearXNGSearch
from aaa.tools.builtin.net import FetchPolicy
from aaa.validate import ValidationHandler, json_validator

from botshub_agent.config import Settings
from botshub_agent.db.repo import Repo
from botshub_agent.discovery.classify import validator as classify_validator
from botshub_agent.enrich.grounding import make_validator
from botshub_agent.models import Classification
from botshub_agent.tools.fetch_html import RawFetcher, make_fetch_tool
from botshub_agent.tools.github_meta import make_github_readme_tool
from botshub_agent.tools.sources import corroboration_tool, github_tool, hn_tool, searxng_tool


@dataclass
class Runtime:
    engine: Engine
    repo: Repo
    settings: Settings
    fetcher: RawFetcher
    registry: ToolRegistry
    algos: object = None
    llm: object = None


def llm_config(s: Settings, model: str | None = None) -> LLMConfig:
    return LLMConfig(model=model or s.llm_model, base_url=s.llm_base_url, api_key=s.llm_api_key or None,
                     price_in_per_mtok=s.price_in_per_mtok, price_out_per_mtok=s.price_out_per_mtok, timeout_s=120)


def build_registry(repo: Repo, s: Settings, fetcher: RawFetcher) -> ToolRegistry:
    searx = SearXNGSearch(s.searxng_url)
    from botshub_agent.tools.research import make_research_tools
    return ToolRegistry([
        *make_research_tools(repo, fetcher, searx),
        github_tool(s.github_token), hn_tool(), searxng_tool(searx), corroboration_tool(searx),
        make_fetch_tool(repo, fetcher), make_github_readme_tool(repo, s.github_token),
        _ingest_tool(repo, s),
    ])


def _ingest_tool(repo: Repo, s: Settings):
    import asyncio
    from aaa.tools import tool
    from botshub_agent.discovery.ingest import ingest_signals, rescore

    @tool(timeout_s=120, pool="db")
    async def ingest_and_score(batches: list[list[dict] | None]) -> dict:
        """Write collected signals as candidates and recompute popularity scores."""
        def run():
            stats = ingest_signals(repo, [b or [] for b in batches])
            stats["scored"] = rescore(repo, s)
            return stats
        return await asyncio.to_thread(run)

    return ingest_and_score


@asynccontextmanager
async def runtime(s: Settings | None = None, llm_client=None, fetcher: RawFetcher | None = None):
    s = s or Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    repo = Repo(s.botshub_db)
    fetcher = fetcher or RawFetcher(FetchPolicy())
    registry = build_registry(repo, s, fetcher)
    store = SQLiteEventStore(s.events_db)

    def pages_for(ctx: dict) -> dict[str, str]:
        cid = ctx["task"]["candidate_id"] if isinstance(ctx.get("task"), dict) else None
        return {p["url"]: p["text"] for p in repo.pages(cid)} if cid else {}

    client = llm_client or build_client(llm_config(s))
    async with Engine(store=store) as engine:
        LLMHandler(client).attach(engine)
        ToolHandler(registry, pools={"default": 16, "github": 2, "hn": 2, "searxng": 2, "fetch": 6, "db": 1}).attach(engine)
        ValidationHandler({"facts": make_validator(pages_for), "classify": classify_validator,
                           "default": json_validator(Classification)}).attach(engine)
        try:
            from botshub_agent.algos import make_algos
            yield Runtime(engine, repo, s, fetcher, registry, make_algos(s, registry), client)
        finally:
            pass
    await store.aclose()
