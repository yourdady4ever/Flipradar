"""CSV import of listings/prices you collected yourself (spreadsheets, notes, other tools).

Format (see docs/API_CONTRACT.md): header row required, case-insensitive columns ``title`` and ``price``
(pesos, e.g. ``12999.00`` or ``$12,999``) are required; optional ``currency`` (MXN), ``url``, ``condition``,
``shipping``, ``source`` (default ``manual``), ``external_id`` (default: hash of url, else of title),
``brand``, ``model``, ``gtin``, ``seller``, ``location``, ``sold_quantity``, ``available_quantity`` and
``observed_at`` (ISO date/datetime, UTC when no offset).  Max 5 MB and 20 000 rows.  ``,`` or ``;``
delimiters are accepted.  Rows with problems are rejected individually (the first 50 errors are
reported); the rest are imported.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..engine.types import RawListing
from ..models import Source, SourceKind
from ..money import pesos_to_cents
from ..schemas import ImportResult
from . import analysis
from .bootstrap import ensure_source
from .errors import Invalid
from .ingestion import DEMO_SOURCE_KEY, ingest_raw_listings
from .timeutil import as_utc, utcnow

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 20_000
MAX_REPORTED_ERRORS = 50
FUTURE_TOLERANCE = timedelta(minutes=10)
KNOWN_COLUMNS = {
    "title", "price", "currency", "url", "condition", "shipping", "source", "external_id", "brand", "model",
    "gtin", "seller", "location", "sold_quantity", "available_quantity", "observed_at",
}
_SOURCE_KEY_RE = re.compile(r"^[a-z0-9_]{2,64}$")


class CsvImportError(Invalid):
    """The file as a whole cannot be imported (422)."""


class CsvTooLarge(CsvImportError):
    """File larger than MAX_BYTES or more than MAX_ROWS rows (413)."""


def _cell(row: dict[str, Any], key: str) -> str | None:
    value = row.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int_cell(row: dict[str, Any], key: str) -> int | None:
    text = _cell(row, key)
    if text is None:
        return None
    try:
        value = int(text.replace(",", ""))
    except ValueError as exc:
        raise ValueError(f"{key} must be a whole number") from exc
    if value < 0:
        raise ValueError(f"{key} cannot be negative")
    return value


def _money_cell(row: dict[str, Any], key: str) -> int | None:
    text = _cell(row, key)
    if text is None:
        return None
    try:
        cents = pesos_to_cents(text)
    except (ValueError, ArithmeticError) as exc:
        raise ValueError(f"{key} is not a valid amount: {text[:30]!r}") from exc
    return cents


def _parse_observed_at(text: str | None, now: datetime) -> datetime | None:
    """ISO date (noon UTC, or now for today) or ISO datetime (UTC when no offset)."""
    if text is None:
        return None
    raw = text.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        if len(raw) == 10:
            d = date.fromisoformat(raw)
            return min(datetime(d.year, d.month, d.day, 12, 0, tzinfo=UTC), now)
        return as_utc(datetime.fromisoformat(raw))
    except ValueError as exc:
        raise ValueError(f"observed_at is not an ISO date: {text[:30]!r}") from exc


def _external_id(url: str | None, title: str) -> str:
    basis = url if url else title.lower()
    return "csv-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]


def _detect_delimiter(header_line: str) -> str:
    return ";" if header_line.count(";") > header_line.count(",") else ","


def parse_csv(data: bytes, *, default_source: str = "manual", now: datetime | None = None) -> tuple[list[tuple[str, RawListing]], int, list[str]]:
    """Parse CSV bytes into (source_key, RawListing) pairs.  Returns (rows, total_rows, errors)."""
    if len(data) > MAX_BYTES:
        raise CsvTooLarge(f"file is larger than {MAX_BYTES // (1024 * 1024)} MB")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CsvImportError("file is not valid UTF-8; export the spreadsheet as 'CSV UTF-8'") from exc
    if not text.strip():
        raise CsvImportError("file is empty")
    first_line = text.splitlines()[0] if text.splitlines() else ""
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=_detect_delimiter(first_line))
    if not reader.fieldnames:
        raise CsvImportError("missing header row")
    normalized = [(name or "").strip().lower() for name in reader.fieldnames]
    if "title" not in normalized or "price" not in normalized:
        raise CsvImportError("header must contain at least 'title' and 'price' columns")
    reader.fieldnames = normalized

    rows: list[tuple[str, RawListing]] = []
    errors: list[str] = []
    total = 0
    now = as_utc(now or utcnow())
    for record in reader:
        total += 1
        if total > MAX_ROWS:
            raise CsvTooLarge(f"more than {MAX_ROWS} rows")
        line = reader.line_num
        try:
            if None in record:  # more cells than headers
                raise ValueError("row has more columns than the header")
            title = _cell(record, "title")
            if not title:
                raise ValueError("title is required")
            if len(title) > 500:
                raise ValueError("title longer than 500 characters")
            price = _money_cell(record, "price")
            if price is None:
                raise ValueError("price is required")
            if price <= 0:
                raise ValueError("price must be greater than zero")
            currency = (_cell(record, "currency") or "MXN").upper()
            if not re.fullmatch(r"[A-Z]{3}", currency):
                raise ValueError(f"invalid currency {currency[:10]!r}")
            shipping = _money_cell(record, "shipping")
            if shipping is not None and shipping < 0:
                raise ValueError("shipping cannot be negative")
            source = (_cell(record, "source") or default_source).lower()
            if not _SOURCE_KEY_RE.match(source):
                raise ValueError("source must match [a-z0-9_]{2,64}")
            if source == DEMO_SOURCE_KEY:
                raise ValueError("the demo source is reserved for synthetic data")
            url = _cell(record, "url")
            external_id = _cell(record, "external_id") or _external_id(url, title)
            if len(external_id) > 128:
                raise ValueError("external_id longer than 128 characters")
            observed_at = _parse_observed_at(_cell(record, "observed_at"), now)
            if observed_at is not None and observed_at > now + FUTURE_TOLERANCE:
                raise ValueError("observed_at is in the future")
            seller = _cell(record, "seller")
            raw = RawListing(
                source_key=source,
                external_id=external_id,
                title=title,
                price_cents=price,
                currency=currency,
                url=url,
                condition_raw=_cell(record, "condition"),
                shipping_cents=shipping,
                free_shipping=(shipping == 0) if shipping is not None else None,
                brand=_cell(record, "brand"),
                model=_cell(record, "model"),
                gtin=_cell(record, "gtin"),
                seller_id=seller[:64] if seller else None,
                seller_name=seller,
                location=_cell(record, "location"),
                available_quantity=_int_cell(record, "available_quantity"),
                sold_quantity=_int_cell(record, "sold_quantity"),
                observed_at=observed_at,
                raw={"csv_line": line, **{k: v for k, v in record.items() if k in KNOWN_COLUMNS and v}},
            )
            rows.append((source, raw))
        except ValueError as exc:
            errors.append(f"line {line}: {exc}")
    return rows, total, errors


def import_csv(
    session: Session,
    data: bytes,
    *,
    default_source: str = "manual",
    settings: Settings | None = None,
    now: datetime | None = None,
    analyze: bool = True,
) -> ImportResult:
    """Parse, ingest and (optionally) recompute.  Commits.  Raises CsvImportError / CsvTooLarge."""
    default_source = (default_source or "manual").strip().lower()
    if not _SOURCE_KEY_RE.match(default_source) or default_source == DEMO_SOURCE_KEY:
        raise CsvImportError("invalid default source key")
    rows, total, errors = parse_csv(data, default_source=default_source, now=now)
    by_source: dict[str, list[RawListing]] = defaultdict(list)
    for source_key, raw in rows:
        by_source[source_key].append(raw)

    imported = updated = rejected = 0
    rejected += len(errors)
    touched: set[int] = set()
    for source_key, raws in by_source.items():
        existing = session.scalars(select(Source).where(Source.key == source_key)).first()
        if existing is None:
            ensure_source(session, source_key, name=f"{source_key} (imported)", kind=SourceKind.MANUAL.value)
        elif existing.kind == SourceKind.DEMO.value:
            rejected += len(raws)
            errors.append(f"source '{source_key}' is reserved")
            continue
        stats = ingest_raw_listings(session, source_key, raws, now=now, settings=settings)
        imported += stats.new
        updated += stats.updated
        rejected += stats.skipped
        errors.extend(stats.errors)
        touched |= stats.product_ids_touched
    session.commit()
    if analyze and touched:
        analysis.recompute(session, sorted(touched), now=now, settings=settings)
    return ImportResult(
        rows_total=total,
        rows_imported=imported,
        rows_updated=updated,
        rows_rejected=rejected,
        errors=errors[:MAX_REPORTED_ERRORS],
    )
