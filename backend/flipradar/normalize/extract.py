"""Listing normalization: turn a raw marketplace title into structured, comparable attributes.

OWNER: normalize/matching agent.  Public functions below are the contract used by the ingestion
service - keep their signatures stable.
"""

from __future__ import annotations

from ..engine.types import NormalizedAttributes, RawListing


def normalize_text(text: str) -> str:
    """Lowercase, strip accents, unify units/punctuation, collapse whitespace (deterministic)."""
    raise NotImplementedError


def validate_gtin(code: str) -> bool:
    """True if ``code`` is a valid GTIN-8/12/13/14 (digits + correct check digit)."""
    raise NotImplementedError


def parse_condition(condition_raw: str | None, title: str, description: str | None = None) -> str:
    """Map source vocabulary + text cues to a flipradar.models.Condition value."""
    raise NotImplementedError


def normalize_listing(raw: RawListing) -> NormalizedAttributes:
    """Extract brand/model/category/condition/storage/etc. and compute the canonical key."""
    raise NotImplementedError
