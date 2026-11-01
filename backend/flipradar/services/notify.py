"""Optional push channels (all free / self-hostable): ntfy, Telegram Bot API and a generic webhook.

Each sender returns a short status string recorded in ``Notification.delivery_json``:
``sent`` | ``not_configured`` | ``error: <reason>``.  Error reasons never include URLs or credentials
(the Telegram URL embeds the bot token).  Nothing here raises.

Secrets in URLs (the Telegram bot token, the webhook URL, the ntfy topic) are also kept out of the
logs: httpx logs every request URL at INFO, so a filter on the ``httpx`` logger replaces every URL
this module posts to with a redacted form before any handler sees the record.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterable
from email.header import Header
from typing import Any
from urllib.parse import quote

import httpx

from ..config import Settings
from ..models import Notification

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10.0
CHANNELS = ("in_app", "ntfy", "telegram", "webhook")
REDACTED = "***"
# Characters that may stay as they are in a Click URL (RFC 3986 reserved + unreserved + existing escapes).
_URL_SAFE = ":/?#[]@!$&'()*+,;=%-._~"


class _SecretUrlFilter(logging.Filter):
    """Replaces registered secret substrings in log records (installed on the ``httpx`` logger)."""

    def __init__(self) -> None:
        super().__init__()
        self._secrets: dict[str, str] = {}
        self._lock = threading.Lock()

    def register(self, secret: str | None, replacement: str) -> None:
        if secret and len(secret) >= 4 and secret != replacement:
            with self._lock:
                self._secrets[secret] = replacement

    def filter(self, record: logging.LogRecord) -> bool:
        with self._lock:
            secrets = sorted(self._secrets.items(), key=lambda item: -len(item[0]))
        if not secrets:
            return True
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - a broken record is not ours to fix
            return True
        redacted = message
        for secret, replacement in secrets:
            redacted = redacted.replace(secret, replacement)
        if redacted != message:
            record.msg, record.args = redacted, None
        return True


_LOG_REDACTOR = _SecretUrlFilter()
logging.getLogger("httpx").addFilter(_LOG_REDACTOR)


def _register_secret_url(url: str, redacted: str) -> None:
    """Register ``url`` (as configured and as httpx prints it) so it never appears in logs."""
    _LOG_REDACTOR.register(url, redacted)
    try:
        _LOG_REDACTOR.register(str(httpx.URL(url)), redacted)
    except (httpx.InvalidURL, TypeError, ValueError):
        pass


def _origin(url: str) -> str:
    try:
        parsed = httpx.URL(url)
        return f"{parsed.scheme}://{parsed.netloc.decode('ascii', 'replace')}" if parsed.host else REDACTED
    except (httpx.InvalidURL, TypeError, ValueError):
        return REDACTED


def _header_value(value: str) -> str:
    """HTTP header values must be ASCII; non-ASCII text is RFC 2047 encoded (ntfy decodes it).

    The value is kept on ONE line: ``Header.encode()`` folds long text into several encoded-words joined
    by a line break, which is illegal in an HTTP header (h11 refuses to send the request).  The words are
    re-joined with single spaces instead; RFC 2047 decoders ignore whitespace between encoded-words."""
    value = " ".join(value.split())
    try:
        value.encode("ascii")
        return value
    except UnicodeEncodeError:
        return " ".join(Header(value, "utf-8").encode().split())


def _url_header_value(url: str) -> str:
    """A URL as an ASCII header value: non-ASCII characters percent-encoded (UTF-8), no whitespace."""
    return quote("".join(url.split()), safe=_URL_SAFE)


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
    base = settings.ntfy_url.rstrip("/")
    url = f"{base}/{settings.ntfy_topic.strip('/')}"
    _register_secret_url(url, f"{base}/{REDACTED}")  # on ntfy.sh the topic name is the password
    headers = {"Title": _header_value(title[:250]), "Priority": priority}
    tag_list = [t for t in tags if t]
    if tag_list:
        headers["Tags"] = _header_value(",".join(tag_list))
    if click_url:
        headers["Click"] = _url_header_value(click_url)
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
    _register_secret_url(url, f"https://api.telegram.org/bot{REDACTED}/sendMessage")
    _LOG_REDACTOR.register(settings.telegram_bot_token, REDACTED)
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
    _register_secret_url(settings.webhook_url, f"{_origin(settings.webhook_url)}/{REDACTED}")
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
