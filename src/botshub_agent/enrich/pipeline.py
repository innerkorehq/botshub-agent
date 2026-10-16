"""Enrichment of one candidate: fetch -> facts (grounded) -> media -> overview -> gate -> save."""

from __future__ import annotations

import asyncio
import json
import re

from aaa.runtime import Budget

from botshub_agent.discovery.resolve import canonical_key
from botshub_agent.enrich import assemble, media as media_mod
from botshub_agent.enrich import overview as ov
from botshub_agent.enrich import gapfill, pricevote
from botshub_agent.enrich.extract import build_prompt, guess_links
from botshub_agent.enrich.grounding import canonicalize_sources, ground, parse_lenient
from botshub_agent.models import Extraction
from botshub_agent.tools.fetch_html import extract_links


async def _gather(rt, algo, inputs: list[dict]) -> list:
    """Run one single-tool graph per input concurrently; None for any that failed."""
    async def one(inp):
        out = await rt.engine.run(algo, inp, budget=Budget(deadline_s=120))
        return out.value if out.kind.value == "success" else None
    return list(await asyncio.gather(*(one(i) for i in inputs)))


def _budget(rt) -> Budget:
    return Budget(max_steps=60, deadline_s=600, **({"max_cost": rt.settings.run_max_cost} if rt.settings.run_max_cost else {}))


async def fetch_sources(rt, c: dict) -> None:
    cid = c["id"]
    a = rt.algos
    readme = None
    tasks = []
    if c.get("website"):
        tasks.append(_gather(rt, a.fetch_page, [{"candidate_id": cid, "url": c["website"], "kind": "homepage"}]))
    if c["key"].startswith("gh:"):
        tasks.append(_gather(rt, a.github_readme, [{"candidate_id": cid, "owner_repo": c["key"][3:]}]))
    res = await asyncio.gather(*tasks)
    if c["key"].startswith("gh:"):
        readme = res[-1][0]
    site = c.get("website") or (readme or {}).get("homepage")
    if site and not c.get("website") and not (canonical_key(site) or "").startswith("gh:"):
        rt.repo.set_candidate(cid, website=site)
        await _gather(rt, a.fetch_page, [{"candidate_id": cid, "url": site, "kind": "homepage"}])
    home = next((p for p in rt.repo.pages(cid) if p["kind"] == "homepage"), None)
    if home and home.get("html"):
        links = [{"candidate_id": cid, "url": u, "kind": k}
                 for k, u in guess_links(extract_links(home["html"], home["url"]), home["url"])
                 if u != home["url"]]
        await _gather(rt, a.fetch_page, links)


async def collect_media(rt, cid: int) -> list[dict]:
    cands: list[dict] = []
    for p in rt.repo.pages(cid):
        if p["kind"] == "homepage" and p.get("html"):
            cands += media_mod.candidates_from_html(p["html"], p["url"])
        elif p["kind"] == "readme":
            cands += media_mod.candidates_from_markdown(p["text"], p["url"])
        elif p["kind"] == "features" and p.get("html"):  # not docs/blog pages: their images are off-topic
            cands += media_mod.candidates_from_html(p["html"], p["url"])
    return await media_mod.validate(media_mod.pick(cands, 12), rt.fetcher)


def _salvage(raw: str | None, pages: dict[str, str]) -> Extraction:
    """Keep only the claims that pass grounding, even if the model never produced a fully clean answer."""
    if not raw:
        return Extraction()
    try:
        return canonicalize_sources(ground(parse_lenient(raw), pages)[0], pages)
    except Exception:
        return Extraction()


def _claims(ex: Extraction) -> int:
    return len(ex.features) + len(ex.use_cases) + len(ex.integrations) + len(ex.pricing) + sum(
        bool(getattr(ex, f)) for f in ("name", "tagline", "description", "website"))


async def extract_facts(rt, c: dict, pages: list[dict]) -> Extraction:
    out = await rt.engine.run(rt.algos.extract, {"task": build_prompt(c, pages), "candidate_id": c["id"]},
                              budget=_budget(rt))
    raw = (out.value or {}).get("candidate") if isinstance(out.value, dict) else None
    return _salvage(raw, {p["url"]: p["text"] for p in pages})


async def enrich_candidate(rt, cid: int, force: bool = False) -> dict:
    s, repo = rt.settings, rt.repo
    c = repo.candidate(cid)
    before = repo.pages_hash(cid)
    repo.set_candidate(cid, status="enriching")
    await fetch_sources(rt, c)
    pages = repo.pages(cid)
    existing = repo.agent_by_candidate(cid)
    if existing and existing["status"] == "published" and not force and repo.pages_hash(cid) == before:
        repo.set_candidate(cid, status="published", last_enriched=_now())
        return {"candidate": cid, "decision": "unchanged"}
    if not pages:
        repo.set_candidate(cid, status="held")
        repo.log_decision(cid, "held", ["no pages could be fetched"], 0)
        return {"candidate": cid, "decision": "held", "reasons": ["no pages could be fetched"]}

    ex = await extract_facts(rt, c, pages)
    notes: list[str] = []

    # Gap-filler: only when something required is still missing, and only for those gaps.
    gaps = gapfill.find_gaps(ex, s.min_features)
    if gaps:
        before_hash = repo.pages_hash(cid)
        for gap in gaps:
            await rt.engine.run(rt.algos.gapfill, {"task": gapfill.task_for(gap, c["name"])},
                                meta={"candidate_id": cid}, budget=Budget(max_steps=30, deadline_s=240))
        if repo.pages_hash(cid) != before_hash:
            pages = repo.pages(cid)
            again = await extract_facts(rt, c, pages)
            if _claims(again) >= _claims(ex):
                ex = again
            notes.append(f"gap-filler ran for {gaps}")

    # Prices are exact strings: keep only those a 3-way vote confirms.
    if ex.pricing:
        price_pages = [p for p in pages if p["kind"] in ("pricing", "homepage", "readme")] or pages
        ex, dropped = await pricevote.vote_prices(rt, ex, build_prompt(c, price_pages))
        notes += dropped
    if not ex.category and c.get("category"):
        ex.category = c["category"]

    facts = assemble.flat_facts(ex)
    overview_text = ""
    if len([f for f in facts if f["field"] in ("description", "tagline", "feature")]) >= 2:
        o = await rt.engine.run(rt.algos.overview,
                                {"task": "Facts:\n" + ov.fact_lines(facts)}, budget=_budget(rt))
        cand_text = (o.value or {}).get("candidate", "") if isinstance(o.value, dict) else ""
        if cand_text and not ov.check(cand_text, facts):
            overview_text = cand_text
        else:
            overview_text = ov.template_overview(facts)
    elif facts:
        overview_text = ov.template_overview(facts)

    media = await collect_media(rt, cid)
    name = ex.name.value if ex.name else c["name"]
    slug = assemble.slugify(name)
    if repo.slug_taken(slug, cid):
        slug = f"{slug}-{cid}"
    listing = assemble.build_listing(c, slug, ex, overview_text, media, ex.category or c.get("category"))
    decision, reasons, q = assemble.decide(listing, s.min_features, s.min_quality)
    repo.save_agent(cid, slug, listing, q, decision, facts, media)
    repo.log_decision(cid, decision, reasons + notes, q)
    repo.set_candidate(cid, status=decision, last_enriched=_now())
    return {"candidate": cid, "slug": slug, "decision": decision, "reasons": reasons, "quality": q}


def _now() -> str:
    from botshub_agent.db.repo import now
    return now()


async def enrich_top(rt, limit: int | None = None) -> list[dict]:
    s = rt.settings
    limit = limit or s.daily_enrich_cap
    pool = rt.repo.candidates("status IN ('classified') AND score >= ?", (s.score_threshold,))
    pool.sort(key=lambda c: -c["score"])
    results = []
    for c in pool[:limit]:
        try:
            results.append(await enrich_candidate(rt, c["id"]))
        except Exception as e:  # one bad candidate must not stop the batch
            rt.repo.set_candidate(c["id"], status="classified")
            results.append({"candidate": c["id"], "decision": "error", "error": f"{type(e).__name__}: {e}"})
    return results
