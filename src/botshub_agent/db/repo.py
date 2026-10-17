"""SQLite access. A fresh connection per call (WAL) keeps it safe from tool worker threads."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

SCHEMA = (Path(__file__).parent / "schema.sql").read_text()


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Repo:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._mem: sqlite3.Connection | None = None
        with self.conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        if self.path == ":memory:":  # tests: one shared connection
            if self._mem is None:
                self._mem = sqlite3.connect(":memory:", check_same_thread=False)
                self._mem.row_factory = sqlite3.Row
            yield self._mem
            self._mem.commit()
            return
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        try:
            yield c
            c.commit()
        finally:
            c.close()

    # ---- candidates -------------------------------------------------------------
    def upsert_candidate(self, key: str, name: str, **kw: Any) -> int:
        t = now()
        with self.conn() as c:
            row = c.execute("SELECT id FROM candidates WHERE key=?", (key,)).fetchone()
            if row:
                sets = {k: v for k, v in kw.items() if v}
                sets["last_seen"] = t
                c.execute(
                    f"UPDATE candidates SET {', '.join(k + '=?' for k in sets)} WHERE id=?",
                    (*sets.values(), row["id"]),
                )
                return row["id"]
            cols = {"key": key, "name": name, "first_seen": t, "last_seen": t, **kw}
            cur = c.execute(
                f"INSERT INTO candidates ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                tuple(cols.values()),
            )
            return int(cur.lastrowid)  # type: ignore[arg-type]

    def candidate(self, cid: int) -> dict | None:
        with self.conn() as c:
            r = c.execute("SELECT * FROM candidates WHERE id=?", (cid,)).fetchone()
            return dict(r) if r else None

    def candidates(self, where: str = "1=1", args: tuple = ()) -> list[dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute(f"SELECT * FROM candidates WHERE {where}", args)]

    def set_candidate(self, cid: int, **kw: Any) -> None:
        with self.conn() as c:
            c.execute(
                f"UPDATE candidates SET {', '.join(k + '=?' for k in kw)} WHERE id=?",
                (*kw.values(), cid),
            )

    def add_merge(self, alias_key: str, cid: int, reason: str) -> None:
        with self.conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO merges VALUES (?,?,?,?)", (alias_key, cid, reason, now())
            )

    def merged_into(self, key: str) -> int | None:
        with self.conn() as c:
            r = c.execute("SELECT candidate_id FROM merges WHERE alias_key=?", (key,)).fetchone()
            return r["candidate_id"] if r else None

    def merge_candidates(self, keep: int, drop: int, reason: str) -> bool:
        """Fold `drop` into `keep` (signals, pages, aliases). Refuses when both already have listings."""
        with self.conn() as c:
            if c.execute("SELECT 1 FROM agents WHERE candidate_id=?", (drop,)).fetchone():
                return False
            d = c.execute("SELECT * FROM candidates WHERE id=?", (drop,)).fetchone()
            k = c.execute("SELECT * FROM candidates WHERE id=?", (keep,)).fetchone()
            if not d or not k:
                return False
            c.execute("UPDATE signals SET candidate_id=? WHERE candidate_id=?", (keep, drop))
            c.execute("UPDATE OR IGNORE pages SET candidate_id=? WHERE candidate_id=?", (keep, drop))
            c.execute("DELETE FROM pages WHERE candidate_id=?", (drop,))
            c.execute("UPDATE merges SET candidate_id=? WHERE candidate_id=?", (keep, drop))
            c.execute("INSERT OR REPLACE INTO merges VALUES (?,?,?,?)", (d["key"], keep, reason, now()))
            for col in ("website", "repo_url"):
                if not k[col] and d[col]:
                    c.execute(f"UPDATE candidates SET {col}=? WHERE id=?", (d[col], keep))
            c.execute("DELETE FROM embedded WHERE candidate_id=?", (drop,))
            c.execute("DELETE FROM candidates WHERE id=?", (drop,))
            return True

    # ---- signals ----------------------------------------------------------------
    def add_signal(
        self, cid: int, source: str, metric: str, value: float, url: str = "", at: str | None = None
    ) -> None:
        with self.conn() as c:
            c.execute(
                "INSERT INTO signals (candidate_id,source,metric,value,observed_at,url) VALUES (?,?,?,?,?,?)",
                (cid, source, metric, float(value), at or now(), url),
            )

    def latest(self, cid: int, metric: str) -> float | None:
        with self.conn() as c:
            r = c.execute(
                "SELECT value FROM signals WHERE candidate_id=? AND metric=? ORDER BY observed_at DESC, id DESC LIMIT 1",
                (cid, metric),
            ).fetchone()
            return r["value"] if r else None

    def value_at_or_before(self, cid: int, metric: str, days: int) -> float | None:
        """Oldest-known reading at least `days` old; None when we have no history that far back."""
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
        with self.conn() as c:
            r = c.execute(
                "SELECT value FROM signals WHERE candidate_id=? AND metric=? AND observed_at<=? "
                "ORDER BY observed_at DESC LIMIT 1",
                (cid, metric, cutoff),
            ).fetchone()
            return r["value"] if r else None

    def last_signal_time(self, cid: int) -> str | None:
        with self.conn() as c:
            r = c.execute(
                "SELECT MAX(observed_at) m FROM signals WHERE candidate_id=?", (cid,)
            ).fetchone()
            return r["m"]

    def sources_for(self, cid: int) -> set[str]:
        with self.conn() as c:
            return {
                r["source"]
                for r in c.execute("SELECT DISTINCT source FROM signals WHERE candidate_id=?", (cid,))
            }

    # ---- pages ------------------------------------------------------------------
    def save_page(
        self, cid: int, url: str, kind: str, status: int, text: str, html: str = ""
    ) -> bool:
        """Returns True when the content is new or changed."""
        h = sha(text)
        with self.conn() as c:
            old = c.execute(
                "SELECT content_hash FROM pages WHERE candidate_id=? AND url=?", (cid, url)
            ).fetchone()
            c.execute(
                "INSERT INTO pages (candidate_id,url,kind,status,text,html,content_hash,fetched_at) VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(candidate_id,url) DO UPDATE SET kind=excluded.kind,status=excluded.status,"
                "text=excluded.text,html=excluded.html,content_hash=excluded.content_hash,fetched_at=excluded.fetched_at",
                (cid, url, kind, status, text, html, h, now()),
            )
            return old is None or old["content_hash"] != h

    def pages(self, cid: int) -> list[dict]:
        with self.conn() as c:
            return [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM pages WHERE candidate_id=? AND status<400 ORDER BY id", (cid,)
                )
            ]

    def pages_hash(self, cid: int) -> str:
        return sha("".join(sorted(p["content_hash"] for p in self.pages(cid))))

    # ---- agents -----------------------------------------------------------------
    def agent_by_candidate(self, cid: int) -> dict | None:
        with self.conn() as c:
            r = c.execute("SELECT * FROM agents WHERE candidate_id=?", (cid,)).fetchone()
            return dict(r) if r else None

    def slug_taken(self, slug: str, cid: int) -> bool:
        with self.conn() as c:
            r = c.execute("SELECT candidate_id FROM agents WHERE slug=?", (slug,)).fetchone()
            return bool(r) and r["candidate_id"] != cid

    def save_agent(
        self, cid: int, slug: str, listing: dict, quality: float, status: str,
        facts: list[dict], media: list[dict],
    ) -> int:
        t = now()
        name, ov = listing.get("name") or "", listing.get("overview") or ""
        with self.conn() as c:
            old = c.execute("SELECT id, listing FROM agents WHERE candidate_id=?", (cid,)).fetchone()
            payload = json.dumps(listing, ensure_ascii=False, sort_keys=True)
            vals = (
                slug, name, listing.get("website"), listing.get("repo_url"), listing.get("category"),
                listing.get("tagline"), ov, payload, quality, status, t,
            )
            if old:
                aid = old["id"]
                if old["listing"] != payload:
                    c.execute("INSERT INTO listing_versions (agent_id,listing,at) VALUES (?,?,?)", (aid, old["listing"], t))
                c.execute(
                    "UPDATE agents SET slug=?,name=?,website=?,repo_url=?,category=?,tagline=?,overview=?,"
                    "listing=?,quality=?,status=?,updated_at=?, published_at=COALESCE(published_at, CASE WHEN ?='published' THEN ? END) WHERE id=?",
                    (*vals, status, t, aid),
                )
            else:
                cur = c.execute(
                    "INSERT INTO agents (candidate_id,slug,name,website,repo_url,category,tagline,overview,listing,quality,status,updated_at,published_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (cid, *vals, t if status == "published" else None),
                )
                aid = int(cur.lastrowid)  # type: ignore[arg-type]
            c.execute("DELETE FROM facts WHERE agent_id=?", (aid,))
            c.executemany(
                "INSERT INTO facts (agent_id,field,value,source_url,quote) VALUES (?,?,?,?,?)",
                [(aid, f["field"], f["value"], f["source_url"], f["quote"]) for f in facts],
            )
            c.execute("DELETE FROM media WHERE agent_id=?", (aid,))
            c.executemany(
                "INSERT OR IGNORE INTO media (agent_id,kind,url,source_url,caption) VALUES (?,?,?,?,?)",
                [(aid, m["kind"], m["url"], m["source_url"], m.get("caption", "")) for m in media],
            )
            return aid

    def update_media(self, agent_id: int, listing: dict, media: list[dict]) -> None:
        """Replace only the media of a listing (no re-extraction), keeping a version for the diff."""
        t = now()
        with self.conn() as c:
            old = c.execute("SELECT listing FROM agents WHERE id=?", (agent_id,)).fetchone()
            payload = json.dumps(listing, ensure_ascii=False, sort_keys=True)
            if old and old["listing"] != payload:
                c.execute("INSERT INTO listing_versions (agent_id,listing,at) VALUES (?,?,?)", (agent_id, old["listing"], t))
            c.execute("UPDATE agents SET listing=?, updated_at=? WHERE id=?", (payload, t, agent_id))
            c.execute("DELETE FROM media WHERE agent_id=?", (agent_id,))
            c.executemany(
                "INSERT OR IGNORE INTO media (agent_id,kind,url,source_url,caption) VALUES (?,?,?,?,?)",
                [(agent_id, m["kind"], m["url"], m["source_url"], m.get("caption", "")) for m in media],
            )

    def log_decision(self, cid: int, decision: str, reasons: list[str], quality: float) -> None:
        with self.conn() as c:
            c.execute(
                "INSERT INTO publish_log (candidate_id,decision,reasons,quality,at) VALUES (?,?,?,?,?)",
                (cid, decision, json.dumps(reasons), quality, now()),
            )

    def agents(self, status: str | None = "published") -> list[dict]:
        with self.conn() as c:
            q = "SELECT * FROM agents" + (" WHERE status=?" if status else "")
            return [dict(r) for r in c.execute(q, (status,) if status else ())]

    def unpublish(self, slug: str) -> bool:
        with self.conn() as c:
            n = c.execute(
                "UPDATE agents SET status='unpublished', updated_at=? WHERE slug=?", (now(), slug)
            ).rowcount
            return n > 0
