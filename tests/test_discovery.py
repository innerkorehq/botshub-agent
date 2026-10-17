"""Discovery DAG end to end with mocked GitHub / HN / SearXNG and a scripted classifier."""

import functools
import json

import httpx
from aaa.llm import FakeLLMClient
from aaa.llm.fake import text
from aaa.tools.builtin import SearXNGSearch

from botshub_agent import runtime as rt_mod
from botshub_agent.config import Settings
from botshub_agent.discovery.workflow import run_discovery
from botshub_agent.tools import sources


def api(request: httpx.Request) -> httpx.Response:
    h = request.url.host
    if h == "api.github.com":
        if "topic:mcp-server" in request.url.params["q"]:
            return httpx.Response(500)  # one dead source must not stop the run
        return httpx.Response(200, json={"items": [
            {"html_url": "https://github.com/acme/zorbo", "name": "zorbo", "description": "An inbox agent",
             "homepage": "https://zorbo.ai", "stargazers_count": 900, "forks_count": 40},
            {"html_url": "https://github.com/acme/datasets", "name": "datasets", "description": "A dataset list",
             "homepage": "", "stargazers_count": 300, "forks_count": 5}]})
    if h == "hn.algolia.com":
        return httpx.Response(200, json={"hits": [{"url": "https://zorbo.ai", "title": "Show HN: Zorbo - an inbox agent", "points": 120, "num_comments": 30}]})
    if request.url.path == "/search":
        return httpx.Response(200, json={"results": [
            {"url": "https://medium.com/@x/best-agents", "title": "Best agents", "content": "list"},
            {"url": "https://github.com/acme/zorbo", "title": "zorbo", "content": "inbox agent"}]})
    return httpx.Response(404)


def llm(req):
    system = req.messages[0].content if req.messages[0].role == "system" else ""
    assert "classify software projects" in system
    is_agent = "zorbo" in req.messages[-1].content.lower()
    return text(json.dumps({"is_agent": is_agent, "category": "Customer Service & Support" if is_agent else "Other",
                            "confidence": 0.9, "reason": "r"}))


async def test_discovery_scores_and_classifies(tmp_path, monkeypatch):
    mock = httpx.MockTransport(api)
    monkeypatch.setattr(rt_mod, "github_tool", functools.partial(sources.github_tool, transport=mock))
    monkeypatch.setattr(rt_mod, "hn_tool", functools.partial(sources.hn_tool, transport=mock))
    monkeypatch.setattr(rt_mod, "SearXNGSearch", lambda url, **kw: SearXNGSearch(url, transport=mock, **kw))
    s = Settings(data_dir=tmp_path, score_threshold=0.0)
    async with rt_mod.runtime(s, llm_client=FakeLLMClient(llm)) as rt:
        stats = await run_discovery(rt)
        assert stats["seen"] >= 4 and stats["new"] == 2   # zorbo (repo+site+search merged) and datasets
        by_key = {c["key"]: c for c in rt.repo.candidates()}
        assert set(by_key) == {"gh:acme/zorbo", "gh:acme/datasets"}
        assert by_key["gh:acme/zorbo"]["status"] == "classified"
        assert by_key["gh:acme/zorbo"]["category"] == "Customer Service & Support"
        assert by_key["gh:acme/datasets"]["status"] == "rejected"
        assert by_key["gh:acme/zorbo"]["score"] > by_key["gh:acme/datasets"]["score"]
        assert by_key["gh:acme/zorbo"]["website"] == "https://zorbo.ai"
        assert rt.repo.sources_for(by_key["gh:acme/zorbo"]["id"]) >= {"github", "hn", "searxng"}
