# Patterns we used

Reusable shapes that came out of building this agent on AAA.

## Verification gate: let code veto the model

`GenerateTestRepair` takes a *named validator*, any function `(candidate, context) -> Verdict`. We use it as an anti-hallucination gate:

```python
--8<-- "src/botshub_agent/enrich/grounding.py:make_validator"
```

The validator parses the model's JSON, checks every claim against pages stored in SQLite, and returns specific feedback
(`quote not found verbatim on <url>`). The model sees that feedback and retries, up to `max_iterations`.
If it never converges, AAA returns `no_solution` *with the best candidate*, and we **salvage** only the claims that pass the same check.
The result: output is never worse than "fewer facts", never "invented facts".

## Single-tool graphs for fan-out

`WorkflowGraph` nodes are static, but "fetch these N URLs" is dynamic. Instead of building a graph per call, define a one-node graph whose
arguments are the run input, and run it many times concurrently:

```python
def _one(tool, name):
    return _named(WorkflowGraph([Node("r", tool, args=lambda r, inp: dict(inp))], output="r"), name)

results = await asyncio.gather(*(engine.run(fetch_page_graph, {"candidate_id": 7, "url": u}) for u in urls))
```

You keep tool timeouts, pools and logging, with no per-call algorithm objects.

## One long-lived instance per role, unique names

AAA routes and recovers runs **by algorithm name**, and registering a new instance each call leaks a bus subscription. So we create
each algorithm once, give it a unique `name`, and put per-run variation in the run **input**, not the object.

```python
algo.name = "bh_extract"
```

## Trusted run metadata for tool scoping

A bounded `ToolLoop` may only read pages on a product's own site. The allowed host must not come from the model, so it travels in the
run's **metadata** (trusted application data) and the tool reads it from `ToolContext`:

```python
await engine.run(gapfill_loop, {"task": "..."}, meta={"candidate_id": 42})

@tool
async def read_page(url: str, ctx: ToolContext) -> dict:
    cand = repo.candidate(int(ctx.meta["candidate_id"]))
    if not allowed(url, cand):
        raise ToolError("only pages on the product's own website or GitHub repo may be read")
```

`ctx` is injected and hidden from the model's schema, so a prompt injection cannot change which candidate it is acting for.

## Route models, not agents

AAA's `LLMHandler` talks to one `LLMClient`. To run a small critic and an embedding model **locally** while extraction stays on a hosted
gateway, we wrap two clients in a router that dispatches on the model id:

```python
--8<-- "src/botshub_agent/local.py:routing"
```

Anything starting with `mlx-community/` runs in-process on Apple silicon ([setup](../run/local-models.md)); everything else goes to the
OpenAI-compatible gateway. Algorithms never know the difference.

## Crash safety on SQLite

`SQLiteEventStore` is WAL-mode and crash-safe, with **one engine process per file**. We keep AAA's event log (`aaa_events.db`) separate from
our business tables (`botshub.db`), run a single worker process with asyncio concurrency, and call `engine.recover(all_algorithms)` at startup.

## "Never downgrade" for re-runs

Re-enriching a published listing can fail (LLM outage, thin page). Rather than overwrite good data, the pipeline records `kept_previous` and
leaves the live listing alone. Idempotent upserts keyed by slug mean replays never duplicate.
