"""Dashboard endpoints for the daily HTS tariff sync."""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.tariffs.scheduler import controller, recent_changes

router = APIRouter(prefix="/api/tariffs", tags=["tariffs"])


class SyncRequest(BaseModel):
    # Omit for a full sync (~3 min); e.g. [85, 99] for a quick demo sync.
    chapters: list[int] | None = Field(default=None, max_length=99)


@router.get("/status")
def status():
    return controller.status()


@router.get("/changes")
def changes(days: int = Query(30, ge=1, le=365), limit: int = Query(25, ge=1, le=200)):
    return {"changes": recent_changes(days, limit)}


@router.post("/sync")
async def sync(body: SyncRequest | None = None):
    chapters = body.chapters if body else None
    if chapters is not None and (not chapters or any(c < 1 or c > 99 or c == 77 for c in chapters)):
        raise HTTPException(422, "chapters must be HTS chapters 1-99 (77 is reserved)")
    if not controller.trigger(sorted(set(chapters)) if chapters else None, "manual"):
        raise HTTPException(409, "A tariff sync is already running")
    return controller.status()
