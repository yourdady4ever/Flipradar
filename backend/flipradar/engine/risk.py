"""Suspicious listing detection (deterministic rules).  OWNER: engine agent.

Every rule has a stable machine ``code`` and a fixed severity, so flags can be filtered, counted and
explained without any AI.  Text rules run on the title and the description after lower-casing and
removing accents (``"Réplica"`` -> ``"replica"``, ``"compañía"`` -> ``"compania"``).

Mexican listings very often *deny* a problem ("100% original, no es réplica", "libre de iCloud",
"no reportado", "liberado para cualquier compañía").  Rules marked negatable are therefore ignored
when the negation governs the matched term itself: a negation word (no, ni, sin, cero, nada, nunca...)
or phrase ("libre de") right before it, or separated from it only by a short chain of copulas,
auxiliaries, articles or refusal verbs ("no es una réplica", "nunca ha sido reportado", "nada de
imitaciones", "no acepto depósitos").  A negation of another word ("sin detalles réplica", "sin caja con
iCloud") does not hide the term.  Guarantees phrased as conditions ("si no enciende te regreso tu
dinero") are not admissions.  This keeps critical flags precise; anything ambiguous is reported with the
matched terms as evidence so a human can check it.

Codes and severities:

==================================  ========  ====================================================
code                                severity  trigger
==================================  ========  ====================================================
replica_or_counterfeit              critical  replica, clon(e), AAA, 1:1, copia, fake, generico, ...
box_or_parts_only                   critical  "solo caja", "caja vacia", for-parts cues
locked_or_reported                  critical  iCloud/FRP/Google account lock, reported/blacklisted
price_far_below_market              crit/warn total price < 35 % / < 55 % of comparable median
price_statistical_outlier           info      robust z-score < -3.5 (unusual, but a real arbitrage gap
                                              is an outlier by definition - informational only)
carrier_locked                      warning   "solo Telcel", "de compania", "bloqueado a AT&T"
off_platform_payment_or_contact     warning   WhatsApp, phone numbers, e-mail, deposits, OXXO
seller_new_or_unrated               warning   < 5 transactions, red/orange, < 90 days, > 10 % neg.
low_match_confidence                warn/info match confidence < 0.85 / unknown
non_original_parts                  info      generic/non-original accessories or parts mentioned
seller_unknown                      info      no seller reputation data
shipping_unknown                    info      shipping cost not reported
condition_unknown                   info      condition not stated
stale_listing                       info      not seen for more than 72 hours
==================================  ========  ====================================================
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from .explain import fmt_money, fmt_pct_ratio
from .stats import age, robust_z, total_price_cents
from .types import ListingView, PriceStats, RiskFlag, Severity

SEVERITY_RANK: dict[str, int] = {"none": 0, "info": 1, "warning": 2, "critical": 3}

# Price rule thresholds (ratio = total price / comparable median).
CRITICAL_PRICE_RATIO = Decimal("0.35")
WARNING_PRICE_RATIO = Decimal("0.55")
PRICE_Z_THRESHOLD = Decimal("-3.5")
# Seller rule thresholds.
MIN_SELLER_TRANSACTIONS = 5
MIN_ACCOUNT_AGE_DAYS = 90
MAX_NEGATIVE_RATINGS_PCT = Decimal(10)
LOW_REPUTATION_LEVELS = ("1_red", "2_orange")
# Match confidence below this is a warning.
MATCH_CONFIDENCE_WARNING = 0.85
STALE_AFTER_HOURS = 72

_FOUR_DP = Decimal("0.0001")

NEGATORS = frozenset(
    {"no", "ni", "sin", "cero", "nada", "nunca", "jamas", "not", "never", "without", "nor", "non"}
)
NEGATION_PHRASES = ("libre de", "libres de", "free of", "free from")
# Words that may stand between a negator and the term it governs: copulas/auxiliaries ("no es replica",
# "nunca ha sido reportado"), articles and "de" ("no es una replica", "nada de imitaciones") and the verbs
# sellers use to refuse something ("no acepto depositos", "no se aceptan depositos", "no cuenta con icloud").
NEGATION_LINKS = frozenset(
    {
        "es", "son", "sea", "esta", "estan", "fue", "ha", "han", "sido", "estado", "tiene", "tienen", "trae",
        "presenta", "cuenta", "con", "de", "del", "la", "el", "los", "las", "un", "una", "se", "acepto",
        "aceptamos", "acepta", "aceptan", "hago", "hacemos", "pido", "pedimos", "recibo", "recibimos", "manejo",
        "manejamos", "maneja", "is", "are", "was", "has", "have", "been", "a", "an", "the", "of", "accept", "do",
    }
)
MAX_NEGATION_LINKS = 3
# "si no enciende te regreso tu dinero" is a guarantee, not an admission.
CONDITIONAL_WORDS = frozenset({"si", "if"})
# Words that, right before a counterfeit term, mean the *accessory/part* is generic, not the product.
ACCESSORY_NOUNS = frozenset(
    {
        "cargador", "cargadores", "cable", "cables", "funda", "fundas", "mica", "micas", "estuche",
        "correa", "correas", "adaptador", "audifonos", "caja", "bateria", "pantalla", "display",
        "tapa", "carcasa", "control", "controles", "eliminador", "cubo", "protector", "manual",
        "refaccion", "refacciones", "pieza", "piezas", "charger", "case", "battery", "screen",
    }
)
# Words that may follow the accessory noun before the counterfeit term ("mica de regalo generica").
ACCESSORY_FILLERS = frozenset(
    {"de", "regalo", "incluido", "incluida", "incluidos", "incluidas", "extra", "adicional", "adicionales"}
)
# Words that, before "compania", mean the phone is UNLOCKED (works with any carrier).
CARRIER_FREE_WORDS = frozenset(
    {
        "cualquier", "toda", "todas", "libre", "liberado", "liberada", "liberados", "desbloqueado",
        "desbloqueada", "any", "unlocked",
    }
)
_CARRIERS = r"(?:telcel|at&t|att|movistar|unefon|bait|virgin(?: mobile)?|altan|weex|pillofon)"
_CLAUSE_BREAK = re.compile(r"[,.;!?\n|()\[\]/]")
_WORD_STRIP = ":;,.!?\"'¡¿*-"


def normalize_text(text: str | None) -> str:
    """Lower-case, strip accents (NFKD + drop combining marks) and collapse whitespace."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", stripped.lower()).strip()


def _words_before(text: str, start: int, count: int) -> list[str]:
    """The last ``count`` words before ``start`` within the same clause (punctuation-stripped)."""
    clause = _CLAUSE_BREAK.split(text[max(0, start - 60) : start])[-1]
    words = [word.strip(_WORD_STRIP) for word in clause.split()]
    return [word for word in words if word][-count:]


def _is_negated(text: str, start: int) -> bool:
    """True when a negation governs the match: a negator (or "libre de") right before it, or separated
    from it only by up to :data:`MAX_NEGATION_LINKS` link words.  "sin detalles replica" is NOT negated:
    "sin" governs "detalles"."""
    words = _words_before(text, start, MAX_NEGATION_LINKS + 2)
    links = 0
    for index in range(len(words) - 1, -1, -1):
        word = words[index]
        if word in NEGATORS:
            return True
        if index > 0 and f"{words[index - 1]} {word}" in NEGATION_PHRASES:
            return True
        if word not in NEGATION_LINKS or links == MAX_NEGATION_LINKS:
            return False
        links += 1
    return False


def _is_conditional(text: str, start: int) -> bool:
    words = _words_before(text, start, 1)
    return bool(words) and words[0] in CONDITIONAL_WORDS


def _accessory_qualified(text: str, start: int, *, is_title: bool) -> bool:
    """True when an accessory/part noun precedes the match ("cargador generico", "mica de regalo
    generica").  An accessory that is said to be missing ("sin caja replica") does not qualify.

    In a title the first word names the product itself ("Audifonos tipo original" sells headphones),
    so it never qualifies the match as being about an included accessory.
    """
    words = _words_before(text, start, 6)
    if is_title and len(text[:start].split()) <= len(words):
        words = words[1:]
    while words and words[-1] in ACCESSORY_FILLERS:
        words.pop()
    return any(
        words[index] in ACCESSORY_NOUNS and not (index > 0 and words[index - 1] in NEGATORS)
        for index in range(max(0, len(words) - 2), len(words))
    )


@dataclass(frozen=True)
class _Term:
    label: str
    regex: re.Pattern[str]
    negatable: bool = True
    # For counterfeit terms: when an accessory/part noun precedes the match, the match is reported as
    # ``non_original_parts`` (info) instead of a critical counterfeit flag.
    accessory_aware: bool = False
    # Optional extra validation of the matched text.
    check: Callable[[str], bool] | None = None
    # Ignored after "si"/"if": "si no enciende te regreso tu dinero" is a guarantee.
    conditional_exempt: bool = False


def _t(
    label: str,
    pattern: str,
    *,
    negatable: bool = True,
    accessory_aware: bool = False,
    check: Callable[[str], bool] | None = None,
    conditional_exempt: bool = False,
) -> _Term:
    return _Term(label, re.compile(pattern), negatable, accessory_aware, check, conditional_exempt)


def _is_mx_phone(matched: str) -> bool:
    """10 national digits, optionally prefixed by the +52 country code."""
    digits = re.sub(r"\D", "", matched)
    return len(digits) == 10 or (len(digits) == 12 and digits.startswith("52"))


# "AAA"/"triple A" also name battery sizes ("pilas tipo AAA") and big-budget video games ("juegos AAA").
_NOT_BATTERY_OR_GAME = (
    r"(?<!pila )(?<!pilas )(?<!bateria )(?<!baterias )(?<!pila tipo )(?<!pilas tipo )(?<!bateria tipo )"
    r"(?<!baterias tipo )(?<!juego )(?<!juegos )(?<!videojuego )(?<!videojuegos )(?<!titulo )(?<!titulos )"
)
_NOT_BATTERY_AFTER = r"(?!\s*(?:pila|bateria|batter))"
_UNITS = r"(?:kg|kilos?|lbs?|cm|mm|mts?|metros?|m|lts?|litros?|l|gb|tb|mb|ghz|mhz|pulgadas|plg|ch)\b|\""
_DOCUMENTS = (
    r"(?:factura|ticket|nota|recibo|garantia|comprobante|seguridad|respaldo|identificacion|ine|documento|compra|guia"
    r"|contrato|pedimento)s?"
)

REPLICA_TERMS = (
    _t("replica", r"\breplicas?\b", accessory_aware=True),
    _t("clon", r"\bclon(?:e|es|ado|ada|ados|adas)?\b", accessory_aware=True),
    _t("aaa", rf"{_NOT_BATTERY_OR_GAME}\baaa\b{_NOT_BATTERY_AFTER}", accessory_aware=True),
    _t("triple a", rf"{_NOT_BATTERY_OR_GAME}\btriple a\b{_NOT_BATTERY_AFTER}", accessory_aware=True),
    _t("1:1", r"(?<![\d:])1 ?: ?1(?![\d:])", accessory_aware=True),
    # "calidad 1.1" - but not versions ("USB 1.1") or measurements ("1.1 kg").
    _t("1.1", rf"(?<![\w.,])(?<!usb )(?<!version )(?<!bluetooth )1\.1(?![\d.,])(?!\s*(?:{_UNITS}))",
       accessory_aware=True),
    # "copia exacta" - but not "copia de la factura".
    _t("copia", rf"\bcopias?\b(?!\s+(?:de|del)\s+(?:(?:la|el|tu|su|mi)\s+)?{_DOCUMENTS}\b)", accessory_aware=True),
    _t("calidad espejo", r"\bcalidad espejo\b", accessory_aware=True),
    _t("fake", r"\bfakes?\b", accessory_aware=True),
    _t("generico", r"\bgeneric[oa]s?\b", accessory_aware=True),
    _t("imitacion", r"\bimitacion(?:es)?\b", accessory_aware=True),
    _t("tipo original", r"\btipo original\b", accessory_aware=True),
    # "similar a Jordan", "similar al original" - but not the condition "(estado) similar a nuevo".
    _t("similar a",
       r"(?<!estado )(?<!condicion )(?<!condiciones )\bsimilar(?:es)? al?\b"
       r"(?!\s+(?:(?:un|uno|una|estar)\s+)?(?:nuev[oa]s?|new|estrenar)\b)",
       accessory_aware=True),
    _t("no original", r"\bno (?:es |son |sea )?(?:(?:al )?100 ?% |cien por ciento )?original(?:es)?\b",
       negatable=False, accessory_aware=True, conditional_exempt=True),
    # "consola alternativa" - but not "color alternativo" or "como alternativa (de pago)".
    _t("alternativo",
       r"(?<!color )(?<!colores )(?<!como )(?<!una )(?<!otra )(?<!forma )(?<!opcion )(?<!manera )(?<!pago )"
       r"(?<!envio )(?<!entrega )\balternativ[oa]s?\b(?!\s+(?:de|para)\s+(?:pago|envio|entrega|contacto)\b)",
       accessory_aware=True),
)

BOX_TERMS = (
    # "PS5 solo caja" - but not the accessory list "incluye solo caja y cable".
    _t("solo caja",
       r"(?<!incluye )(?<!incluyo )(?<!incluimos )(?<!trae )(?<!viene con )"
       r"\bsolo (?:la |el )?caja\b(?! (?:abierta|danada|maltratada|golpeada|rota|sellada))"),
    _t("caja vacia", r"\bcaja vacia\b"),
    _t("empty box", r"\bempty box\b"),
    _t("box only", r"\bbox only\b"),
    _t("solo cargador", r"\bsolo (?:el )?cargador\b"),
)

PARTS_TERMS = (
    _t("para piezas", r"\bpara (?:piezas|partes)\b"),
    _t("refacciones", r"\b(?:para|como|solo) refaccion(?:es)?\b"),
    _t("no enciende", r"\bno (?:enciende|prende)\b", negatable=False, conditional_exempt=True),
    _t("display roto", r"\bdisplay (?:roto|estrellado|quebrado)\b"),
    _t("pantalla rota", r"\bpantalla (?:rota|estrellada|quebrada)\b"),
    _t("for parts", r"\b(?:for parts|parts only)\b"),
    _t("mojado", r"\b(?:se )?moj(?:o|ado|ada)\b"),
    _t("falla", r"\b(?:con|tiene|presenta) fallas?\b"),
    _t("no carga", r"\bno carga\b", negatable=False, conditional_exempt=True),
    _t("no da imagen", r"\bno da (?:imagen|video)\b", negatable=False, conditional_exempt=True),
    _t("cracked screen", r"\bcracked (?:screen|display|glass)\b"),
    _t("not working", r"\b(?:not working|does not (?:power|turn) on|won.?t turn on)\b", negatable=False),
)

_UNLOCK_WORDS = r"(?:libre|limpi[oa]|desvinculad[oa]|liberad[oa]|desbloquead[oa]|eliminad[oa]|borrad[oa]|removid[oa]|quitad[oa]|free|clean|off|removed)"

LOCK_TERMS = (
    _t("icloud", rf"\bicloud\b(?![\s:,-]*{_UNLOCK_WORDS})"),
    _t("bloqueado",
       rf"\bbloquead[oa]s?\b(?![\s:,-]*(?:a|para|con|de|por|en|solo)?\s*(?:la |el )?(?:compania|operador|{_CARRIERS})\b)"),
    _t("reportado", r"\breportad[oa]s?\b"),
    _t("reporte de robo", r"\breporte de robo\b"),
    _t("robado", r"\brobad[oa]s?\b"),
    _t("imei invalido", r"\bimei (?:invalido|reportado|bloqueado|clonado|nulo|borrado|cambiado|baneado|en lista negra)\b",
       negatable=False),
    _t("lista negra", r"\blista negra\b"),
    _t("cuenta google", rf"\bcuenta (?:de )?(?:google|gmail)\b(?![\s:,-]*{_UNLOCK_WORDS})"),
    _t("frp", rf"\bfrp\b(?![\s:,-]*{_UNLOCK_WORDS})"),
    _t("blacklisted", r"\bblack ?listed\b|\bbad (?:esn|imei)\b"),
    _t("activation lock", rf"\bactivation lock(?:ed)?\b(?![\s:,-]*{_UNLOCK_WORDS})"),
    _t("carrier locked english", r"\bcarrier locked\b|\bsim locked\b"),
)

CARRIER_TERMS = (
    _t("solo operador", rf"\bsolo (?:para |con |en )?{_CARRIERS}\b"),
    _t("bloqueado a operador", rf"\bbloquead[oa]s? (?:a|para|con|de|por|en) (?:la )?(?:compania|{_CARRIERS})\b"),
)
_COMPANIA = re.compile(r"\bcompanias?\b(?![\s:,-]*(?:libre|liberad|desbloquead|cualquier|todas))")

OFF_PLATFORM_TERMS = (
    _t("whatsapp", r"\bwhats ?app\b|\bwhats\b|\bwsp\b"),
    _t("wa.me", r"\bwa\.me\b"),
    _t("telegram", r"\btelegram\b"),
    # Mexican grouping: 55 1234 5678 / 551 234 5678 / (55) 1234-5678 / 5512345678, optional +52.
    _t("phone_number", r"(?<![\d+])(?:\+?52[ .-]?)?\(?\d{2,3}\)?[ .-]?\d{3,4}[ .-]?\d{4}(?!\d)",
       negatable=False, check=_is_mx_phone),
    _t("email_address", r"[a-z0-9._%+-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}", negatable=False),
    _t("deposito", r"\bdepositos?\b"),
    _t("transferencia", r"\btransferencias?\b"),
    _t("oxxo", r"\boxxo\b"),
    _t("pago por fuera", r"\bpagos? por fuera\b|\bfuera de (?:la )?plataforma\b"),
    _t("solo efectivo por adelantado", r"\bsolo efectivo (?:por )?adelantado\b"),
)


@dataclass
class _TextHits:
    labels: set[str]
    fields: set[str]

    def add(self, label: str, field: str) -> None:
        self.labels.add(label)
        self.fields.add(field)

    def evidence(self) -> dict[str, Any]:
        return {"matches": sorted(self.labels), "fields": sorted(self.fields)}


def _scan(texts: dict[str, str], terms: tuple[_Term, ...]) -> tuple[_TextHits, _TextHits]:
    """Return (hits, accessory_qualified_hits) for ``terms`` over the normalized ``texts``."""
    hits = _TextHits(set(), set())
    accessory_hits = _TextHits(set(), set())
    for field_name, text in texts.items():
        if not text:
            continue
        for term in terms:
            for match in term.regex.finditer(text):
                if term.check is not None and not term.check(match.group(0)):
                    continue
                if term.negatable and _is_negated(text, match.start()):
                    continue
                if term.conditional_exempt and _is_conditional(text, match.start()):
                    continue
                if term.accessory_aware and _accessory_qualified(
                    text, match.start(), is_title=field_name == "title"
                ):
                    accessory_hits.add(term.label, field_name)
                else:
                    hits.add(term.label, field_name)
    return hits, accessory_hits


def _scan_compania(texts: dict[str, str]) -> _TextHits:
    hits = _TextHits(set(), set())
    for field_name, text in texts.items():
        for match in _COMPANIA.finditer(text):
            words = _words_before(text, match.start(), 3)
            if any(word in CARRIER_FREE_WORDS for word in words) or _is_negated(text, match.start()):
                continue
            hits.add("compania", field_name)
    return hits


def _decimal_or_none(value: Any) -> Decimal | None:
    """Parse a finite number (int/float/str/Decimal); anything else (incl. NaN/inf, bools) -> None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _int_or_none(value: Any) -> int | None:
    number = _decimal_or_none(value)
    return None if number is None else int(number)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _seller_flag(listing: ListingView) -> RiskFlag | None:
    """Seller reputation rule.

    Recognised ``listing.seller`` keys: ``transactions_total`` (completed sales), ``reputation_level``
    (Mercado Libre style ``1_red`` .. ``5_green``), ``registration_date`` (ISO-8601) and
    ``ratings_negative_pct`` (0..100).  ``ratings_negative`` (a 0..1 fraction, the Mercado Libre API
    shape) is accepted as an alias.
    """
    seller = listing.seller or {}
    evidence: dict[str, Any] = {}
    reasons: list[str] = []

    transactions = _int_or_none(seller.get("transactions_total"))
    if transactions is not None:
        evidence["transactions_total"] = transactions
        if transactions < MIN_SELLER_TRANSACTIONS:
            reasons.append(f"only {transactions} completed transaction(s)")

    level = seller.get("reputation_level")
    if isinstance(level, str) and level.strip():
        level = level.strip().lower()
        evidence["reputation_level"] = level
        if level.startswith(LOW_REPUTATION_LEVELS) or level in ("red", "orange"):
            reasons.append(f"reputation level {level}")

    registered = _parse_datetime(seller.get("registration_date"))
    if registered is not None:
        account_days = age(listing.observed_at, registered).days
        evidence["account_age_days"] = account_days
        if account_days < MIN_ACCOUNT_AGE_DAYS:
            reasons.append(f"account is {account_days} days old")

    negative_pct = _decimal_or_none(seller.get("ratings_negative_pct"))
    if negative_pct is None:
        fraction = _decimal_or_none(seller.get("ratings_negative"))
        if fraction is not None:
            negative_pct = fraction * 100
    if negative_pct is not None:
        negative_pct = negative_pct.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        evidence["ratings_negative_pct"] = float(negative_pct)
        if negative_pct > MAX_NEGATIVE_RATINGS_PCT:
            reasons.append(f"{negative_pct}% negative ratings")

    if not evidence:
        return RiskFlag(
            "seller_unknown",
            "info",
            "No seller reputation data is available for this listing.",
            {},
        )
    if not reasons:
        return None
    evidence["reasons"] = reasons
    return RiskFlag(
        "seller_new_or_unrated",
        "warning",
        "Seller looks new or poorly rated: " + "; ".join(reasons) + ".",
        evidence,
    )


def _price_flag(listing: ListingView, stats: PriceStats | None) -> RiskFlag | None:
    if stats is None or not stats.median_cents or stats.median_cents <= 0 or stats.n <= 0:
        return None
    total = total_price_cents(listing)
    if total is None:
        return None
    ratio = (Decimal(total) / Decimal(stats.median_cents)).quantize(_FOUR_DP, rounding=ROUND_HALF_UP)
    z = robust_z(total, stats)
    evidence: dict[str, Any] = {
        "ratio": float(ratio),
        "median_cents": stats.median_cents,
        "n": stats.n,
        "price_cents": total,
        "robust_z": z,
    }
    described = (
        f"Total price {fmt_money(total)} is {fmt_pct_ratio(ratio)} of the median of {stats.n} "
        f"comparables ({fmt_money(stats.median_cents)})"
    )
    if ratio < CRITICAL_PRICE_RATIO:
        return RiskFlag(
            "price_far_below_market",
            "critical",
            described + "; prices this low are typical of scams, replicas or misdescribed items.",
            evidence,
        )
    if ratio < WARNING_PRICE_RATIO:
        return RiskFlag(
            "price_far_below_market",
            "warning",
            described + "; verify the item and the seller before paying.",
            evidence,
        )
    if z is not None and Decimal(str(z)) < PRICE_Z_THRESHOLD:
        return RiskFlag(
            "price_statistical_outlier",
            "info",
            described + f"; robust z-score {z} makes it an unusual low price for this product.",
            evidence,
        )
    return None


def _severity_key(flag: RiskFlag) -> tuple[int, str]:
    return (-SEVERITY_RANK[flag.severity], flag.code)


def assess_listing(listing: ListingView, stats: PriceStats | None) -> list[RiskFlag]:
    """Return risk flags for a listing.  ``stats`` are the comparable stats for its product/condition
    (None when unknown).  Never raises on missing data - missing data is itself a (low) signal.

    Flags are unique per code and sorted by severity (critical first) then code.
    """
    texts = {
        "title": normalize_text(listing.title),
        "description": normalize_text(listing.description),
    }
    flags: list[RiskFlag] = []

    def text_flag(code: str, severity: Severity, message: str, hits: _TextHits) -> None:
        if hits.labels:
            flags.append(RiskFlag(code, severity, message + " Matched: " + ", ".join(sorted(hits.labels)) + ".",
                                  hits.evidence()))

    replica, replica_parts = _scan(texts, REPLICA_TERMS)
    text_flag("replica_or_counterfeit", "critical",
              "Wording suggests a replica, clone or non-original product.", replica)
    text_flag("non_original_parts", "info",
              "Mentions generic or non-original accessories/parts.", replica_parts)

    box, _ = _scan(texts, BOX_TERMS)
    if (listing.condition or "").lower() != "for_parts":
        parts, _ = _scan(texts, PARTS_TERMS)
        box.labels |= parts.labels
        box.fields |= parts.fields
    text_flag("box_or_parts_only", "critical",
              "Wording suggests only the box/accessory, or a damaged unit sold for parts.", box)

    locked, _ = _scan(texts, LOCK_TERMS)
    text_flag("locked_or_reported", "critical",
              "Wording suggests an account lock (iCloud/FRP/Google) or a reported/blacklisted device.", locked)

    carrier, _ = _scan(texts, CARRIER_TERMS)
    compania = _scan_compania(texts)
    carrier.labels |= compania.labels
    carrier.fields |= compania.fields
    text_flag("carrier_locked", "warning",
              "Device may be locked to a carrier; check it works with yours.", carrier)

    off_platform, _ = _scan(texts, OFF_PLATFORM_TERMS)
    text_flag("off_platform_payment_or_contact", "warning",
              "Listing asks for contact or payment outside the platform (no buyer protection).", off_platform)

    price = _price_flag(listing, stats)
    if price is not None:
        flags.append(price)

    seller = _seller_flag(listing)
    if seller is not None:
        flags.append(seller)

    if listing.shipping_mxn_cents is None:
        flags.append(RiskFlag("shipping_unknown", "info", "Shipping cost to you is not reported by the source.", {}))

    if listing.match_confidence is None:
        flags.append(RiskFlag(
            "low_match_confidence", "info",
            "Product match confidence is unknown; confirm the exact model/variant.",
            {"match_confidence": None},
        ))
    elif listing.match_confidence < MATCH_CONFIDENCE_WARNING:
        flags.append(RiskFlag(
            "low_match_confidence", "warning",
            f"Product match confidence is {listing.match_confidence:.2f} (below "
            f"{MATCH_CONFIDENCE_WARNING:.2f}); the listing may be a different model or variant.",
            {"match_confidence": listing.match_confidence, "threshold": MATCH_CONFIDENCE_WARNING},
        ))

    if (listing.condition or "").lower() == "unknown":
        flags.append(RiskFlag(
            "condition_unknown", "info",
            "Item condition is not stated by the source; it is compared as used.", {},
        ))

    hours = _decimal_or_none((listing.attributes or {}).get("hours_since_seen"))
    if hours is not None and hours > STALE_AFTER_HOURS:
        flags.append(RiskFlag(
            "stale_listing", "info",
            f"Listing was last seen {hours.normalize():f} hours ago; it may no longer be available.",
            {"hours_since_seen": float(hours), "threshold_hours": STALE_AFTER_HOURS},
        ))

    return sorted(flags, key=_severity_key)


#: Two or more independent warnings make a listing suspicious even without a critical flag.
SUSPICIOUS_WARNING_COUNT = 2


def is_suspicious(flags: list[RiskFlag] | list[dict]) -> bool:
    """Single definition of "suspicious" used by listings, opportunities, filters and alerts:
    any ``critical`` flag, or at least :data:`SUSPICIOUS_WARNING_COUNT` ``warning`` flags.

    A lone warning (e.g. a price at 50 % of market, or a new seller) is shown as a warning badge but does
    not hide the opportunity - an arbitrage deal is below market by definition.
    """
    severities = [f.get("severity") if isinstance(f, dict) else f.severity for f in flags]
    return "critical" in severities or severities.count("warning") >= SUSPICIOUS_WARNING_COUNT


def risk_level(flags: list[RiskFlag] | list[dict]) -> str:
    """'none' | 'info' | 'warning' | 'critical' (max severity).  Accepts RiskFlag objects or dicts."""
    level = "none"
    for flag in flags:
        severity = flag.get("severity") if isinstance(flag, dict) else flag.severity
        if SEVERITY_RANK.get(severity, 0) > SEVERITY_RANK[level]:
            level = severity
    return level
