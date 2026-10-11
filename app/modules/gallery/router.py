"""Public gallery / retreat images (managed from the admin panel)."""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool

from app.core.rate_limit import rate_limit
from app.modules.admin.repo import AdminRepo, get_admin_repo

router = APIRouter(tags=["gallery"])


@router.get("/gallery", dependencies=[Depends(rate_limit("gallery", 120, 60))])
async def gallery(type: Optional[Literal["gallery", "retreat"]] = None, repo: AdminRepo = Depends(get_admin_repo)):
    items = await run_in_threadpool(repo.list_gallery, type)
    return {"items": [{"id": i["id"], "url": i["url"], "title": i["title"], "type": i["type"]} for i in items]}
