"""Embedding tie-break for entity resolution (sqlite-vec). Conservative: a merge needs a high cosine
similarity AND matching normalised names AND a repo-vs-site pair. Merges are logged in `merges`."""

from __future__ import annotations

import logging

from aaa.data import Doc
from aaa.llm import LLMEmbedder
from aaa.vector import EmbeddingIndex
from aaa.vector.sqlitevec import SqliteVecStore
from rapidfuzz import fuzz

from botshub_agent.db.repo import sha
from botshub_agent.discovery.resolve import normalize_name

log = logging.getLogger("botshub.semantic")


def _text(c: dict) -> str:
    return f"{c['name']}. {c.get('description') or ''}".strip()


def similarity(hit) -> float:
    return hit.score if hit.higher_is_better else 1.0 - hit.score


async def semantic_merge(rt) -> dict:
    s, repo = rt.settings, rt.repo
    if not s.embed_model:
        return {"skipped": "BOTSHUB_EMBED_MODEL not set"}
    embedder = LLMEmbedder(rt.llm, s.embed_model)
    try:
        dim = s.embed_dim or len((await embedder.embed(["dimension probe"]))[0])
    except Exception as e:
        log.warning("embedding model unavailable: %s: %s", type(e).__name__, e)
        return {"error": f"{type(e).__name__}: {e}", "merged": 0}
    store = SqliteVecStore(str(s.data_dir / f"vectors-{dim}.db"), dimension=dim)  # file per dimension: models can change
    index = EmbeddingIndex(store, embedder, namespace="candidates")
    merged = embedded = 0
    try:
        cands = repo.candidates("status != 'rejected'")
        with repo.conn() as c:
            done = {r["candidate_id"]: r["text_hash"] for r in c.execute("SELECT * FROM embedded")}
        fresh = [c for c in cands if done.get(c["id"]) != sha(_text(c) + s.embed_model)]
        if fresh:
            await index.upsert([Doc(id=str(c["id"]), text=_text(c), meta={"kind": c["key"].split(":")[0]}) for c in fresh])
            with repo.conn() as c:
                c.executemany("INSERT OR REPLACE INTO embedded VALUES (?,?)", [(f["id"], sha(_text(f) + s.embed_model)) for f in fresh])
            embedded = len(fresh)
        gone: set[int] = set()
        for c in fresh:
            if c["id"] in gone:
                continue
            for hit in await index.search(_text(c), k=4):
                oid = int(hit.doc.id)
                other = repo.candidate(oid)
                if oid == c["id"] or oid in gone or not other:
                    continue
                if c["key"].split(":")[0] == other["key"].split(":")[0]:
                    continue  # two repos / two sites are different products until proven otherwise
                sim = similarity(hit)
                na, nb = normalize_name(c["name"]), normalize_name(other["name"])
                if sim >= s.semantic_threshold and na and nb and fuzz.ratio(na, nb) >= 85:
                    keep, drop = sorted((c["id"], oid))
                    if repo.merge_candidates(keep, drop, f"semantic sim={sim:.3f} names {na!r}~{nb!r}"):
                        await index.delete([str(drop)])
                        gone.add(drop)
                        merged += 1
                        if drop == c["id"]:
                            break
    except Exception as e:  # embeddings are an optimisation; never fail discovery over them
        log.warning("semantic merge skipped: %s: %s", type(e).__name__, e)
        return {"error": f"{type(e).__name__}: {e}", "merged": merged}
    finally:
        await store.aclose()
    return {"embedded": embedded, "merged": merged}
