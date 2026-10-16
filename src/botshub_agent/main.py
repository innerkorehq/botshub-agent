"""CLI: discover | enrich | refresh | export | unpublish | serve | status"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta

from botshub_agent.config import Settings
from botshub_agent.db.repo import Repo
from botshub_agent.discovery.workflow import run_discovery
from botshub_agent.enrich.pipeline import enrich_candidate, enrich_top
from botshub_agent.export import export_all
from botshub_agent.runtime import runtime


async def refresh(rt) -> list[dict]:
    cutoff = (datetime.now(UTC) - timedelta(days=rt.settings.refresh_days)).isoformat(timespec="seconds")
    due = rt.repo.candidates("status='published' AND (last_enriched IS NULL OR last_enriched < ?)", (cutoff,))
    return [await enrich_candidate(rt, c["id"]) for c in due]


async def serve(rt, interval_h: float = 24) -> None:
    while True:
        print(json.dumps({"discover": await run_discovery(rt)}, default=str), flush=True)
        print(json.dumps({"enrich": await enrich_top(rt)}, default=str), flush=True)
        print(json.dumps({"refresh": await refresh(rt)}, default=str), flush=True)
        print({"exported": export_all(rt.repo, rt.settings.export_dir)}, flush=True)
        await asyncio.sleep(interval_h * 3600)


async def amain(args: argparse.Namespace) -> None:
    s = Settings()
    if args.cmd == "export":
        print(export_all(Repo(s.botshub_db), s.export_dir), "listings exported to", s.export_dir)
        return
    if args.cmd == "unpublish":
        repo = Repo(s.botshub_db)
        print("unpublished" if repo.unpublish(args.slug) else "not found")
        export_all(repo, s.export_dir)
        return
    if args.cmd == "status":
        repo = Repo(s.botshub_db)
        for c in sorted(repo.candidates(), key=lambda c: -c["score"])[:30]:
            print(f'{c["id"]:>4} {c["score"]:>6.1f} {c["status"]:<10} {c["key"]}')
        return
    async with runtime(s) as rt:
        # Crash recovery: re-adopt runs that were open when the last process died.
        await rt.engine.recover(rt.algos.all())
        if args.cmd == "discover":
            print(json.dumps(await run_discovery(rt), indent=2, default=str))
        elif args.cmd == "enrich":
            res = [await enrich_candidate(rt, args.id, force=True)] if args.id else await enrich_top(rt, args.limit)
            print(json.dumps(res, indent=2, default=str))
            export_all(rt.repo, s.export_dir)
        elif args.cmd == "refresh":
            print(json.dumps(await refresh(rt), indent=2, default=str))
            export_all(rt.repo, s.export_dir)
        elif args.cmd == "serve":
            await serve(rt, args.hours)


def cli() -> None:
    p = argparse.ArgumentParser(prog="botshub")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("discover")
    e = sub.add_parser("enrich"); e.add_argument("--id", type=int); e.add_argument("--limit", type=int)
    sub.add_parser("refresh"); sub.add_parser("export"); sub.add_parser("status")
    u = sub.add_parser("unpublish"); u.add_argument("slug")
    sv = sub.add_parser("serve"); sv.add_argument("--hours", type=float, default=24)
    asyncio.run(amain(p.parse_args()))
