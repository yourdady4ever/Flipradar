"""Alert rules -> in-app notifications (+ optional push delivery).

A rule matches an opportunity when ALL of its set conditions hold:

* every keyword token appears in the accent-stripped, lower-cased product title + buy listing title;
* brand / category equal (case-insensitive) to the product's (or buy listing's) brand / category;
* ``net_profit >= min_profit_cents``, ``roi >= min_roi_bp``, ``score >= min_score``,
  ``buy_total <= max_buy_cents``, ``confidence >= min_confidence`` (low < medium < high);
* ``exclude_suspicious``: no warning/critical risk flag on the opportunity and buy listing not suspicious.

Only actionable opportunities (status new/seen/watching) are considered; demo opportunities only when
``include_demo_data`` is on.  One notification per (rule, opportunity) - re-evaluating never duplicates.
Nothing in this module raises: failures are logged.
"""

from __future__ import annotations

import logging
import unicodedata
from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..models import AlertRule, Listing, Notification, Opportunity, OpportunityStatus, Product
from . import notify, settings_store

log = logging.getLogger(__name__)

CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}
ACTIONABLE = frozenset({OpportunityStatus.NEW.value, OpportunityStatus.SEEN.value, OpportunityStatus.WATCHING.value})
SUSPICIOUS = frozenset({"warning", "critical"})


def fold(text: str | None) -> str:
    """Lower-case and strip accents ("Réplica" -> "replica")."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def money(cents: int) -> str:
    value = (Decimal(int(cents)) / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def pct_bp(bp: int) -> str:
    return f"{(Decimal(int(bp)) / 100).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}%"


def is_risky(opp: Opportunity, listing: Listing | None) -> bool:
    if listing is not None and listing.is_suspicious:
        return True
    return any(isinstance(f, dict) and f.get("severity") in SUSPICIOUS for f in (opp.risk_flags_json or []))


def rule_matches(rule: AlertRule, opp: Opportunity, product: Product | None, listing: Listing | None) -> bool:
    if not rule.enabled:
        return False
    if rule.keywords:
        haystack = fold(f"{product.title if product else ''} {listing.title if listing else ''}")
        tokens = [t for t in fold(rule.keywords).split() if t]
        if any(token not in haystack for token in tokens):
            return False
    if rule.brand:
        brands = {fold(product.brand if product else None), fold(listing.brand if listing else None)} - {""}
        if fold(rule.brand).strip() not in brands:
            return False
    if rule.category:
        categories = {fold(product.category if product else None), fold(listing.category if listing else None)} - {""}
        if fold(rule.category).strip() not in categories:
            return False
    if rule.min_profit_cents is not None and opp.net_profit_cents < rule.min_profit_cents:
        return False
    if rule.min_roi_bp is not None and opp.roi_bp < rule.min_roi_bp:
        return False
    if rule.min_score is not None and opp.score < rule.min_score:
        return False
    if rule.max_buy_cents is not None and opp.buy_total_cents > rule.max_buy_cents:
        return False
    if rule.min_confidence:
        needed = CONFIDENCE_RANK.get(rule.min_confidence, 0)
        if CONFIDENCE_RANK.get(opp.confidence, -1) < needed:
            return False
    return not (rule.exclude_suspicious and is_risky(opp, listing))


def notification_text(opp: Opportunity, product: Product | None, listing: Listing | None) -> tuple[str, str]:
    name = (product.title if product else None) or (listing.title if listing else None) or f"Opportunity {opp.id}"
    sign = "+" if opp.net_profit_cents >= 0 else ""
    suffix = f": {sign}{money(opp.net_profit_cents)} (ROI {pct_bp(opp.roi_bp)})"
    title = name[: max(10, 200 - len(suffix))] + suffix
    source = listing.source.key if listing is not None and listing.source is not None else "unknown"
    body = (
        f"Source: {source} | Buy: {money(opp.buy_total_cents)} | Expected sale: {money(opp.expected_sale_cents)} "
        f"| Score: {opp.score:.1f}/100 | Confidence: {opp.confidence} | Comparables: {opp.comparable_count}"
    )
    if opp.is_demo:
        body = "[DEMO - synthetic data] " + body
    return title[:200], body


def evaluate(
    session: Session,
    opportunities: Iterable[Opportunity],
    settings: Settings | None = None,
    *,
    dispatch: bool = True,
) -> list[Notification]:
    """Create notifications for matching (rule, opportunity) pairs.  Flushes; never raises."""
    settings = settings or get_settings()
    created: list[Notification] = []
    try:
        opps = [o for o in opportunities if o is not None and o.id is not None]
        if not opps:
            return created
        rules = list(session.scalars(select(AlertRule).where(AlertRule.enabled.is_(True)).order_by(AlertRule.id)))
        if not rules:
            return created
        include_demo = bool(settings_store.get_value(session, "include_demo_data", settings))
        for opp in opps:
            if opp.status not in ACTIONABLE or (opp.is_demo and not include_demo):
                continue
            product = opp.product
            listing = opp.buy_listing
            for rule in rules:
                try:
                    if not rule_matches(rule, opp, product, listing):
                        continue
                    exists = session.scalar(
                        select(Notification.id).where(Notification.rule_id == rule.id, Notification.opportunity_id == opp.id)
                    )
                    if exists:
                        continue
                    title, body = notification_text(opp, product, listing)
                    note = Notification(rule_id=rule.id, opportunity_id=opp.id, kind="opportunity", title=title,
                                        body=body, delivery_json={})
                    try:
                        with session.begin_nested():
                            session.add(note)
                            session.flush()
                    except IntegrityError:  # created concurrently
                        continue
                    note._flipradar_channels = list(rule.channels or ["in_app"])  # type: ignore[attr-defined]
                    created.append(note)
                except Exception:
                    log.exception("alert rule %s failed for opportunity %s", rule.id, opp.id)
        if dispatch and created:
            deliver(session, created, settings)
    except Exception:
        log.exception("alert evaluation failed")
    return created


def deliver(session: Session, notifications: Iterable[Notification], settings: Settings | None = None) -> None:
    """Send notifications to their rule's channels and record the result.  Flushes; never raises."""
    settings = settings or get_settings()
    for note in notifications:
        try:
            channels = getattr(note, "_flipradar_channels", None)
            if channels is None:
                rule = session.get(AlertRule, note.rule_id) if note.rule_id else None
                channels = list(rule.channels or ["in_app"]) if rule else ["in_app"]
            click_url = None
            if note.opportunity_id:
                opp = session.get(Opportunity, note.opportunity_id)
                if opp is not None and opp.buy_listing is not None:
                    click_url = opp.buy_listing.url
            note.delivery_json = notify.dispatch(note, channels, settings, click_url=click_url)
            session.flush()
        except Exception:
            log.exception("notification %s delivery failed", note.id)
