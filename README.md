# botshub-agent

Autonomous agent for botshub.com, built on [AAA](https://innerkorehq.github.io/AutonomousAgentArchitecture/) (Autonomous Agent Architecture).

**Documentation: https://innerkorehq.github.io/botshub-agent/** · [Using AAA](https://innerkorehq.github.io/botshub-agent/aaa/) · [AAA docs](https://innerkorehq.github.io/AutonomousAgentArchitecture/) · [AAA on GitHub](https://github.com/innerkorehq/AutonomousAgentArchitecture)

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

## Local models (Apple silicon)
`uv sync --extra local`, then set `BOTSHUB_CRITIC_MODEL` / `BOTSHUB_EMBED_MODEL` to HF ids starting with `mlx-community/`
(see `.env.example`). Those ids run in-process via MLX (`src/botshub_agent/local.py`); every other model id still goes to
the OpenAI-compatible gateway. Local models cannot do tool calling, so keep the main `BOTSHUB_LLM_MODEL` on the gateway.

## Added in this pass
- **sqlite-vec tie-break** (`discovery/semantic.py`): set `BOTSHUB_EMBED_MODEL` (+ `BOTSHUB_EMBED_DIM`). Merges a repo and a site only if cosine >= `BOTSHUB_SEMANTIC_THRESHOLD` (0.90) AND normalised names match AND kinds differ. Logged in `merges`.
- **Gap-filler** (`enrich/gapfill.py`, `tools/research.py`): bounded `ToolLoop` (6 turns) runs only when pricing is missing or features < minimum. Its tools can read only the candidate's own site / GitHub repo (allow-list comes from our DB, not the model). It stores pages; facts still pass grounding.
- **Price vote** (`enrich/pricevote.py`): `SelfConsistency` per extracted tier (`BOTSHUB_PRICE_VOTES`, default 3); a price is kept only with >= 2/3 agreement, otherwise dropped and noted in `publish_log`.

## Known gaps
- **Live-tested lightly.** One discovery run and three enrichments (`firecrawl`, `claude-mem`, `browser-use`) against real GitHub, HN, SearXNG and a gateway. No repo-only candidate (no website) enriched yet, `BOTSHUB_RUN_MAX_COST` tripping not verified, and LLM cost per listing not measured. AAA itself is alpha (0.x).
- **Run-to-run variance.** Extraction is an LLM call: a re-run can drop a fact the previous run found (e.g. one `browser-use` price tier). Grounding guarantees what is kept is on the page, not that everything on the page is kept. A worse re-run never replaces a published listing (`kept_previous`).
- **Weak-signal facts.** Integrations can come from "works with X" prompts, "free trial" from a one-time credit, and plan names from table text, so listings can read slightly off. Pricing binds a plan to a price by proximity on the page plus a 3-way vote, not by a single quote.
- **Local models are limited.** MLX models (critic, embeddings) cannot do tool calling; the main model must stay on the gateway. The 2B Gemma critic is a soft check; the code-level citation check is the real gate.
- **Reasoning models need config.** `gpt-6-luna` rejects `temperature=0` and tool calls unless `BOTSHUB_LLM_PARAMS=reasoning_effort=none`.
- **No headless-browser screenshots.** JS-only sites yield thin pages and get held; media is limited to images and videos found in HTML or README.
- **No semantic merge seen in practice.** The sqlite-vec tie-break is deliberately conservative (cosine, name match and repo-vs-site pair); it merged nothing in the first run.
- **Prompt-injection defence** is line filtering, data-only prompting, grounding and own-site-only research tools. AAA `tool_result` guardrails are not used because page text is not returned through tools (the gap-filler preview is filtered).
- **Auto-publish has no human review.** Audit a random sample and keep `unpublish` handy.

## License

MIT, see [LICENSE](LICENSE). Built on [AAA](https://github.com/innerkorehq/AutonomousAgentArchitecture) (also MIT).
