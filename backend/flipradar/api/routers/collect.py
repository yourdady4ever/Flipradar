"""On-demand collection, collection run history and single-URL price checks."""

from __future__ import annotations

import asyncio
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...collectors import jsonld, netguard
from ...collectors.base import CollectorError
from ...config import Settings
from ...models import CollectionRun, Listing, Source
from ...schemas import CollectionRunOut, CollectRequest, CollectResult, UrlCheckIn, UrlCheckOut
from ...services import analysis, collection
from ...services.ingestion import ingest_raw_listings
from ...services.serialize import listing_out
from ..deps import MAX_PAGE_SIZE, get_db, get_settings

log = logging.getLogger(__name__)
router = APIRouter(tags=["collection"])

URL_CHECK_SOURCE = "web_jsonld"
# Security refusals get one generic message: the specific reason can contain the address an internal
# host name resolved to, which would turn this endpoint into a map of the owner's network.
UNSAFE_URL_MESSAGE = (
    "URL refused: it (or a redirect) points to a non-public or disallowed address. Only public http(s) "
    "sites on allowed ports, without user:password@, can be checked."
)
# "scheme://user:password@" - userinfo runs to the last "@" before the path, query or fragment.
_USERINFO_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/?#\s]*@")


def redact_userinfo(text: str) -> str:
    """``text`` with the ``user:password@`` part of every URL in it removed (for logging)."""
    return _USERINFO_RE.sub(r"\1", text)


@router.post("/collect", response_model=CollectResult)
async def collect(body: CollectRequest, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> CollectResult:
    keys = list(dict.fromkeys(k.strip() for k in body.source_keys if k and k.strip()))
    if keys:
        known = set(db.scalars(select(Source.key).where(Source.key.in_(keys))))
        unknown = [k for k in keys if k not in known]
        if unknown:
            raise HTTPException(422, f"unknown source(s): {', '.join(unknown)}")
    return await collection.run_collection(db, body.query, keys, body.limit, analyze=body.analyze, settings=settings)


@router.get("/runs", response_model=list[CollectionRunOut])
def list_runs(limit: int = Query(50, ge=1, le=MAX_PAGE_SIZE), db: Session = Depends(get_db)) -> list[CollectionRunOut]:
    rows = db.execute(
        select(CollectionRun, Source.key)
        .outerjoin(Source, Source.id == CollectionRun.source_id)
        .order_by(CollectionRun.started_at.desc(), CollectionRun.id.desc())
        .limit(limit)
    ).all()
    return [collection.run_out(run, key) for run, key in rows]


@router.post("/check-url", response_model=UrlCheckOut)
async def check_url(body: UrlCheckIn, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> UrlCheckOut:
    url = str(body.url)
    try:
        result = await jsonld.check_url(url, settings)
    except NotImplementedError as exc:
        raise HTTPException(503, "URL check is not available yet") from exc
    except netguard.TooManyRedirects as exc:  # not an unsafe address: say what happened
        return UrlCheckOut(url=url, allowed_by_robots=False, found=False, message=str(exc)[:500])
    except netguard.UnsafeUrlError as exc:
        # The details stay in the server log, without any user:password@ the URL carried.
        log.info("check-url refused %s: %s", redact_userinfo(url)[:200], redact_userinfo(str(exc)))
        raise HTTPException(400, UNSAFE_URL_MESSAGE) from exc
    except CollectorError as exc:
        return UrlCheckOut(url=url, allowed_by_robots=False, found=False, message=str(exc)[:500])
    out = UrlCheckOut(
        url=result.url,
        allowed_by_robots=result.allowed_by_robots,
        found=result.found,
        extracted=result.extracted or {},
        message=result.message or "",
    )
    if body.save and result.found and result.raw is not None:
        stats = await asyncio.to_thread(ingest_raw_listings, db, URL_CHECK_SOURCE, [result.raw], settings=settings)
        db.commit()
        if stats.product_ids_touched:
            await asyncio.to_thread(analysis.recompute, db, sorted(stats.product_ids_touched), settings=settings)
        if stats.listing_ids:
            listing = db.get(Listing, stats.listing_ids[-1])
            if listing is not None:
                db.refresh(listing)
                out.listing = listing_out(listing)
        if stats.errors:
            out.message = (out.message + " " if out.message else "") + "; ".join(stats.errors[:3])
    return out
