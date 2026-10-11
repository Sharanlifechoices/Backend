from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.core.rate_limit import rate_limit
from fastapi.concurrency import run_in_threadpool

from app.modules.auth.dependencies import CurrentUser, optional_user
from app.modules.catalog.recommend import recommend
from app.modules.orders.repo import OrdersRepo, get_orders_repo
from app.modules.catalog.repo import CatalogRepo, get_catalog_repo, public_view

router = APIRouter(tags=["catalog"])


@router.get("/catalog")
def list_catalog(repo: CatalogRepo = Depends(get_catalog_repo)):
    return {"items": [public_view(i) for i in repo.all_active()]}


@router.get("/catalog/{item_id}")
def get_item(item_id: str, repo: CatalogRepo = Depends(get_catalog_repo)):
    item = repo.get(item_id)
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Item not found")
    return public_view(item)


class RecommendationRequest(BaseModel):
    item_ids: list[str] = Field(default_factory=list, max_length=50)


@router.post("/cart/recommendations", dependencies=[Depends(rate_limit("recommendations", 60, 60))])
async def recommendations(
    body: RecommendationRequest, repo: CatalogRepo = Depends(get_catalog_repo),
    user: CurrentUser | None = Depends(optional_user), orders: OrdersRepo = Depends(get_orders_repo),
):
    """Works for guests too (the client sends the ids in its cart). Logged-in users never get books they already own."""
    owned = await run_in_threadpool(orders.owned_item_ids, user.id) if user else []
    picks = recommend(body.item_ids, await run_in_threadpool(repo.all_active), owned_ids=owned)
    return {"recommendations": [{"item": public_view(p["item"]), "reason": p["reason"]} for p in picks]}
