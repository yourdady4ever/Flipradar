"""Listing normalization: turn a raw marketplace title into structured, comparable attributes.

OWNER: normalize/matching agent.  Public functions below are the contract used by the ingestion
service - keep their signatures stable.

Pipeline (all deterministic, no I/O)::

    title --normalize_text--> "apple iphone 15 pro max 256gb titanio natural"
          --vocab.find_model_hits--> brand/model/category (earliest model mention wins)
          --extract_* helpers--> storage, RAM, color, GTIN, variant/identity tokens
          --accessory / bundle / software / ambiguity guards--> may clear the model (=> no cluster)
          --matching.canonical_key--> "apple|iphone 15 pro max|256gb"

Precision first: whenever the title does not pin down the exact sellable product the model is left
``None`` (and :func:`analyze_listing` reports why), so the listing never joins a price cluster.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from typing import Any

from ..engine.types import NormalizedAttributes, RawListing
from ..models import Condition
from . import vocab as V

# ------------------------------------------------------------------------------------------------
# Text normalization
# ------------------------------------------------------------------------------------------------

_CHAR_MAP = str.maketrans(
    {
        "“": '"',
        "”": '"',
        "″": '"',
        "’": "'",
        "‘": "'",
        "´": "'",
        " ": " ",
        "º": " ",  # masculine ordinal / degree-like signs: "2º gen"
        "°": " ",
        "–": "-",
        "—": "-",
    }
)

_UNIT_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:\"|'')"), r"\1 pulgadas "),
    (re.compile(r"(\d),(\d{3})(?!\d)"), r"\1\2"),
    (re.compile(r"(\d),(\d)"), r"\1.\2"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*(?:g\s?b|gigas?|gigabytes?)(?![a-z])"), r"\1gb "),
    (re.compile(r"(\d+(?:\.\d+)?)\s*(?:t\s?b|teras?|terabytes?)(?![a-z])"), r"\1tb "),
    (re.compile(r"(?<![\w.])(16|32|64|128|256|512)\s?g(?![a-z0-9])"), r"\1gb "),
    (re.compile(r"(\d+(?:\.\d+)?)\s*(?:pulgadas?|pulg|plg|inch(?:es)?|in)(?![a-z])"), r"\1in "),
    (re.compile(r"(\d+)\s*mm(?![a-z])"), r"\1mm "),
    (re.compile(r"(\d+)\s*(mah|hz)(?![a-z])"), r"\1\2 "),
    (re.compile(r"(\d)(gb)?\s*/\s*(\d)"), r"\1\2/\3"),
)

_CANON_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bi\s?phone"), "iphone"),
    (re.compile(r"\bi\s?pad"), "ipad"),
    (re.compile(r"\bmac\s?book"), "macbook"),
    (re.compile(r"\bair\s?pods"), "airpods"),
    (re.compile(r"\bplay\s?station\s?([45])(?!\w)"), r"ps\1"),
    (re.compile(r"\bplay\s?station"), "playstation"),
    (re.compile(r"\bplay\s?([45])(?!\w)"), r"ps\1"),
    (re.compile(r"\bps\s([45])(?!\w)"), r"ps\1"),
    (re.compile(r"\bx\s?box"), "xbox"),
    (re.compile(r"\bgo\s?pro(?!\w)"), "gopro"),
    (re.compile(r"\bsteam\s?deck"), "steam deck"),
    (re.compile(r"\bdual\s?sense"), "dualsense"),
    (re.compile(r"\bdual\s?shock"), "dualshock"),
    (re.compile(r"\bjoy\s?con"), "joycon"),
    (re.compile(r"\bone\s?plus(?!\w)"), "oneplus"),
    (re.compile(r"\bwi\s?fi(?!\w)"), "wifi"),
    (re.compile(r"promax(?!\w)"), "pro max"),
    (re.compile(r"\bpro\splus(?!\w)"), "pro+"),
    (re.compile(r"\bz(fold|flip)"), r"z \1"),
    (
        re.compile(
            r"\b(iphone|ipad|pixel|hero|note|edge|fold|flip|charge|kindle|series|serie|razr|mini|air|"
            r"switch|watch|reno|nova|mate|buds|qc|go|forerunner|fenix|venu|solo|redmi|moto)(\d)"
        ),
        r"\1 \2",
    ),
    (re.compile(r"(\d)(pro|max|plus|ultra|mini|lite|fe|ti|super|xtx|xt|gre|slim|oled|digital|pro\+)(?!\w)"), r"\1 \2"),
    (re.compile(r"\b(\d{1,2})\s(a|da|ra|era|ta|va|ma|na)\s(gen|generacion)\b"), r"\1\2 \3"),
)


def normalize_text(text: str) -> str:
    """Lowercase, strip accents, unify units/punctuation, collapse whitespace (deterministic).

    * accents removed (``edición`` -> ``edicion``, ``ñ`` -> ``n``)
    * storage units unified: ``128 GB`` / ``128gb`` / ``128 g b`` / ``128 gigas`` -> ``128gb``;
      ``1 TB`` / ``1 tera`` -> ``1tb``
    * screen sizes: ``13.3"`` / ``13,3 pulgadas`` / ``13 inch`` -> ``13.3in``; ``45 mm`` -> ``45mm``
    * punctuation removed except ``+`` (``s24+``, ``pro+``, bundle separators), decimal points inside
      numbers and ``/`` between numbers (``8/256gb``)
    * common spellings unified (``play station 5``/``play 5`` -> ``ps5``, ``i phone`` -> ``iphone``,
      ``promax`` -> ``pro max``, ``iphone15pro`` -> ``iphone 15 pro``)
    """
    if not text:
        return ""
    t = unicodedata.normalize("NFKD", str(text))
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = t.translate(_CHAR_MAP).lower()
    for pattern, repl in _UNIT_RULES:
        t = pattern.sub(repl, t)
    t = re.sub(r"[^a-z0-9+./\s]", " ", t)
    t = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", t)
    t = re.sub(r"(?<![\db])/|/(?!\d)", " ", t)
    t = re.sub(r"\+{2,}", "+", t)
    t = " ".join(t.split())
    for pattern, repl in _CANON_RULES:
        t = pattern.sub(repl, t)
    return " ".join(t.split())


# ------------------------------------------------------------------------------------------------
# GTIN
# ------------------------------------------------------------------------------------------------


def validate_gtin(code: str) -> bool:
    """True if ``code`` is a valid GTIN-8/12/13/14 (digits + correct check digit)."""
    if not isinstance(code, str):
        return False
    code = code.strip()
    if len(code) not in (8, 12, 13, 14) or not re.fullmatch(r"[0-9]+", code):
        return False
    digits = [int(c) for c in code]
    body, check = digits[:-1], digits[-1]
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check


#: GS1 prefixes that are not globally unique (in-store / variable-measure / coupons) - useless for
#: cross-source matching.
_RESTRICTED_GTIN13_PREFIXES = ("02", "04", "05", "2", "98", "99")


def normalize_gtin(code: Any) -> str | None:
    """Canonical GTIN usable as a cross-source identity, or None.

    Spaces/hyphens are removed, the check digit validated, all-zero and restricted-circulation codes
    rejected.  UPC-A (12) and zero-padded GTIN-14 collapse to the 13-digit form, so the same item
    reported as ``036000291452`` and ``0036000291452`` compares equal.  GTIN-14 with a non-zero
    indicator digit (case packs) and GTIN-8 are kept as-is.
    """
    if code is None:
        return None
    value = re.sub(r"[\s-]", "", str(code))
    if not validate_gtin(value) or set(value) == {"0"}:
        return None
    if len(value) == 8:
        return value
    padded = value.zfill(14)
    canonical = padded[1:] if padded[0] == "0" else padded
    if len(canonical) == 13 and canonical.startswith(_RESTRICTED_GTIN13_PREFIXES):
        return None
    return canonical


def extract_gtin(title: str, *candidates: Any) -> str | None:
    """First valid GTIN among explicit ``candidates`` (source fields), else an 8/12/13/14-digit token
    in ``title`` that passes the check digit.  Never guesses."""
    for candidate in candidates:
        found = normalize_gtin(candidate)
        if found:
            return found
    for m in re.finditer(r"(?<!\d)(\d{14}|\d{13}|\d{12}|\d{8})(?!\d)", title or ""):
        found = normalize_gtin(m.group(1))
        if found:
            return found
    return None


# ------------------------------------------------------------------------------------------------
# Condition
# ------------------------------------------------------------------------------------------------

_NEGATIONS = frozenset({"no", "sin", "nunca", "nada", "ni", "si", "not", "never", "cero"})
_COPULAS = frozenset({"esta", "estan", "es", "fue", "ha", "sido", "tiene", "presenta", "is", "was", "has", "been"})


def _compile_cues(cues: Iterable[str]) -> list[re.Pattern[str]]:
    return [re.compile(r"(?<!\w)(?:" + cue + r")(?!\w)") for cue in cues]


_FOR_PARTS = _compile_cues(V.FOR_PARTS_CUES)
_FOR_PARTS_DESC = _compile_cues(V.FOR_PARTS_DESCRIPTION_CUES)
_REFURB = _compile_cues(V.REFURBISHED_CUES)
_USED = _compile_cues(V.USED_CUES)
_NEW = _compile_cues(c if c != "new" else r"new(?!\s(?:balance|nintendo|3ds|2ds|era|york))" for c in V.NEW_CUES)


def _prev_tokens(text: str, pos: int, n: int = 2) -> list[str]:
    return text[:pos].split()[-n:]


#: Words allowed between a negation and the cue it negates ("no esta danado", "no se ha mojado").
_NEGATION_GLUE = _COPULAS | {"se", "lo", "le", "han"}
#: Damage adjectives that, right after a packaging word, describe the box ("caja un poco dañada").
_PACKAGING_DAMAGE = re.compile(r"danad|mojad")


def _negated(text: str, pos: int) -> bool:
    for tok in reversed(_prev_tokens(text, pos, 3)):
        if tok in _NEGATIONS:
            return True
        if tok not in _NEGATION_GLUE:
            return False
    return False


def _has_cue(text: str, patterns: list[re.Pattern[str]]) -> bool:
    """True when any cue occurs and is not negated by the preceding words."""
    for pattern in patterns:
        for m in pattern.finditer(text):
            first = m.group(0).split()[0]
            prev = _prev_tokens(text, m.start(), 3)
            if first in ("no", "not"):
                # "no enciende": negated only by "si no enciende" style conditionals
                if prev and prev[-1] in ("si", "if", "aunque"):
                    continue
                return True
            if _negated(text, m.start()):
                continue  # "sin danos", "no reportado", "no esta danado" - but not "nunca usado, sellado"
            if _PACKAGING_DAMAGE.match(first) and any(tok in V.PACKAGING_WORDS for tok in prev):
                continue  # "nuevo, caja dañada": the box is damaged, not the item
            return True
    return False


def _condition_from_text(text: str) -> str | None:
    if _has_cue(text, _REFURB):
        return Condition.REFURBISHED.value
    if _has_cue(text, _USED):
        return Condition.USED.value
    if _has_cue(text, _NEW):
        return Condition.NEW.value
    return None


def _condition_from_source(condition_raw: Any) -> str | None:
    if condition_raw is None:
        return None
    raw = str(condition_raw).strip()
    if raw in V.EBAY_CONDITION_IDS:
        return V.EBAY_CONDITION_IDS[raw]
    value = normalize_text(raw.replace("_", " "))
    if value in V.UNSPECIFIED_CONDITIONS:
        return None
    if value in V.SOURCE_CONDITION_MAP:
        return V.SOURCE_CONDITION_MAP[value]
    if _has_cue(value, _FOR_PARTS):
        return Condition.FOR_PARTS.value
    return _condition_from_text(value)


def parse_condition(condition_raw: str | None, title: str, description: str | None = None) -> str:
    """Map source vocabulary + text cues to a flipradar.models.Condition value.

    Priority:

    1. explicit for-parts cues in the title ("para piezas", "no enciende", "icloud bloqueado"...) or
       strong ones in the description - a broken item must never be priced as a working one;
    2. the source's own condition field (Mercado Libre ``new``/``used``/``not_specified``; eBay
       ``conditionId`` codes or labels such as "Open box", "Excellent - Refurbished") - except that a
       source "new" is *downgraded* when the title explicitly says refurbished/used (conservative);
    3. title cues (reacondicionado > usado/seminuevo/como nuevo/caja abierta > nuevo/sellado);
    4. ``unknown``.

    Open-box / "caja abierta" / exhibition items map to ``used``: they are not sealed-new stock.
    """
    t = normalize_text(title or "")
    d = normalize_text(description or "") if description else ""
    if _has_cue(t, _FOR_PARTS) or (d and _has_cue(d, _FOR_PARTS_DESC)):
        return Condition.FOR_PARTS.value
    source = _condition_from_source(condition_raw)
    title_cue = _condition_from_text(t)
    if source is not None:
        if source == Condition.NEW.value and title_cue in (Condition.REFURBISHED.value, Condition.USED.value):
            return title_cue
        return source
    return title_cue or Condition.UNKNOWN.value


# ------------------------------------------------------------------------------------------------
# Storage / RAM / color / variants
# ------------------------------------------------------------------------------------------------

RAM_VALUES = frozenset({1, 2, 3, 4, 6, 8, 10, 12, 16, 18, 20, 24, 32, 36, 48, 64, 96, 128})
_STORAGE_VALUES = frozenset({8, 16, 32, 64, 128, 256, 500, 512, 825, 1024, 2048, 4096, 8192})
_SIZE_RE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(gb|tb)(?!\w)")
_RAM_RES = (
    re.compile(r"(?<![\w.])(\d{1,3})gb\s(?:de\s)?(?:memoria\s)?(?:ram|lpddr\d\w*|ddr\d\w*|unificada|unified)(?!\w)"),
    re.compile(r"(?<!\w)(?:ram|memoria\sram)\s(?:de\s)?(\d{1,3})gb(?!\w)"),
)
_PAIR_RE = re.compile(r"(?<![\w.+])(\d{1,2})(gb)?(\s?[+/]\s?)(\d+(?:\.\d+)?)(gb|tb)(?!\w)")
_STORAGE_HINT_AFTER = frozenset(
    {"ssd", "hdd", "almacenamiento", "interno", "internos", "interna", "rom", "nvme", "emmc", "storage"}
)
_NOT_STORAGE_BEFORE = frozenset({"sd", "microsd", "usb", "tarjeta", "nube", "icloud", "expandible", "hasta"})
_NOT_STORAGE_AFTER = frozenset({"sd", "microsd", "micro", "usb", "ram", "vram", "gddr6", "gddr6x", "gddr7", "gddr5"})


def _to_gb(number: str, unit: str) -> int | None:
    try:
        value = Decimal(number)
    except InvalidOperation:  # pragma: no cover - regex guarantees digits
        return None
    gb = value * 1024 if unit == "tb" else value
    if gb != gb.to_integral_value():
        return None
    gb_int = int(gb)
    return {1000: 1024, 2000: 2048}.get(gb_int, gb_int)


#: "4gb ram 128gb" / "64gb ram 4gb": the word "ram" sits between two sizes.  Both ``_RAM_RES`` would claim
#: a different number; RAM is always the smaller one, the other is storage.
_RAM_BETWEEN_RE = re.compile(
    r"(?<![\w.])(\d{1,4})gb\s(?:de\s)?(?:memoria\s)?ram\s(?:de\s)?(\d+(?:\.\d+)?)(gb|tb)(?!\w)"
)
#: Laptop RAM sizes that are never a storage capacity (M3/M4 Pro/Max: 36/48/96 GB).
_RAM_ONLY_VALUES = RAM_VALUES - _STORAGE_VALUES


def _ram_and_storage(text: str, category: str | None) -> tuple[int | None, int | None]:
    ram: int | None = None
    ram_positions: set[int] = set()
    storage_positions: set[int] = set()  # sizes known to be storage (the other side of "Xgb ram Ygb")
    claimed: list[tuple[int, int]] = []
    storage_hint: int | None = None
    for m in _RAM_BETWEEN_RE.finditer(text):
        left, right = int(m.group(1)), _to_gb(m.group(2), m.group(3))
        if right is None or left == right:
            continue
        (ram_value, ram_pos), storage_pos = (
            ((left, m.start(1)), m.start(2)) if left < right else ((right, m.start(2)), m.start(1))
        )
        if ram_value not in RAM_VALUES:
            continue
        ram = ram if ram is not None else ram_value
        ram_positions.add(ram_pos)
        storage_positions.add(storage_pos)
        claimed.append((m.start(), m.end()))
    for pattern in _RAM_RES:
        for m in pattern.finditer(text):
            if any(start <= m.start() < end for start, end in claimed):
                continue  # already resolved by the "Xgb ram Ygb" rule above
            value = int(m.group(1))
            if value in RAM_VALUES:
                ram = ram if ram is not None else value
                ram_positions.add(m.start(1))
    for m in _PAIR_RE.finditer(text):
        if m.group(2) is None and m.group(3).strip() != m.group(3):
            continue  # "15 + 128gb": a spaced bare number is not RAM
        value = int(m.group(1))
        if value not in RAM_VALUES:
            continue
        ram = ram if ram is not None else value
        ram_positions.add(m.start(1))
        storage_hint = storage_hint or _to_gb(m.group(4), m.group(5))
    candidates: list[tuple[int, bool]] = []  # (gb, explicitly storage)
    for m in _SIZE_RE.finditer(text):
        if m.start() in ram_positions:
            continue
        gb = _to_gb(m.group(1), m.group(2))
        if gb is None:
            continue
        if m.start() in storage_positions:
            candidates.append((gb, True))
            continue
        before = _prev_tokens(text, m.start(), 3)
        after = text[m.end() :].split()[:1]
        if any(tok in _NOT_STORAGE_BEFORE for tok in before[-2:]) or (after and after[0] in _NOT_STORAGE_AFTER):
            continue
        explicit = bool(after and after[0] in _STORAGE_HINT_AFTER) or any(
            tok in ("almacenamiento", "interna", "rom") for tok in before
        )
        candidates.append((gb, explicit))
    if category == "gpu":
        return ram, None
    distinct = sorted({gb for gb, _ in candidates})
    if ram is None and category in ("phone", "tablet", "laptop", "handheld_pc") and len(distinct) >= 2:
        small, rest = distinct[0], distinct[1:]
        small_is_storage = any(gb == small and exp for gb, exp in candidates)  # "128gb ssd 1tb hdd"
        if (
            (small <= 32 and small in RAM_VALUES and min(rest) >= 64)
            # laptops: "M3 Max 36gb 1tb", "M4 Pro 48gb 512gb", "M3 Max 64gb 2tb" - Pro/Max RAM sizes above 32
            or (category == "laptop" and small in _RAM_ONLY_VALUES and min(rest) >= 256)
            or (category == "laptop" and small in (64, 128) and not small_is_storage and min(rest) >= 1024)
        ):
            ram = small  # "12gb 512gb" / "16gb 512gb ssd 1tb hdd": the small value is RAM
            candidates = [(gb, exp) for gb, exp in candidates if gb != small]
    minimum = 16 if category in V.STORAGE_CATEGORIES else 8
    plausible = [(gb, exp) for gb, exp in candidates if gb >= minimum and (gb in _STORAGE_VALUES or gb >= 1024)]
    if storage_hint is not None and storage_hint >= minimum:
        return ram, storage_hint
    explicit = sorted({gb for gb, exp in plausible if exp})
    if len(explicit) == 1:
        return ram, explicit[0]
    values = sorted({gb for gb, _ in plausible})
    if len(values) == 1:
        return ram, values[0]
    return ram, None  # none, or several different capacities (multi-variant listing) -> unknown


def extract_storage_gb(text: str, category: str | None = None) -> int | None:
    """Storage capacity in GB from normalized ``text`` (``1tb`` -> 1024), ignoring RAM mentions.

    ``"8gb ram 256gb"`` -> 256, ``"8+256gb"`` -> 256, ``"12gb 512gb"`` (phone) -> 512.  Returns None
    when no capacity is stated or when several different capacities are listed (ambiguous).
    """
    return _ram_and_storage(text, category)[1]


def extract_ram_gb(text: str, category: str | None = None) -> int | None:
    """RAM in GB from normalized ``text``: ``8gb ram``, ``ram 8gb``, ``8+256gb``, ``8gb/256gb``, or
    (phones/tablets/laptops) the small value of a ``12gb 512gb`` pair.  None when not stated."""
    return _ram_and_storage(text, category)[0]


_COLOR_RES = [(re.compile(r"(?<!\w)(?:" + pattern + r")(?!\w)"), name) for pattern, name in V.COLORS]


def extract_color(text: str) -> str | None:
    """Canonical (English) color name of the first color mentioned in normalized ``text``
    (multi-word names such as "titanio natural" win over "titanio" at the same position)."""
    best: tuple[int, int, int, str] | None = None
    for order, (pattern, name) in enumerate(_COLOR_RES):
        m = pattern.search(text)
        if m:
            candidate = (m.start(), -len(m.group(0)), order, name)
            if best is None or candidate < best:
                best = candidate
    return best[3] if best else None


_EDITION_DIGITAL = re.compile(r"(?<!\w)(?:digital|sin\slector(?:\sde\sdiscos?)?|sin\sdisco|solo\sdigital)(?!\w)")
_EDITION_DISC = re.compile(
    r"(?<!\w)(?:con\slector|lector\sde\sdiscos?|lectora|edicion\s(?:estandar|standard|con\sdisco|disco)|"
    r"estandar|standard|disc|disco(?!\sduro)|blu\s?ray|unidad\sde\sdisco|con\sunidad)(?!\w)"
)


def _edition(text: str) -> str | None:
    digital = _EDITION_DIGITAL.search(text) is not None
    disc = _EDITION_DISC.search(_EDITION_DIGITAL.sub(" ", text)) is not None
    if digital and not disc:
        return "digital"
    if disc and not digital:
        return "disc"
    return None


_WATCH_MATERIAL_RES = (
    (re.compile(r"(?<!\w)(?:acero(?:\sinoxidable)?|inoxidable|stainless(?:\ssteel)?|steel)(?!\w)"), "steel"),
    (re.compile(r"(?<!\w)(?:titanio|titanium)(?!\w)"), "titanium"),
    (re.compile(r"(?<!\w)hermes(?!\w)"), "hermes"),
    (re.compile(r"(?<!\w)(?:aluminio|aluminum|aluminium)(?!\w)"), "aluminum"),
)
#: A material right after one of these describes the band, not the case ("correa de acero", "malla milanesa").
_BAND_WORDS = frozenset(
    {"correa", "correas", "malla", "pulso", "pulsos", "pulsera", "extensible", "brazalete", "band", "banda",
     "loop", "eslabones", "milanesa", "milanese", "strap"}
)


def _watch_materials(text: str) -> list[str]:
    """Case materials stated in a smartwatch title (steel / titanium / hermes / aluminum)."""
    found = set()
    for pattern, material in _WATCH_MATERIAL_RES:
        for m in pattern.finditer(text):
            if not any(tok in _BAND_WORDS for tok in _prev_tokens(text, m.start(), 3)):
                found.add(material)
    return sorted(found)


def _identity_tokens(text: str, category: str | None, model: str | None) -> list[str]:
    tokens: list[str] = []
    if category == "console":
        edition = _edition(text)
        if edition:
            tokens.append(edition)
    if category in ("tablet", "smartwatch"):
        cellular = r"cellular|celular|lte|esim" + (r"|5g|4g" if category == "tablet" else "")
        if V.has(text, cellular):
            tokens.append("cellular")
        if V.has(text, "wifi"):
            tokens.append("wifi")
        if V.has(text, "gps"):
            tokens.append("gps")
    if category in ("tablet", "laptop"):
        sizes = {V.inch_token(m.group(1)) for m in re.finditer(r"(?<![\w.])(\d{1,2}(?:\.\d)?)in(?!\w)", text)}
        tokens.extend(sorted(s for s in sizes if 7 <= int(s[:-2]) <= 18))
    if category == "smartwatch":
        tokens.extend(sorted({m.group(1) + "mm" for m in re.finditer(r"(?<!\w)(3[8-9]|4[0-9]|5[01])mm(?!\w)", text)}))
        tokens.extend(_watch_materials(text))
    if category == "gpu":
        vram = sorted(
            {int(m.group(1)) for m in re.finditer(r"(?<![\w.])(\d{1,2})gb(?!\w)", text) if int(m.group(1)) <= 48}
        )
        if len(vram) == 1:
            tokens.append(f"vram{vram[0]}gb")
    if category == "camera":
        if V.has(text, r"solo\s(?:el\s)?cuerpo|body\sonly|cuerpo\sunicamente|sin\slente|solo\scamara"):
            tokens.append("body_only")
        elif V.has(text, r"kit|con\slente|lente\s(?:de\s)?\d{2}|\d{2}\s\d{2,3}mm"):
            tokens.append("lens_kit")
    if category == "tool":
        if V.has(text, r"solo\sherramienta|sin\sbaterias?|sin\scargador|tool\sonly|bare\stool|herramienta\ssola"):
            tokens.append("tool_only")
        elif V.has(text, r"con\sbaterias?|incluye\sbaterias?|\d\sbaterias|kit"):
            tokens.append("with_battery")
    if category == "sneakers":
        sizes = {
            m.group(1) or m.group(2)
            for m in re.finditer(r"(?<!\w)talla\s?(\d{2}(?:\.5)?)(?!\w)|(?<![\w.])(\d{2}(?:\.5)?)\s?(?:mx|cm)(?!\w)", text)
        }
        sizes = {s for s in sizes if 20 <= float(s) <= 33}
        if len(sizes) == 1:
            tokens.append(f"talla{sizes.pop()}")
    return tokens


def extract_variant_tokens(text: str, category: str | None = None, model: str | None = None) -> list[str]:
    """Sorted descriptive + identity tokens (``pro``, ``oled``, ``5g``, ``digital``, ``45mm``,
    ``cellular``, ``11in``...).  Identity tokens depend on ``category`` (see vocab.identity_dims)."""
    found = {tok for tok in text.split() if tok in V.VARIANT_WORDS}
    if "pro+" in text.split():
        found.update({"pro", "plus"})
    found.update(_identity_tokens(text, category, model))
    return sorted(found)


# ------------------------------------------------------------------------------------------------
# Accessories / bundles / software
# ------------------------------------------------------------------------------------------------


def _word_patterns(words: tuple[tuple[str, str], ...]) -> list[tuple[re.Pattern[str], str]]:
    return [(re.compile(r"(?<!\w)(?:" + w + r")(?!\w)"), typ) for w, typ in words]


_STRONG = _word_patterns(V.STRONG_ACCESSORY_WORDS)
_WEAK = _word_patterns(V.WEAK_ACCESSORY_WORDS)
_EMPTY_BOX = re.compile(r"(?<!\w)(?:" + "|".join(V.EMPTY_BOX_CUES) + r")(?!\w)")
_PART_ANY_CONTEXT = re.compile(r"(?<!\w)(?:" + "|".join(V.PART_WORDS_ANY_CONTEXT) + r")(?!\w)")
_ACCESSORY_CATEGORY = re.compile(
    r"accesorio|funda|mica|protector|cargador|cable|refaccion|repuesto|correa|estuche|case|charger|parts"
)
_LEADING_FILLERS = frozenset(
    {"nuevo", "nueva", "original", "originales", "oferta", "pack", "kit", "set", "lote", "de", "par", "x",
     "1", "2", "3", "4", "5", "2x", "3x", "generico", "generica", "premium", "uso", "rudo", "el", "la", "para"}
)


def _matches(text: str, patterns: list[tuple[re.Pattern[str], str]]) -> list[tuple[int, str]]:
    found = [(m.start(), typ) for pattern, typ in patterns for m in pattern.finditer(text)]
    return sorted(found)


def _is_inclusion(text: str, pos: int, *, digits: bool, model_end: int | None = None) -> bool:
    """True when the accessory word at ``pos`` is introduced as an included item ("con funda",
    "+ cargador", "2 controles").  A number that is the last word of the model name ("iPhone 15 funda",
    "Switch 2 funda" - ``model_end`` is where the model name ends) is not a quantity."""
    prev = _prev_tokens(text, pos, 2)
    if not prev:
        return False
    if prev[-1] in V.INCLUSION_WORDS:
        return True
    if digits and prev[-1].isdigit():
        digit_start = text.rfind(prev[-1], 0, pos)
        word = text[pos:].split()[:1]
        # "2 controles" / "1 funda": a quantity; "iPad Air 11 funda" / "Switch 2 funda": a model number or
        # size followed by a singular word is not
        quantity = prev[-1] == "1" or (bool(word) and word[0].endswith("s"))
        if quantity and (model_end is None or digit_start >= model_end):
            return True
    if prev[-1] == "de" and len(prev) > 1 and prev[-2] in V.DEVICE_NOUNS:
        return True  # "consola de juegos"
    return digits and any(p in V.INCLUSION_WORDS for p in prev)


def _has_device_noun(text: str) -> bool:
    return any(tok in V.DEVICE_NOUNS for tok in text.split())


#: Words right after an accessory word that make it an included item ("juegos incluidos", "funda de regalo").
_INCLUDED_AFTER = frozenset({"incluido", "incluida", "incluidos", "incluidas", "gratis", "regalo"})
#: Words right after a weak accessory word that describe the device's own part ("pantalla impecable",
#: "bateria al 90", "pantalla sin detalles"): the listing is still the device.
_PART_DESCRIPTORS = frozenset(
    {"al", "rota", "roto", "rotas", "estrellada", "estrellado", "quebrada", "quebrado", "impecable", "impecables",
     "perfecta", "perfecto", "intacta", "intacto", "sin", "con", "danada", "danado", "rayada", "rayones",
     "detalle", "detalles", "funcionando", "excelente", "buen", "buena", "salud", "condicion", "estado"}
)
#: "<word> de bateria" style health mentions.
_PART_HEALTH_BEFORE = frozenset({"salud", "condicion", "vida", "estado", "ciclos", "smart"})


def _included_after(text: str, end: int) -> bool:
    return any(tok in _INCLUDED_AFTER for tok in text[end:].split()[:2])


def _describes_own_part(text: str, start: int, end: int) -> bool:
    """Weak accessory word used to describe the device itself ("bateria 85", "lente 18 55mm",
    "pantalla impecable", "salud de bateria", "Echo Show smart display")."""
    nxt = text[end:].split()[:1]
    if nxt and (nxt[0] in _PART_DESCRIPTORS or (nxt[0].isdigit() and int(nxt[0]) <= 100)):
        return True
    prev = _prev_tokens(text, start, 2)
    return bool(prev) and (prev[-1] in _PART_HEALTH_BEFORE or (prev[-1] == "de" and len(prev) > 1
                                                                 and prev[-2] in _PART_HEALTH_BEFORE))


def _word_spans(text: str, patterns: list[tuple[re.Pattern[str], str]]) -> list[tuple[int, int, str]]:
    return sorted((m.start(), m.end(), typ) for pattern, typ in patterns for m in pattern.finditer(text))


#: Words that open an included item, also in English ("with MagSafe charging case", "w/ case").
_INCLUSION_LEADS = V.INCLUSION_WORDS | {"with", "w", "and", "includes"}
#: Words allowed between such a word and the accessory word ("con el estuche", "with MagSafe charging case").
_INCLUDED_ITEM_MODIFIERS = frozenset(
    {"el", "la", "los", "las", "un", "una", "its", "the", "a", "de", "original", "originales", "magsafe",
     "charging", "carga", "wireless", "inalambrico", "inalambrica", "usb", "c", "lightning"}
)


def _introduced_as_included(text: str, pos: int) -> bool:
    """An inclusion word opens the phrase that ends with the accessory word at ``pos``
    ("with MagSafe charging case", "con el estuche de carga")."""
    for tok in reversed(_prev_tokens(text, pos, 4)):
        if tok in _INCLUSION_LEADS:
            return True
        if tok not in _INCLUDED_ITEM_MODIFIERS:
            return False
    return False


#: "para"/"compatible": the band or case is sold for the device; "solo"/"reemplazo": only the part is sold.
_PART_LISTING_RE = re.compile(
    r"(?<!\w)(?:para|compatibles?|for|fits|solo(?!\sloop)|sola|solamente|unicamente|only|reemplazo|replacement|"
    r"suelt[oa]s?)(?!\w)"
)
#: Only the watch itself has GPS/cellular/Bluetooth radios.
_WATCH_RADIO_RE = re.compile(r"(?<!\w)(?:gps|cellular|celular|lte|esim|bluetooth|wifi)(?!\w)")
_WATCH_SIZE_RE = re.compile(r"(?<![\w/])(3[8-9]|4[0-9]|5[01])mm(?!\w)")
#: Band styles right after the band word: the title says which band the watch comes with ("Correa Loop
#: Deportiva", "Pulso Deportivo", "Correa Ocean", "Correa Hibrida").
_BAND_STYLES = frozenset(
    {"deportiva", "deportivo", "deportivas", "sport", "loop", "ocean", "oceano", "alpine", "alpina", "trail",
     "hibrida", "hibrido", "milanesa", "milanese", "eslabones", "link", "trenzada", "trenzado", "nike", "silicon",
     "silicona", "nylon", "cuero", "piel", "magnetica", "fluoroelastomero"}
)
_CHARGING_WORDS = frozenset({"carga", "charging", "magsafe", "inalambrica", "inalambrico", "wireless", "usb", "lightning"})
#: Words that describe the earbuds themselves (a charging case alone is none of these).
_EARBUD_DEVICE_WORDS = frozenset(
    {"audifonos", "auriculares", "earbuds", "headphones", "inalambricos", "inalambricas", "cancelacion", "anc",
     "bluetooth", "audio", "sonido"}
)


def _watch_band_of_device(text: str, start: int, end: int) -> bool:
    """The band word describes the band a watch is sold with ("Apple Watch Series 9 45mm GPS Correa
    Deportiva"), not a band sold on its own ("Apple Watch Series 9 45mm Correa")."""
    sizes = set(_WATCH_SIZE_RE.findall(text))
    if re.search(r"\d/\d{2}mm", text) or len(sizes) > 1:
        return False  # "41/45mm", "41mm 45mm": a band that fits several case sizes
    if len([tok for tok in text[:start].split() if tok.isdigit() and 1 <= int(tok) <= 11]) > 1:
        return False  # "Series 9 8 7": a compatibility list
    if _WATCH_RADIO_RE.search(text) or _watch_materials(text):
        return True  # GPS / cellular / an aluminium, steel or titanium case: only the watch has them
    # "45mm Correa Loop Deportiva": the case size plus the style of the band the watch comes with
    nxt = [tok for tok in text[end:].split()[:2] if tok != "de"][:1]
    return len(sizes) == 1 and bool(nxt) and nxt[0] in _BAND_STYLES


def _charging_case_of_device(text: str, start: int, end: int, model_end: int) -> bool:
    """The charging case named after a description of the earbuds themselves ("AirPods 4 Audífonos
    Inalámbricos ... Estuche de Carga USB-C").  "AirPods Pro 2 Estuche de carga" stays an accessory."""
    if text[start:end] not in ("estuche", "case"):
        return False  # "funda"/"carcasa": a protective cover
    if not any(tok in _CHARGING_WORDS for tok in _prev_tokens(text, start, 1) + text[end:].split()[:3]):
        return False  # a carrying/protective case, not the charging case
    return any(tok in _EARBUD_DEVICE_WORDS for tok in text[model_end:start].split())


def _standard_part_of_device(text: str, start: int, end: int, typ: str, category: str | None, model_end: int) -> bool:
    """The family's standard in-box part (watch band, earbuds' charging case) mentioned while describing
    the device.  These parts are part of the official product name ("Correa deportiva", "Estuche de carga
    MagSafe"), so they only mean an accessory listing when nothing else in the title describes the device."""
    if _PART_LISTING_RE.search(text):
        return False
    if category == "smartwatch" and typ == "strap":
        return _watch_band_of_device(text, start, end)
    if category == "audio" and typ == "case":
        return _charging_case_of_device(text, start, end, model_end)
    return False


def detect_accessory(
    text: str,
    device_start: int | None,
    *,
    has_storage: bool = False,
    source_category: str | None = None,
    device_end: int | None = None,
    category: str | None = None,
) -> tuple[bool, str | None, str | None]:
    """Decide whether normalized ``text`` sells an accessory rather than the device it mentions.

    ``device_start``/``device_end`` delimit the recognised device/model name (None if none) and
    ``category`` is the device's category.  Returns ``(is_accessory, accessory_type, reason)``.

    After the device name, when the title gives no device details (no storage, no "consola"/"celular"
    style noun), any accessory word that is not introduced as an included item ("con funda",
    "+ control", "juegos incluidos", "with MagSafe charging case") makes the listing an accessory:
    "AirPods Pro 2 Estuche de carga", "iPhone 15 Funda", "PS5 Slim Control".  Weak words describing the
    device's own part ("bateria 85", "pantalla impecable", "lente 18-55mm"), the word "caja" (see
    ``EMPTY_BOX_CUES``) and the family's standard in-box part in a title that describes the device ("Apple
    Watch Series 9 45mm GPS Correa Deportiva", see :func:`_standard_part_of_device`) do not count.
    """
    strong = _matches(text, _STRONG)
    weak = _matches(text, _WEAK)
    every = sorted(strong + weak)
    first_type = every[0][1] if every else "other"
    if _EMPTY_BOX.search(text):
        return True, "empty_box", "title says only the box is sold"
    if source_category and _ACCESSORY_CATEGORY.search(normalize_text(source_category)):
        return True, first_type, "source category is an accessories/parts category"
    if device_start is not None:
        if V.COMPAT_BEFORE_DEVICE_RE.search(text[:device_start]):
            return True, first_type, "device is introduced with 'para'/'compatible con'/'for'"
        for start, typ in every:
            if start >= device_start:
                break
            if _is_inclusion(text, start, digits=False):
                continue
            return True, typ, f"title leads with an accessory word ({typ})"
        model_end = device_end if device_end is not None else device_start
        for m in _PART_ANY_CONTEXT.finditer(text, model_end):
            if not _is_inclusion(text, m.start(), digits=False):
                return True, "spare_part", "replacement part named after the device (logic board, housing...)"
        if not has_storage and not _has_device_noun(text):
            spans = sorted(
                [(*span, True) for span in _word_spans(text, _STRONG)]
                + [(*span, False) for span in _word_spans(text, _WEAK)]
            )
            for start, end, typ, is_strong in spans:
                if start < model_end or typ == "empty_box":
                    continue
                if (
                    _is_inclusion(text, start, digits=True, model_end=model_end)
                    or _included_after(text, end)
                    or _introduced_as_included(text, start)
                ):
                    continue
                if not is_strong and _describes_own_part(text, start, end):
                    continue
                if _standard_part_of_device(text, start, end, typ, category, model_end):
                    continue
                return True, typ, f"accessory word ({typ}) after the device name and no device details"
        return False, None, None
    # No recognised device: accessory when the title leads with an accessory word, or targets a brand.
    tokens = text.split()
    offset = 0
    for tok in tokens:
        if tok not in _LEADING_FILLERS:
            break
        offset += len(tok) + 1
    for start, typ in every:
        if start == offset:
            return True, typ, f"title leads with an accessory word ({typ})"
    if re.search(r"(?<!\w)(?:para|compatible\scon|for)\s", text) and every:
        return True, first_type, "accessory word with 'para'/'compatible con'"
    return False, None, None


def _is_software(text: str, category: str | None, device_start: int | None) -> bool:
    if category != "console":
        return False
    if V.SOFTWARE_CUES_RE.search(text):
        return True
    for m in re.finditer(r"(?<!\w)juegos?(?!\w)", text):
        if _is_inclusion(text, m.start(), digits=True):
            continue
        prev = _prev_tokens(text, m.start(), 1)
        if prev and prev[-1] in ("dos", "tres", "cuatro", "cinco", "varios", "mas"):
            continue
        nxt = text[m.end() :].split()[:1]
        if nxt and nxt[0] in ("incluido", "incluidos", "incluida", "gratis", "regalo", "de"):
            continue
        return True
    return False


_BUNDLE_RES = [re.compile(cue) for cue in V.BUNDLE_CUES]
_CONNECTIVITY_PLUS = re.compile(r"(?<!\w)(?:wifi|wi\sfi|gps)\s?\+\s?(?:cellular|celular|lte|5g|4g)(?!\w)")
_INCLUDES_RE = re.compile(r"(?<!\w)(?:incluye|incluido|incluida|incluidos|includes)(?!\w)")


#: "x2" / "x 2" quantities (a model name such as "Poco X6" or "Xbox Series X" is excluded by position).
_TIMES_RE = re.compile(r"(?<!\w)x\s?(?:[2-9]|1\d)(?!\w)")
#: "con/mas/y <another device>" ("iPhone 15 con Apple Watch", "mas AirPods"); not "compatible con".
_WITH_RE = re.compile(
    r"(?<!\w)(?<!compatible\s)(?:con|mas|y|incluye|with|and)\s(?:un\s|una\s|unos\s|unas\s|sus\s|su\s|el\s|la\s|los\s)?"
)
_QUANTITY_NOUNS = frozenset({"consolas", "celulares", "telefonos", "unidades", "piezas", "equipos", "pzas"})


def is_bundle(text: str) -> bool:
    """True when normalized ``text`` sells several items together ("+ 2 controles", "con juegos",
    "combo", "lote", "kit", "2x", "x2", "2 iPhone ...", "(2 piezas)", "par de", "paquete", "bundle",
    "con control extra", "con PS VR2", "con Apple Watch", "incluye <non-standard item>")."""
    text = _CONNECTIVITY_PLUS.sub(" ", text)  # "wifi + cellular" is a variant, not a bundle
    if any(p.search(text) for p in _BUNDLE_RES):
        return True
    for m in _INCLUDES_RE.finditer(text):
        following = [t for t in text[m.end() :].split() if not t.isdigit() and t not in ("un", "una", "el", "la")]
        if following and following[0] not in V.STANDARD_INCLUSIONS:
            return True
    hits = V.find_model_hits(text)
    tokens = text.split()
    if len(tokens) > 1 and tokens[0] in V.LEADING_QUANTITIES:  # "2 PS5 Slim ...", "Dos iPhone 13"
        pos = len(tokens[0]) + 1
        if tokens[1] in _QUANTITY_NOUNS:
            pos += len(tokens[1]) + 1
        if any(h.start == pos for h in hits) or V.BRAND_RE.match(text, pos):
            return True
    for m in _TIMES_RE.finditer(text):
        if not any(h.start <= m.start() < h.end for h in hits):
            return True
    for m in _WITH_RE.finditer(text):
        # another device family named as an extra item; the standard controller of a console is not extra
        if any(h.start == m.end() and h.accessory_type != "controller" for h in hits):
            return True
    return False


def is_accessory(text: str) -> bool:
    """Convenience: True when normalized ``text`` describes an accessory (case, charger, controller,
    replacement part, empty box, game...) rather than a device."""
    analysis = _analyze_text(text)
    return analysis["is_accessory"]


# ------------------------------------------------------------------------------------------------
# Listing analysis
# ------------------------------------------------------------------------------------------------


def _detect_category(text: str, brand: str | None) -> str | None:
    best: tuple[int, str] | None = None
    for pattern, category in V.CATEGORY_KEYWORDS:
        m = re.search(r"(?<!\w)(?:" + pattern + r")(?!\w)", text)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), category)
    if best:
        return best[1]
    if brand:
        return V.BRAND_DEFAULT_CATEGORY.get(brand, "other")
    return None


def _is_filler(token: str) -> bool:
    return token in V.PREFIX_FILLERS or V.BRAND_RE.fullmatch(token) is not None or token in V.VARIANT_WORDS


#: Second words of multi-word color names ("gris espacial", "blanco estelar"), "neon" (Switch Joy-Con) and
#: shade words ("azul celeste", "verde azulado", "negro mate").
_COLOR_WORDS = frozenset(
    {"espacial", "estelar", "natural", "neon", "celeste", "azulado", "azulada", "verdoso", "verdosa", "rojizo",
     "rojiza", "claro", "clara", "oscuro", "oscura", "mate", "metalico", "metalica", "pastel"}
)


def _is_color_word(token: str) -> bool:
    """Color words including plural forms ("joycons blancos", "azules", "grises")."""
    if token in _COLOR_WORDS or extract_color(token) is not None:
        return True
    return any(
        token.endswith(plural) and len(token) > len(plural) + 2 and extract_color(token[: -len(plural)]) is not None
        for plural in ("es", "s")
    )


_SIZE_TOKEN_RE = re.compile(r"\d+(?:\.\d+)?(?:gb|tb|in|mm|hz|mah|w)?")
#: Condition scores: "10/10", "9.5/10".
_SCORE_RE = re.compile(r"\d{1,2}(?:\.\d)?/10")


def _describing_token(token: str) -> bool:
    """A word that describes the unit or the sale (condition, color, edition, standard part, paperwork,
    logistics...).  Unlike :func:`_known_token` it excludes generic prefix words ("super", "gran", "the")
    that also start game titles."""
    return (
        token in V.SUFFIX_FILLERS
        or token in V.LISTING_DESCRIPTION_WORDS
        or _SIZE_TOKEN_RE.fullmatch(token) is not None
        or _SCORE_RE.fullmatch(token) is not None
        or _is_color_word(token)
        or any(p.fullmatch(token) for p, _typ in _STRONG)
        or any(p.fullmatch(token) for p, _typ in _WEAK)
    )


def _known_token(token: str) -> bool:
    """A word that describes a device listing (condition, color, edition, standard part, paperwork,
    logistics...) rather than naming something else (a game, a special edition, another product)."""
    return _is_filler(token) or _describing_token(token)


def _model_end(text: str, hit: V.ModelHit | None) -> int | None:
    """Where the model name ends: the rule's match plus the following words the built model consumed
    ("airpods pro" + "2" for AirPods Pro 2, "apple watch" + "series 9")."""
    if hit is None:
        return None
    end = hit.end
    words = set((hit.model or "").split())
    for m in re.finditer(r"\S+", text[hit.end :]):
        if m.group(0) not in words:
            break
        end = hit.end + m.end()
    return end


def _words_until_inclusion(text: str, pos: int) -> list[str]:
    words: list[str] = []
    for tok in text[pos:].split():
        if tok in V.INCLUSION_WORDS or tok in ("incluye", "para"):
            break
        words.append(tok)
    return words


#: Articles and possessives that may open the phrase after "con"/"y" ("con un rayon", "con su caja").
_PHRASE_ARTICLES = frozenset({"un", "una", "unos", "unas", "el", "la", "los", "las", "lo", "su", "sus", "mi"})
_GAME_WORD_RE = re.compile(r"(?:video)?juegos?|games?")


def _names_extra_item(words: list[str]) -> bool:
    """The words of one "con ..."/"y ..." phrase name an extra item rather than describe the unit."""
    if any(_GAME_WORD_RE.fullmatch(tok) for tok in words):
        return True  # "y juegos", "con juego fisico"
    for tok, nxt in zip(words, words[1:], strict=False):
        if not _known_token(tok) and nxt.isdigit():
            return True  # a numbered title: "Spiderman 2", "FC 25", "Mario Kart 8"
    content = [tok for tok in words if tok not in _PHRASE_ARTICLES]
    # The phrase is about the unit when its first word is a known describing word or a standard in-box item
    # ("con detalle estetico", "con muy poco uso", "con base vertical", "y facturada", "y manuales");
    # otherwise its unknown words name something else, usually a game ("con Spiderman", "y Astro Bot").
    return bool(content) and not (_describing_token(content[0]) or content[0] in V.INBOX_ITEM_WORDS)


def _named_extra_item(text: str, model_end: int) -> bool:
    """A console listing that names an extra item after "con"/"y"/"+" ("con Spiderman 2", "y FC 25"):
    the extra (usually a game) is not part of the standard console.  Descriptions of the unit itself
    ("con detalle estetico", "con ticket de compra", "nueva sellada y facturada") are not extra items."""
    for m in re.finditer(r"(?<!\w)(?:con|y|mas|\+|e|incluye)(?!\w)", text[model_end:]):
        if _names_extra_item(_words_until_inclusion(text, model_end + m.end())):
            return True
    return False


#: Words right after a Joy-Con mention that make it a Joy-Con listing ("Joy-Con par", "Joy-Con (L)/(R)").
_CONTROLLER_LISTING_AFTER = frozenset(
    {"par", "pares", "pair", "set", "l", "r", "izq", "der", "controller", "controllers"}
)
#: Consoles whose own Joy-Con colour names the console variant ("OLED blanco" = white Joy-Con).  Not the
#: base "Nintendo Switch": that name is also the platform's, so "Nintendo Switch Neon Joy-Con" reads as a
#: Joy-Con listing.
_JOYCON_MODELS = frozenset({"switch oled"})


def _standard_controllers_named(text: str, model: str | None, hit: V.ModelHit, model_end: int) -> bool:
    """The console's own Joy-Con set named after a description of the console ("Nintendo Switch OLED
    Blanco Joy-Con Blancos", "Nintendo Switch OLED Model White Joy-Con"): the in-box controllers, not a
    Joy-Con listing.  Right after the model name ("Nintendo Switch OLED Joy-Con Neon") the title reads
    like a Joy-Con listing for that console, the same way as "PS5 Dualsense Blanco", and stays one."""
    if hit.rule != "joycon" or model not in _JOYCON_MODELS:
        return False
    between = text[model_end : hit.start].split()
    if not between or any(tok in _CONTROLLER_LISTING_AFTER for tok in text[hit.end :].split()[:2]):
        return False
    return all(
        (tok in _INCLUSION_LEADS or _known_token(tok))
        and not any(p.fullmatch(tok) for p, _typ in (*_STRONG, *_WEAK))
        for tok in between
    )


def _analyze_text(text: str, *, source_category: str | None = None) -> dict[str, Any]:
    """Core title analysis on normalized text (no source fields)."""
    notes: list[str] = []
    hits = V.find_model_hits(text)
    bundle = is_bundle(text)
    device_hits = [h for h in hits if h.accessory_type is None]
    primary = hits[0] if hits else None
    if primary is not None and primary.accessory_type and bundle and device_hits:
        primary = device_hits[0]  # "dualsense + ps5 slim" bundle: the console is the main item
    brand: str | None
    if primary is not None and primary.model is None and primary.accessory_type is None:
        # Family mentioned but not identified (e.g. "RTX 4060" inside a laptop listing): the title's
        # own brand/category words describe the listing better than the rule's.
        found = V.find_brand(text)
        brand = found[0] if found else primary.brand
        keyword_category = _detect_category(text, None)
        category, model = keyword_category or primary.category, None
        notes.extend(primary.notes)
    elif primary is not None:
        brand, category, model = primary.brand, primary.category, primary.model
        notes.extend(primary.notes)
    else:
        found = V.find_brand(text)
        brand = found[0] if found else None
        category, model = _detect_category(text, brand), None
    ram, storage = _ram_and_storage(text, category)
    model_end = _model_end(text, primary)
    # A storage mention is device evidence, except for single-configuration models where it usually
    # describes a memory card ("Nintendo Switch OLED Memoria 256gb").
    device_storage = storage is not None and model not in V.FIXED_STORAGE_MODELS
    accessory, acc_type, acc_reason = detect_accessory(
        text,
        primary.start if primary else None,
        has_storage=device_storage,
        source_category=source_category,
        device_end=model_end,
        category=category,
    )
    if primary is not None and primary.accessory_type and not accessory:
        accessory, acc_type, acc_reason = True, primary.accessory_type, f"{primary.model or primary.rule} is an accessory"
    if (
        not accessory
        and primary is not None
        and model_end is not None
        and not bundle
        and not device_storage
        and not _has_device_noun(text)
    ):
        # "PS5 Dualsense Blanco": an accessory product named after the device it belongs to.
        for hit in hits:
            if (
                hit.accessory_type
                and hit.start >= model_end
                and not _is_inclusion(text, hit.start, digits=True, model_end=model_end)
                and not _standard_controllers_named(text, model, hit, model_end)
            ):
                accessory, acc_type = True, hit.accessory_type
                acc_reason = f"{hit.model or hit.rule} ({hit.accessory_type}) named after the device: accessory listing"
                break
    if not accessory and _is_software(text, category, primary.start if primary else None):
        accessory, acc_type, acc_reason = True, "software", "title describes a game/software, not the console"
    if acc_reason:
        notes.append(acc_reason)
    ambiguous = False
    if model is not None and primary is not None and not accessory:
        distinct = {h.model for h in device_hits if h.model}
        if len(distinct) > 1 and not bundle:
            notes.append(f"several different models in title ({', '.join(sorted(distinct))}): ambiguous")
            model = None
            ambiguous = True
        prefix = text[: primary.start].split()
        unknown = [] if primary.rule in V.EXACT_CODE_RULES else [tok for tok in prefix if not _is_filler(tok)]
        no_device_details = category == "console" and not device_storage and not _has_device_noun(text)
        suffix_unknown = (
            [tok for tok in _words_until_inclusion(text, model_end) if not _known_token(tok)]
            if no_device_details and model_end is not None
            else []
        )
        if model is not None and len(unknown) >= 3:
            notes.append(f"unrecognised words before the model name ({' '.join(unknown)}): ambiguous")
            model = None
            ambiguous = True
        elif model is not None and no_device_details and (unknown or suffix_unknown):
            where = "named after" if unknown else "name followed by"
            notes.append(
                f"console {where} other words ({' '.join(unknown or suffix_unknown)}) without 'consola' or "
                "storage: possibly a game or accessory"
            )
            model = None
            ambiguous = True
    if not bundle and category == "console" and model_end is not None and _named_extra_item(text, model_end):
        bundle = True
        notes.append("an extra item (e.g. a game) is named after 'con'/'y'/'+': bundle")
    if model is not None:
        refined, note = V.refine_model(model, storage)
        if note:
            notes.append(note)
        model = refined
    tokens = extract_variant_tokens(text, category, model)
    if primary is not None:
        tokens = sorted(set(tokens) | set(primary.tokens))
    if model == "ps5 pro" and "disc" in tokens and not bundle:
        bundle = True  # the PS5 Pro is digital-only: a disc drive is a separately sold add-on
        notes.append("PS5 Pro listed with the separately sold disc drive: bundle")
    if accessory and acc_type:
        tokens = sorted(set(tokens) | {f"acc:{acc_type}"})
    return {
        "brand": brand,
        "model": model,
        "category": category,
        "ram_gb": ram,
        "storage_gb": storage,
        "is_accessory": accessory,
        "accessory_type": acc_type,
        "is_bundle": bundle,
        "variant_tokens": tokens,
        "notes": notes,
        "ambiguous": ambiguous,
    }


_ATTR_STORAGE_KEYS = ("internal_memory", "storage", "storage_capacity", "almacenamiento", "capacidad",
                      "memoria_interna", "capacity", "internal_storage")
_ATTR_RAM_KEYS = ("ram", "ram_memory", "memoria_ram", "memory")
_ATTR_COLOR_KEYS = ("color", "main_color", "colour")
_ATTR_GTIN_KEYS = ("gtin", "ean", "upc", "barcode", "isbn")


def _attr(attributes: dict[str, Any], keys: tuple[str, ...]) -> Any:
    lowered = {str(k).lower(): v for k, v in (attributes or {}).items()}
    for key in keys:
        value = lowered.get(key)
        if value not in (None, "", []):
            return value
    return None


def analyze_listing(raw: RawListing) -> tuple[NormalizedAttributes, list[str]]:
    """Like :func:`normalize_listing` but also returns human-readable notes explaining every
    inference and every reason the listing could not be clustered (canonical_key=None)."""
    from ..matching.matcher import canonical_key, missing_identity  # local import: matching depends on this

    title = raw.title or ""
    text = normalize_text(title)
    a = _analyze_text(text, source_category=raw.category_name)
    notes: list[str] = list(a["notes"])

    source_brand = normalize_text(raw.brand) if raw.brand else ""
    generic_source_brand = source_brand in V.GENERIC_BRANDS
    brand = a["brand"]
    if brand is None and raw.brand:
        brand = V.canonical_brand(source_brand)
        if brand:
            notes.append("brand taken from the source's brand field")
    model, category = a["model"], a["category"]
    if model is None and raw.model and not a["is_accessory"] and not a["ambiguous"]:
        source_hits = [h for h in V.find_model_hits(normalize_text(raw.model)) if h.model and not h.accessory_type]
        hit = source_hits[0] if source_hits else None
        # The model field of accessory and clone listings names the device they fit or imitate, so it is
        # only trusted when the brand is confirmed and the title itself says it sells such a device.
        refusal = None
        if hit is not None:
            if generic_source_brand:
                refusal = "the source brand is generic"
            elif brand != hit.brand:
                refusal = f"the brand {hit.brand} is not confirmed by the title or the source's brand field"
            elif not _title_names_device(text, hit.category):
                refusal = f"the title does not say it sells a {hit.category}"
        if hit is not None and refusal is None:
            brand, model, category = hit.brand, hit.model, hit.category
            notes.append("model taken from the source's model field")
        elif hit is not None:
            notes.append(f"source model field ({hit.model}) ignored: {refusal}")
    if category is None:
        category = _detect_category(text, brand)

    storage, ram = a["storage_gb"], a["ram_gb"]
    if storage is None:
        attr_storage = _attr(raw.attributes, _ATTR_STORAGE_KEYS)
        if attr_storage is not None:
            storage = extract_storage_gb(normalize_text(str(attr_storage)), category)
            if storage is not None:
                notes.append("storage taken from the source's attributes")
    if ram is None:
        attr_ram = _attr(raw.attributes, _ATTR_RAM_KEYS)
        if attr_ram is not None:
            m = _SIZE_RE.search(normalize_text(str(attr_ram)))
            if m:
                value = _to_gb(m.group(1), m.group(2))
                ram = value if value in RAM_VALUES else None
    if model is not None and storage is not None and a["storage_gb"] is None:
        refined, note = V.refine_model(model, storage)
        if note:
            notes.append(note)
        model = refined

    color = extract_color(text)
    if color is None:
        attr_color = _attr(raw.attributes, _ATTR_COLOR_KEYS)
        color = extract_color(normalize_text(str(attr_color))) if attr_color else None
    gtin = extract_gtin(title, raw.gtin, _attr(raw.attributes, _ATTR_GTIN_KEYS))

    tokens = a["variant_tokens"]
    if category != a["category"]:
        tokens = sorted(set(tokens) | set(extract_variant_tokens(text, category, model)))

    attrs = NormalizedAttributes(
        normalized_title=text,
        brand=brand,
        model=model,
        category=category,
        condition=parse_condition(raw.condition_raw, title, raw.description),
        storage_gb=storage,
        ram_gb=ram,
        color=color,
        gtin=gtin,
        is_accessory=a["is_accessory"],
        is_bundle=a["is_bundle"],
        variant_tokens=tokens,
    )
    attrs.canonical_key = canonical_key(attrs)
    if attrs.canonical_key is None:
        if not brand:
            notes.append("brand not recognised: not clustered")
        elif not model:
            notes.append("exact model not identified: not clustered")
        elif attrs.is_accessory:
            notes.append("accessory whose own product cannot be identified: not clustered")
        else:
            notes.extend(f"{reason}: not clustered" for reason in missing_identity(attrs))
    return attrs, notes


#: Device nouns -> category, for checking that a title names the kind of device a source model claims.
_DEVICE_NOUN_CATEGORY: dict[str, str] = {
    "celular": "phone", "smartphone": "phone", "telefono": "phone", "consola": "console", "console": "console",
    "videoconsola": "console", "laptop": "laptop", "notebook": "laptop", "computadora": "laptop",
    "tablet": "tablet", "tableta": "tablet", "smartwatch": "smartwatch", "reloj": "smartwatch",
    "dron": "drone", "drone": "drone",
}
_COMPATIBLE_CATEGORIES: dict[str, frozenset[str]] = {
    "console": frozenset({"console", "handheld_pc"}),
    "handheld_pc": frozenset({"handheld_pc", "console"}),
}


def _title_names_device(text: str, category: str) -> bool:
    """The title itself says it sells a device of ``category`` ("Celular Apple ...", "Consola ...")."""
    allowed = _COMPATIBLE_CATEGORIES.get(category, frozenset({category}))
    named = {_DEVICE_NOUN_CATEGORY[tok] for tok in text.split() if tok in _DEVICE_NOUN_CATEGORY}
    keyword = _detect_category(text, None)
    if keyword:
        named.add(keyword)
    return bool(named & allowed)


def normalize_listing(raw: RawListing) -> NormalizedAttributes:
    """Extract brand/model/category/condition/storage/etc. and compute the canonical key."""
    return analyze_listing(raw)[0]
