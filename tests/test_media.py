"""Screenshots (real headless browser, skipped when none is available) and video discovery."""

import http.server
import threading

import httpx
import pytest
from aaa.tools.builtin import SearXNGSearch
from aaa.tools.builtin.net import FetchPolicy

from botshub_agent.tools.fetch_html import RawFetcher
from botshub_agent.tools.screenshot import make_screenshot_tool
from botshub_agent.tools.videos import make_video_tool, title_is_about, youtube_id

PAGE = b"<html><head><title>Zorbo</title></head><body style='background:#123'><h1 style='font-size:90px'>Zorbo inbox agent</h1>" + \
    b"<div id='cookie-banner' style='position:fixed;inset:0;background:red'>COOKIES</div>" + (b"<p>filler text</p>" * 400) + b"</body></html>"


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == "/" else 404)
        self.send_header("content-type", "text/html")
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *a):
        pass


def browser_available() -> bool:
    try:
        import asyncio

        from playwright.async_api import async_playwright

        from botshub_agent.tools.screenshot import _launch

        async def go():
            async with async_playwright() as p:
                await (await _launch(p)).close()

        asyncio.run(go())
        return True
    except Exception:
        return False


def test_youtube_id_and_title_matching():
    assert youtube_id("https://www.youtube.com/watch?v=abc123XYZ_-") == "abc123XYZ_-"
    assert youtube_id("https://youtu.be/abc123XYZ") == "abc123XYZ"
    assert youtube_id("https://example.com/watch?v=abc") is None
    assert title_is_about("Firecrawl demo: scrape any site", "Dev Channel", "Firecrawl")
    assert not title_is_about("Top 10 AI tools", "Some Channel", "Firecrawl")
    assert not title_is_about("AI agent tutorial", "x", "AI Agent")  # generic-only names never match


async def test_find_product_videos_verifies_titles_via_oembed():
    def web(req: httpx.Request) -> httpx.Response:
        if req.url.host == "youtube.com" or req.url.host == "www.youtube.com":
            vid = req.url.params["url"].split("v=")[1]
            titles = {"aaaaaaaaaaa": "Zorbo in 2 minutes", "bbbbbbbbbbb": "Best CRM tools 2026"}
            return httpx.Response(200, json={"title": titles[vid], "author_name": "Someone"})
        return httpx.Response(200, json={"results": [
            {"url": "https://www.youtube.com/watch?v=bbbbbbbbbbb", "title": "x", "content": ""},
            {"url": "https://www.youtube.com/watch?v=aaaaaaaaaaa", "title": "x", "content": ""},
            {"url": "https://vimeo.com/123", "title": "x", "content": ""}]})

    mock = httpx.MockTransport(web)
    tool = make_video_tool(SearXNGSearch("http://searx", transport=mock), transport=mock)
    vids = await tool.invoke({"name": "Zorbo"}, None)
    assert [v["url"] for v in vids] == ["https://www.youtube.com/embed/aaaaaaaaaaa"]


@pytest.mark.skipif(not browser_available(), reason="no headless browser")
async def test_screenshot_hides_banners_and_blocks_private_hosts(tmp_path):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        open_fetcher = RawFetcher(FetchPolicy(allow_private_networks=True, allowed_ports=(srv.server_port,)))
        tool = make_screenshot_tool(open_fetcher, tmp_path)
        out = await tool.invoke({"candidate_id": 7, "url": f"http://127.0.0.1:{srv.server_port}/", "name": "home"}, None)
        assert out["title"] == "Zorbo" and out["bytes"] > 20_000
        assert (tmp_path / "7" / "home.jpg").exists()
        # With the default (strict) policy, the same private address is refused before a browser is started.
        strict = make_screenshot_tool(RawFetcher(FetchPolicy(allowed_ports=(srv.server_port,))), tmp_path)
        from aaa.tools import ToolError
        with pytest.raises(ToolError):
            await strict.invoke({"candidate_id": 7, "url": f"http://127.0.0.1:{srv.server_port}/"}, None)
    finally:
        srv.shutdown()
