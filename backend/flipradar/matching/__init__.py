"""Product equivalence (GTIN > canonical key > guarded fuzzy) and cluster assignment."""

from .matcher import (
    ProductCandidate,
    ProductMatcher,
    canonical_key,
    compare,
    hard_conflicts,
    missing_identity,
    soft_differences,
)

__all__ = [
    "ProductCandidate",
    "ProductMatcher",
    "canonical_key",
    "compare",
    "hard_conflicts",
    "missing_identity",
    "soft_differences",
]
