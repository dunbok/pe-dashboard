"""Shared paths and settings."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
EPS_EDGAR_DIR = DATA / "eps_edgar"
PRICES_DIR = DATA / "prices"
ADR_RATIOS_FILE = ROOT / "adr_ratios.json"


EPS_AV_DIR = DATA / "eps_av"
AV_STATE_FILE = DATA / "av_backfill_state.json"


def _env(name: str) -> str | None:
    """Env var (GitHub secret in Actions). On Windows, fall back to the user-level
    variable so a fresh `setx` works without opening a new shell."""
    val = os.environ.get(name)
    if not val and os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                val = winreg.QueryValueEx(k, name)[0]
        except OSError:
            val = None
    # Secrets pasted/piped from Windows can carry a BOM or trailing newline.
    val = (val or "").lstrip("﻿").strip()
    return val or None


def alphavantage_key() -> str | None:
    return _env("ALPHAVANTAGE_API_KEY")


def sec_user_agent() -> str:
    """SEC requires 'AppName contact@email' in the User-Agent."""
    ua = _env("SEC_USER_AGENT")
    if not ua or "@" not in ua:
        raise RuntimeError(
            "SEC_USER_AGENT is not set. It must look like 'pe-dashboard you@example.com'."
        )
    return ua
