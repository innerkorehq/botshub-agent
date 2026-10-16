# botshub-agent

Autonomous agent for botshub.com, built on [AAA](https://innerkorehq.github.io/AutonomousAgentArchitecture/).
Finds trending AI agents (GitHub, Hacker News, SearXNG), builds listings **only from facts it fetched**
(every claim stored with a verbatim quote from a page we downloaded), stores everything in SQLite and
exports JSON per agent.

```bash
cp .env.example .env && set -a && . ./.env && set +a
docker compose up -d searxng          # optional but recommended
uv run botshub discover               # collect signals, score, classify
uv run botshub status                 # ranked candidates
uv run botshub enrich --limit 5       # fetch, extract, gate, publish -> ./out/agents/<slug>.json
uv run botshub enrich --id 12         # one candidate (forces re-extraction)
uv run botshub refresh                # re-check published listings older than BOTSHUB_REFRESH_DAYS
uv run botshub unpublish <slug>       # kill switch
uv run botshub serve --hours 24       # daily loop
uv run pytest
```

## Algorithms (see `src/botshub_agent/algos.py`)
| Stage | AAA algorithm | Why |
|---|---|---|
| Collect + score | `WorkflowGraph` | steps known, parallel, zero LLM cost |
| Is it an agent? | `GenerateTestRepair` (+ `SelfConsistency` when confidence 0.4-0.6) | schema-checked; exact label votes |
| Fetch pages | single-tool `WorkflowGraph` per URL | SSRF-hardened, timeouts, fenced retries |
| Extract facts | `GenerateTestRepair` with `facts` validator | code rejects any claim whose quote is not on the page |
| Overview prose | `GeneratorCritic` + code citation check | the only generated text; falls back to a verbatim fact |

Data: `data/botshub.db` (our tables), `data/aaa_events.db` (AAA event log; **one engine process per file**).

## Added in this pass
- **sqlite-vec tie-break** (`discovery/semantic.py`): set `BOTSHUB_EMBED_MODEL` (+ `BOTSHUB_EMBED_DIM`). Merges a repo and a site only if cosine >= `BOTSHUB_SEMANTIC_THRESHOLD` (0.90) AND normalised names match AND kinds differ. Logged in `merges`.
- **Gap-filler** (`enrich/gapfill.py`, `tools/research.py`): bounded `ToolLoop` (6 turns) runs only when pricing is missing or features < minimum. Its tools can read only the candidate's own site / GitHub repo (allow-list comes from our DB, not the model). It stores pages; facts still pass grounding.
- **Price vote** (`enrich/pricevote.py`): `SelfConsistency` per extracted tier (`BOTSHUB_PRICE_VOTES`, default 3); a price is kept only with >= 2/3 agreement, otherwise dropped and noted in `publish_log`.

## Known gaps
- Not run against a live gateway/SearXNG yet; AAA itself is alpha (0.x).
- No headless-browser screenshots (JS-only sites yield thin pages and get held).
- Prompt-injection defence is line filtering + data-only prompting + grounding + own-site-only research tools; AAA `tool_result` guardrails are not used because page text is not returned through tools (the gap-filler preview is filtered).
- Auto-publish has no human review: audit a random sample and keep `unpublish` handy.
