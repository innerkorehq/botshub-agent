"""Settings from environment variables. Secrets are never stored in code."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


def _f(name: str, default: float) -> float:
    return float(os.environ.get(name) or default)  # empty value = unset


def _params(name: str) -> dict:
    """'reasoning_effort=none,top_p=0.9' (shell-safe, no quotes needed) -> {'reasoning_effort': 'none', 'top_p': 0.9}"""
    out: dict = {}
    for part in (os.environ.get(name) or "").split(","):
        if "=" in part:
            k, v = (x.strip() for x in part.split("=", 1))
            try:
                out[k] = json.loads(v)
            except ValueError:
                out[k] = v
    return out


def _i(name: str, default: int) -> int:
    return int(os.environ.get(name) or default)


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path(os.environ.get("BOTSHUB_DATA_DIR", "./data"))
    export_dir: Path = Path(os.environ.get("BOTSHUB_EXPORT_DIR", "./out"))
    # LLM: any OpenAI-compatible gateway.
    llm_base_url: str = os.environ.get("BOTSHUB_LLM_BASE_URL", "https://api.openai.com/v1")
    llm_api_key: str = os.environ.get("BOTSHUB_LLM_API_KEY", "")
    llm_model: str = os.environ.get("BOTSHUB_LLM_MODEL", "gpt-4o-mini")
    embed_model: str = os.environ.get("BOTSHUB_EMBED_MODEL", "")  # empty = semantic merge disabled
    embed_dim: int = _i("BOTSHUB_EMBED_DIM", 0)  # 0 = detect by probing the model
    local_prefixes: tuple[str, ...] = tuple(
        os.environ.get("BOTSHUB_LOCAL_PREFIXES", "mlx-community/").split(",")
    )  # model ids starting with these run in-process on MLX
    semantic_threshold: float = _f("BOTSHUB_SEMANTIC_THRESHOLD", 0.90)
    price_votes: int = _i("BOTSHUB_PRICE_VOTES", 3)
    llm_params: dict = field(
        default_factory=lambda: _params("BOTSHUB_LLM_PARAMS")
    )  # default request params, e.g. reasoning_effort=none
    critic_model: str = os.environ.get("BOTSHUB_CRITIC_MODEL", "")  # empty = same model
    price_in_per_mtok: float | None = (
        _f("BOTSHUB_PRICE_IN", 0) or None
    )  # needed for Budget(max_cost)
    price_out_per_mtok: float | None = _f("BOTSHUB_PRICE_OUT", 0) or None
    # Sources
    searxng_url: str = os.environ.get("BOTSHUB_SEARXNG_URL", "http://localhost:8888")
    github_token: str = os.environ.get("GITHUB_TOKEN", "")
    github_topics: tuple[str, ...] = ("ai-agent", "mcp-server", "llm-agent", "ai-agents")
    search_queries: tuple[str, ...] = (
        "new open source AI agent",
        "best AI agents this month",
        "new MCP server launch",
        "show HN AI agent",
    )
    hn_queries: tuple[str, ...] = ("AI agent", "MCP server", "agentic")
    # Scoring / gating
    score_threshold: float = _f("BOTSHUB_SCORE_THRESHOLD", 40)
    daily_enrich_cap: int = _i("BOTSHUB_DAILY_ENRICH_CAP", 10)
    min_features: int = 3
    min_quality: float = _f("BOTSHUB_MIN_QUALITY", 0.6)
    run_max_cost: float | None = _f("BOTSHUB_RUN_MAX_COST", 0) or None
    refresh_days: int = _i("BOTSHUB_REFRESH_DAYS", 7)
    weights: dict[str, float] = field(
        default_factory=lambda: {"pop": 0.30, "vel": 0.35, "hn": 0.15, "mentions": 0.20}
    )

    @property
    def botshub_db(self) -> Path:
        return self.data_dir / "botshub.db"

    @property
    def events_db(self) -> Path:
        return self.data_dir / "aaa_events.db"
