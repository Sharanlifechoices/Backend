import zipfile

import pytest

from app.tools import upload_material as um


def test_upload_returns_a_public_url_under_an_unguessable_prefix():
    import httpx

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json={"Key": "ok"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        url = um.upload_file(c, "https://x.supabase.co", {"Authorization": "Bearer k"}, "Set.zip", b"zip")
    assert url.startswith("https://x.supabase.co/storage/v1/object/public/products/") and url.endswith("/Set.zip")
    assert seen["path"].startswith("/storage/v1/object/products/") and seen["path"].endswith("/Set.zip")
    assert len(url.split("/")[-2]) >= 12  # random prefix


def test_oversize_and_failed_uploads_raise():
    import httpx

    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(413, json={"message": "too big"}))) as c:
        with pytest.raises(RuntimeError, match="too big"):
            um.upload_file(c, "https://x", {}, "a.zip", b"zip")
        with pytest.raises(RuntimeError, match="50 MB"):
            um.upload_file(c, "https://x", {}, "a.zip", b"0" * (um.MAX_FILE_BYTES + 1))


def test_book_zip_keeps_only_requested_members_flattened(tmp_path):
    src = tmp_path / "kit.zip"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("Kit/1 Books/main.pdf", b"main")
        z.writestr("Kit/1 Books/booklet.pdf", b"booklet")
        z.writestr("Kit/2 Ads/ad.png", b"png")
    out = um.build_book_zip(src, ("1 Books/main.pdf", "1 Books/booklet.pdf"))
    dst = tmp_path / "out.zip"
    dst.write_bytes(out)
    with zipfile.ZipFile(dst) as z:
        assert sorted(z.namelist()) == ["booklet.pdf", "main.pdf"] and z.read("main.pdf") == b"main"


def test_missing_member_is_an_error_not_a_silent_skip(tmp_path):
    src = tmp_path / "kit.zip"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("a.pdf", b"x")
    with pytest.raises(FileNotFoundError):
        um.build_book_zip(src, ("nope.pdf",))


def test_migration_publishes_only_uploaded_items_and_escapes_quotes():
    sql = um.build_migration({"course-a": "https://cdn/it's.zip"}, ["course-b"])
    assert "set pdf_url = 'https://cdn/it''s.zip', active = true where id = 'course-a';" in sql
    assert "-- NOT UPLOADED: course-b stays inactive" in sql and "update public.catalog_items set pdf_url = '' " not in sql


def test_every_product_maps_to_a_catalog_row_in_migration_0008():
    from pathlib import Path

    sql = (Path(__file__).resolve().parents[1] / "supabase" / "migrations" / "0008_courses_and_more_books.sql").read_text(encoding="utf-8")
    for p in um.PRODUCTS:
        assert f"('{p.item_id}'," in sql, p.item_id
