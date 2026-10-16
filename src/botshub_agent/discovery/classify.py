"""Is this an AI agent product, and which category? Cheap structured call; vote when borderline."""

from __future__ import annotations

import json

from aaa.algos import GenerateTestRepair, SelfConsistency
from aaa.validate import json_validator

from botshub_agent.models import CATEGORIES, Classification

SYSTEM = (
    "You classify software projects for an AI-agent directory. An 'agent' is software that uses an LLM to act "
    "autonomously or exposes tools to AI assistants (including MCP servers and agent frameworks/products). "
    "Libraries for training models, datasets, prompt lists and tutorials are NOT agents. "
    f"Categories: {CATEGORIES}. Reply with ONLY JSON: "
    '{"is_agent": bool, "category": <one category>, "confidence": 0..1, "reason": "<short>"}.'
)

validator = json_validator(Classification)


def classify_algo(model: str | None = None) -> GenerateTestRepair:
    return GenerateTestRepair("classify", system=SYSTEM, model=model, max_iterations=2)


def vote_algo(model: str | None = None) -> SelfConsistency:
    """Exact-label vote for borderline items: only the is_agent label is voted on."""

    def extract(text: str) -> str:
        try:
            return str(bool(json.loads(text.strip().splitlines()[-1].split("Answer:")[-1])["is_agent"]))
        except Exception:
            return text.strip().lower()[-5:]

    return SelfConsistency(
        n=3, temperature=0.7, model=model, extract=extract,
        system=SYSTEM + " Think briefly, then end with a line 'Answer: <the JSON>'.",
    )


def prompt_for(c: dict) -> str:
    return (
        f"Name: {c['name']}\nRepo: {c.get('repo_url') or '-'}\nWebsite: {c.get('website') or '-'}\n"
        f"Description: {c.get('description') or '-'}"
    )
