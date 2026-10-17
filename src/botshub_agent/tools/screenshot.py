"""Real screenshots of a product's own pages, captured with a headless browser.

Every request the page makes (the page itself, redirects, scripts, images) is vetted with the same SSRF
rules as the fetcher, so a hostile page cannot use our browser to reach private addresses.
Needs `playwright` (`uv sync --extra screenshots`) and either Playwright's Chromium or a system Chrome."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from aaa.tools import ToolError, tool
from aaa.tools.builtin.net import FetchBlocked

HIDE_BANNERS = """
[id*="cookie" i],[class*="cookie" i],[id*="consent" i],[class*="consent" i],[id*="gdpr" i],[class*="gdpr" i],
[aria-label*="cookie" i],[class*="intercom" i],[id*="intercom" i],[class*="chat-widget" i]{display:none !important}
"""
MIN_BYTES = 20_000  # smaller than this is almost certainly a blank or error page


async def _launch(p):
    try:
        return await p.chromium.launch(headless=True)
    except Exception:
        return await p.chromium.launch(headless=True, channel="chrome")  # system Chrome when no bundled build


def make_screenshot_tool(fetcher, media_dir: Path, headless_launch=_launch):
    @tool(timeout_s=120, pool="browser")
    async def capture_screenshot(candidate_id: int, url: str, name: str = "home", full_page: bool = False) -> dict:
        """Screenshot a public web page (1440x900 viewport) and store it under the candidate's media folder."""
        try:
            from playwright.async_api import async_playwright
        except ImportError as e:
            raise ToolError("playwright is not installed (uv sync --extra screenshots)") from e
        name = "".join(ch for ch in name if ch.isalnum() or ch in "-_")[:40] or "page"
        try:
            await fetcher._target(url)
        except FetchBlocked as e:
            raise ToolError(str(e)) from e
        out = media_dir / str(candidate_id) / f"{name}.jpg"
        out.parent.mkdir(parents=True, exist_ok=True)
        verdicts: dict[str, bool] = {}

        async def vet(route):
            req = route.request
            u = urlsplit(req.url)
            if u.scheme not in ("http", "https"):
                return await route.abort()
            host = u.hostname or ""
            if host not in verdicts:
                try:
                    await fetcher._target(req.url)
                    verdicts[host] = True
                except FetchBlocked:
                    verdicts[host] = False
            await (route.continue_() if verdicts[host] else route.abort())

        async with async_playwright() as p:
            browser = await headless_launch(p)
            try:
                ctx = await browser.new_context(viewport={"width": 1440, "height": 900}, service_workers="block",
                                                accept_downloads=False, locale="en-US")
                await ctx.route("**/*", vet)
                page = await ctx.new_page()
                page.on("dialog", lambda d: d.dismiss())
                try:
                    resp = await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                except Exception as e:
                    raise ToolError(f"could not load page: {type(e).__name__}") from e
                if resp is None or resp.status >= 400:
                    raise ToolError(f"HTTP {resp.status if resp else 'none'}")
                try:
                    await page.wait_for_load_state("networkidle", timeout=8_000)
                except Exception:
                    pass  # some sites never go idle; what has rendered is what we capture
                await page.add_style_tag(content=HIDE_BANNERS)
                await page.wait_for_timeout(800)
                title = (await page.title())[:120]
                await page.screenshot(path=str(out), type="jpeg", quality=82, full_page=full_page)
            finally:
                await browser.close()
        size = out.stat().st_size
        if size < MIN_BYTES:
            out.unlink(missing_ok=True)
            raise ToolError(f"screenshot looks blank ({size} bytes)")
        return {"file": f"{candidate_id}/{name}.jpg", "bytes": size, "title": title, "url": url}

    return capture_screenshot
