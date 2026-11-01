"""In-app notifications (newest first) and read state."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ...models import Notification
from ...schemas import Message, NotificationOut
from ...services.timeutil import as_utc, as_utc_opt, utcnow
from ..deps import MAX_PAGE_SIZE, get_db

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _out(note: Notification) -> NotificationOut:
    out = NotificationOut.model_validate(note)
    return out.model_copy(update={"created_at": as_utc(note.created_at), "read_at": as_utc_opt(note.read_at)})


@router.get("", response_model=list[NotificationOut])
def list_notifications(
    unread_only: bool = False,
    since_id: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
) -> list[NotificationOut]:
    """Notifications, newest first.

    Without ``since_id`` (or with 0) this is the newest page (the inbox).  With ``since_id > 0`` it is a
    forward cursor: the ``limit`` OLDEST notifications with ``id > since_id`` (still listed newest first),
    so a poller that passes the highest id it has seen as the next ``since_id`` never skips notifications
    when more than ``limit`` arrive between two polls.  A page shorter than ``limit`` means caught up.
    """
    stmt = select(Notification).where(Notification.id > since_id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    if since_id > 0:
        rows = list(reversed(db.scalars(stmt.order_by(Notification.id.asc()).limit(limit)).all()))
    else:
        rows = list(db.scalars(stmt.order_by(Notification.id.desc()).limit(limit)).all())
    return [_out(n) for n in rows]


@router.post("/read-all", response_model=Message)
def read_all(db: Session = Depends(get_db)) -> Message:
    result = db.execute(update(Notification).where(Notification.read_at.is_(None)).values(read_at=utcnow()))
    db.commit()
    return Message(message=f"{result.rowcount or 0} notification(s) marked as read")


@router.post("/{notification_id}/read", response_model=Message)
def read_one(notification_id: int, db: Session = Depends(get_db)) -> Message:
    note = db.get(Notification, notification_id)
    if note is None:
        raise HTTPException(404, f"notification {notification_id} not found")
    if note.read_at is None:
        note.read_at = utcnow()
        db.commit()
    return Message(message="marked as read")
