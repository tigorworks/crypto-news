"""Harga, klines, funding rate, dan open interest.

Sumber utama Binance, tapi IP runner GitHub Actions (berbasis AS) ditolak
Binance dengan HTTP 451 — pembatasan wilayah permanen, bukan gangguan
sementara. Karena itu ada dua jalur cadangan:

  harga + klines  -> CoinGecko
  funding + OI    -> Bybit, lalu Deribit

Bybit ternyata ikut memblokir IP yang sama lewat CloudFront, jadi Deribit
dipasang sebagai lapis ketiga. Semuanya publik dan tanpa API key.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..utils.http import HttpError, get_json
from . import bybit, options

log = logging.getLogger(__name__)

SPOT_BASE = "https://api.binance.com"
FUTURES_BASE = "https://fapi.binance.com"
COINGECKO_BASE = "https://api.coingecko.com/api/v3"

# Status yang menandakan Binance memblokir kita, bukan error sementara.
BLOCKED_STATUS = (403, 451)

# Berapa candle CoinGecko yang dipakai per timeframe (granularitas CoinGecko
# ditentukan oleh rentang hari, jadi kita ambil lalu resample sendiri).
_CG_DAYS = {"1d": 250, "4h": 60, "1h": 14}
_TF_MINUTES = {"1h": 60, "4h": 240, "1d": 1440}


def _ringkas(exc: Exception, batas: int = 120) -> str:
    """Pesan 451 Binance memuat kutipan panjang syarat layanan; dipangkas."""
    teks = " ".join(str(exc).split())
    return teks if len(teks) <= batas else teks[:batas] + "…"


class PriceDataError(Exception):
    """Harga/klines tidak bisa diambil dari sumber mana pun — ini fatal."""


# --------------------------------------------------------------------------
# Binance
# --------------------------------------------------------------------------
def _binance_price(symbol: str) -> Dict[str, Any]:
    ticker = get_json(f"{SPOT_BASE}/api/v3/ticker/24hr", params={"symbol": symbol})
    return {
        "last": float(ticker["lastPrice"]),
        "change_24h_pct": float(ticker["priceChangePercent"]),
        "high_24h": float(ticker["highPrice"]),
        "low_24h": float(ticker["lowPrice"]),
        "volume_24h": float(ticker["quoteVolume"]),
    }


def _binance_klines(symbol: str, interval: str, limit: int) -> List[Dict[str, Any]]:
    rows = get_json(
        f"{SPOT_BASE}/api/v3/klines",
        params={"symbol": symbol, "interval": interval, "limit": limit},
    )
    return [
        {
            "open_time": int(r[0]),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "volume": float(r[5]),
            "close_time": int(r[6]),
        }
        for r in rows
    ]


def fetch_funding_rate(symbol: str) -> Optional[float]:
    """Funding rate terkini (fraksi, bukan persen), Binance lalu Bybit."""
    try:
        data = get_json(f"{FUTURES_BASE}/fapi/v1/premiumIndex", params={"symbol": symbol})
        return float(data["lastFundingRate"])
    except (HttpError, KeyError, ValueError, TypeError) as exc:
        log.warning("Funding rate Binance gagal (%s), coba Bybit", _ringkas(exc))
        nilai = bybit.fetch_funding_rate(symbol)
        if nilai is None:
            log.warning("Bybit juga gagal, coba Deribit")
            nilai = options.perp_funding_rate()
        return nilai


def fetch_open_interest(symbol: str) -> Optional[float]:
    """Open interest dalam BTC, Binance lalu Bybit."""
    try:
        data = get_json(f"{FUTURES_BASE}/fapi/v1/openInterest", params={"symbol": symbol})
        return float(data["openInterest"])
    except (HttpError, KeyError, ValueError, TypeError) as exc:
        log.warning("Open interest Binance gagal (%s), coba Bybit", _ringkas(exc))
        nilai = bybit.fetch_open_interest(symbol)
        if nilai is None:
            log.warning("Bybit juga gagal, coba Deribit")
            nilai = options.perp_open_interest()
        return nilai


def fetch_open_interest_history(symbol: str, period: str = "1d", limit: int = 30) -> List[Dict[str, Any]]:
    """Riwayat OI untuk mendeteksi arah OI vs harga. List kosong kalau gagal."""
    try:
        rows = get_json(
            f"{FUTURES_BASE}/futures/data/openInterestHist",
            params={"symbol": symbol, "period": period, "limit": limit},
        )
        return [
            {"timestamp": int(r["timestamp"]), "open_interest": float(r["sumOpenInterest"])}
            for r in rows
        ]
    except (HttpError, KeyError, ValueError, TypeError) as exc:
        log.warning("Riwayat OI Binance gagal (%s), coba Bybit", _ringkas(exc))
        return bybit.fetch_open_interest_history(symbol)


# --------------------------------------------------------------------------
# CoinGecko (fallback)
# --------------------------------------------------------------------------
def _coingecko_price() -> Dict[str, Any]:
    data = get_json(
        f"{COINGECKO_BASE}/coins/bitcoin",
        params={
            "localization": "false",
            "tickers": "false",
            "community_data": "false",
            "developer_data": "false",
        },
    )
    md = data["market_data"]
    return {
        "last": float(md["current_price"]["usd"]),
        "change_24h_pct": float(md.get("price_change_percentage_24h") or 0.0),
        "high_24h": float(md["high_24h"]["usd"]),
        "low_24h": float(md["low_24h"]["usd"]),
        "volume_24h": float(md["total_volume"]["usd"]),
    }


def _coingecko_klines(interval: str, limit: int) -> List[Dict[str, Any]]:
    """Bangun OHLCV dari market_chart CoinGecko.

    CoinGecko tidak menyediakan OHLC gratis dengan granularitas bebas, jadi
    kita ambil deret harga lalu resample jadi candle per timeframe. Hasilnya
    tidak seakurat exchange (open/high/low berasal dari sampling), tapi cukup
    untuk indikator ketika Binance tidak bisa diakses.
    """
    days = _CG_DAYS.get(interval, 90)
    data = get_json(
        f"{COINGECKO_BASE}/coins/bitcoin/market_chart",
        params={"vs_currency": "usd", "days": days},
    )
    prices = data.get("prices") or []
    volumes = {int(t): float(v) for t, v in (data.get("total_volumes") or [])}
    if not prices:
        raise PriceDataError("CoinGecko mengembalikan deret harga kosong")

    bucket_ms = _TF_MINUTES.get(interval, 1440) * 60 * 1000
    buckets: Dict[int, List[Any]] = {}
    for ts, price in prices:
        ts = int(ts)
        key = ts - (ts % bucket_ms)
        buckets.setdefault(key, []).append((ts, float(price)))

    candles: List[Dict[str, Any]] = []
    for key in sorted(buckets):
        points = sorted(buckets[key])
        values = [p for _, p in points]
        vol = 0.0
        for ts, _ in points:
            vol += volumes.get(ts, 0.0)
        candles.append(
            {
                "open_time": key,
                "open": values[0],
                "high": max(values),
                "low": min(values),
                "close": values[-1],
                "volume": vol,
                "close_time": key + bucket_ms - 1,
            }
        )
    return candles[-limit:]


# --------------------------------------------------------------------------
# API publik modul
# --------------------------------------------------------------------------
def fetch_price_and_klines(symbol: str, timeframes: List[str], limit: int) -> Dict[str, Any]:
    """Ambil harga + klines. Raise PriceDataError kalau semua sumber gagal.

    Return: {"price": {...}, "klines": {tf: [...]}, "source": "binance"|"coingecko"}
    """
    try:
        price = _binance_price(symbol)
        klines = {tf: _binance_klines(symbol, tf, limit) for tf in timeframes}
        log.info("Harga & klines dari Binance: $%s", f"{price['last']:,.0f}")
        return {"price": price, "klines": klines, "source": "binance"}
    except HttpError as exc:
        if exc.status_code in BLOCKED_STATUS:
            log.warning("Binance memblokir permintaan (HTTP %s), pakai CoinGecko", exc.status_code)
        else:
            log.warning("Binance gagal (%s), pakai CoinGecko", exc)
    except (KeyError, ValueError, TypeError) as exc:
        log.warning("Respons Binance tidak sesuai harapan (%s), pakai CoinGecko", exc)

    try:
        price = _coingecko_price()
        klines = {tf: _coingecko_klines(tf, limit) for tf in timeframes}
        log.info("Harga & klines dari CoinGecko: $%s", f"{price['last']:,.0f}")
        return {"price": price, "klines": klines, "source": "coingecko"}
    except (HttpError, KeyError, ValueError, TypeError, PriceDataError) as exc:
        raise PriceDataError(
            f"Binance dan CoinGecko sama-sama gagal menyediakan data harga: {exc}"
        ) from exc
