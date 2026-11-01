"""Web dashboard checks that run under pytest.

* Static hygiene of the shipped assets (strict-CSP compatible: no inline code/styles, no remote assets, no
  HTML-injection sinks in the JavaScript).
* The real FastAPI app serves the page and static files with a strict Content-Security-Policy.
* ``integration``: the dashboard<->API contract - every endpoint a view calls on load answers 200 with the
  fields ``app.js`` reads, using the real services and the SYNTHETIC demo dataset.

The browser-level test lives next to this file (``smoke.mjs``, Node + Playwright).
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

WEB_DIR = Path(__file__).resolve().parents[2] / "flipradar" / "web"
INDEX = WEB_DIR / "templates" / "index.html"
STATIC = WEB_DIR / "static"


class _TagCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.inline_scripts: list[str] = []
        self._in_script = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))
        self._in_script = tag == "script"

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._in_script = False

    def handle_data(self, data: str) -> None:
        if self._in_script and data.strip():
            self.inline_scripts.append(data)


def _parsed_index() -> _TagCollector:
    parser = _TagCollector()
    parser.feed(INDEX.read_text(encoding="utf-8"))
    return parser


def _js_code_without_comments() -> str:
    lines = (STATIC / "app.js").read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith(("*", "//", "/*")))


def test_index_has_no_inline_scripts_handlers_or_styles() -> None:
    parsed = _parsed_index()
    assert parsed.inline_scripts == []
    for tag, attrs in parsed.tags:
        assert tag != "style", "inline <style> violates style-src 'self'"
        assert "style" not in attrs, f"style attribute on <{tag}>"
        handlers = [name for name in attrs if name.lower().startswith("on")]
        assert not handlers, f"inline event handler {handlers} on <{tag}>"


def test_index_references_only_existing_local_assets() -> None:
    for tag, attrs in _parsed_index().tags:
        for key in ("src", "href"):
            value = attrs.get(key)
            if not value or value.startswith("#"):
                continue
            assert not re.match(r"^([a-z][a-z0-9+.-]*:|//)", value, re.IGNORECASE), f"remote/absolute URL <{tag} {key}={value}>"
            assert value.startswith("static/"), f"unexpected asset path {value}"
            assert (WEB_DIR / value).is_file(), f"missing asset {value}"


def test_index_is_safe_to_serve_as_a_jinja_template() -> None:
    text = INDEX.read_text(encoding="utf-8")
    for marker in ("{{", "{%", "{#"):
        assert marker not in text


def test_javascript_has_no_html_injection_sinks_or_remote_code() -> None:
    code = _js_code_without_comments()
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "setAttribute('style'"):
        assert sink not in code, f"{sink} found in app.js"
    assert not re.search(r"""\bimport\s*\(|from\s+['"]https?:""", code), "app.js must not load remote modules"


def test_css_loads_nothing_remote() -> None:
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    assert "@import" not in css
    assert not re.search(r"url\(\s*['\"]?(https?:)?//", css)


def test_real_app_serves_dashboard_and_static_files_with_strict_csp(client) -> None:
    res = client.get("/")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html")
    assert 'src="static/app.js"' in res.text
    csp = res.headers.get("content-security-policy", "")
    assert "script-src 'self'" in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
    for path, content_type in (
        ("/static/app.js", "javascript"),
        ("/static/app.css", "text/css"),
        ("/static/favicon.svg", "image/svg+xml"),
    ):
        asset = client.get(path)
        assert asset.status_code == 200, path
        assert content_type in asset.headers["content-type"], (path, asset.headers["content-type"])
    health = client.get("/health").json()
    assert {"status", "version", "api_version", "database"} <= health.keys()


# Endpoint -> fields app.js reads from the response (on view load).
_VIEW_CONTRACT: list[tuple[str, set[str]]] = [
    ("/api/v1/auth/check", {"ok", "token_name", "auth_required"}),
    ("/api/v1/opportunities?include_suspicious=true&limit=50&offset=0&sort=score", {"items", "total", "limit", "offset"}),
    ("/api/v1/opportunities?status=all&limit=200", {"items", "total"}),
    ("/api/v1/fee-profiles", set()),
    ("/api/v1/inventory?limit=100&offset=0", {"items", "total"}),
    ("/api/v1/analytics/summary?days=30", {
        "period_days", "revenue_cents", "realized_profit_cents", "avg_roi_bp", "units_sold", "sales_count",
        "avg_days_to_sell", "inventory_units", "inventory_cost_cents", "inventory_target_value_cents", "aging",
        "open_opportunities", "avg_open_opportunity_score", "potential_profit_open_cents", "opportunities_by_source",
        "top_products_by_profit", "listings_tracked", "products_tracked", "last_collection_at",
    }),
    ("/api/v1/analytics/timeseries?days=30&bucket=day", {"bucket", "points"}),
    ("/api/v1/sources", set()),
    ("/api/v1/searches", set()),
    ("/api/v1/runs?limit=50", set()),
    ("/api/v1/alerts", set()),
    ("/api/v1/notifications?unread_only=false&limit=50", set()),
    ("/api/v1/notifications?unread_only=true&limit=50", set()),
    ("/api/v1/settings", {
        "min_comparables", "comparable_max_age_days", "default_fee_profile_key", "target_roi_bp", "min_profit_cents",
        "fx_usd_mxn_manual", "include_demo_data", "ai_provider", "scheduler_enabled",
    }),
]

_OPPORTUNITY_ROW = {
    "id", "buy_title", "product_title", "buy_source_key", "buy_total_cents", "expected_sale_cents", "net_profit_cents",
    "roi_bp", "score", "confidence", "risk_level", "demand_level", "status", "is_demo",
}
_OPPORTUNITY_DETAIL = _OPPORTUNITY_ROW | {
    "price_stats", "cost_breakdown", "score_components", "risk_flags", "demand", "explanation", "facts", "ai_summary",
    "comparables", "buy_url", "product_key", "fee_profile_key", "margin_bp", "comparable_count",
}


@pytest.mark.integration
def test_dashboard_api_contract_with_real_services_and_demo_data(api) -> None:
    """Uses the real engine/normalizer through the demo seeder; may fail until those modules are implemented."""
    from flipradar.db import session_scope
    from flipradar.services.demo import seed_demo

    with session_scope() as session:
        seed_demo(session)

    for path, fields in _VIEW_CONTRACT:
        res = api.get(path)
        assert res.status_code == 200, (path, res.status_code, res.text[:300])
        body = res.json()
        if fields:
            missing = fields - body.keys()
            assert not missing, (path, missing)

    page = api.get("/api/v1/opportunities?status=all&include_suspicious=true&limit=200").json()
    assert page["items"], "the demo dataset should produce at least one opportunity"
    for row in page["items"]:
        assert not (_OPPORTUNITY_ROW - row.keys())
        for key in ("buy_total_cents", "expected_sale_cents", "net_profit_cents", "roi_bp"):
            assert isinstance(row[key], int), (key, row[key])  # money/bp must be integers for the UI
        assert row["is_demo"] is True
    detail = api.get(f"/api/v1/opportunities/{page['items'][0]['id']}").json()
    assert not (_OPPORTUNITY_DETAIL - detail.keys())
    breakdown = detail["cost_breakdown"]
    for line in breakdown["acquisition_lines"] + breakdown["selling_lines"]:
        assert {"label", "formula", "amount_cents"} <= line.keys()
        assert isinstance(line["amount_cents"], int)
    # the AI summary button must get a structured answer even with no provider configured
    ai = api.post(f"/api/v1/opportunities/{detail['id']}/ai-summary")
    assert ai.status_code == 200
    assert {"validated", "provider", "message", "summary"} <= ai.json().keys()
