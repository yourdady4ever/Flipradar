"""Product equivalence: decide whether two listings are the *same sellable product*.

OWNER: normalize/matching agent.

Matching ladder (most to least certain), all deterministic:

1. **GTIN** - identical canonical GTIN and no hard attribute conflict -> confidence 0.99.
2. **Canonical key** - identical ``brand|model|storage|identity`` key -> 0.95.
3. **Guarded fuzzy** - only when brand and model are equal, nothing conflicts (storage, edition,
   connectivity, sizes, accessory flag/type) -> ``token_set_ratio / 100 * 0.9``, further multiplied by
   0.9 for every attribute stated on one listing only.  Its maximum (0.9) is *below* the default
   ``ProductMatcher`` threshold (0.92), so by default fuzzy similarity never merges clusters on its
   own - it only reports how close the nearest cluster was.  Lower the threshold deliberately to use it.

Any hard conflict yields confidence 0.0 with a human-readable reason such as
``"storage differs (128gb vs 256gb)"``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from rapidfuzz import fuzz

from ..engine.types import MatchResult, NormalizedAttributes
from ..normalize import vocab as V
from ..normalize.extract import normalize_gtin, normalize_text

GTIN_CONFIDENCE = 0.99
KEY_CONFIDENCE = 0.95
FUZZY_WEIGHT = Decimal("0.9")
SOFT_PENALTY = Decimal("0.9")


@dataclass
class ProductCandidate:
    """An existing product cluster the matcher may assign a listing to."""

    key: str  # Product.canonical_key
    title: str
    brand: str | None
    model: str | None
    gtin: str | None
    attributes: dict


# ------------------------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------------------------


def _norm(value: str | None) -> str | None:
    if value is None:
        return None
    value = " ".join(str(value).lower().split())
    return value or None


def _accessory_type(attrs: NormalizedAttributes) -> str | None:
    for token in attrs.variant_tokens or []:
        if token.startswith("acc:"):
            return token[4:]
    return None


def _storage_relevant(category: str | None, model: str | None) -> bool:
    return category in V.STORAGE_CATEGORIES and (_norm(model) not in V.FIXED_STORAGE_MODELS)


def _fmt_storage(gb: int) -> str:
    return f"{gb // 1024}tb" if gb >= 1024 and gb % 1024 == 0 else f"{gb}gb"


def _dims(attrs: NormalizedAttributes) -> dict[str, str]:
    return V.identity_dims(attrs.category, _norm(attrs.model), attrs.variant_tokens or [], attrs.ram_gb)


def _confidence(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


# ------------------------------------------------------------------------------------------------
# Canonical key
# ------------------------------------------------------------------------------------------------


#: Segment of keys created before storage became mandatory; such clusters mix every capacity.
LEGACY_STORAGE_UNKNOWN = "storage:unknown"


def missing_identity(attrs: NormalizedAttributes) -> list[str]:
    """Why a (device) listing does not pin down ONE sellable product, i.e. which price-relevant
    attribute is missing or contradictory.  Empty list = the listing can join a price cluster.

    * storage, for categories where it changes the price (phone, tablet, console, handheld_pc,
      laptop) unless the model ships in a single configuration (e.g. Switch OLED);
    * the edition (digital/disc) of consoles sold both ways (PS5, PS5 Slim, Xbox Series X);
    * the screen size of lines sold in several sizes (iPad Pro, iPad Air M2+, MacBook Air M2+,
      MacBook Pro with a Pro/Max chip);
    * a coherent case material (Apple Watch Series: aluminium vs steel/titanium/Hermes).
    """
    model = _norm(attrs.model)
    reasons: list[str] = []
    if _storage_relevant(attrs.category, model) and not attrs.storage_gb:
        reasons.append("storage not stated (different capacities are different products)")
    dims = _dims(attrs)
    if model in V.EDITION_MODELS and "edition" not in dims:
        reasons.append("edition (digital or disc) not stated")
    if V.requires_screen(model) and "screen" not in dims:
        reasons.append("screen size not stated (this line is sold in several sizes)")
    if V.material_conflict(model, attrs.variant_tokens or []):
        reasons.append("contradictory case materials in the title")
    return reasons


def canonical_key(attrs: NormalizedAttributes) -> str | None:
    """Stable cluster key (e.g. 'apple|iphone 15 pro|256gb'), or None if attributes are insufficient.

    Format: ``brand|model[|<storage>gb][|<identity values, sorted, comma-joined>]``

    * ``None`` when brand or model is unknown.
    * ``None`` when a price-relevant attribute is missing (:func:`missing_identity`): a cluster of
      "iPhone 15 Pro, any capacity" or "PS5 Slim, digital or disc" would price one product against
      another, so such listings never join a price cluster (precision first).
    * Storage is part of the key for categories where it changes the price (phone, tablet, console,
      handheld_pc, laptop) unless the model ships in a single configuration (e.g. Switch OLED).
    * Identity values are the attributes that make a different product: PS5 ``digital``/``disc``,
      tablet ``wifi``/``cellular``, ``11in`` screens, ``45mm`` watch cases, laptop ``ram16gb``,
      Apple Watch ``steel``/``titanium``/``hermes`` cases...
    * Accessories only get a key (prefixed ``acc|``) when the identified model *is* the accessory
      (e.g. ``acc|sony|dualsense``); a "funda para iPhone 15" identifies the phone, not the case, so it
      gets ``None`` and never mixes with devices or with other accessories.
    """
    brand = _norm(attrs.brand)
    model = _norm(attrs.model)
    if not brand or not model or "|" in brand or "|" in model:
        return None
    if attrs.is_accessory:
        expected = V.ACCESSORY_MODELS.get(model)
        acc_type = _accessory_type(attrs)
        if expected is None or acc_type not in (None, expected):
            return None
        return f"acc|{brand}|{model}"
    if missing_identity(attrs):
        return None
    parts = [brand, model]
    if _storage_relevant(attrs.category, model) and attrs.storage_gb:
        parts.append(f"{attrs.storage_gb}gb")
    dims = _dims(attrs)
    if dims:
        parts.append(",".join(sorted(dims.values())))
    return "|".join(parts)


# ------------------------------------------------------------------------------------------------
# Pairwise comparison
# ------------------------------------------------------------------------------------------------


def hard_conflicts(a: NormalizedAttributes, b: NormalizedAttributes) -> list[str]:
    """Reasons why ``a`` and ``b`` can NOT be the same sellable product (empty list = no conflict)."""
    reasons: list[str] = []
    brand_a, brand_b = _norm(a.brand), _norm(b.brand)
    model_a, model_b = _norm(a.model), _norm(b.model)
    if brand_a and brand_b and brand_a != brand_b:
        reasons.append(f"brand differs ({brand_a} vs {brand_b})")
    if model_a and model_b and model_a != model_b:
        reasons.append(f"model differs ({model_a} vs {model_b})")
    if a.category and b.category and a.category != b.category and not (model_a and model_a == model_b):
        reasons.append(f"category differs ({a.category} vs {b.category})")
    if a.is_accessory != b.is_accessory:
        acc, dev = (a, b) if a.is_accessory else (b, a)
        kind = _accessory_type(acc) or "accessory"
        reasons.append(f"one listing is an accessory ({kind}) and the other is a device")
    elif a.is_accessory and b.is_accessory:
        type_a, type_b = _accessory_type(a), _accessory_type(b)
        if type_a and type_b and type_a != type_b:
            reasons.append(f"accessory type differs ({type_a} vs {type_b})")
    if a.storage_gb and b.storage_gb and a.storage_gb != b.storage_gb:
        category = a.category or b.category
        model = model_a or model_b
        if category is None or _storage_relevant(category, model):
            reasons.append(f"storage differs ({_fmt_storage(a.storage_gb)} vs {_fmt_storage(b.storage_gb)})")
    dims_a, dims_b = _dims(a), _dims(b)
    for dim in sorted(set(dims_a) & set(dims_b)):
        if dims_a[dim] != dims_b[dim]:
            reasons.append(f"{V.IDENTITY_LABELS.get(dim, dim)} differs ({dims_a[dim]} vs {dims_b[dim]})")
    for x, y, dims_x in ((a, b, dims_a), (b, a, dims_b)):
        # an explicitly aluminium Apple Watch is never the steel/titanium/Hermes one
        if "material" in dims_x and "material" not in _dims(y) and "aluminum" in (y.variant_tokens or []):
            reasons.append(f"{V.IDENTITY_LABELS['material']} differs ({dims_x['material']} vs aluminum)")
    return reasons


def soft_differences(a: NormalizedAttributes, b: NormalizedAttributes) -> list[str]:
    """Attributes stated on one listing only (not a conflict, but less certainty)."""
    notes: list[str] = []
    category = a.category or b.category
    model = _norm(a.model) or _norm(b.model)
    if _storage_relevant(category, model) and bool(a.storage_gb) != bool(b.storage_gb):
        known = a.storage_gb or b.storage_gb
        notes.append(f"storage stated on one listing only ({_fmt_storage(int(known or 0))})")
    dims_a, dims_b = _dims(a), _dims(b)
    for dim in sorted(set(dims_a) ^ set(dims_b)):
        value = dims_a.get(dim) or dims_b.get(dim)
        notes.append(f"{V.IDENTITY_LABELS.get(dim, dim)} stated on one listing only ({value})")
    if a.is_bundle != b.is_bundle:
        notes.append("one listing is a bundle (extra items included)")
    return notes


def _info_notes(a: NormalizedAttributes, b: NormalizedAttributes) -> list[str]:
    notes: list[str] = []
    if a.is_bundle != b.is_bundle:
        notes.append("note: one listing is a bundle (extra items included)")
    if a.condition != b.condition:
        notes.append(f"note: conditions differ ({a.condition} vs {b.condition}) - same product, different price segment")
    return notes


def compare(a: NormalizedAttributes, b: NormalizedAttributes) -> MatchResult:
    """Equivalence of two normalized listings with a confidence in 0..1 and human-readable reasons.

    ``product_key`` is the shared canonical key when matched by key, otherwise ``b``'s key (``b`` is
    treated as the reference listing), or None for no match.
    """
    conflicts = hard_conflicts(a, b)
    gtin_a, gtin_b = normalize_gtin(a.gtin), normalize_gtin(b.gtin)
    if gtin_a and gtin_a == gtin_b:
        if conflicts:
            return MatchResult(None, 0.0, "none", [f"same GTIN {gtin_a} but attributes conflict", *conflicts])
        return MatchResult(
            b.canonical_key or a.canonical_key, GTIN_CONFIDENCE, "gtin", [f"identical GTIN {gtin_a}", *_info_notes(a, b)]
        )
    if a.canonical_key and a.canonical_key == b.canonical_key and not conflicts:
        return MatchResult(
            a.canonical_key,
            KEY_CONFIDENCE,
            "canonical_key",
            [f"same canonical key '{a.canonical_key}'", *_info_notes(a, b)],
        )
    if conflicts:
        return MatchResult(None, 0.0, "none", conflicts)
    missing: list[str] = []
    if not (_norm(a.brand) and _norm(b.brand)):
        missing.append("brand not identified on both listings")
    if not (_norm(a.model) and _norm(b.model)):
        missing.append("exact model not identified on both listings")
    if missing:
        return MatchResult(None, 0.0, "none", missing)
    ratio = Decimal(str(fuzz.token_set_ratio(a.normalized_title, b.normalized_title)))
    confidence = ratio / 100 * FUZZY_WEIGHT
    soft = soft_differences(a, b)
    for _ in soft:
        confidence *= SOFT_PENALTY
    reasons = [
        f"same brand and model ({_norm(a.brand)} {_norm(a.model)}), no conflicting attributes",
        f"title similarity {ratio.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}/100",
        *(f"uncertain: {s}" for s in soft),
    ]
    if gtin_a and gtin_b:
        reasons.append(f"GTINs differ ({gtin_a} vs {gtin_b}) - may be a color/region variant")
    if a.condition != b.condition:
        reasons.append(f"note: conditions differ ({a.condition} vs {b.condition})")
    return MatchResult(b.canonical_key or a.canonical_key, _confidence(confidence), "fuzzy", reasons)


# ------------------------------------------------------------------------------------------------
# Matcher
# ------------------------------------------------------------------------------------------------

_STORAGE_SEGMENT = re.compile(r"(\d+)gb")
_RAM_TOKEN = re.compile(r"ram(\d{1,3})gb")


def _attrs_from_candidate(c: ProductCandidate) -> NormalizedAttributes:
    """Rebuild comparable attributes for an existing cluster from its stored data and its key."""
    stored: dict[str, Any] = dict(c.attributes or {})
    parts = c.key.split("|")
    is_acc = bool(stored.get("is_accessory")) or (bool(parts) and parts[0] == "acc")
    body = parts[1:] if parts and parts[0] == "acc" else parts
    storage = stored.get("storage_gb")
    key_tokens: list[str] = []
    for segment in body[2:]:
        if segment == LEGACY_STORAGE_UNKNOWN:
            continue
        m = _STORAGE_SEGMENT.fullmatch(segment)
        if m:
            storage = storage or int(m.group(1))
            continue
        key_tokens.extend(t for t in segment.split(",") if t)
    tokens = list(stored.get("variant_tokens") or []) or key_tokens
    ram = stored.get("ram_gb")
    if ram is None:
        for token in tokens:
            rm = _RAM_TOKEN.fullmatch(token)
            if rm:
                ram = int(rm.group(1))
    brand = _norm(c.brand) or _norm(stored.get("brand")) or (body[0] if body else None)
    model = _norm(c.model) or _norm(stored.get("model")) or (body[1] if len(body) > 1 else None)
    return NormalizedAttributes(
        normalized_title=normalize_text(c.title or ""),
        brand=brand,
        model=model,
        category=stored.get("category"),
        condition=str(stored.get("condition") or "unknown"),
        storage_gb=int(storage) if storage else None,
        ram_gb=int(ram) if ram else None,
        color=stored.get("color"),
        gtin=c.gtin,
        is_accessory=is_acc,
        is_bundle=bool(stored.get("is_bundle", False)),
        variant_tokens=sorted(set(tokens)),
        canonical_key=c.key,
    )


def _less_specific(product: NormalizedAttributes, listing: NormalizedAttributes) -> list[str]:
    """Price-relevant attributes the listing states but the existing product does not."""
    missing: list[str] = []
    if _storage_relevant(listing.category or product.category, _norm(listing.model) or _norm(product.model)):
        if listing.storage_gb and not product.storage_gb:
            missing.append("storage")
    product_dims = _dims(product)
    missing.extend(V.IDENTITY_LABELS.get(dim, dim) for dim in sorted(set(_dims(listing)) - set(product_dims)))
    return missing


def _why_unclustered(attrs: NormalizedAttributes) -> list[str]:
    reasons: list[str] = []
    if not _norm(attrs.brand):
        reasons.append("brand not recognised")
    if not _norm(attrs.model):
        reasons.append("exact model not identified (unknown family or ambiguous generation/variant)")
    if attrs.is_accessory and _norm(attrs.model):
        reasons.append(
            f"accessory ({_accessory_type(attrs) or 'unknown type'}) for {attrs.model}: the accessory itself "
            "is not identifiable"
        )
    elif _norm(attrs.brand) and _norm(attrs.model):
        reasons.extend(missing_identity(attrs))
    return reasons or ["insufficient attributes to cluster safely"]


class ProductMatcher:
    """Assigns normalized listings to product clusters (GTIN > canonical key > guarded fuzzy match)."""

    def __init__(self, candidates: list[ProductCandidate] | None = None, fuzzy_threshold: float = 0.92) -> None:
        if not 0.0 < fuzzy_threshold <= 1.0:
            raise ValueError("fuzzy_threshold must be within (0, 1]")
        self.fuzzy_threshold = fuzzy_threshold
        self._by_key: dict[str, ProductCandidate] = {}
        self._attrs: dict[str, NormalizedAttributes] = {}
        self._by_gtin: dict[str, str] = {}
        self._by_brand_model: dict[tuple[str, str], list[str]] = {}
        for candidate in candidates or []:
            self.add(candidate)

    def __len__(self) -> int:
        return len(self._by_key)

    def add(self, candidate: ProductCandidate) -> None:
        """Index an existing cluster.  The first candidate registered for a key/GTIN wins.

        Clusters that no longer pin down one product (legacy ``|storage:unknown`` keys, PS5 keys
        without an edition, multi-size lines without a screen size...) are not indexed: nothing new is
        matched into them, neither by key nor by GTIN.
        """
        if not candidate.key or candidate.key in self._by_key:
            return
        attrs = _attrs_from_candidate(candidate)
        if LEGACY_STORAGE_UNKNOWN in candidate.key.split("|") or (not attrs.is_accessory and missing_identity(attrs)):
            return
        self._by_key[candidate.key] = candidate
        self._attrs[candidate.key] = attrs
        gtin = normalize_gtin(candidate.gtin)
        if gtin and gtin not in self._by_gtin:
            self._by_gtin[gtin] = candidate.key
        brand, model = _norm(attrs.brand), _norm(attrs.model)
        if brand and model:
            self._by_brand_model.setdefault((brand, model), []).append(candidate.key)

    def match(self, attrs: NormalizedAttributes, title: str) -> MatchResult:
        """Return the best existing cluster (product_key set) or a new-cluster suggestion.

        When no existing cluster matches but ``attrs.canonical_key`` is set, return
        MatchResult(product_key=attrs.canonical_key, method="canonical_key", ...) so the caller creates it.
        When the listing cannot be clustered safely return product_key=None, method="none".
        """
        reasons: list[str] = []
        gtin = normalize_gtin(attrs.gtin)
        if gtin and gtin in self._by_gtin:
            key = self._by_gtin[gtin]
            conflicts = hard_conflicts(attrs, self._attrs[key])
            vaguer = _less_specific(self._attrs[key], attrs)
            if not conflicts and not vaguer:
                return MatchResult(key, GTIN_CONFIDENCE, "gtin", [f"GTIN {gtin} matches existing product '{key}'"])
            why = conflicts or [f"the product does not state {', '.join(vaguer)} while this listing does"]
            reasons.append(f"GTIN {gtin} matches product '{key}' but was ignored: {'; '.join(why)}")

        own_key = attrs.canonical_key
        if own_key and own_key in self._by_key:
            return MatchResult(
                own_key, KEY_CONFIDENCE, "canonical_key", [*reasons, f"canonical key '{own_key}' matches an existing product"]
            )

        brand, model = _norm(attrs.brand), _norm(attrs.model)
        if brand and model:
            probe = replace(attrs, normalized_title=normalize_text(title)) if title else attrs
            best: tuple[float, str, MatchResult] | None = None
            for key in self._by_brand_model.get((brand, model), []):
                result = compare(probe, self._attrs[key])
                if result.method != "fuzzy":
                    continue
                if best is None or result.confidence > best[0] or (result.confidence == best[0] and key < best[1]):
                    best = (result.confidence, key, result)
            if best is not None:
                confidence, key, result = best
                if confidence >= self.fuzzy_threshold:
                    return MatchResult(key, confidence, "fuzzy", [*reasons, f"fuzzy match to '{key}'", *result.reasons])
                reasons.append(
                    f"closest existing product '{key}' scored {confidence:.2f} "
                    f"(< threshold {self.fuzzy_threshold:.2f}); not merged"
                )

        if own_key:
            return MatchResult(own_key, KEY_CONFIDENCE, "canonical_key", [*reasons, f"new product cluster '{own_key}'"])
        return MatchResult(None, 0.0, "none", [*reasons, *_why_unclustered(attrs)])
