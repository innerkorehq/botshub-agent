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


async def collect_media(rt, c: dict) -> list[dict]:
    """Videos first, then real screenshots we capture ourselves, then product images found in the HTML/README."""
    s, cid, a = rt.settings, c["id"], rt.algos
    pages = rt.repo.pages(cid)
    cands: list[dict] = []
    for p in pages:
        if p["kind"] == "homepage" and p.get("html"):
            cands += media_mod.candidates_from_html(p["html"], p["url"])
        elif p["kind"] == "readme":
            cands += media_mod.candidates_from_markdown(p["text"], p["url"])
        elif p["kind"] == "features" and p.get("html"):  # not docs/blog pages: their images are off-topic
            cands += media_mod.candidates_from_html(p["html"], p["url"])
    found = await media_mod.validate(media_mod.pick(cands, 14), rt.fetcher, limit=10)
    videos = [m for m in found if m["kind"] == "video"][:3]
    images = [m for m in found if m["kind"] == "image"][:4]

    shots: list[dict] = []
    if s.screenshots > 0:
        by_kind = {p["kind"]: p["url"] for p in pages}
        targets = [(k, by_kind[k]) for k in ("homepage", "pricing", "features") if k in by_kind]
        if not targets and c["key"].startswith("gh:"):
            targets = [("repo", f"https://github.com/{c['key'][3:]}")]
        results = await _gather(rt, a.capture, [{"candidate_id": cid, "url": u, "name": k} for k, u in targets[: s.screenshots]])
        for (k, u), r in zip(targets, results):
            if r:
                shots.append({"kind": "screenshot", "url": "", "file": r["file"], "source_url": u,
                              "caption": f"{k.capitalize()} page" + (f": {r['title']}" if r.get("title") else "")})

    if s.find_videos and len(videos) < 2:
        res = (await _gather(rt, a.videos, [{"name": c["name"]}]))[0] or []
        have = {v["url"] for v in videos}
        videos += [{"kind": "video", "url": v["url"], "source_url": v["source_url"], "caption": v["title"]}
                   for v in res if v["url"] not in have][: 2 - len(videos)]
    return videos[:3] + shots + images


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

    media = await collect_media(rt, c)
    name = ex.name.value if ex.name else c["name"]
    slug = assemble.slugify(name)
    if repo.slug_taken(slug, cid):
        slug = f"{slug}-{cid}"
    for m in media:  # screenshots live next to the listing in the export: media/<slug>/<file>
        if m.get("file"):
            m["url"] = f"media/{slug}/{m['file'].split('/')[-1]}"
    listing = assemble.build_listing(c, slug, ex, overview_text, media, ex.category or c.get("category"))
    decision, reasons, q = assemble.decide(listing, s.min_features, s.min_quality)
    if decision == "held" and existing and existing["status"] == "published":
        # A weaker re-run (LLM outage, thin page) must never replace a good live listing.
        repo.log_decision(cid, "kept_previous", reasons + notes, q)
        repo.set_candidate(cid, status="published")
        return {"candidate": cid, "slug": existing["slug"], "decision": "kept_previous", "reasons": reasons, "quality": q}
    repo.save_agent(cid, slug, listing, q, decision, facts, media)
    repo.log_decision(cid, decision, reasons + notes, q)
    repo.set_candidate(cid, status=decision, last_enriched=_now())
    return {"candidate": cid, "slug": slug, "decision": decision, "reasons": reasons, "quality": q}


async def refresh_media(rt, cid: int) -> dict:
    """Re-collect videos, screenshots and images for a published listing. No extraction, no LLM calls."""
    c, agent = rt.repo.candidate(cid), rt.repo.agent_by_candidate(cid)
    if not c or not agent or agent["status"] != "published":
        return {"candidate": cid, "decision": "skipped"}
    media = await collect_media(rt, c)
    for m in media:
        if m.get("file"):
            m["url"] = f"media/{agent['slug']}/{m['file'].split('/')[-1]}"
    listing = json.loads(agent["listing"])
    listing["media"] = [{"kind": m["kind"], "url": m["url"], "caption": m.get("caption", ""), "source_url": m["source_url"],
                         **({"file": m["file"]} if m.get("file") else {})} for m in media]
    rt.repo.update_media(agent["id"], listing, media)
    kinds = [m["kind"] for m in media]
    return {"candidate": cid, "slug": agent["slug"], **{k: kinds.count(k) for k in ("video", "screenshot", "image")}}


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
