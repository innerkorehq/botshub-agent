"""One long-lived instance per role, each with a UNIQUE name.

AAA routes and recovers runs by algorithm name, and registering a new instance per call would
leak a bus subscription each time. Per-run variation travels in the run input, not in the object."""

from __future__ import annotations

from dataclasses import dataclass

from aaa.algos import GenerateTestRepair, GeneratorCritic, Node, SelfConsistency, WorkflowGraph

from botshub_agent.config import Settings
from botshub_agent.discovery import classify
from botshub_agent.discovery.workflow import build_graph
from botshub_agent.enrich import extract, gapfill, overview, pricevote


def _named(algo, name: str):
    algo.name = name
    return algo


def _one(tool: str, name: str) -> WorkflowGraph:
    """A single-tool graph whose arguments are the run input."""
    return _named(WorkflowGraph([Node("r", tool, args=lambda r, inp: dict(inp))], output="r"), name)


@dataclass
class Algos:
    discovery: WorkflowGraph
    fetch_page: WorkflowGraph
    github_readme: WorkflowGraph
    count_mentions: WorkflowGraph
    classify: GenerateTestRepair
    vote: SelfConsistency
    extract: GenerateTestRepair
    overview: GeneratorCritic
    gapfill: object
    price_vote: SelfConsistency

    def all(self) -> list:
        return [self.discovery, self.fetch_page, self.github_readme, self.count_mentions,
                self.classify, self.vote, self.extract, self.overview, self.gapfill, self.price_vote]


def make_algos(s: Settings, registry) -> Algos:
    return Algos(
        discovery=_named(build_graph(s), "bh_discovery"),
        fetch_page=_one("fetch_page", "bh_fetch_page"),
        github_readme=_one("github_readme", "bh_github_readme"),
        count_mentions=_one("count_mentions", "bh_count_mentions"),
        classify=_named(classify.classify_algo(s.llm_model), "bh_classify"),
        vote=_named(classify.vote_algo(s.llm_model), "bh_classify_vote"),
        extract=_named(extract.extraction_algo(s.llm_model), "bh_extract"),
        overview=_named(overview.overview_algo(s.llm_model, s.critic_model), "bh_overview"),
        gapfill=_named(gapfill.gapfill_algo(registry, s.llm_model), "bh_gapfill"),
        price_vote=_named(pricevote.price_vote_algo(s.llm_model, s.price_votes), "bh_price_vote"),
    )
