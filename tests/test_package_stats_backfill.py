import json
import logging
import zipfile

from server.app.db import Database
from server.app.main import create_app
from server.app.services.package_stats_backfill import backfill_package_stats
from server.app.settings import load_settings
from tests.postgres_support import TEST_DATABASE_URL


def _make_package_zip(path, video_ids):
    manifest = {"created_at": "2026-06-03T00:00:00+00:00", "videos": [{"id": v} for v in video_ids]}
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))


def test_backfill_fills_missing_stats_from_manifest(db, settings):
    zip_path = settings.packages_dir / "legacy.zip"
    _make_package_zip(zip_path, ["v1", "v2"])
    db.insert_package("packages/legacy.zip")

    updated = backfill_package_stats(db, settings)

    assert updated == 1
    row = db.list_packages(limit=1)[0]
    assert row["name"] == "批次 (2个视频)"
    assert row["video_count"] == 2
    assert row["size_bytes"] == zip_path.stat().st_size


def test_backfill_preserves_existing_name(db, settings):
    _make_package_zip(settings.packages_dir / "named.zip", ["v1"])
    db.insert_package("packages/named.zip", name="我的批次")

    updated = backfill_package_stats(db, settings)

    assert updated == 1
    row = db.list_packages(limit=1)[0]
    assert row["name"] == "我的批次"
    assert row["video_count"] == 1


def test_backfill_skips_rows_with_stats(db, settings):
    db.insert_package("packages/complete.zip", name="完整", video_count=3, size_bytes=100)

    assert backfill_package_stats(db, settings) == 0
    row = db.list_packages(limit=1)[0]
    assert (row["name"], row["video_count"], row["size_bytes"]) == ("完整", 3, 100)


def test_backfill_skips_missing_zip_and_logs_warning(db, settings, caplog):
    db.insert_package("packages/missing.zip")

    with caplog.at_level(logging.WARNING, logger="server.app.services.package_stats_backfill"):
        updated = backfill_package_stats(db, settings)

    assert updated == 0
    assert any("Cannot backfill stats" in record.getMessage() for record in caplog.records)
    row = db.list_packages(limit=1)[0]
    assert (row["name"], row["video_count"], row["size_bytes"]) == ("", 0, 0)


def test_backfill_skips_escaping_path_and_logs_warning(db, settings, caplog):
    db.insert_package("../escaped.zip")

    with caplog.at_level(logging.WARNING, logger="server.app.services.package_stats_backfill"):
        updated = backfill_package_stats(db, settings)

    assert updated == 0
    assert any("Skip package stats backfill" in record.getMessage() for record in caplog.records)
    row = db.list_packages(limit=1)[0]
    assert (row["name"], row["video_count"]) == ("", 0)


def test_create_app_backfills_legacy_package_stats_at_startup(tmp_path):
    settings = load_settings(data_dir=tmp_path)
    _make_package_zip(settings.packages_dir / "legacy.zip", ["v1", "v2", "v3"])
    db = Database(TEST_DATABASE_URL)
    db.insert_package("packages/legacy.zip")

    create_app(data_dir=tmp_path, start_worker=False)

    row = db.list_packages(limit=1)[0]
    assert row["name"] == "批次 (3个视频)"
    assert row["video_count"] == 3
    assert row["size_bytes"] > 0
