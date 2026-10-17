# Enrichment and grounding

Enrichment turns a candidate into a listing. The central rule: **a claim survives only if it is quoted verbatim from a page we downloaded.**

## 1. Fetch

Pages are fetched through AAA's SSRF-hardened `SafeFetcher` (public hosts only, https preferred, size caps, redirect re-checks) and stored in
SQLite with a content hash: homepage, README and repo metadata, and likely `/pricing`, `/features`, `/integrations`, docs pages discovered from homepage links.

## 2. Extract

`GenerateTestRepair` asks the model for JSON in which **every** claim has `source_url` and `quote`. Page text is wrapped as untrusted data,
and lines matching injection patterns are stripped before the model sees them.

```json
{ "features": [ { "name": "Draft replies", "description": "Draft replies for human approval",
                  "source_url": "https://zorbo.ai/", "quote": "Draft replies for human approval" } ] }
```

## 3. Verify (the anti-invention core)

`enrich/grounding.py` checks each claim:

| Check | Rejects |
|---|---|
| `source_url` is a page we fetched | citing pages we never saw |
| `quote` appears in that page (case/whitespace/markdown-normalised, ≥12 chars) | fabricated or stitched quotes |
| value is supported by its quote (most words, **all numbers**) | a real quote with an embellished claim |
| integrations and prices appear exactly in the quote | invented partners or prices |
| plan name precedes its price within 400 chars on the page | wrong plan/price pairing |

Rejected items produce precise feedback, the model retries (up to 3 times), and anything still unsupported is **dropped**. Salvage parses
leniently, field by field, so one malformed item never discards the rest.

## 4. Fill gaps

If pricing is missing or there are fewer than three features, a bounded `ToolLoop` (6 turns) may `site_search` and `read_page`, **but only on the
product's own website or GitHub repo**. The allowed host comes from our database via trusted run metadata, never from the model. It only stores pages;
facts still pass step 3.

## 5. Confirm prices

For every extracted tier, a three-sample `SelfConsistency` vote asks the plan's price. A tier is kept only with ≥ 2/3 agreement.
Unconfirmed prices are dropped and noted in `publish_log`.

## 6. Overview

The only generated prose. A generator sees **only the numbered, validated facts** and must end every sentence with citations like `[f3]`.
A second model (`GeneratorCritic`) fact-checks it, then plain code verifies that each sentence cites existing facts and that every number
appears in the cited facts. If anything fails, the fallback is the verbatim description fact. Nothing is ever shown that was not checked.

## 7. Media: videos, screenshots, images

Collected by code, never by the model, in this order of preference:

1. **Videos.** YouTube/Vimeo/Loom embeds and links found on the product's own pages and README, plus `.mp4`/`.webm` files. If fewer than two are found,
   a SearXNG video search proposes more, and each result is **proved relevant** through YouTube's oEmbed endpoint: the product name must appear in the
   video title or channel name (names made only of generic words such as "AI Agent" never match).
2. **Screenshots we capture ourselves.** A headless browser (Playwright; bundled Chromium, falling back to system Chrome) renders the product's homepage,
   pricing and features pages at 1440×900 and saves JPEGs. Cookie and chat overlays are hidden by CSS, and blank captures (< 20 KB) are discarded.
   For a repo-only candidate the GitHub page is captured. Every request the browser makes (page, redirects, scripts, images) is vetted with the same SSRF rules
   as the fetcher, so a hostile page cannot use the browser to reach private addresses.
3. **Page images.** `<img>` tags and README images, with logos, icons, badges, sponsors, testimonials, star-history charts, QR codes and comparison/social cards
   filtered out. Next.js image-proxy variants collapse to one asset, and each remaining image is checked with a `HEAD` request (real image, minimum size). The
   `og:image` social card is the last resort.

Screenshots are written to `data/media/<candidate>/` and copied next to the exported JSON as `out/media/<slug>/…`; videos and images are stored as references
with their source page. Backfill existing listings, with no LLM calls, using `botshub media`.

## 8. Publish gate

Deterministic. A listing publishes only with a name, a website or repo, an evidence-backed overview, at least three evidenced features, a category
and quality ≥ 0.6. Otherwise it is **held** with reasons. A held or failed re-run never replaces a published listing.

!!! warning "There is no human in the loop"
    Auto-publish is a design choice, and quote verification makes invented facts very unlikely, but it cannot catch a page that is itself wrong,
    or a true-but-misleading quote. Audit a random sample and keep `botshub unpublish <slug>` handy.
