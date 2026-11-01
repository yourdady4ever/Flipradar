"""Runtime configuration.

All settings are read from environment variables prefixed with ``FLIPRADAR_`` (or a ``.env`` file in the
working directory).  Every external integration is OPTIONAL: if its credentials are missing the
connector reports itself as "not configured" instead of failing the whole application.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = BACKEND_DIR / "data"


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
    # Seconds; protects the login-less token endpoints against brute force.
    rate_limit_per_minute: int = 240
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

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

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
