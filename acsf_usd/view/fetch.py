"""Find, download and verify the pinned Strata library (``strata.lock``).

The ``[view]`` extra does not bundle ``libstrata``. ``acsf-usd fetch-strata`` downloads the
release asset named in ``strata.lock`` into a per-user cache, checks its SHA-256 and deletes it
on a mismatch. ``ACSF_USD_STRATA_LIB`` points at a local build instead (no hash check: the
override is explicit).
"""

from __future__ import annotations

import hashlib
import os
import platform
import sys
import tomllib
import urllib.request
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

ENV_LIB = "ACSF_USD_STRATA_LIB"
ENV_CACHE = "ACSF_USD_CACHE"


class FetchError(Exception):
    """The pinned library cannot be found, downloaded or verified."""


@dataclass(frozen=True)
class Asset:
    platform: str
    name: str
    sha256: str
    url: str


@dataclass(frozen=True)
class Lock:
    release: str
    version: str
    assets: dict[str, Asset]


def load_lock(text: str | None = None) -> Lock:
    if text is None:
        text = resources.files("acsf_usd.view").joinpath("strata.lock").read_text("utf-8")
    data = tomllib.loads(text)
    base = str(data["base_url"]).rstrip("/")
    assets = {
        key: Asset(key, str(a["name"]), str(a["sha256"]).lower(), f"{base}/{a['name']}")
        for key, a in dict(data["assets"]).items()
    }
    return Lock(str(data["release"]), str(data["version"]), assets)


def platform_key(system: str | None = None, machine: str | None = None) -> str:
    system = (system or sys.platform).lower()
    machine = (machine or platform.machine()).lower()
    if machine not in ("x86_64", "amd64", "x64"):
        raise FetchError(f"Strata has no release build for {machine} (x86-64 only)")
    if system.startswith("linux"):
        return "linux-x64"
    if system in ("win32", "windows", "cygwin"):
        return "win-x64"
    raise FetchError(f"Strata has no release build for {system} (Linux and Windows only)")


def cache_dir() -> Path:
    if os.environ.get(ENV_CACHE):
        return Path(os.environ[ENV_CACHE])
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "acsf-usd-env" / "cache"
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "acsf-usd-env"


def cached_path(lock: Lock, asset: Asset) -> Path:
    return cache_dir() / "strata" / lock.release / asset.name


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verify(path: Path, asset: Asset) -> None:
    """Raise and delete ``path`` when its hash is not the pinned one."""
    actual = sha256_of(path)
    if actual != asset.sha256:
        path.unlink(missing_ok=True)
        raise FetchError(
            f"{asset.name}: SHA-256 {actual} does not match the pinned {asset.sha256}; "
            "the file was deleted"
        )


def fetch(lock: Lock | None = None, key: str | None = None, *, force: bool = False) -> Path:
    """Download (or re-verify) the pinned asset for this platform and return its path."""
    lock = lock or load_lock()
    asset = lock.assets.get(key or platform_key())
    if asset is None:
        raise FetchError(f"strata.lock has no asset for {key or platform_key()}")
    dest = cached_path(lock, asset)
    if dest.exists() and not force:
        verify(dest, asset)
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    try:
        with urllib.request.urlopen(asset.url, timeout=120) as resp, part.open("wb") as out:
            while block := resp.read(1 << 20):
                out.write(block)
    except OSError as exc:
        part.unlink(missing_ok=True)
        raise FetchError(f"downloading {asset.url}: {exc}") from None
    verify(part, asset)
    os.replace(part, dest)
    return dest


def resolve_library(lock: Lock | None = None) -> Path:
    """The library to load: ``ACSF_USD_STRATA_LIB``, else the verified cached download."""
    override = os.environ.get(ENV_LIB)
    if override:
        path = Path(override)
        if not path.is_file():
            raise FetchError(f"{ENV_LIB}={override} is not a file")
        return path
    lock = lock or load_lock()
    asset = lock.assets.get(platform_key())
    if asset is None:
        raise FetchError(f"strata.lock has no asset for {platform_key()}")
    path = cached_path(lock, asset)
    if not path.exists():
        raise FetchError(
            f"Strata {lock.version} is not downloaded yet: run `acsf-usd fetch-strata` "
            f"(or set {ENV_LIB} to a local libstrata)"
        )
    verify(path, asset)
    return path
