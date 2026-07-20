"""Tests for the rsod_uploads attachment-cache cleanup."""

import os
import time

import app.agent.upload_cache_cleaner as cleaner
from app.agent.upload_cache_cleaner import cleanup_upload_cache


def _touch(path: str, age_seconds: float) -> None:
    """Create a file whose mtime is ``age_seconds`` in the past."""
    with open(path, "wb") as fh:
        fh.write(b"x")
    stamp = time.time() - age_seconds
    os.utime(path, (stamp, stamp))


def test_removes_only_files_older_than_retention(tmp_path, monkeypatch):
    monkeypatch.setattr(cleaner, "UPLOAD_CACHE_DIR", str(tmp_path))

    old = os.path.join(tmp_path, "old.jpg")
    fresh = os.path.join(tmp_path, "fresh.jpg")
    _touch(old, age_seconds=10_000)
    _touch(fresh, age_seconds=100)

    removed = cleanup_upload_cache(retention_seconds=5_000)

    assert removed == 1
    assert not os.path.exists(old)
    assert os.path.exists(fresh)


def test_missing_directory_is_noop(tmp_path, monkeypatch):
    missing = os.path.join(tmp_path, "does-not-exist")
    monkeypatch.setattr(cleaner, "UPLOAD_CACHE_DIR", missing)

    assert cleanup_upload_cache(retention_seconds=1) == 0


def test_subdirectories_are_left_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(cleaner, "UPLOAD_CACHE_DIR", str(tmp_path))
    subdir = os.path.join(tmp_path, "rsod_zip_extracted")
    os.makedirs(subdir)
    stamp = time.time() - 10_000
    os.utime(subdir, (stamp, stamp))

    removed = cleanup_upload_cache(retention_seconds=5_000)

    assert removed == 0
    assert os.path.isdir(subdir)


def test_retention_defaults_to_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(cleaner, "UPLOAD_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(
        cleaner.settings, "UPLOAD_CACHE_RETENTION_SECONDS", 5_000
    )
    old = os.path.join(tmp_path, "old.jpg")
    _touch(old, age_seconds=10_000)

    assert cleanup_upload_cache() == 1
    assert not os.path.exists(old)
