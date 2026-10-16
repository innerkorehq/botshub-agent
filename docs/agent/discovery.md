# Discovery

Goal: a ranked list of AI agents that are *getting popular*, not merely popular.

## Sources

| Source | Tool | Signal |
|---|---|---|
| GitHub | `gh_search` (topics `ai-agent`, `mcp-server`, `llm-agent`, `ai-agents`; pushed in last 30 days; ≥20 stars) | stars, forks, homepage |
| Hacker News | `hn_search` (Algolia API; last 14 days, >5 points) | points, comments |
| SearXNG | `searx_search` (templated queries) and `count_mentions` | URLs, and number of *distinct other domains* mentioning a product |

All collectors are ordinary AAA `@tool`s running as nodes of one `WorkflowGraph`. Each has `on_error="continue"`: in the first live run a source can
fail (rate limit, outage) and the others still land. Signals are stored as **dated rows** and never overwritten, which is what makes velocity possible.

## Scoring

Pure functions in `discovery/scoring.py`:

- `pop = log1p(stars)`
- `vel = Δstars over 7 days / sqrt(max(stars, 50))`. The square root damps giants, so a 3k-star project gaining 500 stars outranks a 100k-star one gaining 500.
- `hn = log1p(points) + 0.5·log1p(comments)`
- `mentions = log1p(distinct other domains)` (corroboration, not raw hit counts)

Each component is converted to a **percentile** within the current pool, weighted (default 0.30 / 0.35 / 0.15 / 0.20), multiplied by
time decay (half-life 14 days) and a corroboration bonus of `1 + 0.15 × (sources − 1)`.

## Entity resolution

A repo, its website and a Hacker News post should become one candidate.

1. **Canonical keys.** `gh:owner/repo` for repositories, `web:host` for product sites; aggregator hosts (Medium, Reddit, YouTube, ...) are ignored.
2. **Rule merge.** A repo and a site merge when the normalised names match *and* the host root equals the name.
3. **Embedding tie-break (sqlite-vec).** A repo and a site merge only if cosine similarity ≥ 0.90 **and** names match **and** their kinds differ.
   Two repos or two sites are different products until proven otherwise. Every merge is recorded in `merges` with its reason.

The bias is deliberate: **prefer a duplicate over a wrong merge**.

## Classification

For candidates with a worthwhile score, one cheap structured call asks "is this an AI agent / MCP server / agent product, and which of 12 categories?".
Output is JSON validated against a Pydantic model (`GenerateTestRepair`); confidence between 0.4 and 0.6 triggers a three-sample `SelfConsistency` vote on the label.

## Run it

```bash
uv run botshub discover   # collect, score, merge, classify (about 2.5 minutes for 266 signals)
uv run botshub status     # ranked candidates
```

In our first live run this produced 221 candidates from 266 signals; 32 of the 40 highest-scored were classified as agents.
