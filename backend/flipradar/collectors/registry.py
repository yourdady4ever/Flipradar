"""Collector registry.  OWNER: collectors agent (keep the public functions stable).

Services look collectors up by key; tests may register fakes with :func:`register`.
"""

from __future__ import annotations

import importlib
import logging

import httpx

from ..config import Settings, get_settings
from .base import Collector, CollectorError

log = logging.getLogger(__name__)

_REGISTRY: dict[str, type[Collector]] = {}

# Modules that define built-in collectors (imported lazily so a broken optional module never takes the
# whole application down).
BUILTIN_MODULES = (
    "flipradar.collectors.mercadolibre",
    "flipradar.collectors.ebay",
    "flipradar.collectors.rss",
)


def register(cls: type[Collector]) -> type[Collector]:
    _REGISTRY[cls.info.key] = cls
    return cls


def unregister(key: str) -> None:
    _REGISTRY.pop(key, None)


def _load_builtins() -> None:
    for module in BUILTIN_MODULES:
        try:
            importlib.import_module(module)
        except ImportError as exc:  # pragma: no cover - only when a module is missing
            log.debug("collector module %s not available: %s", module, exc)


def collector_classes() -> dict[str, type[Collector]]:
    _load_builtins()
    return dict(_REGISTRY)


def get_collector(key: str, settings: Settings | None = None, client: httpx.AsyncClient | None = None) -> Collector:
    classes = collector_classes()
    if key not in classes:
        raise CollectorError(f"unknown source '{key}'")
    return classes[key](settings or get_settings(), client)
