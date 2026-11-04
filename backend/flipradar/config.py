"""Runtime configuration.

All settings are read from environment variables prefixed with ``FLIPRADAR_`` (or a ``.env`` file in the
working directory).  Every external integration is OPTIONAL: if its credentials are missing the
connector reports itself as "not configured" instead of failing the whole application.
"""

from __future__ import annotations

import ipaddress
import re
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = BACKEND_DIR / "data"
_HOSTNAME_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*")


def _split(value: str) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def normalize_host(host: str) -> str:
    """Lower-case host name without brackets or trailing dot (``[::1]`` -> ``::1``, ``Flip.`` -> ``flip``)."""
    host = host.strip().lower()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host.rstrip(".")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FLIPRADAR_", env_file=".env", extra="ignore")

    # --- core -----------------------------------------------------------------------------------
    database_url: str = Field(default=f"sqlite:///{DEFAULT_DATA_DIR / 'flipradar.db'}")
    host: str = "127.0.0.1"
    port: int = 8000
    # Comma separated list of allowed CORS origins for the web dashboard (Android app does not need CORS).
    cors_origins: str = ""
    # When true, /api endpoints require a bearer token (strongly recommended whenever the server is
    # reachable from other devices).  Tokens are created with ``flipradar create-token``.
    require_auth: bool = True
    # Requests per minute: per bearer token for authenticated API calls, per client IP otherwise.
    rate_limit_per_minute: int = 240
    # Comma separated IPs/CIDRs of reverse proxies (Caddy, nginx...) whose X-Forwarded-For header the rate
    # limiter may use to find the real client address, e.g. "127.0.0.1".  Empty: the header is ignored.
    trusted_proxies: str = ""
    # Only used when require_auth=false: extra host names accepted in the Host header besides localhost,
    # 127.0.0.1 and [::1] (comma separated).  Protects the token-less mode against DNS rebinding.
    allowed_hosts: str = ""
    user_agent: str = "FlipRadar/0.1 (self-hosted personal price research tool)"
    http_timeout_seconds: float = 20.0
    log_level: str = "INFO"

    # --- scheduler ------------------------------------------------------------------------------
    scheduler_enabled: bool = True
    scheduler_tick_seconds: int = 60

    # --- market defaults --------------------------------------------------------------------------
    # Listings older than this are not used as price comparables.
    comparable_max_age_days: int = 30
    # Minimum number of independent comparables needed before an expected resale price is computed.
    min_comparables: int = 3
    # Manual USD->MXN rate used ONLY when no automatic FX source (Banxico) is configured.  None means
    # foreign-currency listings are not converted (and are therefore excluded from comparisons).
    fx_usd_mxn_manual: float | None = None

    # --- Mercado Libre (official API, free app registration) -------------------------------------
    ml_client_id: str | None = None
    ml_client_secret: str | None = None
    ml_refresh_token: str | None = None
    ml_access_token: str | None = None
    ml_redirect_uri: str | None = None
    ml_api_base: str = "https://api.mercadolibre.com"
    ml_site_id: str = "MLM"

    # Enrich catalog listings with public seller reputation (max 20 sellers per search).
    ml_enrich_sellers: bool = True

    # --- eBay Browse API (official, free developer program) --------------------------------------
    ebay_client_id: str | None = None
    ebay_client_secret: str | None = None
    ebay_api_base: str = "https://api.ebay.com"
    ebay_marketplace_id: str = "EBAY_US"
    # Safety margin below eBay's default 5,000 Browse calls/day per application.
    ebay_daily_call_limit: int = 4500

    # --- Banco de Mexico SIE API (free token) ------------------------------------------------------
    banxico_token: str | None = None
    banxico_api_base: str = "https://www.banxico.org.mx/SieAPIRest/service/v1"
    # Key-less public FX fallbacks (labelled as estimates; ExchangeRate-API requires attribution).
    fx_public_sources_enabled: bool = True
    frankfurter_api_base: str = "https://api.frankfurter.dev/v1"
    exchangerate_api_url: str = "https://open.er-api.com/v6/latest/USD"

    # --- single-URL JSON-LD check ------------------------------------------------------------------
    # Extra TCP ports allowed for user-initiated URL checks (comma separated; 80/443 always allowed).
    url_check_allowed_ports: str = ""

    # --- RSS deal feeds ---------------------------------------------------------------------------
    # Comma separated list of RSS/Atom feed URLs to poll (e.g. a deal community feed you are allowed
    # to read).  Empty by default: the operator decides which feeds to use.
    rss_feeds: str = ""

    # --- notifications (all optional, all free/self-hostable) ------------------------------------
    ntfy_url: str | None = None  # e.g. https://ntfy.sh or your self-hosted ntfy server
    ntfy_topic: str | None = None
    ntfy_token: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    webhook_url: str | None = None

    # --- optional AI narrative (local first) -------------------------------------------------------
    # "none" (default), "ollama" (local, free) or "openai_compatible" (any compatible server; may cost
    # money if you point it at a paid provider).
    llm_provider: str = "none"
    llm_base_url: str = "http://127.0.0.1:11434"
    llm_model: str = "llama3.1:8b"
    llm_api_key: str | None = None
    llm_timeout_seconds: float = 60.0

    # --- presentation -------------------------------------------------------------------------------
    # IANA time zone used for analytics day/week/month buckets (sales in the evening in Mexico belong
    # to that local day, not to the next UTC day).
    timezone: str = "America/Mexico_City"

    # --- marketplace fee cache --------------------------------------------------------------------
    # Days before a cached per-category marketplace commission (category_fees) is refreshed.
    category_fee_ttl_days: int = 7

    @field_validator("trusted_proxies")
    @classmethod
    def _check_trusted_proxies(cls, value: str) -> str:
        for item in _split(value):
            try:
                ipaddress.ip_network(item, strict=False)
            except ValueError as exc:
                raise ValueError(f"FLIPRADAR_TRUSTED_PROXIES: {item!r} is not an IP address or CIDR") from exc
        return value

    @field_validator("allowed_hosts")
    @classmethod
    def _check_allowed_hosts(cls, value: str) -> str:
        for item in _split(value):
            host = normalize_host(item)
            try:
                ipaddress.ip_address(host)
            except ValueError:
                if not _HOSTNAME_RE.fullmatch(host):
                    raise ValueError(f"FLIPRADAR_ALLOWED_HOSTS: {item!r} is not a host name") from None
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def trusted_proxy_networks(self) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
        return [ipaddress.ip_network(item, strict=False) for item in _split(self.trusted_proxies)]

    @property
    def allowed_host_list(self) -> list[str]:
        return [normalize_host(item) for item in _split(self.allowed_hosts)]

    @property
    def rss_feed_list(self) -> list[str]:
        return [u.strip() for u in self.rss_feeds.split(",") if u.strip()]

    @property
    def sqlite_path(self) -> Path | None:
        prefix = "sqlite:///"
        if self.database_url.startswith(prefix):
            raw = self.database_url[len(prefix):]
            if raw and raw != ":memory:":
                return Path(raw)
        return None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
