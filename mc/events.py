"""Send events to the hub, which streams them to the dashboard."""
import datetime as dt
import uuid

import httpx

from .config import hub_url
from .http import client


async def emit(type: str, message: str = "", *, actor: str | None = None, subject: str | None = None,
               mission_id: str | None = None, **data) -> None:
    event = {
        "id": uuid.uuid4().hex[:12],
        "ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds"),
        "type": type, "message": message, "actor": actor, "subject": subject,
        "mission_id": mission_id, "data": data,
    }
    try:
        await client().post(f"{hub_url()}/events", json=event, timeout=3)
    except (httpx.HTTPError, RuntimeError):
        pass  # the dashboard is optional; never break an agent because the hub is down
