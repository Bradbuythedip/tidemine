"""Download and install mining binaries."""

import os
import platform
import shutil
import stat
import subprocess
import tarfile
import zipfile
from pathlib import Path

import httpx

from tidecoin_miner.config import BIN_DIR, BASE_DIR, ensure_dirs

SRBMINER_API = "https://api.github.com/repos/doktor83/SRBMiner-Multi/releases/latest"
TIDECOIN_URL = "https://github.com/tidecoin/tidecoin/releases/download/v0.18.3/linux64.tar.gz"


def get_srbminer_path() -> Path:
    """Return path to SRBMiner binary."""
    candidates = list(BIN_DIR.glob("SRBMiner-Multi-*"))
    for c in sorted(candidates, reverse=True):
        binary = c / "SRBMiner-MULTI"
        if binary.exists():
            return binary
    return BIN_DIR / "SRBMiner-MULTI"


def install_srbminer(force: bool = False) -> Path:
    """Download and install latest SRBMiner-MULTI."""
    ensure_dirs()

    existing = get_srbminer_path()
    if existing.exists() and not force:
        print(f"[OK] SRBMiner already installed at {existing}")
        return existing

    print("[*] Fetching latest SRBMiner-MULTI release...")
    resp = httpx.get(SRBMINER_API, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    release = resp.json()
    tag = release["tag_name"]
    print(f"[*] Latest version: {tag}")

    # Find linux tarball
    asset_url = None
    for asset in release["assets"]:
        name = asset["name"].lower()
        if "linux" in name and name.endswith(".tar.gz"):
            asset_url = asset["browser_download_url"]
            asset_name = asset["name"]
            break

    if not asset_url:
        # Try .tar.xz
        for asset in release["assets"]:
            name = asset["name"].lower()
            if "linux" in name and (name.endswith(".tar.xz") or name.endswith(".tar.gz")):
                asset_url = asset["browser_download_url"]
                asset_name = asset["name"]
                break

    if not asset_url:
        raise RuntimeError(f"No Linux release found in {tag}. Assets: {[a['name'] for a in release['assets']]}")

    archive_path = BIN_DIR / asset_name
    print(f"[*] Downloading {asset_name}...")
    with httpx.stream("GET", asset_url, timeout=120, follow_redirects=True) as stream:
        stream.raise_for_status()
        with open(archive_path, "wb") as f:
            for chunk in stream.iter_bytes(8192):
                f.write(chunk)

    print("[*] Extracting...")
    if asset_name.endswith(".tar.gz") or asset_name.endswith(".tar.xz"):
        with tarfile.open(archive_path) as tar:
            tar.extractall(BIN_DIR)
    elif asset_name.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(BIN_DIR)

    archive_path.unlink()

    binary = get_srbminer_path()
    if binary.exists():
        binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
        print(f"[OK] SRBMiner installed at {binary}")
        return binary

    raise RuntimeError("SRBMiner binary not found after extraction")


def install_tidecoin_wallet(force: bool = False) -> Path:
    """Download and install Tidecoin wallet/node binaries."""
    ensure_dirs()
    wallet_dir = BASE_DIR / "tidecoin-wallet"

    if wallet_dir.exists() and not force:
        cli = wallet_dir / "tidecoin-cli"
        if cli.exists():
            print(f"[OK] Tidecoin wallet already installed at {wallet_dir}")
            return wallet_dir

    wallet_dir.mkdir(parents=True, exist_ok=True)
    archive_path = BIN_DIR / "tidecoin-linux64.tar.gz"

    print("[*] Downloading Tidecoin wallet...")
    with httpx.stream("GET", TIDECOIN_URL, timeout=120, follow_redirects=True) as stream:
        stream.raise_for_status()
        with open(archive_path, "wb") as f:
            for chunk in stream.iter_bytes(8192):
                f.write(chunk)

    print("[*] Extracting...")
    with tarfile.open(archive_path) as tar:
        tar.extractall(BIN_DIR)

    # Move binaries to wallet dir
    extracted = BIN_DIR / "linux64"
    if not extracted.exists():
        # Try finding the extracted directory
        for d in BIN_DIR.iterdir():
            if d.is_dir() and "tidecoin" in d.name.lower():
                extracted = d
                break

    if extracted.exists() and extracted.is_dir():
        for f in extracted.iterdir():
            dest = wallet_dir / f.name
            if f.is_file():
                shutil.move(str(f), str(dest))
                dest.chmod(dest.stat().st_mode | stat.S_IEXEC)
        shutil.rmtree(extracted, ignore_errors=True)

    archive_path.unlink(missing_ok=True)
    print(f"[OK] Tidecoin wallet installed at {wallet_dir}")
    return wallet_dir


def install_cpuminer(force: bool = False) -> Path:
    """Build cpuminer-opt from source as fallback."""
    ensure_dirs()
    cpuminer_dir = BIN_DIR / "cpuminer-opt"
    binary = cpuminer_dir / "cpuminer"

    if binary.exists() and not force:
        print(f"[OK] cpuminer-opt already installed at {binary}")
        return binary

    print("[*] Building cpuminer-opt from source...")
    if cpuminer_dir.exists():
        shutil.rmtree(cpuminer_dir)

    subprocess.run(
        ["git", "clone", "https://github.com/JayDDee/cpuminer-opt.git", str(cpuminer_dir)],
        check=True, capture_output=True,
    )
    subprocess.run(["./autogen.sh"], cwd=cpuminer_dir, check=True, capture_output=True)
    subprocess.run(
        ["./configure", "CFLAGS=-O3 -march=native -mtune=native"],
        cwd=cpuminer_dir, check=True, capture_output=True,
    )
    subprocess.run(
        ["make", f"-j{os.cpu_count() or 4}"],
        cwd=cpuminer_dir, check=True, capture_output=True,
    )

    if binary.exists():
        print(f"[OK] cpuminer-opt built at {binary}")
        return binary

    raise RuntimeError("cpuminer-opt build failed")


def install_all(force: bool = False):
    """Install all mining binaries."""
    install_srbminer(force=force)
    install_tidecoin_wallet(force=force)
