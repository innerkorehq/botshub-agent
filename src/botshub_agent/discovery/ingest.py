"""Turn raw signals into candidates + dated metric rows, then (re)score the pool."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from botshub_agent.config import Settings
from botshub_agent.db.repo import Repo
from botshub_agent.discovery.resolve import canonical_key, host_of, same_entity
from botshub_agent.discovery.scoring import components, score_pool


def ingest_signals(repo: Repo, batches: list[list[dict]]) -> dict[str, int]:
    """Writes candidates/signals. Returns counters. Pure-ish: all effects go through `repo`."""
    seen = new = skipped = merged = 0
    for batch in batches:
        for s in batch or []:
            seen += 1
            key = canonical_key(s["url"])
            if not key:
                skipped += 1
                continue
            existing = repo.candidates("key=?", (key,))
            alias = repo.merged_into(key)
            site = s.get("website") or (s["url"] if key.startswith("web:") else "")
            repo_url = s["url"] if key.startswith("gh:") else ""
            if alias:
                cid = alias
            elif existing:
                cid = existing[0]["id"]
            else:
                cid = None
                probe = {"key": key, "name": s["name"], "website": site}
                for other in repo.candidates():
                    if reason := same_entity(probe, other | {"website": other.get("website")}):
                        repo.add_merge(key, other["id"], reason)
                        cid, merged = other["id"], merged + 1
                        break
                if cid is None:
                    new += 1
            cid = repo.upsert_candidate(
                key if cid is None else repo.candidate(cid)["key"],
                s["name"],
                website=site if site and not (canonical_key(site) or "").startswith("gh:") else "",
                repo_url=repo_url,
                description=s.get("description", "")[:500],
            )
            for metric, value in (s.get("metrics") or {}).items():
                repo.add_signal(cid, s["source"], metric, value, s["url"])
            if not (s.get("metrics")):
                repo.add_signal(cid, s["source"], "mention", 1, s["url"])
    return {"seen": seen, "new": new, "merged": merged, "skipped": skipped}


def rescore(repo: Repo, s: Settings, now: datetime | None = None) -> int:
    rows = []
    cands = [c for c in repo.candidates("status != 'rejected'")]
    for c in cands:
        cid = c["id"]
        stars = repo.latest(cid, "stars")
        old = repo.value_at_or_before(cid, "stars", 7)
        delta = (stars - old) if stars is not None and old is not None else None
        comps = components(
            stars, delta, repo.latest(cid, "hn_points"), repo.latest(cid, "hn_comments"),
            repo.latest(cid, "mention_domains"),
        )
        rows.append({"id": cid, "comps": comps, "last_seen": repo.last_signal_time(cid),
                     "n_sources": len(repo.sources_for(cid))})
    import json

    for r in score_pool(rows, s.weights, now or datetime.now(UTC)):
        repo.set_candidate(r["id"], score=r["score"], score_detail=json.dumps(r["detail"]))
    return len(rows)


def own_host(c: dict[str, Any]) -> str:
    return host_of(c.get("website") or "")
