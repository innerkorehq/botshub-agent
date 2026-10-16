"""Build the listing JSON + flat fact rows from grounded claims; slugs; publish gate."""

from __future__ import annotations

import re

from botshub_agent.enrich import overview as ov
from botshub_agent.models import Extraction


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "agent"


def flat_facts(ex: Extraction) -> list[dict]:
    rows: list[dict] = []

    def add(field: str, value: str, e) -> None:
        rows.append({"field": field, "value": value, "source_url": e.source_url, "quote": e.quote})

    for fld in ("name", "tagline", "description", "website", "free_trial"):
        v = getattr(ex, fld)
        if v:
            add(fld, v.value, v)
    for f in ex.features:
        add("feature", f"{f.name}: {f.description}".strip(": "), f)
    for u in ex.use_cases:
        add("use_case", u.title + (f" ({u.audience})" if u.audience else ""), u)
    for i in ex.integrations:
        add("integration", i.name, i)
    for t in ex.pricing:
        add("pricing", f"{t.name}: {t.price}", t)
    for d in ex.deployment:
        add("deployment", d.value, d)
    for d in ex.target_audience:
        add("target_audience", d.value, d)
    return rows


def pricing_badge(ex: Extraction) -> str:
    if not ex.pricing:
        return ""
    def amount(price: str) -> float:
        m = re.search(r"\d[\d,]*\.?\d*", price)
        return float(m.group().replace(",", "")) if m else float("inf")

    paid = sorted((t.price for t in ex.pricing if re.search(r"/\s*(mo|month|yr|year)|per\s+(month|year)|monthly|annual", t.price, re.I) and re.search(r"[$€£]\s*0*[1-9]|\b[1-9][\d,.]*\s*(usd|eur|gbp)\b", t.price, re.I)), key=amount)  # cheapest first
    parts = []
    if ex.free_trial:
        parts.append("Free trial")
    elif any(t.price.strip().lower() in ("free", "$0", "0") for t in ex.pricing):
        parts.append("Free plan")
    if paid:
        parts.append(f"from {paid[0]}")
    return ", ".join(parts)


def build_listing(cand: dict, slug: str, ex: Extraction, overview_text: str, media: list[dict],
                  category: str | None) -> dict:
    secs = ov.sections(overview_text) if overview_text else {}
    groups: dict[str, list[dict]] = {}
    for f in ex.features:
        groups.setdefault(f.group or "Capabilities", []).append(
            {"name": f.name, "description": f.description, "source_url": f.source_url})
    return {
        "slug": slug,
        "name": ex.name.value if ex.name else cand["name"],
        "website": ex.website.value if ex.website else (cand.get("website") or None),
        "repo_url": cand.get("repo_url") or None,
        "category": category,
        "tagline": ex.tagline.value if ex.tagline else None,
        "pricing_badge": pricing_badge(ex) or None,
        "overview": " ".join(secs.values()) or None,
        "overview_sections": secs,
        "features": groups,
        "use_cases": [{"title": u.title, "audience": u.audience or None, "source_url": u.source_url} for u in ex.use_cases],
        "integrations": [{"name": i.name, "source_url": i.source_url} for i in ex.integrations],
        "pricing": [{"name": t.name, "price": t.price, "includes": t.includes, "source_url": t.source_url} for t in ex.pricing],
        "specs": {
            "deployment": [d.value for d in ex.deployment],
            "target_audience": [d.value for d in ex.target_audience],
        },
        "media": [{"kind": m["kind"], "url": m["url"], "caption": m.get("caption", ""), "source_url": m["source_url"]} for m in media],
        "sources": sorted({r["source_url"] for r in flat_facts(ex)}),
    }


def quality(listing: dict) -> tuple[float, list[str]]:
    """Coverage score 0..1 plus hard-requirement failures. No evidence => no credit."""
    n_feat = sum(len(v) for v in listing["features"].values())
    parts = {
        "name": 0.10 * bool(listing["name"]),
        "website": 0.10 * bool(listing["website"] or listing["repo_url"]),
        "tagline": 0.05 * bool(listing["tagline"]),
        "overview": 0.20 * bool(listing["overview"]),
        "features": 0.25 * min(n_feat, 6) / 6,
        "use_cases": 0.08 * min(len(listing["use_cases"]), 3) / 3,
        "integrations": 0.05 * min(len(listing["integrations"]), 3) / 3,
        "pricing": 0.10 * bool(listing["pricing"]),
        "media": 0.07 * min(len(listing["media"]), 2) / 2,
    }
    return round(sum(parts.values()), 3), []


def decide(listing: dict, min_features: int, min_quality: float) -> tuple[str, list[str], float]:
    q, _ = quality(listing)
    reasons = []
    n_feat = sum(len(v) for v in listing["features"].values())
    if not listing["name"]:
        reasons.append("missing name")
    if not (listing["website"] or listing["repo_url"]):
        reasons.append("missing website/repo")
    if not listing["overview"]:
        reasons.append("missing evidence-backed overview")
    if n_feat < min_features:
        reasons.append(f"only {n_feat} evidenced features (need {min_features})")
    if not listing["category"]:
        reasons.append("missing category")
    if q < min_quality:
        reasons.append(f"quality {q} < {min_quality}")
    return ("held" if reasons else "published"), reasons, q
