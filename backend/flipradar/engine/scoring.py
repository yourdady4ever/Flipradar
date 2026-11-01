"""Opportunity scoring (0..100) with explainable components.  OWNER: engine agent.

The score is a transparent weighted sum - no machine learning, no hidden weights:

=================  ==========  ===============================================================
component          max points  rule
=================  ==========  ===============================================================
roi                30          linear in ROI, full points at 50 % ROI (negative ROI -> 0)
profit             20          linear in net profit, full points at $3,000.00 MXN
price_evidence     20          12 x min(n / 10, 1) + 8 x (1 - min(dispersion / 0.5, 1))
match              10          match_confidence x 10 (unknown -> 5, neutral)
demand             10          high 10, medium 6, low 2, unknown 3
risk               penalty     critical -40, warning -10, info -1 each; total capped at -60
=================  ==========  ===============================================================

Each component's points are rounded half-up to 2 decimals; the sum is clamped to 0..100 and rounded
half-up to 1 decimal.  Confidence (high/medium/low) describes how much the *evidence* can be trusted,
independent of how attractive the deal is.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from .explain import fmt_money, fmt_pct_bp, fmt_pct_ratio, fmt_points
from .types import ConfidenceLevel, DemandEstimate, PriceStats, RiskFlag, ScoreComponent

ROI_MAX_POINTS = Decimal(30)
ROI_FULL_BP = 5000
PROFIT_MAX_POINTS = Decimal(20)
PROFIT_FULL_CENTS = 300_000
EVIDENCE_COUNT_POINTS = Decimal(12)
EVIDENCE_DISPERSION_POINTS = Decimal(8)
EVIDENCE_FULL_N = 10
DISPERSION_ZERO_AT = Decimal("0.5")
MATCH_MAX_POINTS = Decimal(10)
MATCH_UNKNOWN_POINTS = Decimal(5)
DEMAND_MAX_POINTS = Decimal(10)
DEMAND_POINTS: dict[str, Decimal] = {
    "high": Decimal(10),
    "medium": Decimal(6),
    "low": Decimal(2),
    "unknown": Decimal(3),
}
RISK_PENALTY: dict[str, Decimal] = {"critical": Decimal(-40), "warning": Decimal(-10), "info": Decimal(-1)}
RISK_PENALTY_FLOOR = Decimal(-60)

# Confidence thresholds.
LOW_EVIDENCE_FACTOR = Decimal("1.5")
HIGH_EVIDENCE_FACTOR = Decimal(2)
LOW_CONFIDENCE_DISPERSION = Decimal("0.5")
HIGH_CONFIDENCE_DISPERSION = Decimal("0.25")
LOW_CONFIDENCE_MATCH = Decimal("0.8")
HIGH_CONFIDENCE_MATCH = Decimal("0.9")

_CENT_POINTS = Decimal("0.01")
_TENTH = Decimal("0.1")


def _clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(high, value))


def _pts(value: Decimal) -> Decimal:
    return value.quantize(_CENT_POINTS, rounding=ROUND_HALF_UP)


def _dec(value: float) -> Decimal:
    return Decimal(str(value))


def _roi_component(roi_bp: int) -> tuple[Decimal, ScoreComponent]:
    fraction = _clamp(Decimal(roi_bp) / Decimal(ROI_FULL_BP), Decimal(0), Decimal(1))
    points = _pts(ROI_MAX_POINTS * fraction)
    text = (
        f"ROI of {fmt_pct_bp(roi_bp)} earns {fmt_points(points)} of {fmt_points(ROI_MAX_POINTS)} points "
        f"(linear, full points at {fmt_pct_bp(ROI_FULL_BP)} ROI or more)."
    )
    return points, ScoreComponent("roi", "Return on investment", roi_bp / 10000, float(points),
                                  float(ROI_MAX_POINTS), text)


def _profit_component(net_profit_cents: int) -> tuple[Decimal, ScoreComponent]:
    fraction = _clamp(Decimal(net_profit_cents) / Decimal(PROFIT_FULL_CENTS), Decimal(0), Decimal(1))
    points = _pts(PROFIT_MAX_POINTS * fraction)
    text = (
        f"Net profit of {fmt_money(net_profit_cents)} earns {fmt_points(points)} of "
        f"{fmt_points(PROFIT_MAX_POINTS)} points (linear, full points at {fmt_money(PROFIT_FULL_CENTS)} or more)."
    )
    return points, ScoreComponent("profit", "Net profit", float(net_profit_cents), float(points),
                                  float(PROFIT_MAX_POINTS), text)


def _evidence_component(stats: PriceStats) -> tuple[Decimal, ScoreComponent]:
    n = max(stats.n, 0)
    count_points = _pts(EVIDENCE_COUNT_POINTS * min(Decimal(n) / Decimal(EVIDENCE_FULL_N), Decimal(1)))
    if stats.dispersion is None:
        dispersion_points = Decimal("0.00")
        dispersion_text = "unknown dispersion earns 0.00 points"
    else:
        dispersion = _dec(stats.dispersion)
        dispersion_points = _pts(
            EVIDENCE_DISPERSION_POINTS * (Decimal(1) - min(dispersion / DISPERSION_ZERO_AT, Decimal(1)))
        )
        dispersion_text = (
            f"a price dispersion (IQR / median) of {fmt_pct_ratio(dispersion)} earns "
            f"{fmt_points(dispersion_points)} of {fmt_points(EVIDENCE_DISPERSION_POINTS)} points "
            f"(0 at {fmt_pct_ratio(DISPERSION_ZERO_AT)} or more)"
        )
    points = count_points + dispersion_points
    text = (
        f"{n} comparables earn {fmt_points(count_points)} of {fmt_points(EVIDENCE_COUNT_POINTS)} points "
        f"(full at {EVIDENCE_FULL_N}) and {dispersion_text}."
    )
    total_max = EVIDENCE_COUNT_POINTS + EVIDENCE_DISPERSION_POINTS
    return points, ScoreComponent("price_evidence", "Price evidence", float(n), float(points),
                                  float(total_max), text)


def _match_component(match_confidence: float | None) -> tuple[Decimal, ScoreComponent]:
    if match_confidence is None:
        points = _pts(MATCH_UNKNOWN_POINTS)
        text = (
            f"Match confidence is unknown, so a neutral {fmt_points(points)} of "
            f"{fmt_points(MATCH_MAX_POINTS)} points is used."
        )
    else:
        confidence = _clamp(_dec(match_confidence), Decimal(0), Decimal(1))
        points = _pts(MATCH_MAX_POINTS * confidence)
        text = (
            f"Product match confidence of {fmt_pct_ratio(confidence)} earns {fmt_points(points)} of "
            f"{fmt_points(MATCH_MAX_POINTS)} points."
        )
    return points, ScoreComponent("match", "Product match", match_confidence, float(points),
                                  float(MATCH_MAX_POINTS), text)


def _demand_component(demand: DemandEstimate) -> tuple[Decimal, ScoreComponent]:
    level = demand.level if demand.level in DEMAND_POINTS else "unknown"
    points = _pts(DEMAND_POINTS[level])
    if level == "unknown":
        text = (
            f"Demand is unknown (no sales evidence), so {fmt_points(points)} of "
            f"{fmt_points(DEMAND_MAX_POINTS)} points is used."
        )
    else:
        text = f"Demand level '{level}' earns {fmt_points(points)} of {fmt_points(DEMAND_MAX_POINTS)} points."
    return points, ScoreComponent("demand", "Demand", None, float(points), float(DEMAND_MAX_POINTS), text)


def _risk_component(risk_flags: list[RiskFlag]) -> tuple[Decimal, ScoreComponent]:
    counts = {sev: sum(1 for flag in risk_flags if flag.severity == sev) for sev in ("critical", "warning", "info")}
    raw = sum((RISK_PENALTY[sev] * counts[sev] for sev in counts), Decimal(0))
    points = _pts(max(raw, RISK_PENALTY_FLOOR))
    capped = " (capped)" if raw < RISK_PENALTY_FLOOR else ""
    text = (
        f"{counts['critical']} critical, {counts['warning']} warning and {counts['info']} info risk flags "
        f"give a penalty of {fmt_points(points)} points{capped}."
    )
    return points, ScoreComponent("risk", "Risk penalty", float(len(risk_flags)), float(points), 0.0, text)


def _confidence(
    *,
    stats: PriceStats,
    match_confidence: float | None,
    risk_flags: list[RiskFlag],
    min_comparables: int,
) -> ConfidenceLevel:
    n = Decimal(stats.n)
    dispersion = _dec(stats.dispersion) if stats.dispersion is not None else None
    severities = {flag.severity for flag in risk_flags}
    match = _dec(match_confidence) if match_confidence is not None else None
    if (
        n < LOW_EVIDENCE_FACTOR * min_comparables
        or (dispersion is not None and dispersion > LOW_CONFIDENCE_DISPERSION)
        or "critical" in severities
        or (match is not None and match < LOW_CONFIDENCE_MATCH)
    ):
        return "low"
    if (
        n >= HIGH_EVIDENCE_FACTOR * min_comparables
        and dispersion is not None
        and dispersion <= HIGH_CONFIDENCE_DISPERSION
        and (match or Decimal(0)) >= HIGH_CONFIDENCE_MATCH
        and not severities & {"warning", "critical"}
    ):
        return "high"
    return "medium"


def score_opportunity(
    *,
    net_profit_cents: int,
    roi_bp: int,
    stats: PriceStats,
    match_confidence: float | None,
    demand: DemandEstimate,
    risk_flags: list[RiskFlag],
    min_comparables: int,
) -> tuple[float, ConfidenceLevel, list[ScoreComponent]]:
    """Deterministic weighted score; returns (score rounded to 1 decimal, confidence, components).

    Components are returned in a fixed order: roi, profit, price_evidence, match, demand, risk.
    """
    parts = [
        _roi_component(roi_bp),
        _profit_component(net_profit_cents),
        _evidence_component(stats),
        _match_component(match_confidence),
        _demand_component(demand),
        _risk_component(risk_flags),
    ]
    total = sum((points for points, _ in parts), Decimal(0))
    score = _clamp(total, Decimal(0), Decimal(100)).quantize(_TENTH, rounding=ROUND_HALF_UP)
    confidence = _confidence(
        stats=stats, match_confidence=match_confidence, risk_flags=risk_flags, min_comparables=min_comparables
    )
    return float(score), confidence, [component for _, component in parts]
