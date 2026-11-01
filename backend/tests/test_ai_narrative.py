"""AI narrative: providers mocked with respx; the validator only lets through numbers from the facts."""

import asyncio
import json

import httpx
import pytest
import respx

from flipradar.ai import narrative
from tests.conftest import make_settings

FACTS = {
    "product_title": "Apple iPhone 15 Pro 256GB",
    # Numbers in free-text facts (titles) are not trusted; the product's own name comes from product_key.
    "product_key": "apple|iphone 15 pro|256gb",
    "buy_total_cents": 1_000_000,
    "expected_sale_cents": 1_350_000,
    "net_profit_cents": 235_000,
    "roi_bp": 2350,
    "comparable_count": 7,
    "net_profit_fmt": "$2,350.00 MXN",
    "dispersion": 0.125,
    "risks": [{"code": "shipping_unknown", "severity": "info"}],
}

GOOD = ("El Apple iPhone 15 Pro 256GB se puede comprar en $10,000.00 y la venta esperada es de $13,500.00. "
        "La ganancia neta estimada es de $2,350.00, un ROI de 23.5%, con base en 7 comparables. "
        "La dispersion de precios es de 12.5%.")
INVENTED = GOOD + " Se venden 45 unidades al mes."


def run(coro):
    return asyncio.run(coro)


def test_validator_accepts_numbers_from_facts():
    ok, unknown = narrative.validate_summary(GOOD, FACTS)
    assert ok and unknown == []
    assert narrative.validate_summary("Ganancia de 2350 pesos (2,350.00) y 13500.", FACTS)[0]
    assert narrative.validate_summary("Redondeado: $2,350 y $13,500.", FACTS)[0]


def test_validator_rejects_invented_numbers():
    ok, unknown = narrative.validate_summary(INVENTED, FACTS)
    assert not ok and unknown == ["45"]
    assert not narrative.validate_summary("ROI de 25%", FACTS)[0]
    assert not narrative.validate_summary("Ganancia de $2,351.00", FACTS)[0]


def test_extract_numbers_formats():
    values = narrative.extract_numbers("$12,345.67 MXN, 23.5% y 1,000 piezas; -5")
    assert [str(v) for v in values] == ["12345.67", "23.5", "1000", "5"]


def test_provider_none(tmp_path):
    assert run(narrative.summarize(FACTS, make_settings(tmp_path))) == (None, False, "none", "AI disabled")


def test_unknown_provider_and_empty_facts(tmp_path):
    s = make_settings(tmp_path, llm_provider="magic")
    assert run(narrative.summarize(FACTS, s))[1:3] == (False, "magic")
    s = make_settings(tmp_path, llm_provider="ollama")
    summary, validated, _, message = run(narrative.summarize({}, s))
    assert summary is None and not validated and "recompute" in message


@respx.mock
def test_ollama_validated_summary(tmp_path):
    s = make_settings(tmp_path, llm_provider="ollama", llm_base_url="http://ollama.local:11434/", llm_model="llama3.1:8b")
    route = respx.post("http://ollama.local:11434/api/generate").mock(
        return_value=httpx.Response(200, json={"response": "<think>calculo 99</think>" + GOOD})
    )
    summary, validated, provider, message = run(narrative.summarize(FACTS, s))
    assert validated and provider == "ollama" and summary == GOOD and message.startswith("Validated")
    sent = json.loads(route.calls.last.request.content)
    assert sent["model"] == "llama3.1:8b" and sent["stream"] is False and sent["options"] == {"temperature": 0}
    assert "2350" in sent["prompt"] and "espanol" in sent["prompt"]


@respx.mock
def test_ollama_invented_number_is_rejected(tmp_path):
    s = make_settings(tmp_path, llm_provider="ollama")
    respx.post("http://127.0.0.1:11434/api/generate").mock(return_value=httpx.Response(200, json={"response": INVENTED}))
    summary, validated, _, message = run(narrative.summarize(FACTS, s))
    assert summary is None and validated is False and "45" in message and "not stored" in message


@respx.mock
def test_openai_compatible(tmp_path):
    s = make_settings(tmp_path, llm_provider="openai_compatible", llm_base_url="http://llm.local:8080/v1",
                      llm_api_key="sk-local")
    route = respx.post("http://llm.local:8080/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": GOOD}}]})
    )
    summary, validated, provider, _ = run(narrative.summarize(FACTS, s))
    assert validated and summary == GOOD and provider == "openai_compatible"
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer sk-local"
    assert json.loads(request.content)["temperature"] == 0


@respx.mock
@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (httpx.Response(500), "HTTP 500"),
        (httpx.Response(200, json={"unexpected": True}), "could not be parsed"),
        (httpx.Response(200, json={"response": "   "}), "empty response"),
        (httpx.ConnectError("refused"), "ConnectError"),
    ],
)
def test_network_and_format_errors_never_raise(tmp_path, response, expected):
    s = make_settings(tmp_path, llm_provider="ollama")
    route = respx.post("http://127.0.0.1:11434/api/generate")
    if isinstance(response, Exception):
        route.mock(side_effect=response)
    else:
        route.mock(return_value=response)
    summary, validated, _, message = run(narrative.summarize(FACTS, s))
    assert summary is None and validated is False and expected in message


@respx.mock
def test_ai_summary_endpoint_stores_only_validated(tmp_path):
    from fastapi.testclient import TestClient

    from flipradar.db import session_scope
    from flipradar.main import create_app
    from flipradar.models import Opportunity
    from flipradar.security import create_token

    app = create_app(make_settings(tmp_path, llm_provider="ollama"))
    with TestClient(app) as c:
        with session_scope() as db:
            _, token = create_token(db, "t")
            from tests.test_api_alerts import fabricate_opportunity

            opp_id = fabricate_opportunity(db).id
            opp = db.get(Opportunity, opp_id)
            opp.explanation_facts_json = FACTS
        headers = {"Authorization": f"Bearer {token}"}
        route = respx.post("http://127.0.0.1:11434/api/generate")
        route.mock(return_value=httpx.Response(200, json={"response": INVENTED}))
        r = c.post(f"/api/v1/opportunities/{opp_id}/ai-summary", headers=headers)
        assert r.status_code == 200 and r.json()["validated"] is False and r.json()["summary"] is None
        assert c.get(f"/api/v1/opportunities/{opp_id}", headers=headers).json()["ai_summary"] is None
        route.mock(return_value=httpx.Response(200, json={"response": GOOD}))
        r = c.post(f"/api/v1/opportunities/{opp_id}/ai-summary", headers=headers)
        assert r.json()["validated"] is True and r.json()["summary"] == GOOD
        assert c.get(f"/api/v1/opportunities/{opp_id}", headers=headers).json()["ai_summary"] == GOOD
