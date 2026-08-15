"""Data makro lewat yfinance, plus FRED opsional."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..utils.http import HttpError, get_json

log = logging.getLogger(__name__)

TICKERS = {
    "dxy": "DX-Y.NYB",
    "ust10y": "^TNX",
    "wti": "CL=F",
    "gold": "GC=F",
    "nasdaq": "^IXIC",
    "sp500": "^GSPC",
    "vix": "^VIX",
}

FRED_URL = "https://api.stlouisfed.org/fred/series/observations"
FRED_SERIES = {"fed_balance_sheet": "WALCL", "m2": "M2SL"}


def _fetch_yfinance() -> Dict[str, Any]:
    """Ambil harga terakhir + perubahan harian untuk semua ticker sekaligus."""
    import yfinance as yf  # impor di dalam fungsi supaya import modul tetap ringan

    out: Dict[str, Any] = {}
    symbols = list(TICKERS.values())
    data = yf.download(
        tickers=symbols,
        period="5d",
        interval="1d",
        progress=False,
        auto_adjust=False,
        group_by="ticker",
        threads=True,
    )

    if data is None or data.empty:
        raise ValueError("yfinance mengembalikan data kosong")

    for key, symbol in TICKERS.items():
        try:
            if len(symbols) > 1:
                closes = data[symbol]["Close"].dropna()
            else:
                closes = data["Close"].dropna()
            if closes.empty:
                out[key] = None
                out[f"{key}_change_pct"] = None
                continue
            last = float(closes.iloc[-1])
            out[key] = round(last, 2)
            if len(closes) >= 2:
                prev = float(closes.iloc[-2])
                out[f"{key}_change_pct"] = round((last - prev) / prev * 100, 2) if prev else None
            else:
                out[f"{key}_change_pct"] = None
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            log.warning("Ticker %s (%s) gagal dibaca: %s", key, symbol, exc)
            out[key] = None
            out[f"{key}_change_pct"] = None

    if all(out.get(k) is None for k in TICKERS):
        raise ValueError("semua ticker makro kosong")
    return out


def _fetch_fred(api_key: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, series_id in FRED_SERIES.items():
        try:
            data = get_json(
                FRED_URL,
                params={
                    "series_id": series_id,
                    "api_key": api_key,
                    "file_type": "json",
                    "sort_order": "desc",
                    "limit": 2,
                },
                timeout=30,
            )
            obs = [o for o in (data.get("observations") or []) if o.get("value") not in (".", None)]
            if obs:
                out[key] = float(obs[0]["value"])
        except (HttpError, ValueError, KeyError, TypeError) as exc:
            log.warning("FRED %s gagal: %s", series_id, exc)
    return out


def collect(fred_api_key: Optional[str] = None) -> Dict[str, Any]:
    """Kumpulkan data makro. Sumber yang gagal dilaporkan di `failed`."""
    data: Dict[str, Any] = {key: None for key in TICKERS}
    failed: List[str] = []

    try:
        data.update(_fetch_yfinance())
    except Exception as exc:  # yfinance melempar bermacam exception internal
        log.warning("yfinance gagal total: %s", exc)
        failed.append("macro")

    if fred_api_key:
        fred = _fetch_fred(fred_api_key)
        if fred:
            data.update(fred)
        else:
            failed.append("fred")
    else:
        log.info("FRED_API_KEY kosong, langkah FRED dilewati")

    return {"data": data, "failed": failed}
