"""The anti-invention core: a claim survives only if its quote is verbatim on a page we fetched."""

from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from aaa.validate import Verdict
from pydantic import ValidationError

from botshub_agent.models import CATEGORIES, Extraction, Evidence

_PUNCT = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "})
MIN_QUOTE = 12
_NUM = re.compile(r"\d[\d,.]*")


def norm(s: str) -> str:
    s = s.translate(_PUNCT).lower()
    s = re.sub(r"[*_`#>|\[\]()]+", " ", s)  # markdown decoration
    return re.sub(r"\s+", " ", s).strip()


def _url(u: str) -> str:
    p = urlsplit(u.strip())
    return f"{p.hostname or ''}{p.path.rstrip('/')}".lower().removeprefix("www.")


def numbers(s: str) -> set[str]:
    return {n.strip(".,") for n in _NUM.findall(s.translate(_PUNCT)) if n.strip(".,")}


def tokens(s: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", norm(s)) if len(t) >= 3]


def supported(value: str, quote: str, thr: float = 0.6) -> bool:
    """Most of the value's significant words, and ALL its numbers, appear in the quote."""
    if not numbers(value) <= numbers(quote):
        return False
    toks = tokens(value)
    if not toks:
        return True
    q = set(tokens(quote))
    return sum(t in q for t in toks) / len(toks) >= thr


class Grounder:
    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = {_url(u): norm(t) for u, t in pages.items()}

    def check(self, ev: Evidence, value: str | None = None, thr: float = 0.6, strict: bool = False,
              min_quote: int = MIN_QUOTE) -> str | None:
        q = norm(ev.quote)
        if len(q) < min_quote:
            return f"quote too short ({ev.quote!r}); copy a full sentence or phrase"
        text = self.pages.get(_url(ev.source_url))
        if text is None:
            return f"source_url {ev.source_url} was not fetched; use one of the provided page URLs"
        if q not in text:
            return f"quote not found verbatim on {ev.source_url}: {ev.quote[:80]!r}"
        if value is not None:
            ok = norm(value).replace(" ", "") in q.replace(" ", "") if strict else supported(value, ev.quote, thr)
            if not ok:
                return f"value {value[:60]!r} is not supported by its quote"
        return None


def ground(ex: Extraction, pages: dict[str, str]) -> tuple[Extraction, list[str]]:
    """Return (only the supported claims, problems for the rejected ones)."""
    g, problems = Grounder(pages), []

    def keep(label: str, item, value: str | None, **kw) -> bool:
        p = g.check(item, value, **kw)
        if p:
            problems.append(f"{label}: {p}")
        return p is None

    out = Extraction(category=ex.category if ex.category in CATEGORIES else None)
    if ex.category and out.category is None:
        problems.append(f"category must be one of {CATEGORIES}")
    for fld in ("name", "tagline", "description", "free_trial"):
        v = getattr(ex, fld)
        if v and keep(fld, v, v.value, strict=(fld == "name"), thr=0.5):
            setattr(out, fld, v)
    if ex.website:
        host_ok = _url(ex.website.value).split("/")[0] in {u.split("/")[0] for u in g.pages} or \
            _url(ex.website.value).split("/")[0] in norm(ex.website.quote).replace(" ", "")
        if host_ok and keep("website", ex.website, None):
            out.website = ex.website
        elif not host_ok:
            problems.append(f"website {ex.website.value}: host is neither a fetched page nor in its quote")
    out.features = [f for f in ex.features if keep(f"feature {f.name!r}", f, f"{f.name} {f.description}", thr=0.5)]
    out.use_cases = [u for u in ex.use_cases if keep(f"use case {u.title!r}", u, u.title, thr=0.5)]
    out.integrations = [i for i in ex.integrations if keep(f"integration {i.name!r}", i, i.name, strict=True)]
    # The price must be in its quote and the plan name must precede it on the page (see _name_near_price).
    out.pricing = [t for t in ex.pricing if keep(f"pricing {t.name!r}", t, t.price, strict=True, min_quote=2)
                   and _name_near_price(g, t, problems)]
    out.deployment = [d for d in ex.deployment if keep("deployment", d, d.value, thr=0.5)]
    out.target_audience = [d for d in ex.target_audience if keep("audience", d, d.value, thr=0.5)]
    return out, problems


def canonicalize_sources(ex: Extraction, pages: dict[str, str]) -> Extraction:
    """Rewrite each claim's source_url to the exact URL of the stored page it matched."""
    by_key = {_url(u): u for u in pages}

    def fix(obj):
        if isinstance(obj, Evidence):
            obj.source_url = by_key.get(_url(obj.source_url), obj.source_url)
        elif isinstance(obj, list):
            for o in obj:
                fix(o)

    for fld in Extraction.model_fields:
        fix(getattr(ex, fld))
    return ex


def _name_near_price(g: Grounder, t, problems: list[str], window: int = 400) -> bool:
    """Pricing tables put plan name and price on different lines, so no single quote spans both.
    Bind them deterministically instead: the name must occur on the page shortly BEFORE the price."""
    text = (g.pages.get(_url(t.source_url)) or "")
    flat, price, name = text.replace(" ", ""), norm(t.price).replace(" ", ""), norm(t.name).replace(" ", "")
    if price in ("", "custom", "free", "contactsales") and name in flat:
        return True  # non-numeric tiers: the name on the page is all there is to check
    i = flat.find(name)
    while i != -1:
        if price in flat[i : i + len(name) + window]:
            return True
        i = flat.find(name, i + 1)
    problems.append(f"pricing {t.name!r}: price {t.price!r} does not appear within {window} characters after the plan name on {t.source_url}")
    return False


def parse_extraction(candidate: str) -> Extraction:
    s = candidate.strip()
    if s.startswith("```"):
        s = re.sub(r"^```\w*\n?|```$", "", s).strip()
    return Extraction.model_validate(json.loads(s))


def parse_lenient(candidate: str) -> Extraction:
    """Salvage path: validate field by field and item by item, dropping whatever is malformed
    (e.g. a bare string where an evidence object was required) instead of discarding the whole answer."""
    s = candidate.strip()
    if s.startswith("```"):
        s = re.sub(r"^```\w*\n?|```$", "", s).strip()
    raw = json.loads(s)
    out: dict = {}
    for name, field in Extraction.model_fields.items():
        if name not in raw or raw[name] is None:
            continue
        if isinstance(raw[name], list):
            ok = []
            for item in raw[name]:
                try:
                    Extraction.model_validate({name: [item]})
                    ok.append(item)
                except ValidationError:
                    pass
            out[name] = ok
        else:
            try:
                Extraction.model_validate({name: raw[name]})
                out[name] = raw[name]
            except ValidationError:
                pass
    return Extraction.model_validate(out)


def make_validator(get_pages):
    """get_pages(context) -> {url: text}. Registered on ValidationHandler as 'facts'."""

    def validate(candidate: str, context: dict) -> Verdict:
        try:
            ex = parse_extraction(candidate)
        except (ValueError, ValidationError) as e:
            return Verdict(ok=False, feedback=f"Reply with ONLY valid JSON matching the schema. Error: {str(e)[:300]}")
        _, problems = ground(ex, get_pages(context))
        if not problems:
            return Verdict(ok=True)
        shown = "; ".join(problems[:8]) + (f" (+{len(problems) - 8} more)" if len(problems) > 8 else "")
        return Verdict(
            ok=False,
            feedback="Remove or fix every unsupported claim. Never guess; use null/[] when the pages do not say it. Each quote must be ONE short contiguous snippet copied exactly (never stitch lines together or skip text between them). "
            f"Problems: {shown}",
        )

    return validate
