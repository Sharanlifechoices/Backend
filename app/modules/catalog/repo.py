"""Catalog access with a short in-memory cache (the catalog is small and changes rarely)."""

from __future__ import annotations

import time
from typing import Optional

from app.core.supabase_client import get_service_client

_TTL_SECONDS = 300
# pdf_url is private (emailed after purchase); it never appears in public views.
PRIVATE_FIELDS = ("pdf_url",)


def public_view(item: dict) -> dict:
    return {k: v for k, v in item.items() if k not in PRIVATE_FIELDS}


class CatalogRepo:
    def __init__(self) -> None:
        self._cache: list[dict] | None = None
        self._loaded_at = 0.0

    def all_active(self) -> list[dict]:
        if self._cache is None or time.monotonic() - self._loaded_at > _TTL_SECONDS:
            rows = (
                get_service_client().table("catalog_items").select("*").eq("active", True)
                .order("sort_order").execute().data
            )
            self._cache, self._loaded_at = rows, time.monotonic()
        return self._cache

    def get(self, item_id: str) -> Optional[dict]:
        return next((i for i in self.all_active() if i["id"] == item_id), None)

    def invalidate(self) -> None:
        self._cache = None


_repo = CatalogRepo()


def get_catalog_repo() -> CatalogRepo:
    return _repo
