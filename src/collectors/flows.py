"""Indikator aliran dana: premium Coinbase dan pasokan stablecoin.

Dua sinyal yang murah diambil tapi jarang dipasang orang:

  Premium Coinbase   Selisih harga BTC di Coinbase (bursa utama institusi
                     dan ritel AS) terhadap bursa global. Premium positif
                     berarti permintaan AS lebih agresif daripada pasar
                     dunia — sering mendahului pergerakan yang didorong
                     arus institusional. Premium negatif menandakan
                     tekanan jual dari sisi AS.

  Pasokan stablecoin Total kapitalisasi USDT + USDC. Ini "amunisi" yang
                     duduk di bursa menunggu dibelanjakan. Pasokan yang
                     naik berarti likuiditas masuk ke ekosistem kripto;
                     turun berarti modal keluar.

Keduanya dari API publik gratis.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..utils.http import HttpError, get_json

log = logging.getLogger(__name__)

COINBASE_TICKER = "https://api.exchange.coinbase.com/products/BTC-USD/ticker"
COINGECKO_MARKETS = "https://api.coingecko.com/api/v3/coins/markets"

STABLECOIN = ["tether", "usd-coin"]


def _premium_coinbase(harga_global: Optional[float]) -> Dict[str, Any]:
    """Premium Coinbase terhadap harga acuan global kita."""
    if not harga_global:
        raise ValueError("harga global tidak tersedia sebagai pembanding")

    data = get_json(COINBASE_TICKER, timeout=30)
    harga_cb = float(data["price"])
    selisih = harga_cb - harga_global
    premium_pct = selisih / harga_global * 100

    if premium_pct > 0.05:
        label = "permintaan AS lebih agresif"
    elif premium_pct < -0.05:
        label = "tekanan jual dari sisi AS"
    else:
        label = "seimbang"

    return {
        "harga_coinbase": round(harga_cb, 2),
        "premium_coinbase_usd": round(selisih, 2),
        "premium_coinbase_pct": round(premium_pct, 4),
        "premium_coinbase_label": label,
    }


def _pasokan_stablecoin() -> Dict[str, Any]:
    """Kapitalisasi USDT + USDC beserta perubahan 24 jamnya."""
    rows = get_json(
        COINGECKO_MARKETS,
        params={
            "vs_currency": "usd",
            "ids": ",".join(STABLECOIN),
            "price_change_percentage": "24h",
        },
        timeout=30,
    )
    if not rows:
        raise ValueError("CoinGecko tidak mengembalikan data stablecoin")

    total = 0.0
    perubahan_nominal = 0.0
    rincian: Dict[str, Any] = {}

    for row in rows:
        cap = float(row.get("market_cap") or 0)
        if not cap:
            continue
        total += cap
        rincian[row["id"]] = round(cap, 0)
        # market_cap_change_24h adalah perubahan nominal, bukan persen.
        perubahan = row.get("market_cap_change_24h")
        if perubahan is not None:
            try:
                perubahan_nominal += float(perubahan)
            except (TypeError, ValueError):
                pass

    if not total:
        raise ValueError("kapitalisasi stablecoin nol")

    return {
        "stablecoin_cap_usd": round(total, 0),
        "stablecoin_perubahan_24j_usd": round(perubahan_nominal, 0),
        "stablecoin_perubahan_24j_pct": round(
            perubahan_nominal / (total - perubahan_nominal) * 100, 3
        ) if total != perubahan_nominal else None,
        "stablecoin_rincian": rincian,
    }


def collect(harga_global: Optional[float]) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    failed: List[str] = []

    try:
        data.update(_premium_coinbase(harga_global))
    except (HttpError, ValueError, KeyError, TypeError) as exc:
        log.warning("Premium Coinbase gagal: %s", exc)
        failed.append("premium_coinbase")

    try:
        data.update(_pasokan_stablecoin())
    except (HttpError, ValueError, KeyError, TypeError) as exc:
        log.warning("Pasokan stablecoin gagal: %s", exc)
        failed.append("stablecoin")

    if data:
        log.info(
            "Aliran: premium Coinbase %s%%, stablecoin cap $%s",
            data.get("premium_coinbase_pct"),
            f"{data.get('stablecoin_cap_usd', 0):,.0f}",
        )

    return {"data": data, "failed": ["flows"] if len(failed) == 2 else []}
