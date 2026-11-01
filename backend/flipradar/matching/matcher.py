"""Product equivalence: decide whether two listings are the *same sellable product*.

OWNER: normalize/matching agent.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..engine.types import MatchResult, NormalizedAttributes


@dataclass
class ProductCandidate:
    """An existing product cluster the matcher may assign a listing to."""

    key: str  # Product.canonical_key
    title: str
    brand: str | None
    model: str | None
    gtin: str | None
    attributes: dict


def canonical_key(attrs: NormalizedAttributes) -> str | None:
    """Stable cluster key (e.g. 'apple|iphone 15 pro|256gb'), or None if attributes are insufficient."""
    raise NotImplementedError


def compare(a: NormalizedAttributes, b: NormalizedAttributes) -> MatchResult:
    """Equivalence of two normalized listings with a confidence in 0..1 and human-readable reasons."""
    raise NotImplementedError


class ProductMatcher:
    """Assigns normalized listings to product clusters (GTIN > canonical key > guarded fuzzy match)."""

    def __init__(self, candidates: list[ProductCandidate] | None = None, fuzzy_threshold: float = 0.92) -> None:
        raise NotImplementedError

    def add(self, candidate: ProductCandidate) -> None:
        raise NotImplementedError

    def match(self, attrs: NormalizedAttributes, title: str) -> MatchResult:
        """Return the best existing cluster (product_key set) or a new-cluster suggestion.

        When no existing cluster matches but ``attrs.canonical_key`` is set, return
        MatchResult(product_key=attrs.canonical_key, method="canonical_key", ...) so the caller creates it.
        When the listing cannot be clustered safely return product_key=None, method="none".
        """
        raise NotImplementedError
