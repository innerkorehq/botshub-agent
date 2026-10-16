"""Canonical keys and entity resolution. Prefer a duplicate over a wrong merge."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from rapidfuzz import fuzz

# Hosts that carry content *about* products, not products themselves.
AGGREGATORS = {
    "medium.com", "reddit.com", "news.ycombinator.com", "youtube.com", "youtu.be", "x.com",
    "twitter.com", "linkedin.com", "dev.to", "substack.com", "hackernoon.com", "facebook.com",
    "producthunt.com", "huggingface.co", "arxiv.org", "wikipedia.org", "npmjs.com",
    "pypi.org", "aiagentslist.com", "futurepedia.io", "theresanaiforthat.com",
}
_GH = re.compile(r"^/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?:/.*)?$")
_NOT_REPO_OWNERS = {"topics", "orgs", "sponsors", "marketplace", "features", "about", "search", "apps"}


def host_of(url: str) -> str:
    h = (urlsplit(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def is_aggregator(url: str) -> bool:
    h = host_of(url)
    return any(h == a or h.endswith("." + a) for a in AGGREGATORS)


def canonical_key(url: str) -> str | None:
    """gh:owner/repo for GitHub repos, web:host for product sites, None for aggregator pages."""
    if not url:
        return None
    parts = urlsplit(url if "//" in url else "https://" + url)
    h = host_of(url)
    if h == "github.com":
        m = _GH.match(parts.path)
        if m and m.group(1).lower() not in _NOT_REPO_OWNERS:
            return f"gh:{m.group(1).lower()}/{m.group(2).lower()}"
        return None
    if not h or "." not in h or is_aggregator(url):
        return None
    return f"web:{h}"


def normalize_name(name: str) -> str:
    n = name.lower()
    n = re.sub(r"\b(mcp|server|agent|ai|the|by|open|source)\b", " ", n)
    return re.sub(r"[^a-z0-9]+", "", n)


def same_entity(a: dict, b: dict) -> str | None:
    """Return a reason string if two candidate dicts (name, key, website) look like one product."""
    if a["key"] == b["key"]:
        return "same key"
    ha, hb = host_of(a.get("website") or ""), host_of(b.get("website") or "")
    if ha and ha == hb:
        return "same website host"
    na, nb = normalize_name(a["name"]), normalize_name(b["name"])
    if len(na) < 4 or len(nb) < 4:
        return None
    # Name equality across a repo and a site is the only fuzzy merge we trust.
    kinds = {a["key"].split(":")[0], b["key"].split(":")[0]}
    if kinds == {"gh", "web"} and (na == nb or fuzz.ratio(na, nb) >= 96):
        host_root = normalize_name((ha or hb).split(".")[0]) if (ha or hb) else ""
        if host_root and host_root == na:
            return f"name match {na!r} + host {host_root!r}"
    return None
