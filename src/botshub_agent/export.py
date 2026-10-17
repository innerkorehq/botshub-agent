"""Write published listings as JSON for botshub.com (a publisher adapter can replace this later)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from botshub_agent.db.repo import Repo


def export_all(repo: Repo, out_dir: Path, media_dir: Path | None = None) -> int:
    agents_dir = out_dir / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    keep, index = set(), []
    for a in repo.agents("published"):
        listing = json.loads(a["listing"])
        for m in listing.get("media", []):  # copy screenshots next to the JSON; drop internal paths from the output
            f = m.pop("file", None)
            if f and media_dir and (media_dir / f).exists():
                dest = out_dir / "media" / a["slug"]
                dest.mkdir(parents=True, exist_ok=True)
                shutil.copy2(media_dir / f, dest / Path(f).name)
        (agents_dir / f"{a['slug']}.json").write_text(json.dumps(listing, indent=2, ensure_ascii=False))
        keep.add(f"{a['slug']}.json")
        index.append({"slug": a["slug"], "name": a["name"], "category": a["category"],
                      "tagline": a["tagline"], "quality": a["quality"], "updated_at": a["updated_at"]})
    for f in agents_dir.glob("*.json"):  # unpublished / held agents must disappear from the export
        if f.name not in keep:
            f.unlink()
    (out_dir / "index.json").write_text(json.dumps(sorted(index, key=lambda x: -x["quality"]), indent=2))
    return len(index)
