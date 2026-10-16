# Lessons from a live run

AAA's [status page](https://innerkorehq.github.io/AutonomousAgentArchitecture/about/limitations/) is candid about what upstream has and has not verified.
These are the sharp edges we hit running this agent against a real gateway, GitHub, Hacker News and SearXNG. None are AAA bugs; they are
the integration details that tutorials skip.

!!! success "What worked first time"
    The engine, `WorkflowGraph` fan-out with `on_error="continue"`, SQLite event store, validators, `GeneratorCritic`, `SelfConsistency`,
    the SearXNG search provider and the SSRF-hardened fetcher all behaved as documented. A local MLX critic (Gemma) correctly accepted a
    supported overview and rejected one with invented claims.

## 1. Reasoning models reject common parameters

`gpt-6-luna` answered HTTP 400 for `temperature=0`, and for any request with tools unless `reasoning_effort` was `none`.
`LLMConfig.params` is the right place for default request parameters:

```bash
BOTSHUB_LLM_PARAMS=reasoning_effort=none
```

Check your model against your gateway before relying on `temperature` in an algorithm.

## 2. `http_fetch` returns text, not HTML

The built-in fetcher reduces HTML to text, which is what you want for the model and not for extracting images, links or embeds.
`SafeFetcher._target` does the SSRF vetting, so we subclassed `SafeFetcher` to keep the raw body while reusing its address pinning,
redirect and size rules. (A first-class "raw" option upstream would remove the need.)

## 3. Algorithm names are identity

Run routing and `recover()` are by `name`. Two `GenerateTestRepair` instances share a default name; give each role its own,
and create them once. See [patterns](patterns.md#one-long-lived-instance-per-role-unique-names).

## 4. Models ignore your output marker

`SelfConsistency` extracts an answer with a function you supply. Our prompt said "end with `Answer: <price>`"; the model sometimes wrote
`**Answer:** $16`, sometimes `{"price": "$16/month"}`, sometimes escaped newlines. Votes silently became `none` until the extractor
tolerated all of those. **Test your extractor against the model you ship**, and make a miss fail safe (a dropped price, never a wrong one).

## 5. Verbatim quotes and tables do not mix

Requiring a verbatim quote is a strong anti-hallucination rule, but pricing tables put "Hobby" and "$16" on different lines, and models
"helpfully" stitched the lines together, producing quotes that exist nowhere on the page. We kept the strict check for text claims and, for
prices, required the price to be in its quote plus the **plan name to precede it within 400 characters** on the page, then confirmed with a vote.

## 6. A failed run must not destroy a good result

One of our own shell mistakes ran enrichment without the API key. Every LLM call failed, and three published listings were overwritten with
empty ones (failing *closed*, but still data loss). Now a weaker re-run records `kept_previous` and leaves the live listing alone.
The listing history (`listing_versions`) also keeps every prior version.

## 7. Empty environment variables

`BOTSHUB_EMBED_DIM=` (set but empty) crashed `int()` at import. Treat empty as unset when reading numeric settings.

## 8. MLX in-process: one thread, no thinking

MLX state is thread-bound, so loading and generating run on one dedicated worker thread. Gemma 4 "thinks" by default and spends the
token budget on a scratchpad; pass `enable_thinking=False` to the chat template. See [local models](../run/local-models.md).
