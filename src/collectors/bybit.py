"""Sumber cadangan data derivatif: Bybit.

Kenapa ada: Binance mengembalikan HTTP 451 ("restricted location") untuk IP
runner GitHub Actions yang berbasis di AS. Itu pembatasan wilayah permanen,
bukan gangguan sementara — tanpa cadangan, funding rate, open interest, dan
seluruh data posisi tidak akan pernah tersedia di produksi.

Endpoint Bybit v5 di bawah publik dan tanpa API key.

Satu perbedaan penting: Bybit hanya menyediakan rasio long/short AGREGAT
seluruh akun, sedangkan Binance memisahkan "top trader" dari seluruh akun.
Artinya saat Binance terblokir, divergensi whale-vs-ritel memang tidak bisa
dihitung — dan itu dilaporkan apa adanya, bukan ditambal dengan angka
agregat yang berbeda maknanya.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..utils.http import HttpError, get_json

log = logging.getLogger(__name__)

BASE = "https://api.bybit.com"


def _list_hasil(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Bybit membungkus semuanya di retCode/result.list."""
    if data.get("retCode") not in (0, None):
        raise ValueError(f"Bybit retCode {data.get('retCode')}: {data.get('retMsg')}")
    hasil = (data.get("result") or {}).get("list")
    if not hasil:
        raise ValueError("Bybit mengembalikan result.list kosong")
    return hasil


def fetch_funding_rate(symbol: str = "BTCUSDT") -> Optional[float]:
    """Funding rate terkini sebagai fraksi (bukan persen)."""
    try:
        data = get_json(
            f"{BASE}/v5/market/tickers",
            params={"category": "linear", "symbol": symbol},
            timeout=30,
        )
        return float(_list_hasil(data)[0]["fundingRate"])
    except (HttpError, ValueError, KeyError, TypeError, IndexError) as exc:
        log.warning("Funding rate Bybit gagal: %s", exc)
        return None


def fetch_open_interest(symbol: str = "BTCUSDT") -> Optional[float]:
    """Open interest dalam BTC."""
    try:
        data = get_json(
            f"{BASE}/v5/market/tickers",
            params={"category": "linear", "symbol": symbol},
            timeout=30,
        )
        return float(_list_hasil(data)[0]["openInterest"])
    except (HttpError, ValueError, KeyError, TypeError, IndexError) as exc:
        log.warning("Open interest Bybit gagal: %s", exc)
        return None


def fetch_open_interest_history(
    symbol: str = "BTCUSDT", interval: str = "1h", limit: int = 30
) -> List[Dict[str, Any]]:
    """Riwayat OI, diurutkan dari paling lama ke paling baru.

    Bybit mengembalikan urutan terbaru dulu; kode lain di proyek ini
    mengasumsikan elemen terakhir adalah yang terkini, jadi dibalik di sini.
    """
    try:
        data = get_json(
            f"{BASE}/v5/market/open-interest",
            params={"category": "linear", "symbol": symbol,
                    "intervalTime": interval, "limit": limit},
            timeout=30,
        )
        rows = _list_hasil(data)
        hasil = [
            {"timestamp": int(r["timestamp"]), "open_interest": float(r["openInterest"])}
            for r in rows
        ]
        hasil.sort(key=lambda r: r["timestamp"])
        return hasil
    except (HttpError, ValueError, KeyError, TypeError) as exc:
        log.warning("Riwayat OI Bybit gagal: %s", exc)
        return []


def fetch_account_ratio(
    symbol: str = "BTCUSDT", period: str = "1h", limit: int = 24
) -> Dict[str, Any]:
    """Rasio long/short SELURUH akun (bukan khusus top trader).

    Dikembalikan dengan penanda `sumber` supaya pemanggil tahu ini bukan
    padanan langsung dari statistik top trader Binance.
    """
    kosong = {"long_pct": None, "short_pct": None, "tren_long_pp": None, "sumber": "bybit"}
    try:
        data = get_json(
            f"{BASE}/v5/market/account-ratio",
            params={"category": "linear", "symbol": symbol,
                    "period": period, "limit": limit},
            timeout=30,
        )
        rows = _list_hasil(data)
        rows.sort(key=lambda r: int(r.get("timestamp", 0)))
        seri = [float(r["buyRatio"]) for r in rows if r.get("buyRatio") is not None]
        if not seri:
            return kosong
        return {
            "long_pct": round(seri[-1] * 100, 2),
            "short_pct": round((1 - seri[-1]) * 100, 2),
            "tren_long_pp": round((seri[-1] - seri[0]) * 100, 2) if len(seri) > 1 else None,
            "sumber": "bybit",
        }
    except (HttpError, ValueError, KeyError, TypeError) as exc:
        log.warning("Rasio akun Bybit gagal: %s", exc)
        return kosong
