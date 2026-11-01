"""Generic RSS 2.0 / RSS 1.0 / Atom deal-feed collector.  OWNER: collectors agent.

The operator lists feed URLs in ``FLIPRADAR_RSS_FEEDS`` (comma separated).  FLIP RADAR ships NO default
feed: the terms of specific deal sites were not verified, so choosing a feed you are allowed to read
is the operator's decision.  Feeds are trusted configuration (they may point to a self-hosted
RSS-Bridge on the LAN), so they are not SSRF-filtered; they ARE size-capped (2 MB) and parsed with
``defusedxml`` (no entity expansion / external entities).

Prices are extracted conservatively from the title (then the description) in Mexican formats such as
``$12,999``, ``$12,999.00``, ``MXN 12999``, ``12 999 pesos``.  An item is SKIPPED when there is no
price, more than one distinct price (e.g. "antes $15,999 ahora $12,999"), a "desde"/"a partir de"
(from) qualifier, or only US-dollar amounts.  Nothing is guessed.
"""

from __future__ import annotations

import email.utils
import hashlib
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any, ClassVar
from xml.etree.ElementTree import Element, ParseError  # noqa: S405 - types only; parsing uses defusedxml

import httpx
from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring as safe_fromstring

from ..config import Settings
from ..engine.types import RawListing
from ..money import pesos_to_cents
from . import netguard
from .base import Collector, CollectorError, CollectorInfo, CollectorNotConfigured, HealthStatus
from .registry import register

log = logging.getLogger(__name__)

SOURCE_KEY = "rss_deals"
MAX_FEED_BYTES = 2 * 1024 * 1024
MAX_ITEMS_PER_FEED = 500
MAX_DESCRIPTION_CHARS = 2000
FEED_ACCEPT = "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5"

# ------------------------------------------------------------------------------------------------
# price extraction
# ------------------------------------------------------------------------------------------------

_INT_COMMA = r"\d{1,3}(?:,\d{3})+"  # 12,999
_INT_SPACE = r"\d{1,3}(?:[  ]\d{3})+"  # 12 999 (only accepted when followed by "pesos"/"MXN")
_INT_PLAIN = r"\d+"
_DEC_END = r"(?:\.(?P<dec>\d{2}))?(?!\d|[.,]\d)"  # optional 2-digit cents; reject "12.999" / "12,99"
_NUM = rf"(?<![\d.,])(?P<int>{_INT_COMMA}|{_INT_PLAIN}){_DEC_END}"
_NUM_SPACED = rf"(?<![\d.,])(?P<int>{_INT_COMMA}|{_INT_SPACE}|{_INT_PLAIN}){_DEC_END}"
# "$12,999" / "$ 12,999.00" / "MX$12,999"   (the look-behind rejects "US$", "U$S", "CA$"...)
_DOLLAR = re.compile(r"(?<![A-Za-z])(?:MXN?|M\.N\.)?\s?\$\s?" + _NUM)
# "MXN 12999" / "MXN $12,999" / "MN 12,999"
_CODE_FIRST = re.compile(r"\b(?:MXN|MN)\s?\$?\s?" + _NUM, re.IGNORECASE)
# "12 999 pesos" / "12999 MXN" / "12,999 M.N."
_CODE_AFTER = re.compile(_NUM_SPACED + r"\s?(?:pesos\b|mxn\b|m\.n\.)", re.IGNORECASE)
_USD_AFTER = re.compile(r"^\s?(?:usd|us\$|dls|dlls|d[oó]lares)\b", re.IGNORECASE)
_USD_BEFORE = re.compile(r"(?:\bUS|U\$S|\bUSD)\s?$", re.IGNORECASE)
_FROM_QUALIFIER = re.compile(r"(?:\bdesde|\ba partir de)\s*$", re.IGNORECASE)


@dataclass
class PriceExtraction:
    cents: int | None
    reason: str
    candidates: list[int] = field(default_factory=list)


def _to_cents(int_part: str, dec_part: str | None) -> int | None:
    digits = re.sub(r"[ , ]", "", int_part)
    try:
        amount = Decimal(f"{digits}.{dec_part}" if dec_part else digits)
    except InvalidOperation:
        return None
    if amount <= 0:
        return None
    return pesos_to_cents(amount)


def _scan(text: str) -> tuple[list[int], bool]:
    """Return (MXN amounts in cents, saw a 'from' qualifier).  Amounts marked as USD are ignored."""
    amounts: list[int] = []
    spans: list[tuple[int, int]] = []
    qualifier = False
    for pattern in (_CODE_FIRST, _DOLLAR, _CODE_AFTER):
        for match in pattern.finditer(text):
            start, end = match.span()
            if any(start < s_end and s_start < end for s_start, s_end in spans):
                continue  # already covered by a more specific pattern
            spans.append((start, end))
            if pattern is _DOLLAR and (
                _USD_BEFORE.search(text[max(0, start - 6) : start]) or _USD_AFTER.match(text[end : end + 10])
            ):
                continue
            cents = _to_cents(match.group("int"), match.group("dec"))
            if cents is None:
                continue
            if _FROM_QUALIFIER.search(text[max(0, start - 20) : start]):
                qualifier = True
            amounts.append(cents)
    return amounts, qualifier


def extract_price(title: str, description: str | None = None) -> PriceExtraction:
    """Single unambiguous MXN price from the title, else from the description; otherwise None."""
    for label, text in (("title", title or ""), ("description", description or "")):
        if not text:
            continue
        amounts, qualifier = _scan(text)
        distinct = sorted(set(amounts))
        if not distinct:
            continue
        if qualifier:
            return PriceExtraction(None, f"'from' price qualifier in {label}", distinct)
        if len(distinct) > 1:
            return PriceExtraction(None, f"{len(distinct)} different prices in {label}", distinct)
        return PriceExtraction(distinct[0], f"single price in {label}", distinct)
    return PriceExtraction(None, "no MXN price found")


_NEW_PATTERNS = re.compile(
    r"\b(?:nuev[oa]s?\s+(?:y\s+)?sellad[oa]s?|sellad[oa]s?|brand new|"
    r"(?:condicion|estado|condition)\s*:?\s*(?:nuev[oa]|new))\b|[\(\[]\s*(?:nuev[oa]|new)\s*[\)\]]",
    re.IGNORECASE,
)


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def says_new(text: str) -> bool:
    """True only for explicit 'new/sealed' wording (not "nuevo precio", "nuevo mínimo"...)."""
    return bool(_NEW_PATTERNS.search(strip_accents(text or "")))


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", strip_accents(text or "").lower()) if t]


def matches_query(title: str, query: str) -> bool:
    """Every query token appears in the accent-stripped, lower-cased title."""
    haystack = " ".join(_tokens(title))
    tokens = _tokens(query)
    return bool(tokens) and all(token in haystack for token in tokens)


# ------------------------------------------------------------------------------------------------
# feed parsing
# ------------------------------------------------------------------------------------------------


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("br", "p", "div", "li"):
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception as exc:  # noqa: BLE001 - lenient: fall back to what was collected
        log.debug("HTML-to-text stopped early: %s", exc)
    return re.sub(r"\s+", " ", "".join(parser.parts)).strip()


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _children(element: Element, name: str) -> list[Element]:
    return [child for child in element if _local(child.tag) == name]


def _child_text(element: Element, *names: str) -> str | None:
    for name in names:
        for child in _children(element, name):
            text = "".join(child.itertext()).strip()
            if text:
                return text
    return None


def _atom_link(entry: Element) -> str | None:
    fallback = None
    for link in _children(entry, "link"):
        href = (link.get("href") or "").strip()
        if not href:
            text = (link.text or "").strip()  # RSS <link>text</link>
            if text:
                return text
            continue
        rel = (link.get("rel") or "alternate").lower()
        if rel == "alternate":
            return href
        fallback = fallback or href
    return fallback


def _parse_date(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        parsed = None
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@dataclass
class FeedItem:
    title: str
    link: str | None
    guid: str | None
    published: datetime | None
    description: str
    categories: list[str]
    merchant: str | None
    explicit_price: str | None
    condition_text: str | None


def parse_feed(content: bytes) -> list[FeedItem]:
    """Parse RSS 2.0, RSS 1.0 (RDF) or Atom with defusedxml.  Raises CollectorError on invalid XML."""
    try:
        root = safe_fromstring(content)
    except (ParseError, DefusedXmlException, ValueError) as exc:
        raise CollectorError(f"feed is not valid/safe XML: {exc}") from exc
    items: list[FeedItem] = []
    for element in root.iter():
        if _local(element.tag) not in ("item", "entry"):
            continue
        title = html_to_text(_child_text(element, "title") or "")
        if not title:
            continue
        description_html = _child_text(element, "encoded", "description", "summary", "content") or ""
        categories: list[str] = []
        merchant = _child_text(element, "merchant")
        for category in _children(element, "category"):
            value = (category.get("term") or category.text or "").strip()
            if not value:
                continue
            categories.append(value)
            domain = (category.get("domain") or category.get("scheme") or "").lower()
            if not merchant and domain in ("merchant", "store", "tienda"):
                merchant = value
        items.append(
            FeedItem(
                title=title[:500],
                link=_atom_link(element),
                guid=_child_text(element, "guid", "id"),
                published=_parse_date(_child_text(element, "pubdate", "published", "updated", "date")),
                description=html_to_text(description_html)[:MAX_DESCRIPTION_CHARS],
                categories=categories[:20],
                merchant=merchant,
                explicit_price=_child_text(element, "price"),
                condition_text=_child_text(element, "condition"),
            )
        )
        if len(items) >= MAX_ITEMS_PER_FEED:
            break
    return items


def item_to_listing(
    item: FeedItem, feed_url: str, *, now: datetime | None = None
) -> tuple[RawListing | None, str]:
    """FeedItem -> RawListing, or (None, reason) when no unambiguous price exists."""
    now = now or datetime.now(UTC)
    extraction: PriceExtraction | None = None
    if item.explicit_price:  # a <price> element still needs an MXN marker ($, MXN, pesos)
        explicit = extract_price(item.explicit_price)
        if explicit.cents is not None:
            extraction = PriceExtraction(explicit.cents, "price element", explicit.candidates)
    if extraction is None:
        extraction = extract_price(item.title, item.description)
    if extraction.cents is None:
        return None, extraction.reason
    identity = (
        item.guid or item.link or f"{item.title}|{item.published.isoformat() if item.published else ''}"
    )
    external_id = hashlib.sha256(f"{feed_url}\n{identity}".encode()).hexdigest()[:32]
    condition_text = " ".join(filter(None, [item.condition_text, item.title, item.description]))
    observed_at = item.published if item.published and item.published <= now else now
    attributes: dict[str, Any] = {"feed_url": feed_url, "price_extraction": extraction.reason}
    if item.categories:
        attributes["categories"] = item.categories
    if item.published:
        attributes["published_at"] = item.published.isoformat()
    return (
        RawListing(
            source_key=SOURCE_KEY,
            external_id=external_id,
            title=item.title,
            price_cents=extraction.cents,
            currency="MXN",
            url=item.link,
            condition_raw="new" if says_new(condition_text) else None,
            category_name=item.categories[0][:80] if item.categories else None,
            attributes=attributes,
            seller_name=item.merchant[:200] if item.merchant else None,
            description=item.description or None,
            status="active",
            observed_at=observed_at,
            raw={"guid": item.guid, "link": item.link, "feed_url": feed_url},
        ),
        extraction.reason,
    )


# ------------------------------------------------------------------------------------------------
# collector
# ------------------------------------------------------------------------------------------------


@register
class RssDealsCollector(Collector):
    info = CollectorInfo(
        key=SOURCE_KEY,
        name="RSS/Atom deal feeds",
        kind="feed",
        country="MX",
        currency="MXN",
        default_fee_profile_key=None,
        requires_credentials=False,
        docs_url=None,
        notes=(
            "Polls the RSS/Atom feeds listed in FLIPRADAR_RSS_FEEDS (none by default - choose feeds whose terms "
            "allow reading them). Prices are taken only when the title/description contains exactly one MXN "
            "price; deal posts are retailer asking prices and may expire quickly."
        ),
        capabilities=["search", "feed"],
    )
    max_retry_after: ClassVar[float] = netguard.MAX_RETRY_AFTER_SECONDS

    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(settings, client)
        self.last_errors: dict[str, str] = {}
        self.last_skipped: dict[str, int] = {}

    def is_configured(self) -> bool:
        return bool(self.settings.rss_feed_list)

    async def _download(self, url: str) -> bytes:
        if not url.lower().startswith(("http://", "https://")):
            raise CollectorError(f"unsupported feed URL scheme: {url}")
        for attempt in range(2):
            wait = 0.0
            try:
                async with self.client.stream("GET", url, headers={"Accept": FEED_ACCEPT}) as response:
                    if response.status_code == 429 and attempt == 0:
                        wait = netguard.parse_retry_after(
                            response.headers.get("retry-after"), maximum=self.max_retry_after
                        )
                    elif response.status_code == 429:
                        raise CollectorError("feed still rate limited (HTTP 429) after waiting")
                    elif not 200 <= response.status_code < 300:
                        raise CollectorError(f"feed returned HTTP {response.status_code}")
                    else:
                        return await netguard.read_capped(response, MAX_FEED_BYTES)
            except httpx.HTTPError as exc:
                raise CollectorError(f"feed request failed ({exc.__class__.__name__}: {exc})") from exc
            await netguard.sleep(wait)
        raise CollectorError("feed rate limited (HTTP 429)")

    async def fetch_feed(self, url: str) -> list[RawListing]:
        """Download + parse one feed; items without an unambiguous price are counted in last_skipped."""
        items = parse_feed(await self._download(url))
        now = datetime.now(UTC)
        listings: list[RawListing] = []
        skipped = 0
        for item in items:
            listing, _reason = item_to_listing(item, url, now=now)
            if listing is None:
                skipped += 1
            else:
                listings.append(listing)
        self.last_skipped[url] = skipped
        return listings

    async def fetch_latest(self, *, limit: int = 500) -> list[RawListing]:
        """All priced items of every configured feed (feed capability).  Per-feed errors go to last_errors;
        raises CollectorError only when every feed failed."""
        if not self.is_configured():
            raise CollectorNotConfigured("no RSS feeds configured (FLIPRADAR_RSS_FEEDS)")
        self.last_errors = {}
        listings: list[RawListing] = []
        for url in self.settings.rss_feed_list:
            try:
                listings.extend(await self.fetch_feed(url))
            except CollectorError as exc:
                self.last_errors[url] = str(exc)
                log.warning("RSS feed %s failed: %s", url, exc)
        if self.last_errors and len(self.last_errors) == len(self.settings.rss_feed_list):
            raise CollectorError(
                "all RSS feeds failed: " + "; ".join(f"{u}: {e}" for u, e in self.last_errors.items())
            )
        return listings[: max(1, int(limit))]

    async def search(
        self, query: str, *, limit: int = 50, category_id: str | None = None
    ) -> list[RawListing]:
        if not _tokens(query):
            raise CollectorError("empty search query")
        listings = await self.fetch_latest()
        return [listing for listing in listings if matches_query(listing.title, query)][: max(1, int(limit))]

    async def health(self) -> HealthStatus:
        if not self.is_configured():
            return HealthStatus(
                configured=False, ok=None, message="no feeds configured (FLIPRADAR_RSS_FEEDS)"
            )
        details: dict[str, Any] = {}
        for url in self.settings.rss_feed_list:
            try:
                priced = await self.fetch_feed(url)
                details[url] = {
                    "ok": True,
                    "priced_items": len(priced),
                    "skipped_items": self.last_skipped.get(url, 0),
                }
            except CollectorError as exc:
                details[url] = {"ok": False, "error": str(exc)}
        failures = [u for u, d in details.items() if not d["ok"]]
        if failures:
            return HealthStatus(True, False, f"{len(failures)} of {len(details)} feed(s) failed", details)
        return HealthStatus(True, True, f"{len(details)} feed(s) OK", details)
