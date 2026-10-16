# Testing

```bash
uv run pytest -q
```

25 tests run with **no network and no tokens**: AAA's `FakeLLMClient` scripts the model, and `httpx.MockTransport` serves GitHub, HN, SearXNG and the web.

| Test | What it proves |
|---|---|
| `test_units.py` | canonical keys; conservative merging; scoring (velocity beats size, decay, corroboration); **grounding rejects invented features, prices and sources**; price/plan binding; vote-answer parsing; overview citations; gate; listing versions and unpublish |
| `test_pipeline.py` | end to end: a fake model "invents" a feature, and it never reaches the listing; every stored quote exists on its page; unchanged sources skip re-extraction; **a weaker re-run never replaces a published listing** |
| `test_discovery.py` | the discovery DAG with one source returning HTTP 500 still completes, merges and classifies |
| `test_gaps.py` | the gap-filler finds pricing on the product's own site and **refuses an off-site URL**; a disagreeing vote drops the price; sqlite-vec merges a repo and a site but not a stranger |
| `test_local.py` | model routing, plus a smoke test of the real MLX models when cached |

Test the model-facing parts against the model you ship: scripted models cannot reveal format drift like the ones in [Lessons](../aaa/lessons.md).
