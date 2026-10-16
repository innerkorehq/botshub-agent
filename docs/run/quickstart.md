# Quickstart

Requirements: Python 3.12+, [`uv`](https://docs.astral.sh/uv/), an OpenAI-compatible LLM endpoint, and (optionally) Docker for SearXNG.

```bash
git clone https://github.com/innerkorehq/botshub-agent
cd botshub-agent
uv sync                                  # add --extra local for MLX models, --extra docs to build this site
cp .env.example .env                     # then edit: gateway URL, key, model
```

Load the environment (the CLI reads plain environment variables):

```bash
set -a && . ./.env && set +a
```

## SearXNG (recommended)

```bash
docker compose up -d searxng             # serves JSON on http://localhost:8888
```

The bundled `deploy/searxng/settings.yml` enables `format=json`, which the agent requires. Without SearXNG, GitHub and Hacker News still work.

## Run

```bash
uv run botshub discover                  # collect, score, merge, classify
uv run botshub status                    # ranked candidates with ids
uv run botshub enrich --limit 3          # top 3 classified candidates
uv run botshub enrich --id 12            # one specific candidate (re-extracts)
uv run botshub refresh                   # re-check published listings older than BOTSHUB_REFRESH_DAYS
uv run botshub export                    # (re)write ./out/agents/*.json
uv run botshub unpublish <slug>          # kill switch
uv run botshub serve --hours 24          # discover → enrich → refresh → export, on a loop
```

## Enrich more than the daily cap

`enrich --limit N` overrides `BOTSHUB_DAILY_ENRICH_CAP`. Set `BOTSHUB_SCORE_THRESHOLD=0` to include low scores. Only `classified` candidates are
enriched: run `discover` again to classify more (40 per run), and retry `held` ones individually with `enrich --id`.
Repo-only candidates (no website) are supported: the README and repo metadata are the sources, and the repo's `homepage` field is followed if it exists.

!!! tip "Budget your first run"
    Each enrichment is many LLM calls (extract, optional retries, price votes, overview, critic). Set `BOTSHUB_PRICE_IN`, `BOTSHUB_PRICE_OUT` and
    `BOTSHUB_RUN_MAX_COST` so AAA's `Budget(max_cost=...)` can actually trip; without prices the cost cap never fires.
