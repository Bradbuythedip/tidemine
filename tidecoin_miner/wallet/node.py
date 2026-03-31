"""Tidecoin node and wallet management."""

import json
import subprocess
from pathlib import Path
from typing import Optional

from tidecoin_miner.config import BASE_DIR


WALLET_DIR = BASE_DIR / "tidecoin-wallet"
TIDECOIN_CONF = Path.home() / ".tidecoin" / "tidecoin.conf"


def get_cli_path() -> Path:
    return WALLET_DIR / "tidecoin-cli"


def get_daemon_path() -> Path:
    return WALLET_DIR / "tidecoind"


def setup_conf():
    """Create tidecoin.conf with RPC credentials."""
    import secrets
    conf_dir = TIDECOIN_CONF.parent
    conf_dir.mkdir(parents=True, exist_ok=True)

    if TIDECOIN_CONF.exists():
        return

    rpc_user = "tidemine"
    rpc_pass = secrets.token_hex(16)

    TIDECOIN_CONF.write_text(
        f"rpcuser={rpc_user}\n"
        f"rpcpassword={rpc_pass}\n"
        "rpcallowip=127.0.0.1\n"
        "server=1\n"
        "daemon=1\n"
        "txindex=1\n"
    )
    print(f"[OK] Created {TIDECOIN_CONF}")


def rpc_call(method: str, params: Optional[list] = None) -> Optional[dict]:
    """Make RPC call to Tidecoin node."""
    cli = get_cli_path()
    if not cli.exists():
        return None

    cmd = [str(cli), method]
    if params:
        cmd.extend(str(p) for p in params)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                return {"result": result.stdout.strip()}
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def get_balance() -> Optional[float]:
    """Get wallet balance."""
    result = rpc_call("getbalance")
    if result and "result" in result:
        try:
            return float(result["result"])
        except (ValueError, TypeError):
            pass
    return None


def get_block_count() -> Optional[int]:
    """Get current block height."""
    result = rpc_call("getblockcount")
    if result and "result" in result:
        try:
            return int(result["result"])
        except (ValueError, TypeError):
            pass
    return None


def get_new_address() -> Optional[str]:
    """Generate a new wallet address."""
    result = rpc_call("getnewaddress")
    if result and "result" in result:
        return result["result"]
    return None


def validate_address(address: str) -> bool:
    """Validate a Tidecoin address."""
    result = rpc_call("validateaddress", [address])
    if result:
        return result.get("isvalid", False)
    # Basic format check as fallback
    return len(address) >= 26 and len(address) <= 62
