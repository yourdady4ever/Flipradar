"""Data-source connectors ("collectors").

Built-in collectors (registered lazily by :mod:`.registry`):

* ``mercadolibre`` - Mercado Libre México catalog API (:mod:`.mercadolibre`)
* ``ebay`` - eBay Browse API, US items that ship to Mexico (:mod:`.ebay`)
* ``rss_deals`` - operator-configured RSS/Atom deal feeds (:mod:`.rss`)

Non-collector integrations: :mod:`.fx_sources` / :mod:`.banxico` (USD/MXN rate) and :mod:`.jsonld`
(user-initiated single-URL check, SSRF-guarded by :mod:`.netguard`).
"""

from .base import Collector, CollectorError, CollectorInfo, CollectorNotConfigured, HealthStatus, RateLimiter
from .registry import collector_classes, get_collector, register, unregister

__all__ = [
    "Collector",
    "CollectorError",
    "CollectorInfo",
    "CollectorNotConfigured",
    "HealthStatus",
    "RateLimiter",
    "collector_classes",
    "get_collector",
    "register",
    "unregister",
]
