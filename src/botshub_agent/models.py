"""Pydantic shapes. Every claim carries its evidence (source_url + verbatim quote)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

CATEGORIES = [
    "Customer Service & Support",
    "Coding & Development",
    "Research & Analysis",
    "Sales & Marketing",
    "Productivity & Automation",
    "Data & Analytics",
    "Communication & Messaging",
    "Content & Creative",
    "DevOps & Infrastructure",
    "Finance & Legal",
    "General Purpose",
    "Other",
]


class Evidence(BaseModel):
    source_url: str
    quote: str = Field(description="Verbatim text copied from the source page")


class Scalar(Evidence):
    value: str


class Feature(Evidence):
    name: str
    description: str = ""
    group: str = ""


class UseCase(Evidence):
    title: str
    audience: str = ""


class Integration(Evidence):
    name: str


class PricingTier(Evidence):
    name: str
    price: str = Field(description="Exactly as written on the page, e.g. '$35/month', 'Free', 'Contact sales'")
    includes: list[str] = []


class Extraction(BaseModel):
    """What the model returns. Anything it cannot support with a quote must be null/empty."""

    name: Scalar | None = None
    tagline: Scalar | None = None
    description: Scalar | None = None
    website: Scalar | None = None
    features: list[Feature] = []
    use_cases: list[UseCase] = []
    integrations: list[Integration] = []
    pricing: list[PricingTier] = []
    free_trial: Scalar | None = None
    deployment: list[Scalar] = []
    target_audience: list[Scalar] = []
    category: str | None = None


class Classification(BaseModel):
    is_agent: bool
    category: str
    confidence: float = Field(ge=0, le=1)
    reason: str = ""


Status = Literal["new", "classified", "rejected", "enriching", "published", "held", "stale"]
