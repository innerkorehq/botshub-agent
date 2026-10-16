"""Gap-filler, price vote and sqlite-vec merge."""

import copy
import json

import httpx
from aaa.llm import FakeLLMClient
from aaa.llm.fake import text, tool_call
from aaa.tools.builtin.net import FetchPolicy

from botshub_agent.config import Settings
from botshub_agent.discovery.ingest import ingest_signals
from botshub_agent.discovery.semantic import semantic_merge
from botshub_agent.enrich.pipeline import enrich_candidate
from botshub_agent.runtime import runtime
from botshub_agent.tools.fetch_html import RawFetcher
from tests.test_pipeline import EXTRACTION, HOME, resolver

HOME_NO_PRICING = HOME.replace('<a href="/pricing">Pricing</a>', '<a href="/cost-sheet">Cost sheet</a>')
COSTS = "<html><body><p>Starter plan costs $35/month.</p></body></html>"


def web(request: httpx.Request) -> httpx.Response:
    h = {"content-type": "text/html"}
    if request.url.path in ("/", ""):
        return httpx.Response(200, text=HOME_NO_PRICING, headers=h)
    if request.url.path == "/cost-sheet":
        return httpx.Response(200, text=COSTS, headers=h)
    if request.url.path.endswith(".png"):
        return httpx.Response(200, headers={"content-type": "image/png", "content-length": "90000"})
    return httpx.Response(404, text="nope", headers=h)


def make_llm(price_answer="$35/month"):
    calls = {"loop": 0}

    def llm(req):
        system = req.messages[0].content if req.messages[0].role == "system" else ""
        prompt = " ".join(m.content or "" for m in req.messages if isinstance(m.content, str))
        if "extract facts" in system:
            ex = copy.deepcopy(EXTRACTION)
            ex["pricing"] = []
            ex["integrations"] = []
            if "cost-sheet" in prompt:  # second pass: the stored page is now in the prompt
                ex["pricing"] = [{"name": "Starter", "price": "$35/month", "includes": [],
                                  "source_url": "https://zorbo.ai/cost-sheet", "quote": "Starter plan costs $35/month"}]
            return text(json.dumps(ex))
        if "find missing information pages" in system:
            calls["loop"] += 1
            if req.messages[-1].role == "tool":
                return text("stored the pricing page") if "zorbo.ai" in (req.messages[-1].content or "") else \
                    tool_call("read_page", url="https://zorbo.ai/cost-sheet", kind="pricing")
            return tool_call("read_page", url="https://evil.example/pricing", kind="pricing")
        if "pricing question" in system:
            return text(f"Answer: {price_answer}")
        if "fact-checker" in system:
            return text(json.dumps({"ok": True, "feedback": ""}))
        return text("## What you get\nZorbo triages your support inbox automatically. [f2]")

    return llm, calls


async def run(tmp_path, price_answer):
    s = Settings(data_dir=tmp_path, min_features=3, min_quality=0.5)
    fetcher = RawFetcher(FetchPolicy(), resolver=resolver, transport=httpx.MockTransport(web))
    llm, calls = make_llm(price_answer)
    async with runtime(s, llm_client=FakeLLMClient(llm), fetcher=fetcher) as rt:
        ingest_signals(rt.repo, [[{"source": "searxng", "url": "https://zorbo.ai", "name": "Zorbo", "metrics": {}}]])
        cid = rt.repo.candidates()[0]["id"]
        res = await enrich_candidate(rt, cid)
        pages = [p["url"] for p in rt.repo.pages(cid)]
        with rt.repo.conn() as c:
            log = c.execute("SELECT reasons FROM publish_log").fetchone()["reasons"]
        return res, json.loads(rt.repo.agent_by_candidate(cid)["listing"]), pages, log, calls


async def test_gapfill_finds_pricing_on_own_site_only(tmp_path):
    res, listing, pages, log, calls = await run(tmp_path, "$35/month")
    assert "https://zorbo.ai/cost-sheet" in pages          # found by the loop
    assert not any("evil.example" in p for p in pages)      # off-site read was refused
    assert [p["price"] for p in listing["pricing"]] == ["$35/month"]
    assert "gap-filler ran" in log


async def test_unconfirmed_price_is_dropped(tmp_path):
    res, listing, _, log, _ = await run(tmp_path, "$29/month")  # the vote disagrees with the extraction
    assert listing["pricing"] == [] and listing["pricing_badge"] is None
    assert "not confirmed" in log


class EmbedFake(FakeLLMClient):
    VEC = {"zorbo": [1.0, 0.0, 0.0], "zorbo ai": [0.99, 0.05, 0.0], "other": [0.0, 1.0, 0.0]}

    async def embed(self, texts, model=None):
        from aaa.llm import EmbedResponse
        return EmbedResponse(vectors=[self.VEC[t.split(".")[0].lower()] for t in texts], model="fake")


async def test_semantic_merge_joins_repo_and_site_but_not_strangers(tmp_path):
    s = Settings(data_dir=tmp_path, embed_model="fake", embed_dim=3, semantic_threshold=0.9)
    async with runtime(s, llm_client=EmbedFake(lambda r: text("x"))) as rt:
        ingest_signals(rt.repo, [[
            {"source": "github", "url": "https://github.com/a/zorbo", "name": "zorbo", "description": "inbox", "metrics": {"stars": 5}},
            {"source": "searxng", "url": "https://getzorbo.io", "name": "Zorbo AI", "description": "inbox", "metrics": {}},
            {"source": "searxng", "url": "https://other.com", "name": "other", "description": "x", "metrics": {}},
        ]])
        assert len(rt.repo.candidates()) == 3                 # the rule-based pass could not link them
        out = await semantic_merge(rt)
        assert out["merged"] == 1, out
        keys = {c["key"] for c in rt.repo.candidates()}
        assert "web:other.com" in keys and len(keys) == 2
        kept = next(c for c in rt.repo.candidates() if c["key"] != "web:other.com")
        assert kept["website"] == "https://getzorbo.io" and kept["repo_url"].endswith("a/zorbo")
        assert rt.repo.sources_for(kept["id"]) >= {"github", "searxng"}
