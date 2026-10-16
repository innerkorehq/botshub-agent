"""The one generated artifact: overview prose written ONLY from validated facts, with checkable citations."""

from __future__ import annotations

import re

from aaa.algos import GeneratorCritic

from botshub_agent.enrich.grounding import numbers

SECTIONS = ("Who it's for", "What you get", "How it works")
CITE = re.compile(r"\[f(\d+)\]")

GEN_SYSTEM = (
    "You write the overview of a software product for an AI-agent directory, using ONLY the numbered facts provided. "
    "Write three short sections with these exact markdown headings: '## Who it's for', '## What you get', '## How it works'. "
    "Every sentence MUST end with citation tags like [f3] or [f2][f7] naming the facts that support it. "
    "No claims, numbers, or product names beyond the facts. No marketing adjectives. Omit a section if the facts do not support it. "
    "Do not mention that you used facts."
)
CRITIC_SYSTEM = (
    "You are a strict fact-checker. Judge whether EVERY sentence of the candidate overview is fully supported by the cited facts "
    'in the task, adds nothing unsupported, and contains no promotional fluff. Reply with JSON {"ok": bool, "feedback": "specific fixes"}.'
)


def overview_algo(model: str | None, critic_model: str | None) -> GeneratorCritic:
    return GeneratorCritic(critic_system=CRITIC_SYSTEM, critic_model=critic_model or None,
                           system=GEN_SYSTEM, model=model, max_iterations=2, params={"temperature": 0.2})


def fact_lines(facts: list[dict]) -> str:
    return "\n".join(f'[f{i}] {f["field"]}: {f["value"]} | evidence: "{f["quote"][:240]}"' for i, f in enumerate(facts, 1))


def check(text: str, facts: list[dict]) -> list[str]:
    """Deterministic: each sentence cites existing facts; numbers appear in cited facts."""
    problems, n = [], len(facts)
    body = [l for l in text.splitlines() if l.strip() and not l.startswith("#")]
    if not body:
        return ["empty overview"]
    for line in body:
        for sent in re.findall(r"\S.*?(?:(?:\[f\d+\])+|$)", line.strip()):
            if not sent.strip():
                continue
            ids = [int(i) for i in CITE.findall(sent)]
            if not ids or any(i < 1 or i > n for i in ids):
                problems.append(f"uncited or invalid citation: {sent[:70]!r}")
                continue
            support = " ".join(facts[i - 1]["value"] + " " + facts[i - 1]["quote"] for i in ids)
            if not numbers(CITE.sub("", sent)) <= numbers(support):
                problems.append(f"number not in cited facts: {sent[:70]!r}")
    return problems


def strip_tags(text: str) -> str:
    return re.sub(r"\s*\[f\d+\]", "", text).strip()


def sections(text: str) -> dict[str, str]:
    out, cur = {}, None
    for line in strip_tags(text).splitlines():
        if line.startswith("##"):
            cur = line.lstrip("# ").strip()
            out[cur] = ""
        elif cur and line.strip():
            out[cur] += (" " if out[cur] else "") + line.strip()
    return {k: v for k, v in out.items() if v}


def template_overview(facts: list[dict]) -> str:
    """Safe fallback: restate the best description fact verbatim (no generation at all)."""
    for fld in ("description", "tagline"):
        for i, f in enumerate(facts, 1):
            if f["field"] == fld:
                return f"## What you get\n{f['value'].rstrip('.')}. [f{i}]"
    return ""
