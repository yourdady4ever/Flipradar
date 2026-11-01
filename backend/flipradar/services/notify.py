"""Optional push channels (all free / self-hostable): ntfy, Telegram Bot API and a generic webhook.

Each sender returns a short status string recorded in ``Notification.delivery_json``:
``sent`` | ``not_configured`` | ``error: <reason>``.  Error reasons never include URLs or credentials
(the Telegram URL embeds the bot token).  Nothing here raises.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from email.header import Header
from typing import Any

import httpx

from ..config import Settings
from ..models import Notification

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10.0
CHANNELS = ("in_app", "ntfy", "telegram", "webhook")


def _header_value(value: str) -> str:
    """HTTP header values must be ASCII; non-ASCII text is RFC 2047 encoded (ntfy decodes it)."""
    value = " ".join(value.split())
    try:
        value.encode("ascii")
        return value
    except UnicodeEncodeError:
        return Header(value, "utf-8").encode()


def _error(exc: Exception | None = None, status: int | None = None) -> str:
    if status is not None:
        return f"error: HTTP {status}"
    return f"error: {type(exc).__name__}" if exc is not None else "error"


def _client(client: httpx.Client | None, settings: Settings) -> tuple[httpx.Client, bool]:
    if client is not None:
        return client, False
    return httpx.Client(timeout=TIMEOUT_SECONDS, headers={"User-Agent": settings.user_agent}), True


def send_ntfy(
    title: str,
    body: str,
    settings: Settings,
    *,
    priority: str = "default",
    tags: Iterable[str] = (),
    click_url: str | None = None,
    client: httpx.Client | None = None,
) -> str:
    if not settings.ntfy_url or not settings.ntfy_topic:
        return "not_configured"
    url = f"{settings.ntfy_url.rstrip('/')}/{settings.ntfy_topic.strip('/')}"
    headers = {"Title": _header_value(title[:250]), "Priority": priority}
    tag_list = [t for t in tags if t]
    if tag_list:
        headers["Tags"] = _header_value(",".join(tag_list))
    if click_url:
        headers["Click"] = click_url
    if settings.ntfy_token:
        headers["Authorization"] = f"Bearer {settings.ntfy_token}"
    http, owned = _client(client, settings)
    try:
        response = http.post(url, content=body.encode("utf-8"), headers=headers, timeout=TIMEOUT_SECONDS)
        if response.status_code >= 400:
            return _error(status=response.status_code)
        return "sent"
    except Exception as exc:  # network errors, invalid URL...
        log.warning("ntfy delivery failed: %s", type(exc).__name__)
        return _error(exc)
    finally:
        if owned:
            http.close()


def send_telegram(text: str, settings: Settings, *, client: httpx.Client | None = None) -> str:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return "not_configured"
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    payload = {"chat_id": settings.telegram_chat_id, "text": text[:4000], "disable_web_page_preview": True}
    http, owned = _client(client, settings)
    try:
        response = http.post(url, json=payload, timeout=TIMEOUT_SECONDS)
        if response.status_code >= 400:
            return _error(status=response.status_code)
        return "sent"
    except Exception as exc:
        log.warning("telegram delivery failed: %s", type(exc).__name__)  # never log the URL (token)
        return _error(exc)
    finally:
        if owned:
            http.close()


def send_webhook(payload: dict[str, Any], settings: Settings, *, client: httpx.Client | None = None) -> str:
    if not settings.webhook_url:
        return "not_configured"
    http, owned = _client(client, settings)
    try:
        response = http.post(settings.webhook_url, json=payload, timeout=TIMEOUT_SECONDS)
        if response.status_code >= 400:
            return _error(status=response.status_code)
        return "sent"
    except Exception as exc:
        log.warning("webhook delivery failed: %s", type(exc).__name__)
        return _error(exc)
    finally:
        if owned:
            http.close()


def dispatch(
    notification: Notification,
    channels: Iterable[str],
    settings: Settings,
    *,
    click_url: str | None = None,
    client: httpx.Client | None = None,
) -> dict[str, str]:
    """Deliver one notification to ``channels``.  Returns {channel: status}; never raises."""
    results: dict[str, str] = {}
    for channel in dict.fromkeys(channels):  # de-duplicate, keep order
        try:
            if channel == "in_app":
                results[channel] = "stored"
            elif channel == "ntfy":
                results[channel] = send_ntfy(
                    notification.title, notification.body, settings,
                    priority="high" if notification.kind == "opportunity" else "default",
                    tags=("moneybag",), click_url=click_url, client=client,
                )
            elif channel == "telegram":
                text = f"{notification.title}\n{notification.body}"
                if click_url:
                    text += f"\n{click_url}"
                results[channel] = send_telegram(text, settings, client=client)
            elif channel == "webhook":
                payload = {
                    "notification_id": notification.id,
                    "kind": notification.kind,
                    "title": notification.title,
                    "body": notification.body,
                    "opportunity_id": notification.opportunity_id,
                    "rule_id": notification.rule_id,
                    "created_at": notification.created_at.isoformat() if notification.created_at else None,
                    "url": click_url,
                }
                results[channel] = send_webhook(payload, settings, client=client)
            else:
                results[channel] = "unknown_channel"
        except Exception as exc:  # pragma: no cover - senders already catch
            log.exception("delivery to %s failed", channel)
            results[channel] = _error(exc)
    return results
