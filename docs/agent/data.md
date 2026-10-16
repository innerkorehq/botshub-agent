# Data and export format

## Two SQLite files

AAA's `SQLiteEventStore` supports **one engine process per file**, so the agent's own data lives separately.

| File | Owner | Contents |
|---|---|---|
| `data/aaa_events.db` | AAA | append-only event log of every run (audit, replay, crash recovery) |
| `data/botshub.db` | this project | business tables below |
| `data/vectors-<dim>.db` | sqlite-vec | candidate embeddings (one file per embedding dimension) |

### Business tables

| Table | Purpose |
|---|---|
| `candidates` | one row per product: canonical `key`, name, website, repo, category, score, status |
| `signals` | **dated** metrics (stars, HN points, mention counts), append-only |
| `merges` | which keys were folded into which candidate, and why |
| `pages` | every fetched page: URL, kind, text, raw HTML, content hash |
| `agents` | published/held listings with the full listing JSON |
| `facts` | one row per claim: field, value, `source_url`, `quote` |
| `media` | validated images/videos with their source pages |
| `listing_versions` | every previous version of a listing (diffable) |
| `publish_log` | every gate decision with reasons and notes |

Because `facts` stores the quote next to every claim, any listing field can be audited in one query:

```sql
select field, value, source_url, quote from facts
where agent_id = (select id from agents where slug = 'firecrawl');
```

## Export format

`botshub export` (also run after enrich/refresh) writes `out/agents/<slug>.json` and `out/index.json`. Unpublished listings are removed.
Sections mirror a typical directory page: identity, pricing badge, overview, grouped features, use cases, integrations, pricing tiers, specs, media and sources.
Real output (trimmed) for one agent:

```json
{
  "slug": "firecrawl",
  "website": "https://firecrawl.dev",
  "repo_url": "https://github.com/firecrawl/firecrawl",
  "category": "Data & Analytics",
  "tagline": "Give your AI agents web data and beyond",
  "pricing_badge": "Free trial, from $16 /month",
  "features": {
    "Browser interaction": [
      { "name": "Interact",
        "description": "Scrape a page, then interact with it using AI prompts or code.",
        "source_url": "https://www.firecrawl.dev/" }
    ]
  },
  "use_cases": [ { "title": "Deep research", "audience": "Researchers", "source_url": "https://www.firecrawl.dev/" } ],
  "pricing": [
    { "name": "Hobby", "price": "$16 /month",
      "includes": ["5,000 credits / month", "5 concurrent requests"],
      "source_url": "https://www.firecrawl.dev/pricing" }
  ],
  "media": [ { "kind": "image", "url": "https://www.firecrawl.dev/og.png", "source_url": "https://www.firecrawl.dev/" } ],
  "sources": ["https://docs.firecrawl.dev/introduction", "https://github.com/firecrawl/firecrawl"]
}
```

Every item points at the page it came from via `source_url`, so a consuming site can show attribution and link out.
Facts here are quoted from the product's own public pages; follow the links for the authoritative version.
