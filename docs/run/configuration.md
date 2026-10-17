# Configuration

All settings are environment variables ([`config.py`](https://github.com/innerkorehq/botshub-agent/blob/master/src/botshub_agent/config.py)).
Empty values count as unset.

| Variable | Default | Meaning |
|---|---|---|
| `BOTSHUB_LLM_BASE_URL` | `https://api.openai.com/v1` | any OpenAI-compatible endpoint |
| `BOTSHUB_LLM_API_KEY` | | bearer token |
| `BOTSHUB_LLM_MODEL` | `gpt-4o-mini` | main model (extraction, classification, overview, gap-filler) |
| `BOTSHUB_LLM_PARAMS` | | default request params, `k=v,k2=v2` (e.g. `reasoning_effort=none`) |
| `BOTSHUB_CRITIC_MODEL` | same as main | overview fact-checker; can be a [local MLX model](local-models.md) |
| `BOTSHUB_EMBED_MODEL` | | embedding model for the semantic merge; empty disables it |
| `BOTSHUB_EMBED_DIM` | auto | auto-detected by probing the model |
| `BOTSHUB_SEMANTIC_THRESHOLD` | `0.90` | cosine similarity needed to merge a repo with a site |
| `BOTSHUB_LOCAL_PREFIXES` | `mlx-community/` | model ids with these prefixes run in-process on MLX |
| `BOTSHUB_PRICE_IN` / `_OUT` | | $ per million tokens, required for cost caps |
| `BOTSHUB_RUN_MAX_COST` | | per-run cost cap |
| `BOTSHUB_PRICE_VOTES` | `3` | samples per price vote |
| `BOTSHUB_SCREENSHOTS` | `3` | pages to screenshot per listing (homepage, pricing, features); `0` disables |
| `BOTSHUB_FIND_VIDEOS` | `1` | search for demo videos when the product's pages have fewer than two |
| `BOTSHUB_SEARXNG_URL` | `http://localhost:8888` | SearXNG with `json` enabled |
| `GITHUB_TOKEN` | | raises GitHub API rate limits |
| `BOTSHUB_SCORE_THRESHOLD` | `40` | minimum score to enrich |
| `BOTSHUB_DAILY_ENRICH_CAP` | `10` | default `--limit` for `enrich` |
| `BOTSHUB_MIN_QUALITY` | `0.6` | publish gate threshold |
| `BOTSHUB_REFRESH_DAYS` | `7` | when a published listing is re-checked |
| `BOTSHUB_DATA_DIR` / `BOTSHUB_EXPORT_DIR` | `./data` / `./out` | where SQLite files and JSON go |

Search topics, queries, scoring weights and the minimum feature count are in `Settings` and are easy to change in code.
