"""End-to-end enrichment with a scripted LLM and a mocked web: no network, no tokens."""

import json

import httpx
from aaa.llm import FakeLLMClient
from aaa.llm.fake import text
from aaa.tools.builtin.net import FetchPolicy

from botshub_agent.config import Settings
from botshub_agent.discovery.ingest import ingest_signals
from botshub_agent.enrich.pipeline import enrich_candidate
from botshub_agent.runtime import runtime
from botshub_agent.tools.fetch_html import RawFetcher

HOME = """<html><head><title>Zorbo</title><meta property="og:image" content="/hero.png"></head><body>
<h1>Zorbo: the inbox agent</h1><p>Zorbo triages your support inbox automatically.</p>
<ul><li>Auto-label every incoming ticket</li><li>Draft replies for human approval</li><li>Route chats to the right teammate</li></ul>
<a href="/pricing">Pricing</a><img src="/shot.png" alt="Inbox view"></body></html>"""
PRICING = "<html><body><h2>Pricing</h2><p>Starter plan costs $35/month. Enterprise: contact sales for custom limits.</p><p>Integrates with Slack and Zendesk.</p></body></html>"

EXTRACTION = {
    "name": {"value": "Zorbo", "source_url": "https://zorbo.ai/", "quote": "Zorbo: the inbox agent"},
    "description": {"value": "Zorbo triages your support inbox automatically", "source_url": "https://zorbo.ai/", "quote": "Zorbo triages your support inbox automatically."},
    "features": [
        {"name": "Auto-labeling", "description": "Auto-label every incoming ticket", "group": "Triage", "source_url": "https://zorbo.ai/", "quote": "Auto-label every incoming ticket"},
        {"name": "Draft replies", "description": "Draft replies for human approval", "group": "Replies", "source_url": "https://zorbo.ai/", "quote": "Draft replies for human approval"},
        {"name": "Routing", "description": "Route chats to the right teammate", "group": "Triage", "source_url": "https://zorbo.ai/", "quote": "Route chats to the right teammate"},
        {"name": "Voice calls", "description": "Answers phone calls", "source_url": "https://zorbo.ai/", "quote": "Answers phone calls around the clock"},  # INVENTED
    ],
    "pricing": [{"name": "Starter", "price": "$35/month", "includes": [], "source_url": "https://zorbo.ai/pricing", "quote": "Starter plan costs $35/month"}],
    "integrations": [{"name": "Slack", "source_url": "https://zorbo.ai/pricing", "quote": "Integrates with Slack and Zendesk."}],
    "category": "Customer Service & Support",
}


def llm(req):
    system = req.messages[0].content if req.messages[0].role == "system" else ""
    if "extract facts" in system:
        return text(json.dumps(EXTRACTION))
    if "pricing question" in system:  # the price vote
        return text("The page says Starter plan costs $35/month.\nAnswer: $35/month")
    if "fact-checker" in system:
        return text(json.dumps({"ok": True, "feedback": ""}))
    return text("## What you get\nZorbo triages your support inbox automatically. [f2]\nIt can auto-label every incoming ticket. [f3]")


def web(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path in ("/", ""):
        return httpx.Response(200, text=HOME, headers={"content-type": "text/html"})
    if path == "/pricing":
        return httpx.Response(200, text=PRICING, headers={"content-type": "text/html"})
    if path.endswith(".png"):
        return httpx.Response(200, headers={"content-type": "image/png", "content-length": "90000"})
    return httpx.Response(404, text="nope", headers={"content-type": "text/html"})


async def resolver(host, port):
    return ["93.184.216.34"]


async def test_enrich_publishes_only_grounded_facts(tmp_path):
    s = Settings(data_dir=tmp_path, min_features=3, min_quality=0.5, screenshots=0, find_videos=False)
    fetcher = RawFetcher(FetchPolicy(), resolver=resolver, transport=httpx.MockTransport(web))
    async with runtime(s, llm_client=FakeLLMClient(llm), fetcher=fetcher) as rt:
        ingest_signals(rt.repo, [[{"source": "searxng", "url": "https://zorbo.ai", "name": "Zorbo", "metrics": {}}]])
        cid = rt.repo.candidates()[0]["id"]
        res = await enrich_candidate(rt, cid)
        assert res["decision"] == "published", res
        listing = json.loads(rt.repo.agent_by_candidate(cid)["listing"])
        names = [f["name"] for g in listing["features"].values() for f in g]
        assert sorted(names) == ["Auto-labeling", "Draft replies", "Routing"]  # invented "Voice calls" dropped
        assert [p["price"] for p in listing["pricing"]] == ["$35/month"]
        assert [i["name"] for i in listing["integrations"]] == ["Slack"]
        assert "[f" not in listing["overview"]
        assert any(m["url"] == "https://zorbo.ai/shot.png" for m in listing["media"])
        assert listing["pricing_badge"] == "from $35/month"
        # Every stored fact has a quote that really is on the page it cites.
        pages = {p["url"]: p["text"].lower() for p in rt.repo.pages(cid)}
        with rt.repo.conn() as c:
            for f in c.execute("SELECT * FROM facts"):
                assert f["quote"].lower().rstrip(".") in pages[f["source_url"]]
        # Unchanged sources => no re-extraction
        assert (await enrich_candidate(rt, cid))["decision"] == "unchanged"


async def test_weaker_rerun_never_replaces_a_published_listing(tmp_path):
    mode = {"broken": False}

    def flaky(req):
        return text("not json at all") if mode["broken"] else llm(req)

    s = Settings(data_dir=tmp_path, min_features=3, min_quality=0.5, screenshots=0, find_videos=False)
    fetcher = RawFetcher(FetchPolicy(), resolver=resolver, transport=httpx.MockTransport(web))
    async with runtime(s, llm_client=FakeLLMClient(flaky), fetcher=fetcher) as rt:
        ingest_signals(rt.repo, [[{"source": "searxng", "url": "https://zorbo.ai", "name": "Zorbo", "metrics": {}}]])
        cid = rt.repo.candidates()[0]["id"]
        assert (await enrich_candidate(rt, cid))["decision"] == "published"
        before = rt.repo.agent_by_candidate(cid)["listing"]
        mode["broken"] = True
        res = await enrich_candidate(rt, cid, force=True)
        assert res["decision"] == "kept_previous"
        assert rt.repo.agent_by_candidate(cid)["listing"] == before
        assert rt.repo.agent_by_candidate(cid)["status"] == "published"
