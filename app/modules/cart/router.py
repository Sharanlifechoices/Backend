"""Server-side cart for logged-in users. Guests keep their cart in the browser and merge it on login."""

from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.core.scheduling import validate_preferred_slot
from app.modules.auth.dependencies import CurrentUser, get_current_user
from app.modules.cart.repo import CartRepo, get_cart_repo
from app.modules.cart.rules import check_line
from app.modules.catalog.repo import CatalogRepo, get_catalog_repo, public_view

router = APIRouter(prefix="/cart", tags=["cart"])


async def warm_catalog(catalog: CatalogRepo = Depends(get_catalog_repo)) -> CatalogRepo:
    """Refresh the catalog cache in a worker thread so the (sync) Supabase fetch never blocks the loop."""
    await run_in_threadpool(catalog.all_active)
    return catalog


class CartLineIn(BaseModel):
    item_id: str = Field(min_length=1, max_length=64)
    preferred_date: Optional[date] = None
    preferred_time: Optional[str] = Field(default=None, max_length=16)


class ScheduleUpdate(BaseModel):
    preferred_date: date
    preferred_time: str = Field(max_length=16)


class MergeRequest(BaseModel):
    items: list[CartLineIn] = Field(max_length=50)


def _check_line(item: Optional[dict], line: CartLineIn) -> None:
    """Raise ValueError (user-facing) if this line cannot be added to a cart."""
    check_line(item, line.preferred_date, line.preferred_time)


def _cart_view(lines: list[dict], catalog: CatalogRepo) -> dict:
    items, total = [], 0
    for line in lines:
        item = catalog.get(line["item_id"])
        if item is None:  # item deactivated after being added; hide it rather than fail the cart
            continue
        total += item["price_minor"]
        items.append(
            {
                "id": line["id"],
                **{k: v for k, v in public_view(item).items() if k in ("type", "title", "subtitle", "cover", "route", "price_minor", "currency", "requires_schedule")},
                "item_id": item["id"],
                "preferred_date": line.get("preferred_date"),
                "preferred_time": line.get("preferred_time"),
            }
        )
    currency = items[0]["currency"] if items else "SGD"
    return {"items": items, "total_minor": total, "currency": currency}


@router.get("")
async def get_cart(
    user: CurrentUser = Depends(get_current_user), repo: CartRepo = Depends(get_cart_repo),
    catalog: CatalogRepo = Depends(warm_catalog),
):
    return _cart_view(await run_in_threadpool(repo.lines, user.id), catalog)


@router.post("/items", status_code=201)
async def add_item(
    body: CartLineIn, user: CurrentUser = Depends(get_current_user), repo: CartRepo = Depends(get_cart_repo),
    catalog: CatalogRepo = Depends(warm_catalog),
):
    try:
        _check_line(catalog.get(body.item_id), body)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
    await run_in_threadpool(repo.add_line, user.id, body.item_id, body.preferred_date, body.preferred_time)
    return _cart_view(await run_in_threadpool(repo.lines, user.id), catalog)


@router.patch("/items/{line_id}")
async def update_item(
    line_id: str, body: ScheduleUpdate, user: CurrentUser = Depends(get_current_user),
    repo: CartRepo = Depends(get_cart_repo), catalog: CatalogRepo = Depends(warm_catalog),
):
    line = await run_in_threadpool(repo.get_line, user.id, line_id)
    item = catalog.get(line["item_id"]) if line else None
    if not line or not item or not item["requires_schedule"]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Cart item not found")
    try:
        validate_preferred_slot(body.preferred_date, body.preferred_time)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
    result = await run_in_threadpool(repo.update_line, user.id, line_id, body.preferred_date, body.preferred_time)
    if result and result.get("conflict"):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="You already have this session in your cart at that time")
    return _cart_view(await run_in_threadpool(repo.lines, user.id), catalog)


@router.delete("/items/{line_id}")
async def remove_item(
    line_id: str, user: CurrentUser = Depends(get_current_user), repo: CartRepo = Depends(get_cart_repo),
    catalog: CatalogRepo = Depends(warm_catalog),
):
    if not await run_in_threadpool(repo.remove_line, user.id, line_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Cart item not found")
    return _cart_view(await run_in_threadpool(repo.lines, user.id), catalog)


@router.delete("")
async def clear_cart(user: CurrentUser = Depends(get_current_user), repo: CartRepo = Depends(get_cart_repo)):
    await run_in_threadpool(repo.clear, user.id)
    return {"items": [], "total_minor": 0, "currency": "SGD"}


@router.post("/merge")
async def merge_guest_cart(
    body: MergeRequest, user: CurrentUser = Depends(get_current_user), repo: CartRepo = Depends(get_cart_repo),
    catalog: CatalogRepo = Depends(warm_catalog),
):
    """Idempotent merge of a guest cart after login. Invalid lines are skipped and reported,
    never fatal, so one stale item cannot lose the rest of the cart."""
    skipped = []
    for line in body.items:
        try:
            _check_line(catalog.get(line.item_id), line)
        except ValueError as exc:
            skipped.append({"item_id": line.item_id, "reason": str(exc)})
            continue
        await run_in_threadpool(repo.add_line, user.id, line.item_id, line.preferred_date, line.preferred_time)
    return {**_cart_view(await run_in_threadpool(repo.lines, user.id), catalog), "skipped": skipped}
