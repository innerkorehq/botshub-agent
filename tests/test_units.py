import json
from datetime import UTC, datetime, timedelta

from botshub_agent.db.repo import Repo
from botshub_agent.discovery import scoring
from botshub_agent.discovery.ingest import ingest_signals, rescore
from botshub_agent.discovery.resolve import canonical_key, same_entity
from botshub_agent.config import Settings
from botshub_agent.enrich import assemble, media, overview as ov
from botshub_agent.enrich.grounding import ground, make_validator, norm
from botshub_agent.models import Extraction
from botshub_agent.export import export_all

PAGE = "WhatsApp MCP by TimelinesAI. Starter plan costs $35/month. Connect Claude, ChatGPT and Cursor. Assign chats to team members."
URL = "https://timelines.ai/whatsapp-mcp"


def ev(quote, **kw):
    return {"source_url": URL, "quote": quote, **kw}


# ---- resolve -------------------------------------------------------------
def test_canonical_keys():
    assert canonical_key("https://github.com/Foo/Bar-Mcp.git") == "gh:foo/bar-mcp"
    assert canonical_key("https://www.example.com/pricing") == "web:example.com"
    assert canonical_key("https://github.com/topics/mcp") is None
    assert canonical_key("https://medium.com/@x/best-agents") is None


def test_merge_requires_name_and_host_agreement():
    gh = {"key": "gh:acme/zorbo", "name": "zorbo", "website": ""}
    web = {"key": "web:zorbo.ai", "name": "Zorbo AI", "website": "https://zorbo.ai"}
    assert same_entity(gh, web)
    other = {"key": "web:zorbo-tools.com", "name": "Zorbo", "website": "https://zorbo-tools.com"}
    assert same_entity(gh, other) is None  # prefer a duplicate to a wrong merge


# ---- scoring -------------------------------------------------------------
def test_velocity_beats_size():
    big = scoring.components(100_000, 500, None, None, None)
    small = scoring.components(3_000, 500, None, None, None)
    assert small["vel"] > big["vel"] * 3


def test_decay_and_corroboration():
    now = datetime.now(UTC)
    assert abs(scoring.decay((now - timedelta(days=14)).isoformat(), now) - 0.5) < 0.01
    assert scoring.corroboration(3) == 1.3
    rows = [{"id": i, "comps": scoring.components(100 * (i + 1), i, None, None, None), "last_seen": now.isoformat(), "n_sources": 1} for i in range(3)]
    out = scoring.score_pool(rows, Settings().weights, now)
    assert out[2]["score"] > out[0]["score"]


def test_ingest_dedupes_and_velocity_uses_history():
    repo = Repo(":memory:")
    s = Settings()
    sig = lambda stars: [{"source": "github", "url": "https://github.com/a/zorbo", "name": "zorbo", "metrics": {"stars": stars}}]
    ingest_signals(repo, [sig(100)])
    old = (datetime.now(UTC) - timedelta(days=8)).isoformat(timespec="seconds")
    cid = repo.candidates()[0]["id"]
    repo.add_signal(cid, "github", "stars", 40, at=old)
    ingest_signals(repo, [sig(100), [{"source": "hn", "url": "https://zorbo.ai", "name": "Zorbo", "metrics": {"hn_points": 50}}]])
    assert len(repo.candidates()) == 1  # zorbo.ai host + name match => same product, logged as a merge
    assert repo.merged_into("web:zorbo.ai") == cid
    rescore(repo, s)
    assert repo.value_at_or_before(cid, "stars", 7) == 40


# ---- grounding (the anti-invention core) -----------------------------------
def test_grounding_keeps_supported_and_rejects_invented():
    ex = Extraction.model_validate({
        "features": [
            ev("Assign chats to team members", name="Chat assignment", description="Assign chats to team members"),
            ev("Generate legal contracts automatically", name="Contract drafting", description="Writes contracts"),
        ],
        "pricing": [ev("Starter plan costs $35/month", name="Starter", price="$35/month"),
                    ev("Starter plan costs $35/month", name="Starter", price="$29/month")],
        "integrations": [ev("Connect Claude, ChatGPT and Cursor", name="Cursor"), ev("Connect Claude, ChatGPT and Cursor", name="Slack")],
    })
    kept, problems = ground(ex, {URL: PAGE})
    assert [f.name for f in kept.features] == ["Chat assignment"]
    assert [t.price for t in kept.pricing] == ["$35/month"]
    assert [i.name for i in kept.integrations] == ["Cursor"]
    assert len(problems) == 3


def test_grounding_rejects_unfetched_source_and_short_quote():
    ex = Extraction.model_validate({"features": [
        {"source_url": "https://evil.example/x", "quote": "Assign chats to team members", "name": "Chat assignment", "description": ""},
        ev("Assign", name="Assign", description="")]})
    kept, problems = ground(ex, {URL: PAGE})
    assert kept.features == [] and len(problems) == 2


def test_validator_feedback_and_markdown_tolerance():
    v = make_validator(lambda ctx: {URL: PAGE})
    bad = json.dumps({"features": [ev("Totally made up sentence here", name="X", description="")]})
    r = v(bad, {})
    assert not r.ok and "not found verbatim" in r.feedback
    assert not v("not json", {}).ok
    good = json.dumps({"name": ev("WhatsApp MCP by TimelinesAI", value="WhatsApp MCP by TimelinesAI")})
    assert v(good, {}).ok
    assert norm("**Bold**  _text_") == "bold text"


# ---- overview ----------------------------------------------------------------
FACTS = [{"field": "description", "value": "Chat management for WhatsApp", "quote": "Starter plan costs $35/month"},
         {"field": "feature", "value": "Assign chats", "quote": "Assign chats to team members"}]


def test_overview_citation_check():
    ok = "## What you get\nManage WhatsApp chats. [f1] Assign chats to teammates. [f2]"
    assert ov.check(ok, FACTS) == []
    assert ov.check("## What you get\nIt is fast.", FACTS)
    assert ov.check("## What you get\nIt costs $99 per month. [f1]", FACTS)
    assert ov.check("Invented. [f9]", FACTS)
    assert "[f" not in ov.strip_tags(ok)
    assert ov.sections(ok)["What you get"].startswith("Manage")


# ---- media / assemble / export ---------------------------------------------
def test_media_candidates_filter_noise():
    html = '<meta property="og:image" content="/og.png"><img src="/logo.png"><img src="/shot.png" alt="Inbox"><iframe src="https://www.youtube.com/embed/abcdefg"></iframe>'
    c = media.pick(media.candidates_from_html(html, "https://x.com/"))
    urls = [m["url"] for m in c]
    assert "https://x.com/logo.png" not in urls and "https://x.com/shot.png" in urls
    assert c[0]["kind"] == "video"
    md = media.candidates_from_markdown("![demo](docs/demo.png) https://youtu.be/abcdefghijk", "https://github.com/a/b")
    assert any(m["kind"] == "video" for m in md)


def _listing(n_features=4, overview="Does things."):
    ex = Extraction.model_validate({
        "name": ev("WhatsApp MCP by TimelinesAI", value="WhatsApp MCP by TimelinesAI"),
        "features": [ev("Assign chats to team members", name=f"F{i}", description="") for i in range(n_features)],
        "pricing": [ev("Starter plan costs $35/month", name="Starter", price="$35/month")],
        "category": "Communication & Messaging"})
    return assemble.build_listing({"name": "x", "website": "https://timelines.ai"}, "whatsapp-mcp", ex, f"## What you get\n{overview}", [], ex.category), ex


def test_gate_publishes_or_holds_with_reasons():
    listing, ex = _listing()
    assert assemble.pricing_badge(ex) == "from $35/month"
    decision, reasons, q = assemble.decide(listing, 3, 0.5)
    assert decision == "published", reasons
    listing, _ = _listing(n_features=1)
    decision, reasons, _ = assemble.decide(listing, 3, 0.5)
    assert decision == "held" and any("features" in r for r in reasons)


def test_repo_versions_and_export_removes_unpublished(tmp_path):
    repo = Repo(":memory:")
    cid = repo.upsert_candidate("web:timelines.ai", "x", website="https://timelines.ai")
    listing, ex = _listing()
    repo.save_agent(cid, "whatsapp-mcp", listing, 0.8, "published", assemble.flat_facts(ex), [])
    assert export_all(repo, tmp_path) == 1 and (tmp_path / "agents/whatsapp-mcp.json").exists()
    listing["tagline"] = "changed"
    repo.save_agent(cid, "whatsapp-mcp", listing, 0.8, "published", assemble.flat_facts(ex), [])
    with repo.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM listing_versions").fetchone()[0] == 1
    repo.unpublish("whatsapp-mcp")
    assert export_all(repo, tmp_path) == 0 and not (tmp_path / "agents/whatsapp-mcp.json").exists()


def test_lenient_parse_keeps_good_items_and_drops_malformed():
    from botshub_agent.enrich.grounding import parse_lenient
    raw = json.dumps({"name": ev("WhatsApp MCP by TimelinesAI", value="WhatsApp MCP by TimelinesAI"),
                      "target_audience": ["Developers"],  # bare string: malformed
                      "features": [ev("Assign chats to team members", name="Assign", description=""), "oops"]})
    ex = parse_lenient(raw)
    assert ex.name and len(ex.features) == 1 and ex.target_audience == []
