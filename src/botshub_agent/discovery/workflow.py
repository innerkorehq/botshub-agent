"""Discovery as a deterministic DAG: collectors fan out in parallel, one failure never stops the rest."""

from __future__ import annotations

import json

from aaa.algos import Node, WorkflowGraph
from aaa.runtime import Budget

from botshub_agent.config import Settings
from botshub_agent.discovery.classify import prompt_for
from botshub_agent.models import Classification


def build_graph(s: Settings) -> WorkflowGraph:
    nodes: list[Node] = []
    for i, t in enumerate(s.github_topics):
        nodes.append(Node(f"gh{i}", "gh_search", args={"topic": t}, on_error="continue"))
    for i, q in enumerate(s.hn_queries):
        nodes.append(Node(f"hn{i}", "hn_search", args={"query": q}, on_error="continue"))
    for i, q in enumerate(s.search_queries):
        nodes.append(Node(f"sx{i}", "searx_search", args={"query": q}, on_error="continue"))
    ids = [n.id for n in nodes]
    nodes.append(Node("ingest", "ingest_and_score", after=ids,
                      args=lambda r, inp: {"batches": [r.get(k) for k in ids]}))
    return WorkflowGraph(nodes, output="ingest")


async def classify_new(rt, limit: int = 40) -> dict:
    """LLM classification of candidates whose score is worth the tokens. Borderline => 3-way vote."""
    done = flagged = 0
    todo = rt.repo.candidates("status='new' AND score >= ?", (rt.settings.score_threshold / 2,))
    todo.sort(key=lambda c: -c["score"])
    for c in todo[:limit]:
        out = await rt.engine.run(rt.algos.classify, {"task": prompt_for(c)}, budget=Budget(max_steps=20))
        if out.kind.value != "success":
            continue
        try:
            cl = Classification.model_validate_json(out.value["candidate"])
        except ValueError:
            continue
        is_agent = cl.is_agent
        if 0.4 <= cl.confidence <= 0.6:
            v = await rt.engine.run(rt.algos.vote, {"task": prompt_for(c)}, budget=Budget(max_steps=30))
            if v.kind.value == "success":
                is_agent = v.value["answer"] == "True"
        rt.repo.set_candidate(c["id"], is_agent=int(is_agent), category=cl.category,
                              class_confidence=cl.confidence, status="classified" if is_agent else "rejected")
        done += 1
        flagged += int(is_agent)
    return {"classified": done, "agents": flagged}


async def corroborate(rt, top: int = 25) -> int:
    """Count distinct other domains mentioning each top candidate (SearXNG), stored as a dated signal."""
    from botshub_agent.discovery.ingest import own_host, rescore

    n = 0
    for c in sorted(rt.repo.candidates("status != 'rejected'"), key=lambda c: -c["score"])[:top]:
        out = await rt.engine.run(rt.algos.count_mentions, {"name": c["name"], "own_host": own_host(c)},
                                  budget=Budget(deadline_s=60))
        if out.kind.value == "success":
            rt.repo.add_signal(c["id"], "searxng", "mention_domains", float(out.value))
            n += 1
    rescore(rt.repo, rt.settings)
    return n


async def run_discovery(rt) -> dict:
    out = await rt.engine.run(rt.algos.discovery, {}, budget=Budget(deadline_s=300))
    stats = out.value if out.kind.value == "success" else {"error": out.reason}
    from botshub_agent.discovery.semantic import semantic_merge

    stats["semantic"] = await semantic_merge(rt)
    stats["corroborated"] = await corroborate(rt)
    stats["classify"] = await classify_new(rt)
    return json.loads(json.dumps(stats, default=str))
