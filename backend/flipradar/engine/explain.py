"""Deterministic, fact-grounded explanations.  OWNER: engine agent."""

from __future__ import annotations

from typing import Any

from .types import OpportunityCandidate


def build_facts(candidate: OpportunityCandidate) -> dict[str, Any]:
    """Flat dict of every number/label an explanation may cite (money as centavos + formatted)."""
    raise NotImplementedError


def render_explanation(facts: dict[str, Any]) -> str:
    """Plain-text multi-line explanation built ONLY from ``facts``."""
    raise NotImplementedError
