# Status and known gaps

BotsHub Agent is **alpha**, built on [AAA](https://innerkorehq.github.io/AutonomousAgentArchitecture/), which is itself 0.x.

## Verified

- Discovery run against live GitHub, Hacker News and SearXNG (266 signals → 221 candidates).
- Three enrichments against a live gateway (Firecrawl, claude-mem, Browser Use): all published, and an independent audit confirmed **79 of 79 stored facts** have their quote on the cited page.
- 25 offline tests; MLX critic and embeddings smoke-tested.

## Known gaps

- **Lightly live-tested.** No repo-only candidate enriched yet, `BOTSHUB_RUN_MAX_COST` tripping not verified, and cost per listing not measured.
- **Run-to-run variance.** A re-run can drop a fact the previous run found. Grounding guarantees what is kept is on the page, not that everything on the page is kept.
- **Weak-signal facts.** Integrations can come from "works with X" prompts, "free trial" from a one-time credit, and plan names from table text.
- **Local models are limited.** No tool calling; a 2B critic is a soft check.
- **Reasoning models need config** (`BOTSHUB_LLM_PARAMS=reasoning_effort=none`).
- **No headless-browser screenshots.** JS-only sites yield thin pages and get held.
- **Semantic merge is conservative** and merged nothing in the first run.
- **Prompt-injection defence** is line filtering, data-only prompting, grounding and own-site-only research tools. AAA `tool_result` guardrails are not used because page text is not returned through tools.
- **Auto-publish has no human review.** Audit a sample; keep `unpublish` handy.

## Related

- [AAA documentation](https://innerkorehq.github.io/AutonomousAgentArchitecture/) · [AAA on GitHub](https://github.com/innerkorehq/AutonomousAgentArchitecture) · [AAA status and limitations](https://innerkorehq.github.io/AutonomousAgentArchitecture/about/limitations/)

## License

MIT. See [LICENSE](https://github.com/innerkorehq/botshub-agent/blob/master/LICENSE).
