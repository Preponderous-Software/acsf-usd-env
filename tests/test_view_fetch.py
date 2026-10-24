"""strata.lock, the SHA-checked download and library resolution."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

from acsf_usd.view import fetch as F


def test_packaged_lock_pins_v040() -> None:
    lock = F.load_lock()
    assert lock.release == "v0.4.0"
    linux = lock.assets["linux-x64"]
    assert linux.name == "libstrata-0.4.0-linux-x64.so"
    assert linux.sha256.startswith("6044127f") and len(linux.sha256) == 64
    assert linux.url.endswith("/releases/download/v0.4.0/libstrata-0.4.0-linux-x64.so")
    assert lock.assets["win-x64"].name.endswith(".dll")


def test_platform_key() -> None:
    assert F.platform_key("linux", "x86_64") == "linux-x64"
    assert F.platform_key("win32", "AMD64") == "win-x64"
    with pytest.raises(F.FetchError, match="x86-64 only"):
        F.platform_key("linux", "aarch64")
    with pytest.raises(F.FetchError, match="Linux and Windows only"):
        F.platform_key("darwin", "x86_64")


def _lock_for(tmp_path: Path, payload: bytes, sha: str | None = None) -> F.Lock:
    src = tmp_path / "release"
    src.mkdir(exist_ok=True)
    (src / "libstrata-test.so").write_bytes(payload)
    digest = sha or hashlib.sha256(payload).hexdigest()
    text = (
        f'release = "vtest"\nversion = "test"\nbase_url = "{src.as_uri()}"\n'
        f'[assets.linux-x64]\nname = "libstrata-test.so"\nsha256 = "{digest}"\n'
    )
    return F.load_lock(text)


def test_fetch_downloads_verifies_and_caches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(F.ENV_CACHE, str(tmp_path / "cache"))
    monkeypatch.delenv(F.ENV_LIB, raising=False)
    lock = _lock_for(tmp_path, b"\x7fELF fake library")
    path = F.fetch(lock, "linux-x64")
    assert path.read_bytes() == b"\x7fELF fake library"
    assert path == tmp_path / "cache" / "strata" / "vtest" / "libstrata-test.so"
    assert F.fetch(lock, "linux-x64") == path  # cached, re-verified
    monkeypatch.setattr(F, "platform_key", lambda *a: "linux-x64")
    assert F.resolve_library(lock) == path
    # a tampered cache file is deleted, not loaded
    path.write_bytes(b"tampered")
    with pytest.raises(F.FetchError, match="does not match"):
        F.resolve_library(lock)
    assert not path.exists()
    with pytest.raises(F.FetchError, match="fetch-strata"):
        F.resolve_library(lock)


def test_fetch_rejects_a_mismatching_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(F.ENV_CACHE, str(tmp_path / "cache"))
    lock = _lock_for(tmp_path, b"payload", sha="0" * 64)
    with pytest.raises(F.FetchError, match="was deleted"):
        F.fetch(lock, "linux-x64")
    assert not list((tmp_path / "cache").rglob("*.so*"))


def test_fetch_reports_download_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(F.ENV_CACHE, str(tmp_path / "cache"))
    lock = _lock_for(tmp_path, b"x")
    (tmp_path / "release" / "libstrata-test.so").unlink()
    with pytest.raises(F.FetchError, match="downloading"):
        F.fetch(lock, "linux-x64")
    with pytest.raises(F.FetchError, match="no asset"):
        F.fetch(lock, "win-x64")


def test_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lib = tmp_path / "libstrata.so"
    lib.write_bytes(b"local build")
    monkeypatch.setenv(F.ENV_LIB, str(lib))
    assert F.resolve_library() == lib
    monkeypatch.setenv(F.ENV_LIB, str(tmp_path / "missing.so"))
    with pytest.raises(F.FetchError, match="is not a file"):
        F.resolve_library()


def test_cache_dir_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(F.ENV_CACHE, raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    if sys.platform != "win32":
        assert F.cache_dir() == tmp_path / "acsf-usd-env"
