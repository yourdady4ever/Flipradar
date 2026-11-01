"""User-initiated single-URL price check via schema.org Product/Offer JSON-LD.  OWNER: collectors agent.

Flow of :func:`check_url` (one page, only when the user asks for it):

1. URL syntax + SSRF validation (:mod:`.netguard`) and a refusal list for sites whose terms prohibit
   automated collection (Meta, Amazon) or that have an official API (Mercado Libre);
2. ``robots.txt`` of the host is fetched through the same guard and evaluated with
   :mod:`urllib.robotparser` for our User-Agent (falls back to the ``*`` group).  404/410 -> allowed;
   any other failure -> NOT allowed (conservative, RFC 9309 "unreachable");
3. the page is fetched (max 3 redirects, each re-validated and robots-checked, ``text/html`` only,
   2 MB cap) and ``<script type="application/ld+json">`` blocks are parsed with :mod:`json`;
4. the first ``Product``/``ProductGroup`` with a priced ``Offer``/``AggregateOffer`` becomes a
   :class:`RawListing` (``source_key='web_jsonld'``).  Nothing is guessed: a missing price or currency
   means ``found=False``.

Only :class:`~.netguard.UnsafeUrlError` is raised (security rejection -> HTTP 400 in the API); every other
outcome is reported in the returned :class:`UrlCheckResult`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qsl, urlencode
from urllib.robotparser import RobotFileParser

import httpx

from ..config import Settings
from ..engine.types import RawListing
from ..money import pesos_to_cents
from . import netguard
from .base import CollectorError

log = logging.getLogger(__name__)

SOURCE_KEY = "web_jsonld"
MAX_ROBOTS_BYTES = 512 * 1024
MAX_JSONLD_BLOCKS = 50
MAX_NODES = 5000
MAX_DEPTH = 12

_TRACKING_PARAMS = {"gclid", "fbclid", "msclkid", "dclid", "yclid", "mc_cid", "mc_eid", "_ga", "igshid"}

_BLOCKED_SUFFIXES: dict[str, str] = {
    # Meta: "You may not access or collect data from our Products using automated means".
    "facebook.com": "meta",
    "fb.com": "meta",
    "fb.me": "meta",
    "fb.watch": "meta",
    "messenger.com": "meta",
    "instagram.com": "meta",
    "instagr.am": "meta",
    # Amazon short links (they redirect to Amazon stores).
    "amzn.to": "amazon",
    "amzn.com": "amazon",
    "a.co": "amazon",
    # Mercado Libre short links.
    "meli.la": "mercadolibre",
}
_BLOCKED_BRAND_LABELS = {"amazon": "amazon", "mercadolibre": "mercadolibre", "mercadolivre": "mercadolibre"}
_BLOCK_MESSAGES = {
    "meta": (
        "Facebook/Instagram (Meta) terms prohibit collecting their content by automated means. "
        "FLIP RADAR will not fetch it: enter the price manually (manual listing or CSV import)."
    ),
    "amazon": (
        "Amazon's Conditions of Use prohibit data mining, robots and similar data-gathering tools. "
        "FLIP RADAR will not fetch Amazon pages: enter the price manually (manual listing or CSV import)."
    ),
    "mercadolibre": (
        "Mercado Libre pages are not scraped: Mercado Libre has an official API (configure the "
        "'mercadolibre' source) and its terms do not allow automated collection from the website. "
        "Use the API source or enter the price manually."
    ),
}


class RobotsDisallowed(CollectorError):
    pass


@dataclass
class UrlCheckResult:
    url: str
    allowed_by_robots: bool
    found: bool
    raw: RawListing | None = None
    extracted: dict[str, Any] = field(default_factory=dict)
    message: str = ""


# ------------------------------------------------------------------------------------------------
# domain policy
# ------------------------------------------------------------------------------------------------


def _block_kind(host: str) -> str | None:
    host = (host or "").strip().strip("[]").rstrip(".").lower()
    if not host:
        return None
    for suffix, kind in _BLOCKED_SUFFIXES.items():
        if host == suffix or host.endswith("." + suffix):
            return kind
    labels = host.split(".")
    for index, label in enumerate(labels):
        kind = _BLOCKED_BRAND_LABELS.get(label)
        if kind is None:
            continue
        tail = labels[index + 1 :]
        # "amazon.com.mx", "www.mercadolibre.com.mx", "articulo.mercadolibre.com.mx", "amazon.co.uk"...
        if tail and all(0 < len(part) <= 3 for part in tail):
            return kind
    return None


def is_blocked_domain(host: str) -> bool:
    """True for hosts FLIP RADAR refuses to fetch (Meta, Amazon, Mercado Libre web pages)."""
    return _block_kind(host) is not None


def blocked_domain_reason(host: str) -> str | None:
    kind = _block_kind(host)
    return _BLOCK_MESSAGES[kind] if kind else None


class BlockedDomainError(netguard.UnsafeUrlError):
    pass


def _host_policy(host: str) -> None:
    reason = blocked_domain_reason(host)
    if reason:
        raise BlockedDomainError(f"{host}: {reason}")


# ------------------------------------------------------------------------------------------------
# URL helpers
# ------------------------------------------------------------------------------------------------


def canonicalize_url(url: str) -> str:
    """Lower-case scheme/host, drop default port, fragment and tracking parameters (utm_*, gclid...)."""
    parsed = httpx.URL(url)
    host = parsed.host.lower()
    port = parsed.port
    netloc = host if ":" not in host else f"[{host}]"
    if port is not None and not (
        (parsed.scheme == "http" and port == 80) or (parsed.scheme == "https" and port == 443)
    ):
        netloc = f"{netloc}:{port}"
    query_pairs = [
        (k, v)
        for k, v in parse_qsl(parsed.query.decode("ascii", "ignore"), keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
    ]
    path = parsed.raw_path.split(b"?", 1)[0].decode("ascii", "ignore") or "/"
    query = urlencode(query_pairs)
    return f"{parsed.scheme}://{netloc}{path}" + (f"?{query}" if query else "")


def external_id_for(url: str) -> str:
    return hashlib.sha256(canonicalize_url(url).encode("utf-8")).hexdigest()[:24]


def _robots_url(url: httpx.URL) -> str:
    default_port = 443 if url.scheme == "https" else 80
    host = url.host if ":" not in url.host else f"[{url.host}]"
    port = f":{url.port}" if url.port not in (None, default_port) else ""
    return f"{url.scheme}://{host}{port}/robots.txt"


# ------------------------------------------------------------------------------------------------
# JSON-LD extraction (pure functions, unit-tested with fixtures)
# ------------------------------------------------------------------------------------------------


class _JsonLdScriptParser(HTMLParser):
    """Collects the text of ``<script type="application/ld+json">`` elements (nothing else)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self._buffer: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "script" or len(self.blocks) >= MAX_JSONLD_BLOCKS:
            return
        script_type = ""
        for name, value in attrs:
            if name.lower() == "type" and value:
                script_type = value.split(";", 1)[0].strip().lower()
        if script_type == "application/ld+json":
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._buffer is not None:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._buffer is not None:
            self.blocks.append("".join(self._buffer))
            self._buffer = None


def extract_jsonld_blocks(html: str) -> list[Any]:
    """Parse every JSON-LD script block; malformed blocks are skipped."""
    parser = _JsonLdScriptParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:  # noqa: BLE001 - HTMLParser is lenient; never fail the whole check
        log.debug("HTML parser stopped early: %s", exc)
    parsed: list[Any] = []
    for block in parser.blocks:
        text = block.strip()
        for prefix in ("<!--", "//<![CDATA[", "<![CDATA["):
            if text.startswith(prefix):
                text = text[len(prefix) :]
        for suffix in ("-->", "//]]>", "]]>"):
            if text.endswith(suffix):
                text = text[: -len(suffix)]
        text = text.strip()
        if not text:
            continue
        try:
            parsed.append(json.loads(text, strict=False))
        except ValueError:
            log.debug("skipping malformed JSON-LD block (%d chars)", len(text))
    return parsed


def _iter_nodes(value: Any, depth: int = 0, counter: list[int] | None = None) -> Any:
    counter = counter if counter is not None else [0]
    if depth > MAX_DEPTH or counter[0] > MAX_NODES:
        return
    if isinstance(value, list):
        for item in value:
            yield from _iter_nodes(item, depth + 1, counter)
    elif isinstance(value, dict):
        counter[0] += 1
        yield value
        for child in value.values():
            if isinstance(child, (dict, list)):
                yield from _iter_nodes(child, depth + 1, counter)


def _local_name(value: Any) -> str:
    text = str(value or "").strip()
    return text.rsplit("/", 1)[-1].rsplit("#", 1)[-1].rsplit(":", 1)[-1]


def _types(node: dict[str, Any]) -> set[str]:
    raw = node.get("@type")
    values = raw if isinstance(raw, list) else [raw]
    return {_local_name(v) for v in values if v}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _first_text(value: Any) -> str | None:
    for item in _as_list(value):
        if isinstance(item, dict):
            item = item.get("name") or item.get("@value")
        if isinstance(item, (str, int, float)) and not isinstance(item, bool):
            text = str(item).strip()
            if text:
                return text
    return None


_PLAIN_NUMBER = re.compile(r"^\d+(?:\.\d+)?$")
_THOUSANDS_COMMA = re.compile(r"^\d{1,3}(?:,\d{3})+(?:\.\d+)?$")


def parse_price_value(value: Any) -> Decimal | None:
    """schema.org prices: number or string with '.' decimals.  Ambiguous formats return None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        text = str(value)
    elif isinstance(value, str):
        text = value.strip().replace("$", "").replace("MXN", "").replace(" ", "").strip()
        if _THOUSANDS_COMMA.match(text):
            text = text.replace(",", "")
        elif not _PLAIN_NUMBER.match(text):
            return None
    else:
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount <= 0:
        return None
    return amount


def gtin_is_valid(code: str) -> bool:
    if not code.isdigit() or len(code) not in (8, 12, 13, 14):
        return False
    digits = [int(c) for c in code]
    check = digits.pop()
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(digits)))
    return (10 - total % 10) % 10 == check


def _gtin(node: dict[str, Any]) -> tuple[str | None, str | None]:
    """(valid gtin or None, raw value seen)."""
    for key in ("gtin", "gtin13", "gtin12", "gtin14", "gtin8"):
        raw = _first_text(node.get(key))
        if raw:
            digits = re.sub(r"[\s-]", "", raw)
            return (digits if gtin_is_valid(digits) else None), raw
    return None, None


def _image(value: Any, base_url: str | None) -> str | None:
    for item in _as_list(value):
        if isinstance(item, dict):
            item = item.get("url") or item.get("contentUrl")
        if isinstance(item, str) and item.strip():
            candidate = item.strip()
            if base_url:
                try:
                    candidate = str(httpx.URL(base_url).join(candidate))
                except (httpx.InvalidURL, ValueError):
                    continue
            if candidate.startswith(("http://", "https://")):
                return candidate
    return None


_CONDITIONS = {
    "NewCondition": "new",
    "UsedCondition": "used",
    "RefurbishedCondition": "refurbished",
    "DamagedCondition": "damaged",
}
_UNAVAILABLE = {"OutOfStock", "SoldOut", "Discontinued"}


@dataclass
class _OfferPick:
    price: Decimal
    currency: str
    price_kind: str  # "price" | "lowPrice"
    availability: str | None
    condition: str | None
    seller: str | None
    offer_count: int


def _offer_candidates(offer: dict[str, Any], depth: int = 0) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    types = _types(offer)
    price_kind = "price"
    price = None
    currency = _first_text(offer.get("priceCurrency"))
    if "AggregateOffer" in types:
        price = parse_price_value(offer.get("lowPrice"))
        price_kind = "lowPrice"
        if price is None:
            price = parse_price_value(offer.get("price"))
            price_kind = "price"
        if price is None and depth < 2:
            for nested in _as_list(offer.get("offers")):
                if isinstance(nested, dict):
                    out.extend(_offer_candidates(nested, depth + 1))
            return out
    else:
        price = parse_price_value(offer.get("price"))
        if price is None:
            for spec in _as_list(offer.get("priceSpecification")):
                if isinstance(spec, dict):
                    price = parse_price_value(spec.get("price"))
                    currency = currency or _first_text(spec.get("priceCurrency"))
                    if price is not None:
                        break
    seller = offer.get("seller")
    out.append(
        {
            "price": price,
            "currency": currency.upper() if currency else None,
            "price_kind": price_kind,
            "availability": _local_name(offer.get("availability")) or None,
            "condition": _local_name(offer.get("itemCondition")) or None,
            "seller": _first_text(seller.get("name")) if isinstance(seller, dict) else _first_text(seller),
        }
    )
    return out


def _pick_offer(offers: Any) -> _OfferPick | None:
    candidates: list[dict[str, Any]] = []
    for offer in _as_list(offers):
        if isinstance(offer, dict):
            candidates.extend(_offer_candidates(offer))
    priced = [c for c in candidates if c["price"] is not None and c["currency"]]
    if not priced:
        return None
    available = [c for c in priced if c["availability"] not in _UNAVAILABLE]
    pool = available or priced
    currencies = {c["currency"] for c in pool}
    if len(currencies) > 1:
        preferred = "MXN" if "MXN" in currencies else pool[0]["currency"]
        pool = [c for c in pool if c["currency"] == preferred]
    best = min(pool, key=lambda c: c["price"])
    price_kind = best["price_kind"]
    if price_kind == "price" and len(priced) > 1:
        price_kind = "lowest_offer"  # several Offer prices on the page; the lowest available one is used
    return _OfferPick(
        price=best["price"],
        currency=best["currency"],
        price_kind=price_kind,
        availability=best["availability"],
        condition=best["condition"],
        seller=best["seller"],
        offer_count=len(priced),
    )


def find_products(documents: list[Any]) -> list[dict[str, Any]]:
    """All Product/ProductGroup nodes in document order (handles @graph, lists and nesting)."""
    products: list[dict[str, Any]] = []
    for document in documents:
        for node in _iter_nodes(document):
            if _types(node) & {"Product", "ProductGroup"}:
                products.append(node)
    return products


def extract_offer(documents: list[Any], page_url: str | None = None) -> dict[str, Any] | None:
    """Return the extracted fields of the first Product with a priced offer, or None."""
    products = find_products(documents)
    for product in products:
        pick = _pick_offer(product.get("offers"))
        if pick is None:
            continue
        gtin, gtin_raw = _gtin(product)
        condition = pick.condition or (_local_name(product.get("itemCondition")) or None)
        return {
            "name": _first_text(product.get("name")),
            "brand": _first_text(product.get("brand")),
            "gtin": gtin,
            "gtin_raw": gtin_raw,
            "sku": _first_text(product.get("sku")),
            "mpn": _first_text(product.get("mpn")),
            "image": _image(product.get("image"), page_url),
            "price": str(pick.price),
            "price_currency": pick.currency,
            "price_kind": pick.price_kind,
            "offer_count": pick.offer_count,
            "availability": pick.availability,
            "item_condition": condition,
            "offer_seller": pick.seller,
            "product_type": sorted(_types(product) & {"Product", "ProductGroup"})[0],
            "product_count": len(products),
        }
    return None


def build_raw_listing(extracted: dict[str, Any], page_url: str) -> RawListing | None:
    name = extracted.get("name")
    if not name or not extracted.get("price") or not extracted.get("price_currency"):
        return None
    host = (httpx.URL(page_url).host or "").lower()
    availability = extracted.get("availability")
    status = "ended" if availability in _UNAVAILABLE else "active"
    condition = extracted.get("item_condition")
    canonical = canonicalize_url(page_url)
    attributes = {
        k: extracted[k]
        for k in ("sku", "mpn", "availability", "price_kind", "offer_count", "offer_seller")
        if extracted.get(k) is not None
    }
    return RawListing(
        source_key=SOURCE_KEY,
        external_id=external_id_for(page_url),
        title=str(name)[:500],
        price_cents=pesos_to_cents(Decimal(extracted["price"])),
        currency=str(extracted["price_currency"])[:3],
        url=canonical,
        condition_raw=_CONDITIONS.get(condition or "", condition),
        brand=extracted.get("brand"),
        gtin=extracted.get("gtin"),
        attributes=attributes,
        seller_id=host[:64] or None,
        seller_name=host or None,
        image_url=extracted.get("image"),
        status=status,
        observed_at=datetime.now(UTC),
        raw={"jsonld": {k: v for k, v in extracted.items() if v is not None}},
    )


# ------------------------------------------------------------------------------------------------
# robots.txt
# ------------------------------------------------------------------------------------------------


@dataclass
class _RobotsEntry:
    robots_url: str
    parser: RobotFileParser | None = None  # set when a robots.txt was parsed
    fixed: bool = False  # decision when there is no parser
    note: str = ""


async def _load_robots(
    client: httpx.AsyncClient, robots_url: str, allowed_ports: tuple[int, ...], user_agent: str
) -> _RobotsEntry:
    try:
        response = await netguard.guarded_get(
            client,
            robots_url,
            headers={"Accept": "text/plain,*/*;q=0.5", "User-Agent": user_agent},
            max_bytes=MAX_ROBOTS_BYTES,
            allowed_ports=allowed_ports,
            host_policy=_host_policy,
        )
    except netguard.UnsafeUrlError:
        raise
    except (CollectorError, httpx.HTTPError) as exc:
        return _RobotsEntry(
            robots_url, fixed=False, note=f"robots.txt could not be fetched ({exc}); page not fetched"
        )
    if response.status_code in (404, 410):
        return _RobotsEntry(robots_url, fixed=True, note=f"no robots.txt (HTTP {response.status_code})")
    if 200 <= response.status_code < 300:
        parser = RobotFileParser(robots_url)
        parser.parse(response.text().splitlines())
        return _RobotsEntry(robots_url, parser=parser)
    return _RobotsEntry(
        robots_url,
        fixed=False,
        note=(
            f"robots.txt returned HTTP {response.status_code}; permission could not be verified, "
            "so the page was not fetched"
        ),
    )


async def _robots_allows(
    client: httpx.AsyncClient,
    target: httpx.URL,
    settings: Settings,
    cache: dict[str, _RobotsEntry],
    allowed_ports: tuple[int, ...],
) -> tuple[bool, str]:
    """Evaluate robots.txt for ``target``.  urllib.robotparser applies the group matching our
    User-Agent product token and falls back to the ``*`` group."""
    robots_url = _robots_url(target)
    entry = cache.get(robots_url)
    if entry is None:
        entry = cache[robots_url] = await _load_robots(client, robots_url, allowed_ports, settings.user_agent)
    if entry.parser is None:
        return entry.fixed, entry.note
    allowed = entry.parser.can_fetch(settings.user_agent, str(target))
    return allowed, (
        f"allowed by {robots_url}" if allowed else f"{robots_url} disallows this page for our user agent"
    )


def _extra_ports(settings: Settings) -> tuple[int, ...]:
    raw = getattr(settings, "url_check_allowed_ports", "") or ""
    ports: list[int] = []
    for part in str(raw).split(","):
        part = part.strip()
        if part.isdigit() and 0 < int(part) < 65536:
            ports.append(int(part))
    return tuple(ports)


# ------------------------------------------------------------------------------------------------
# public entry point
# ------------------------------------------------------------------------------------------------


async def check_url(url: str, settings: Settings, client: httpx.AsyncClient | None = None) -> UrlCheckResult:
    """Fetch ONE page the user asked for (robots.txt respected, SSRF-guarded, size-capped) and extract a
    Product/Offer.  Returned RawListing uses source_key 'web_jsonld'.

    Pass ``client=None`` in production: a :func:`netguard.build_guarded_client` is created, which also
    re-checks the address at connect time.  Raises :class:`netguard.UnsafeUrlError` for unsafe URLs."""
    url = str(url).strip()
    allowed_ports = _extra_ports(settings)
    parsed = netguard.parse_and_validate_url(url, allowed_ports=allowed_ports)
    host = parsed.host.lower()
    reason = blocked_domain_reason(host)
    if reason:
        return UrlCheckResult(
            url=url, allowed_by_robots=False, found=False, extracted={"blocked_domain": host}, message=reason
        )

    own_client = client is None
    http = client or netguard.build_guarded_client(settings)
    robots_cache: dict[str, _RobotsEntry] = {}
    try:
        try:
            await netguard.validate_url(parsed, allowed_ports=allowed_ports, host_policy=_host_policy)
        except BlockedDomainError as exc:
            return UrlCheckResult(url=url, allowed_by_robots=False, found=False, message=str(exc))

        allowed, note = await _robots_allows(http, parsed, settings, robots_cache, allowed_ports)
        if not allowed:
            return UrlCheckResult(url=url, allowed_by_robots=False, found=False, message=note)

        async def _before_redirect(target: httpx.URL) -> None:
            ok, why = await _robots_allows(http, target, settings, robots_cache, allowed_ports)
            if not ok:
                raise RobotsDisallowed(f"redirect target {target}: {why}")

        try:
            page = await netguard.guarded_get(
                http,
                parsed,
                headers={
                    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
                    "User-Agent": settings.user_agent,
                },
                max_bytes=netguard.MAX_BODY_BYTES,
                allowed_ports=allowed_ports,
                host_policy=_host_policy,
                allowed_content_types=("text/html",),
                before_redirect=_before_redirect,
            )
        except BlockedDomainError as exc:
            return UrlCheckResult(url=url, allowed_by_robots=True, found=False, message=str(exc))
        except RobotsDisallowed as exc:
            return UrlCheckResult(url=url, allowed_by_robots=False, found=False, message=str(exc))
        except netguard.UnsafeUrlError:
            raise
        except (CollectorError, httpx.HTTPError) as exc:
            return UrlCheckResult(
                url=url, allowed_by_robots=True, found=False, message=f"page fetch failed: {exc}"
            )

        extracted: dict[str, Any] = {"final_url": page.url, "redirects": page.redirects, "robots": note}
        if not 200 <= page.status_code < 300:
            return UrlCheckResult(
                url=url,
                allowed_by_robots=True,
                found=False,
                extracted=extracted,
                message=f"page returned HTTP {page.status_code}",
            )
        documents = extract_jsonld_blocks(page.text())
        extracted["jsonld_blocks"] = len(documents)
        offer = extract_offer(documents, page.url)
        if offer is None:
            products = find_products(documents)
            extracted["product_count"] = len(products)
            message = (
                "no schema.org Product JSON-LD found on the page"
                if not products
                else "Product JSON-LD found but no offer with both a valid price and priceCurrency"
            )
            if products:
                extracted["name"] = _first_text(products[0].get("name"))
            return UrlCheckResult(
                url=url, allowed_by_robots=True, found=False, extracted=extracted, message=message
            )
        extracted.update(offer)
        raw = build_raw_listing(offer, page.url)
        if raw is None:
            return UrlCheckResult(
                url=url,
                allowed_by_robots=True,
                found=False,
                extracted=extracted,
                message="offer found but the product has no name",
            )
        message = f"found {raw.currency} {offer['price']} ({offer['price_kind']})"
        if offer.get("offer_count", 1) > 1:
            message += f"; {offer['offer_count']} offers on the page, the lowest available one was used"
        return UrlCheckResult(
            url=url, allowed_by_robots=True, found=True, raw=raw, extracted=extracted, message=message
        )
    finally:
        if own_client:
            await http.aclose()
