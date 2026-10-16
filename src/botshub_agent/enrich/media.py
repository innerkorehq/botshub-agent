"""Media candidates collected by code (never by the LLM) and validated before use."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx
from aaa.tools.builtin.net import FetchBlocked, SafeFetcher
from bs4 import BeautifulSoup

SKIP = re.compile(r"(logo|icon|avatar|sprite|pixel|badge|favicon|spinner|emoji|shields\.io|\.svg(\?|$)|gravatar|1x1|tracking)", re.I)
VIDEO_HOSTS = ("youtube.com/embed/", "youtube-nocookie.com/embed/", "player.vimeo.com/video/", "loom.com/embed/")
MD_IMG = re.compile(r"!\[([^\]]*)\]\((\S+?)(?:\s+\"[^\"]*\")?\)")
HTML_IMG = re.compile(r"<img[^>]+src=[\"']([^\"']+)[\"'][^>]*>", re.I)
MP4 = re.compile(r"https?://\S+?\.(?:mp4|webm|mov)(?:\?\S*)?", re.I)


def candidates_from_html(html: str, base: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    for prop in ("og:image", "twitter:image"):
        for m in soup.find_all("meta", attrs={"property": prop}) + soup.find_all("meta", attrs={"name": prop}):
            if m.get("content"):
                out.append({"kind": "image", "url": urljoin(base, m["content"]), "source_url": base, "caption": "", "rank": 0})
    for i, img in enumerate(soup.find_all("img", src=True)):
        url = urljoin(base, img["src"])
        w = int(re.sub(r"\D", "", str(img.get("width") or "0")) or 0)
        if SKIP.search(url) or SKIP.search(" ".join(img.get("class") or [])) or (0 < w < 200):
            continue
        out.append({"kind": "image", "url": url, "source_url": base, "caption": (img.get("alt") or "")[:120], "rank": 1 + i})
    for fr in soup.find_all(["iframe", "video", "source"]):
        src = urljoin(base, fr.get("src") or "")
        if any(h in src for h in VIDEO_HOSTS) or MP4.fullmatch(src or ""):
            out.append({"kind": "video", "url": src, "source_url": base, "caption": "", "rank": 0})
    return out


def candidates_from_markdown(md: str, base: str) -> list[dict]:
    out = []
    for i, (alt, url) in enumerate(MD_IMG.findall(md)):
        out.append({"kind": "image", "url": urljoin(base + "/", url) if not url.startswith("http") else url,
                    "source_url": base, "caption": alt[:120], "rank": 1 + i})
    for url in HTML_IMG.findall(md):
        out.append({"kind": "image", "url": url, "source_url": base, "caption": "", "rank": 5})
    for url in MP4.findall(md):
        out.append({"kind": "video", "url": url, "source_url": base, "caption": "", "rank": 2})
    for m in re.finditer(r"https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)([\w-]{6,})", md):
        out.append({"kind": "video", "url": f"https://www.youtube.com/embed/{m.group(1)}", "source_url": base, "caption": "", "rank": 2})
    return [o for o in out if not SKIP.search(o["url"]) or "youtube" in o["url"]]


def unwrap(url: str) -> str:
    """Next.js/Vercel image proxies (/_next/image?url=<real>&w=..) -> the underlying asset, so variants dedupe."""
    q = parse_qs(urlsplit(url).query)
    if "/_next/image" in url and q.get("url"):
        return urljoin(url, q["url"][0])
    return url


def pick(cands: list[dict], limit: int = 8) -> list[dict]:
    seen, out = set(), []
    for c in sorted(cands, key=lambda c: (c["kind"] != "video", c["rank"])):
        u = unwrap(c["url"]).split("#")[0]
        key = u.rsplit("-", 1)[0] if re.search(r"-[0-9a-f]{6,}\.\w+$", u) else u  # hashed build variants of one asset
        if key in seen or urlsplit(u).scheme != "https":
            continue
        seen.add(key)
        out.append(c | {"url": c["url"]})
    return out[:limit]


async def validate(cands: list[dict], fetcher: SafeFetcher, limit: int = 6, min_bytes: int = 5_000) -> list[dict]:
    """HEAD each image through the fetcher's SSRF vetting; drop dead links, non-images, and tiny files.
    Videos on known embed hosts are kept without a request."""
    ok: list[dict] = []
    async with httpx.AsyncClient(follow_redirects=False, trust_env=False, timeout=10, transport=fetcher._transport) as client:
        for c in cands:
            if len(ok) >= limit:
                break
            if c["kind"] == "video" and any(h in c["url"] for h in VIDEO_HOSTS):
                ok.append(c)
                continue
            try:
                connect, host, host_header = await fetcher._target(c["url"])
                ext = {"sni_hostname": host} if connect.scheme == "https" else {}
                r = await client.head(connect, headers={"host": host_header, "user-agent": fetcher.policy.user_agent}, extensions=ext)
            except (FetchBlocked, httpx.HTTPError):
                continue
            ctype = r.headers.get("content-type", "").lower()
            size = int(r.headers.get("content-length") or 0)
            want = "image/" if c["kind"] == "image" else "video/"
            if r.status_code < 300 and ctype.startswith(want) and "svg" not in ctype and (size == 0 or size >= min_bytes):
                ok.append(c)
    return ok
