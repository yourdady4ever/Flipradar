"""Deterministic, fact-grounded explanations.  OWNER: engine agent.

:func:`build_facts` gathers every number and label that may be cited about an opportunity into one
JSON-serialisable dict (money as integer centavos *and* as a formatted ``"$12,345.67"`` string,
percentages as basis points *and* as ``"23.45%"``).  :func:`render_explanation` turns those facts into
readable English text and uses nothing else - so every figure in the text can be traced back to the
facts, and an optional AI summary can be validated against the same dict.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from .types import OpportunityCandidate

FACTS_VERSION = 1
SCORE_MAX = 100

ESTIMATE_METHOD_LABELS = {
    "sold_median": "median of {n} sold comparables",
    "comparables_p25": "25th percentile of {n} comparable listing prices",
    "comparables_median": "median of {n} comparable listing prices",
}
ESTIMATE_PERCENTILES = {"sold_median": 50, "comparables_p25": 25, "comparables_median": 50}

CAVEAT_ASKING_PRICES = (
    "The resale estimate comes from asking prices of listings that have not sold; asking prices are "
    "not guaranteed sale prices."
)
CAVEAT_SHIPPING_IN_COMPARABLES = (
    "Resale estimates use item prices only (buyer-paid shipping is not seller revenue); seller-paid "
    "shipping is charged by the fee profile."
)
CAVEAT_TAX = (
    "Platform tax withholding (ISR/IVA) and whether it is a final cost depend on the seller's tax "
    "regime; this is an estimate, not tax advice."
)


# ------------------------------------------------------------------------------------------------
# Formatting helpers (shared by risk/scoring so messages and facts use identical strings)
# ------------------------------------------------------------------------------------------------


def fmt_money(cents: int | None) -> str:
    """Format centavos as ``"$12,345.67"`` (``"-$5.00"`` when negative, ``"unknown"`` for None)."""
    if cents is None:
        return "unknown"
    value = (Decimal(abs(int(cents))) / 100).quantize(Decimal("0.01"))
    sign = "-" if cents < 0 else ""
    return f"{sign}${value:,.2f}"


def fmt_pct_bp(bp: int | None) -> str:
    """Format basis points as ``"23.45%"`` (``"n/a"`` for None)."""
    if bp is None:
        return "n/a"
    return f"{(Decimal(int(bp)) / 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}%"


def fmt_pct_ratio(ratio: Decimal | float | None) -> str:
    """Format a fraction (``0.1234``) as a percentage with 2 decimals (``"12.34%"``)."""
    if ratio is None:
        return "n/a"
    value = (Decimal(str(ratio)) * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{value}%"


def fmt_points(points: float | Decimal) -> str:
    """Format score points with 2 decimals (``"14.07"``)."""
    return f"{Decimal(str(points)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}"


def fmt_number(value: Any) -> str:
    """Stable string for a signal value (ints as-is, floats via ``str``)."""
    if value is None:
        return "unknown"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


# ------------------------------------------------------------------------------------------------
# Facts
# ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AnalysisContext:
    """Analysis details that are not stored on :class:`OpportunityCandidate` itself."""

    estimate_method: str  # sold_median | comparables_p25 | comparables_median
    sold_comparables: int = 0
    comparable_sources: tuple[str, ...] = ()
    fee_profile_name: str | None = None
    fee_profile_as_of: str | None = None
    shipping_assumed: bool = False  # True when buy shipping was unknown and an assumption was used
    break_even_sale_cents: int | None = None
    max_buy_price_cents: int | None = None
    target_roi_bp: int | None = None
    extra_caveats: tuple[str, ...] = field(default_factory=tuple)


def _money(prefix: str, cents: int | None) -> dict[str, Any]:
    return {f"{prefix}_cents": cents, prefix: fmt_money(cents)}


def _lines(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "key": line["key"],
            "label": line["label"],
            "amount_cents": line["amount_cents"],
            "amount": fmt_money(line["amount_cents"]),
            "formula": line["formula"],
        }
        for line in lines
    ]


def _line_amount(lines: list[dict[str, Any]], key: str) -> int | None:
    for line in lines:
        if line["key"] == key:
            return int(line["amount_cents"])
    return None


def build_facts(candidate: OpportunityCandidate, *, context: AnalysisContext | None = None) -> dict[str, Any]:
    """Flat dict of every number/label an explanation may cite (money as centavos + formatted).

    ``context`` carries details the candidate does not store (how the resale price was derived, fee
    profile name/date, break-even, max buy price).  Without it those facts are reported as unknown -
    never guessed.
    """
    buy = candidate.buy
    stats = candidate.stats
    profit = candidate.profit
    method = context.estimate_method if context else "unknown"
    n = stats.n
    estimate_from_sold = method == "sold_median"
    method_template = ESTIMATE_METHOD_LABELS.get(method)
    if method_template is None:
        method_label = "derivation not recorded"
    elif estimate_from_sold:
        method_label = method_template.format(n=context.sold_comparables if context else 0)
    else:
        method_label = method_template.format(n=n)

    acquisition_lines = _lines(profit.get("acquisition_lines", []))
    selling_lines = _lines(profit.get("selling_lines", []))
    shipping_used = _line_amount(acquisition_lines, "buy_shipping")
    buy_price = _line_amount(acquisition_lines, "buy_price")
    import_costs = _line_amount(acquisition_lines, "import_costs") or 0
    shipping_known = buy.shipping_mxn_cents is not None

    flags = [{"code": f.code, "severity": f.severity, "message": f.message} for f in candidate.risk_flags]
    risk_counts = {sev: sum(1 for f in candidate.risk_flags if f.severity == sev) for sev in ("critical", "warning", "info")}
    risk_level = "none"
    for sev in ("info", "warning", "critical"):
        if risk_counts[sev]:
            risk_level = sev

    signals = [
        {
            "name": s.get("name"),
            "value": s.get("value"),
            "value_str": fmt_number(s.get("value")),
            "unit": s.get("unit"),
            "source": s.get("source"),
            "note": s.get("note"),
        }
        for s in candidate.demand.signals
    ]
    components = [
        {
            "key": comp.key,
            "label": comp.label,
            "raw_value": comp.raw_value,
            "points": comp.points,
            "points_str": fmt_points(comp.points),
            "max_points": comp.max_points,
            "max_points_str": fmt_points(comp.max_points),
            "explanation": comp.explanation,
        }
        for comp in candidate.components
    ]

    caveats: list[str] = []
    if not estimate_from_sold:
        caveats.append(CAVEAT_ASKING_PRICES)
    caveats.append(CAVEAT_SHIPPING_IN_COMPARABLES)
    caveats.append(CAVEAT_TAX)
    if not shipping_known:
        caveats.append(
            f"Shipping to you is not reported by the source; {fmt_money(shipping_used)} was assumed."
        )
    if buy.import_required and buy.import_note:
        caveats.append(buy.import_note)
    if candidate.demand.level == "unknown":
        caveats.append("Demand is unknown because no sales evidence was observed; unknown is not the same as low.")
    if context:
        caveats.extend(context.extra_caveats)
    assumptions = list(profit.get("assumptions", []))

    facts: dict[str, Any] = {
        "facts_version": FACTS_VERSION,
        "product_key": candidate.product_key,
        "buy_listing_id": buy.id,
        "buy_title": buy.title,
        "buy_source": buy.source_key,
        "buy_url": buy.url,
        "buy_condition": buy.condition,
        **_money("buy_price", buy_price if buy_price is not None else buy.price_mxn_cents),
        "buy_shipping_known": shipping_known,
        "buy_shipping_assumed": bool(context.shipping_assumed) if context else not shipping_known,
        **_money("buy_shipping", shipping_used),
        "import_required": bool(buy.import_required),
        **_money("import_costs", import_costs),
        "import_note": buy.import_note if buy.import_required else None,
        **_money("buy_total", candidate.buy_total_cents),
        **_money("expected_sale", candidate.expected_sale_cents),
        "estimate_method": method,
        "estimate_method_label": method_label,
        "estimate_percentile": ESTIMATE_PERCENTILES.get(method),
        "estimate_from_sold": estimate_from_sold,
        "sold_comparables_n": context.sold_comparables if context else None,
        "comparables_n": n,
        "comparables_source_count": stats.source_count,
        "comparables_seller_count": stats.seller_count,
        "comparables_sources": list(context.comparable_sources) if context else [],
        "comparables_window_days": stats.window_days,
        **_money("comparables_median", stats.median_cents),
        **_money("comparables_p25", stats.p25_cents),
        **_money("comparables_p75", stats.p75_cents),
        **_money("comparables_min", stats.min_cents),
        **_money("comparables_max", stats.max_cents),
        "comparables_dispersion": stats.dispersion,
        "comparables_dispersion_pct": fmt_pct_ratio(stats.dispersion),
        "comparables_excluded": dict(stats.excluded),
        "comparable_ids": list(stats.comparable_ids),
        "fee_profile_key": candidate.fee_profile_key,
        "fee_profile_name": context.fee_profile_name if context else None,
        "fee_profile_as_of": context.fee_profile_as_of if context else None,
        "acquisition_lines": acquisition_lines,
        "selling_lines": selling_lines,
        **_money("total_acquisition", profit.get("total_acquisition_cents")),
        **_money("total_selling_costs", profit.get("total_selling_costs_cents")),
        **_money("net_proceeds", profit.get("net_proceeds_cents")),
        **_money("net_profit", candidate.net_profit_cents),
        "roi_bp": candidate.roi_bp,
        "roi_pct": fmt_pct_bp(candidate.roi_bp),
        "margin_bp": candidate.margin_bp,
        "margin_pct": fmt_pct_bp(candidate.margin_bp),
        **_money("break_even_sale", context.break_even_sale_cents if context else None),
        "target_roi_bp": context.target_roi_bp if context else None,
        "target_roi_pct": fmt_pct_bp(context.target_roi_bp if context else None),
        **_money("max_buy_price", context.max_buy_price_cents if context else None),
        "demand_level": candidate.demand.level,
        "demand_confidence": candidate.demand.confidence,
        "demand_note": candidate.demand.note,
        "demand_signals": signals,
        "risk_level": risk_level,
        "risk_counts": risk_counts,
        "risk_flags": flags,
        "score": candidate.score,
        "score_str": f"{Decimal(str(candidate.score)).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}",
        "score_max": SCORE_MAX,
        "confidence": candidate.confidence,
        "score_components": components,
        "assumptions": assumptions,
        "caveats": caveats,
    }
    return facts


# ------------------------------------------------------------------------------------------------
# Rendering
# ------------------------------------------------------------------------------------------------


def _get(facts: dict[str, Any], key: str, default: str = "unknown") -> str:
    value = facts.get(key)
    return default if value is None or value == "" else str(value)


def render_explanation(facts: dict[str, Any]) -> str:
    """Plain-text multi-line explanation built ONLY from ``facts``.

    Sections: Buy, Resale estimate, Costs, Profit, Demand, Risks, Score, Caveats.  Every number in the
    output is copied from a fact (formatted strings are used verbatim).
    """
    out: list[str] = [f"Opportunity: {_get(facts, 'buy_title')}", ""]

    out.append("Buy")
    out.append(
        f"- Listing #{_get(facts, 'buy_listing_id')} on {_get(facts, 'buy_source')} "
        f"(condition: {_get(facts, 'buy_condition')})"
    )
    if facts.get("buy_url"):
        out.append(f"- URL: {facts['buy_url']}")
    shipping = _get(facts, "buy_shipping")
    if not facts.get("buy_shipping_known"):
        shipping += " (not reported, assumed)"
    duties = f" + import duties {_get(facts, 'import_costs')}" if facts.get("import_required") else ""
    out.append(
        f"- Price {_get(facts, 'buy_price')} + shipping {shipping}{duties} = total {_get(facts, 'buy_total')}"
    )
    out.append("")

    out.append("Resale estimate")
    out.append(f"- Expected sale price: {_get(facts, 'expected_sale')} ({_get(facts, 'estimate_method_label')})")
    sources = facts.get("comparables_sources") or []
    source_text = f" ({', '.join(str(s) for s in sources)})" if sources else ""
    out.append(
        f"- Evidence: {_get(facts, 'comparables_n')} comparables from {_get(facts, 'comparables_source_count')} "
        f"source(s){source_text} and {_get(facts, 'comparables_seller_count')} seller(s), "
        f"seen in the last {_get(facts, 'comparables_window_days')} days"
    )
    out.append(
        f"- Comparable median {_get(facts, 'comparables_median')}, interquartile range "
        f"{_get(facts, 'comparables_p25')} to {_get(facts, 'comparables_p75')}, full range "
        f"{_get(facts, 'comparables_min')} to {_get(facts, 'comparables_max')}, dispersion "
        f"{_get(facts, 'comparables_dispersion_pct', 'n/a')}"
    )
    if facts.get("sold_comparables_n") is not None:
        out.append(f"- Comparables reported as sold: {_get(facts, 'sold_comparables_n')}")
    out.append("")

    profile = _get(facts, "fee_profile_name", _get(facts, "fee_profile_key"))
    as_of = f", rates as of {facts['fee_profile_as_of']}" if facts.get("fee_profile_as_of") else ""
    out.append(f"Costs (fee profile: {profile} [{_get(facts, 'fee_profile_key')}]{as_of})")
    for line in [*facts.get("acquisition_lines", []), *facts.get("selling_lines", [])]:
        if line.get("amount_cents") or line.get("key") == "buy_price":
            out.append(f"- {line['label']}: {line['amount']} ({line['formula']})")
    out.append(
        f"- Total acquisition {_get(facts, 'total_acquisition')}; total selling costs "
        f"{_get(facts, 'total_selling_costs')}; net proceeds {_get(facts, 'net_proceeds')}"
    )
    out.append("")

    out.append("Profit")
    out.append(
        f"- Net profit {_get(facts, 'net_profit')} | ROI {_get(facts, 'roi_pct')} | "
        f"margin {_get(facts, 'margin_pct')}"
    )
    out.append(f"- Break-even sale price: {_get(facts, 'break_even_sale')}")
    if facts.get("target_roi_bp") is not None:
        out.append(
            f"- Maximum buy price for {_get(facts, 'target_roi_pct')} ROI: {_get(facts, 'max_buy_price')}"
        )
    out.append("")

    out.append("Demand")
    out.append(
        f"- Level: {_get(facts, 'demand_level')} (confidence: {_get(facts, 'demand_confidence', 'n/a')})"
    )
    if facts.get("demand_note"):
        out.append(f"- {facts['demand_note']}")
    for signal in facts.get("demand_signals", []):
        out.append(
            f"- {signal.get('name')}: {signal.get('value_str')} {signal.get('unit') or ''}".rstrip()
            + f" (source: {signal.get('source')})"
        )
    out.append("")

    out.append("Risks")
    out.append(f"- Risk level: {_get(facts, 'risk_level')}")
    flags = facts.get("risk_flags", [])
    if not flags:
        out.append("- No risk flags.")
    for flag in flags:
        out.append(f"- [{flag['severity']}] {flag['code']}: {flag['message']}")
    out.append("")

    out.append("Score")
    out.append(
        f"- Score {_get(facts, 'score_str')} of {_get(facts, 'score_max')} "
        f"(confidence: {_get(facts, 'confidence')})"
    )
    for comp in facts.get("score_components", []):
        out.append(f"- {comp['label']}: {comp['explanation']}")
    out.append("")

    out.append("Caveats")
    for caveat in [*facts.get("caveats", []), *facts.get("assumptions", [])]:
        out.append(f"- {caveat}")
    return "\n".join(out)
