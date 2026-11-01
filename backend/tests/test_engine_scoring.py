import pytest

from flipradar.engine.scoring import score_opportunity
from flipradar.engine.types import DemandEstimate, PriceStats, RiskFlag


def make_stats(n: int = 6, dispersion: float | None = 0.1) -> PriceStats:
    return PriceStats(
        n=n,
        median_cents=1_000_000,
        p25_cents=950_000,
        p75_cents=1_050_000,
        min_cents=900_000,
        max_cents=1_100_000,
        mad_cents=50_000,
        iqr_cents=100_000,
        dispersion=dispersion,
        source_count=1,
        seller_count=n,
    )


def demand(level: str = "medium") -> DemandEstimate:
    return DemandEstimate(level=level, confidence=None if level == "unknown" else "medium")


def flag(severity: str, code: str = "x") -> RiskFlag:
    return RiskFlag(code, severity, "test flag")


def score(**overrides):
    kwargs = dict(
        net_profit_cents=150_000,
        roi_bp=2500,
        stats=make_stats(),
        match_confidence=0.95,
        demand=demand("medium"),
        risk_flags=[],
        min_comparables=3,
    )
    kwargs.update(overrides)
    return score_opportunity(**kwargs)


def points(components) -> dict[str, float]:
    return {c.key: c.points for c in components}


def test_exact_component_points_and_total():
    value, confidence, components = score(risk_flags=[flag("warning")])
    assert [c.key for c in components] == ["roi", "profit", "price_evidence", "match", "demand", "risk"]
    assert points(components) == {
        "roi": 15.0,  # 30 * 25% / 50%
        "profit": 10.0,  # 20 * 1,500 / 3,000
        "price_evidence": 13.6,  # 12 * 6/10 + 8 * (1 - 0.1/0.5)
        "match": 9.5,
        "demand": 6.0,
        "risk": -10.0,
    }
    assert value == 44.1
    assert confidence == "medium"
    assert {c.key: c.max_points for c in components} == {
        "roi": 30.0, "profit": 20.0, "price_evidence": 20.0, "match": 10.0, "demand": 10.0, "risk": 0.0,
    }


def test_explanations_cite_the_numbers():
    _, _, components = score(risk_flags=[flag("warning")])
    text = {c.key: c.explanation for c in components}
    assert "25.00%" in text["roi"] and "15.00 of 30.00" in text["roi"]
    assert "$1,500.00" in text["profit"] and "$3,000.00" in text["profit"]
    assert "6 comparables" in text["price_evidence"] and "10.00%" in text["price_evidence"]
    assert "95.00%" in text["match"]
    assert "'medium'" in text["demand"]
    assert "1 warning" in text["risk"] and "-10.00" in text["risk"]


def test_score_is_monotonic_in_roi():
    scores = [score(roi_bp=bp)[0] for bp in range(-2000, 8001, 250)]
    assert scores == sorted(scores)
    assert score(roi_bp=0)[0] < score(roi_bp=2500)[0] < score(roi_bp=5000)[0]
    assert score(roi_bp=5000)[0] == score(roi_bp=9000)[0]  # capped at 50 % ROI
    assert points(score(roi_bp=-500)[2])["roi"] == 0.0


def test_score_is_monotonic_in_profit():
    scores = [score(net_profit_cents=c)[0] for c in range(-100_000, 400_001, 25_000)]
    assert scores == sorted(scores)


def test_score_decreases_with_risk():
    none = score(risk_flags=[])[0]
    info = score(risk_flags=[flag("info")])[0]
    warning = score(risk_flags=[flag("warning")])[0]
    critical = score(risk_flags=[flag("critical")])[0]
    assert none > info > warning > critical
    flags: list[RiskFlag] = []
    previous = none
    for severity in ["info", "warning", "info", "warning", "critical"]:
        flags.append(flag(severity))
        current = score(risk_flags=list(flags))[0]
        assert current <= previous
        previous = current


def test_risk_penalty_is_capped():
    _, _, components = score(risk_flags=[flag("critical"), flag("critical"), flag("warning")])
    assert points(components)["risk"] == -60.0
    assert "(capped)" in components[-1].explanation


def test_score_is_clamped_at_zero():
    value, confidence, _ = score(roi_bp=-5000, net_profit_cents=-100_000, risk_flags=[flag("critical")] * 2,
                                 demand=demand("low"), stats=make_stats(n=1, dispersion=None),
                                 match_confidence=0.1)
    assert value == 0.0
    assert confidence == "low"


def test_maximum_score():
    value, confidence, _ = score(roi_bp=6000, net_profit_cents=500_000, stats=make_stats(n=12, dispersion=0.0),
                                 match_confidence=1.0, demand=demand("high"))
    assert value == 90.0  # 30 + 20 + 20 + 10 + 10, no penalty
    assert confidence == "high"


@pytest.mark.parametrize(("level", "expected"), [("high", 10.0), ("medium", 6.0), ("low", 2.0), ("unknown", 3.0)])
def test_demand_points(level, expected):
    assert points(score(demand=demand(level))[2])["demand"] == expected


def test_unknown_match_and_dispersion():
    comps = points(score(match_confidence=None, stats=make_stats(n=5, dispersion=None))[2])
    assert comps["match"] == 5.0
    assert comps["price_evidence"] == 6.0  # 12 * 5/10, dispersion unknown -> 0


def test_evidence_saturates():
    comps = points(score(stats=make_stats(n=40, dispersion=0.9))[2])
    assert comps["price_evidence"] == 12.0  # full count credit, no dispersion credit


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, "high"),
        ({"stats": make_stats(n=4)}, "low"),  # 4 < 1.5 * 3
        ({"stats": make_stats(n=5)}, "medium"),  # 5 >= 4.5 but < 6
        ({"stats": make_stats(dispersion=0.6)}, "low"),
        ({"stats": make_stats(dispersion=0.3)}, "medium"),
        ({"stats": make_stats(dispersion=None)}, "medium"),
        ({"risk_flags": [flag("critical")]}, "low"),
        ({"risk_flags": [flag("warning")]}, "medium"),
        ({"risk_flags": [flag("info")]}, "high"),
        ({"match_confidence": 0.79}, "low"),
        ({"match_confidence": 0.85}, "medium"),
        ({"match_confidence": None}, "medium"),
        ({"min_comparables": 2, "stats": make_stats(n=4)}, "high"),
    ],
)
def test_confidence_rules(overrides, expected):
    assert score(**overrides)[1] == expected


def test_scoring_is_deterministic():
    kwargs = dict(risk_flags=[flag("warning"), flag("info")], match_confidence=0.912345, roi_bp=1234,
                  net_profit_cents=98_765)
    first = score(**kwargs)
    second = score(**kwargs)
    assert first[0] == second[0] and first[1] == second[1]
    assert [c.to_dict() for c in first[2]] == [c.to_dict() for c in second[2]]
