"""Write published listings as JSON for botshub.com (a publisher adapter can replace this later)."""

from __future__ import annotations

import json
from pathlib import Path

from botshub_agent.db.repo import Repo


def export_all(repo: Repo, out_dir: Path) -> int:
    agents_dir = out_dir / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    keep, index = set(), []
    for a in repo.agents("published"):
        listing = json.loads(a["listing"])
        (agents_dir / f"{a['slug']}.json").write_text(json.dumps(listing, indent=2, ensure_ascii=False))
        keep.add(f"{a['slug']}.json")
        index.append({"slug": a["slug"], "name": a["name"], "category": a["category"],
                      "tagline": a["tagline"], "quality": a["quality"], "updated_at": a["updated_at"]})
    for f in agents_dir.glob("*.json"):  # unpublished / held agents must disappear from the export
        if f.name not in keep:
            f.unlink()
    (out_dir / "index.json").write_text(json.dumps(sorted(index, key=lambda x: -x["quality"]), indent=2))
    return len(index)
