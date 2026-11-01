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
from decimal import ROUND_FLOOR, Decimal, InvalidOperation
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


def _has_cue(text: str, patterns: list[re.Pattern[str]]) -> bool:
    """True when any cue occurs and is not negated by one of the two preceding words."""
    for pattern in patterns:
        for m in pattern.finditer(text):
            first = m.group(0).split()[0]
            prev = _prev_tokens(text, m.start(), 2)
            if first in ("no", "not"):
                # "no enciende": negated only by "si no enciende" style conditionals
                if prev and prev[-1] in ("si", "if", "aunque"):
                    continue
                return True
            if prev and (prev[-1] in _NEGATIONS or (len(prev) > 1 and prev[-2] in _NEGATIONS and prev[-1] in _COPULAS)):
                continue  # "sin danos", "no reportado", "no esta danado" - but not "nunca usado, sellado"
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
    if any(p.search(value) for p in _FOR_PARTS):
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


def _ram_and_storage(text: str, category: str | None) -> tuple[int | None, int | None]:
    ram: int | None = None
    ram_positions: set[int] = set()
    storage_hint: int | None = None
    for pattern in _RAM_RES:
        for m in pattern.finditer(text):
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
        if small <= 32 and small in RAM_VALUES and min(rest) >= 64:
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


def _inch_token(value: str) -> str:
    number = Decimal(value)
    whole = number.to_integral_value(rounding=ROUND_FLOOR)
    if number - whole >= Decimal("0.85"):
        whole += 1
    return f"{int(whole)}in"


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
        sizes = {_inch_token(m.group(1)) for m in re.finditer(r"(?<![\w.])(\d{1,2}(?:\.\d)?)in(?!\w)", text)}
        tokens.extend(sorted(s for s in sizes if 7 <= int(s[:-2]) <= 18))
    if category == "smartwatch":
        tokens.extend(sorted({m.group(1) + "mm" for m in re.finditer(r"(?<!\w)(3[8-9]|4[0-9]|5[01])mm(?!\w)", text)}))
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


def _is_inclusion(text: str, pos: int, *, digits: bool) -> bool:
    prev = _prev_tokens(text, pos, 2)
    if not prev:
        return False
    if prev[-1] in V.INCLUSION_WORDS or (digits and prev[-1].isdigit()):
        return True
    if prev[-1] == "de" and len(prev) > 1 and prev[-2] in V.DEVICE_NOUNS:
        return True  # "consola de juegos"
    return digits and any(p in V.INCLUSION_WORDS for p in prev)


def _has_device_noun(text: str) -> bool:
    return any(tok in V.DEVICE_NOUNS for tok in text.split())


def detect_accessory(
    text: str, device_start: int | None, *, has_storage: bool = False, source_category: str | None = None
) -> tuple[bool, str | None, str | None]:
    """Decide whether normalized ``text`` sells an accessory rather than the device it mentions.

    ``device_start`` is the position of the recognised device/model name (None if none).  Returns
    ``(is_accessory, accessory_type, reason)``.
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
        if not has_storage and not _has_device_noun(text):
            for start, typ in strong:
                if start < device_start or _is_inclusion(text, start, digits=True):
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


def is_bundle(text: str) -> bool:
    """True when normalized ``text`` sells several items together ("+ 2 controles", "con juegos",
    "combo", "lote", "kit", "2x", "paquete", "bundle", "incluye <non-standard item>")."""
    text = _CONNECTIVITY_PLUS.sub(" ", text)  # "wifi + cellular" is a variant, not a bundle
    if any(p.search(text) for p in _BUNDLE_RES):
        return True
    for m in _INCLUDES_RE.finditer(text):
        following = [t for t in text[m.end() :].split() if not t.isdigit() and t not in ("un", "una", "el", "la")]
        if following and following[0] not in V.STANDARD_INCLUSIONS:
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
    accessory, acc_type, acc_reason = detect_accessory(
        text, primary.start if primary else None, has_storage=storage is not None, source_category=source_category
    )
    if primary is not None and primary.accessory_type and not accessory:
        accessory, acc_type, acc_reason = True, primary.accessory_type, f"{primary.model or primary.rule} is an accessory"
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
        if model is not None and len(unknown) >= 3:
            notes.append(f"unrecognised words before the model name ({' '.join(unknown)}): ambiguous")
            model = None
            ambiguous = True
        elif model is not None and category == "console" and unknown and storage is None and not _has_device_noun(text):
            notes.append(
                f"console named after other words ({' '.join(unknown)}) without 'consola' or storage: "
                "possibly a game or accessory"
            )
            model = None
            ambiguous = True
    if model is not None:
        refined, note = V.refine_model(model, storage)
        if note:
            notes.append(note)
        model = refined
    tokens = extract_variant_tokens(text, category, model)
    if primary is not None:
        tokens = sorted(set(tokens) | set(primary.tokens))
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
    from ..matching.matcher import canonical_key  # local import: matching depends on this package

    title = raw.title or ""
    text = normalize_text(title)
    a = _analyze_text(text, source_category=raw.category_name)
    notes: list[str] = list(a["notes"])

    brand = a["brand"]
    if brand is None and raw.brand:
        brand = V.canonical_brand(normalize_text(raw.brand))
        if brand:
            notes.append("brand taken from the source's brand field")
    model, category = a["model"], a["category"]
    if model is None and raw.model and not a["is_accessory"] and not a["ambiguous"]:
        source_hits = [h for h in V.find_model_hits(normalize_text(raw.model)) if h.model and not h.accessory_type]
        if source_hits and (brand is None or brand == source_hits[0].brand):
            hit = source_hits[0]
            brand, model, category = hit.brand, hit.model, hit.category
            notes.append("model taken from the source's model field")
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
    return attrs, notes


def normalize_listing(raw: RawListing) -> NormalizedAttributes:
    """Extract brand/model/category/condition/storage/etc. and compute the canonical key."""
    return analyze_listing(raw)[0]
