"""Banxico SIE + FX fallback chain tests (respx mocks + fixtures; no network)."""

import asyncio
import json
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from flipradar.collectors import fx_sources
from flipradar.collectors.banxico import fetch_usd_mxn_fix, parse_fix_response
from flipradar.collectors.base import CollectorError, CollectorNotConfigured
from flipradar.config import Settings

FIX = Path(__file__).parent / "fixtures" / "collectors"
BANXICO_URL = "https://www.banxico.org.mx/SieAPIRest/service/v1/series/SF43718/datos/oportuno"
FRANKFURTER_URL = "https://api.frankfurter.dev/v1/latest"
ER_API_URL = "https://open.er-api.com/v6/latest/USD"
TOKEN = "a" * 64


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _reset():
    fx_sources.reset_cache()
    yield
    fx_sources.reset_cache()


# ------------------------------------------------------------------------------------------------
# Banxico
# ------------------------------------------------------------------------------------------------


def test_banxico_parse_and_header():
    settings = Settings(_env_file=None, banxico_token=TOKEN)
    with respx.mock(assert_all_called=False) as router:
        route = router.get(BANXICO_URL).mock(return_value=httpx.Response(200, json=load("banxico_fix.json")))
        rate_date, rate = run(fetch_usd_mxn_fix(settings))
        request = route.calls[0].request
    assert (rate_date, rate) == ("2026-10-30", Decimal("18.1234"))
    assert isinstance(rate, Decimal)
    assert request.headers["Bmx-Token"] == TOKEN
    assert request.headers["Accept"] == "application/json"


def test_banxico_ne_is_an_error():
    with pytest.raises(CollectorError, match="N/E"):
        parse_fix_response(load("banxico_ne.json"))


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"bmx": {"series": []}},
        {"bmx": {"series": [{"idSerie": "SF43718", "datos": []}]}},
        {"bmx": {"series": [{"idSerie": "SF43718", "datos": [{"fecha": "30/10/2026", "dato": "abc"}]}]}},
        {"bmx": {"series": [{"idSerie": "SF43718", "datos": [{"fecha": "2026-10-30", "dato": "18.1"}]}]}},
        {"bmx": {"series": [{"idSerie": "SF43718", "datos": [{"fecha": "30/10/2026", "dato": "1812.34"}]}]}},
    ],
)
def test_banxico_malformed_payloads(payload):
    with pytest.raises(CollectorError):
        parse_fix_response(payload)


def test_banxico_not_configured_and_http_errors():
    with pytest.raises(CollectorNotConfigured):
        run(fetch_usd_mxn_fix(Settings(_env_file=None)))
    settings = Settings(_env_file=None, banxico_token=TOKEN)
    with respx.mock(assert_all_called=False) as router:
        router.get(BANXICO_URL).mock(
            return_value=httpx.Response(401, json={"error": {"mensaje": "Token inválido"}})
        )
        with pytest.raises(CollectorError, match="Token inválido"):
            run(fetch_usd_mxn_fix(settings))
    with respx.mock(assert_all_called=False) as router:
        router.get(BANXICO_URL).mock(return_value=httpx.Response(429))
        with pytest.raises(CollectorError, match="80 queries/min"):
            run(fetch_usd_mxn_fix(settings))


# ------------------------------------------------------------------------------------------------
# fallback chain
# ------------------------------------------------------------------------------------------------


def test_chain_prefers_banxico():
    settings = Settings(_env_file=None, banxico_token=TOKEN)
    with respx.mock(assert_all_called=False) as router:
        router.get(BANXICO_URL).mock(return_value=httpx.Response(200, json=load("banxico_fix.json")))
        frank = router.get(FRANKFURTER_URL).mock(
            return_value=httpx.Response(200, json=load("frankfurter_latest.json"))
        )
        quote = run(fx_sources.get_usd_mxn(settings))
        assert frank.call_count == 0
    assert quote == fx_sources.FxQuote(
        Decimal("18.1234"), "2026-10-30", "banxico_fix", fx_sources.BANXICO_ATTRIBUTION, True
    )


def test_chain_falls_back_to_frankfurter():
    settings = Settings(_env_file=None, banxico_token=TOKEN)
    with respx.mock(assert_all_called=False) as router:
        router.get(BANXICO_URL).mock(return_value=httpx.Response(200, json=load("banxico_ne.json")))
        frank = router.get(FRANKFURTER_URL).mock(
            return_value=httpx.Response(200, json=load("frankfurter_latest.json"))
        )
        er = router.get(ER_API_URL)
        quote = run(fx_sources.get_usd_mxn(settings))
        assert parse_qs(frank.calls[0].request.url.query.decode()) == {"base": ["USD"], "symbols": ["MXN"]}
        assert er.call_count == 0
    assert quote.rate == Decimal("18.1012")
    assert quote.rate_date == "2026-10-30"
    assert quote.source == "frankfurter_ecb"
    assert quote.is_official is False
    assert "European Central Bank" in quote.attribution


def test_chain_without_token_uses_exchangerate_api_last():
    settings = Settings(_env_file=None)
    with respx.mock(assert_all_called=False) as router:
        banxico = router.get(BANXICO_URL)
        router.get(FRANKFURTER_URL).mock(return_value=httpx.Response(503))
        er = router.get(ER_API_URL).mock(return_value=httpx.Response(200, json=load("er_api_latest.json")))
        quote = run(fx_sources.get_usd_mxn(settings))
        assert banxico.call_count == 0
        # second call within 24 h is served from the in-process cache (open access terms)
        again = run(fx_sources.get_usd_mxn(settings))
        assert er.call_count == 1
    assert quote == again
    assert quote.rate == Decimal("18.0567")
    assert quote.rate_date == "2026-10-31"
    assert quote.source == "exchangerate_api_open"
    assert quote.attribution == "Rates By Exchange Rate API (https://www.exchangerate-api.com)"


def test_chain_all_fail_aggregates_reasons_in_order():
    settings = Settings(_env_file=None, banxico_token=TOKEN)
    with respx.mock(assert_all_called=False) as router:
        router.get(BANXICO_URL).mock(return_value=httpx.Response(500))
        router.get(FRANKFURTER_URL).mock(side_effect=httpx.ConnectTimeout("timeout"))
        router.get(ER_API_URL).mock(
            return_value=httpx.Response(200, json={"result": "error", "error-type": "quota"})
        )
        with pytest.raises(CollectorError) as err:
            run(fx_sources.get_usd_mxn(settings))
    message = str(err.value)
    assert message.startswith("no USD/MXN exchange rate available: ")
    banxico_pos = message.index("banxico: Banxico SIE HTTP 500")
    frank_pos = message.index("frankfurter: request failed (ConnectTimeout")
    er_pos = message.index("exchangerate-api: result='error' error='quota'")
    assert banxico_pos < frank_pos < er_pos


def test_chain_not_configured_reason_listed():
    settings = Settings(_env_file=None)
    with respx.mock(assert_all_called=False) as router:
        router.get(FRANKFURTER_URL).mock(
            return_value=httpx.Response(200, json={"base": "USD", "date": "2026-10-30", "rates": {}})
        )
        router.get(ER_API_URL).mock(
            return_value=httpx.Response(200, json={"result": "success", "rates": {"MXN": 500}})
        )
        with pytest.raises(CollectorError) as err:
            run(fx_sources.get_usd_mxn(settings))
    message = str(err.value)
    assert "banxico: not configured" in message
    assert "frankfurter: MXN rate missing" in message
    assert "exchangerate-api: MXN rate 500 is outside the plausible range" in message


def test_rates_parsed_exactly_via_str():
    settings = Settings(_env_file=None)
    with respx.mock(assert_all_called=False) as router:
        router.get(FRANKFURTER_URL).mock(
            return_value=httpx.Response(
                200, content=b'{"base":"USD","date":"2026-10-30","rates":{"MXN":18.1}}'
            )
        )
        quote = run(fx_sources.fetch_frankfurter(settings))
    assert quote.rate == Decimal("18.1")  # not Decimal(18.1) = 18.10000000000000142...
