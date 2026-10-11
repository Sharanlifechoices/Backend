"""Cart-based recommendations. Pure function: no I/O, fully unit-testable.

Scoring (higher is better):
  +3  the item is curated as related to something in the cart (`related_ids`)
  +1  per tag shared with items in the cart
  +1  it is a service and the cart has no service yet (nudge towards a session)
Ties break by catalog `sort_order`. Items already in the cart, already owned (books), inactive,
or the retreat enquiry card are handled as documented below.
"""

from __future__ import annotations

from typing import Iterable

_PEOPLE_ALSO_LIKED = "people_also_liked"
_EXPLORE_WITH_SHARAN = "explore_with_sharan"


def recommend(
    cart_item_ids: Iterable[str],
    catalog: list[dict],
    owned_ids: Iterable[str] = (),
    limit: int = 4,
) -> list[dict]:
    """Return up to `limit` entries shaped {"item": <catalog item>, "reason": <label key>}."""
    in_cart = set(cart_item_ids)
    owned = set(owned_ids)
    by_id = {i["id"]: i for i in catalog}
    cart_items = [by_id[i] for i in in_cart if i in by_id]

    related: set[str] = {rid for it in cart_items for rid in it.get("related_ids", [])}
    cart_tags: set[str] = {t for it in cart_items for t in it.get("tags", []) if t not in ("book", "service")}
    cart_has_service = any(it["type"] == "service" for it in cart_items)

    scored: list[tuple[float, int, dict, str]] = []
    for item in catalog:
        if item["id"] in in_cart:
            continue
        if item["type"] == "book" and item["id"] in owned:
            continue
        score = 0.0
        directly_related = item["id"] in related
        if directly_related:
            score += 3
        score += len(cart_tags & set(item.get("tags", [])))
        if item["type"] == "service" and not cart_has_service:
            score += 1
        if score == 0 and cart_items:
            continue  # with a non-empty cart, only suggest things that actually relate
        reason = _PEOPLE_ALSO_LIKED if directly_related and item["type"] == "book" else _EXPLORE_WITH_SHARAN
        scored.append((-score, item.get("sort_order", 0), item, reason))

    scored.sort(key=lambda t: (t[0], t[1]))
    return [{"item": item, "reason": reason} for _, _, item, reason in scored[:limit]]
