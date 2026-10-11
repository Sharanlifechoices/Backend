from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.core.rate_limit import rate_limit
from app.modules.catalog.recommend import recommend
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
def recommendations(body: RecommendationRequest, repo: CatalogRepo = Depends(get_catalog_repo)):
    """Works for guests too (the client sends the ids in its cart)."""
    picks = recommend(body.item_ids, repo.all_active())
    return {"recommendations": [{"item": public_view(p["item"]), "reason": p["reason"]} for p in picks]}
