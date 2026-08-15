"""Data posisi whale vs ritel, dan aliran taker.

Gagasan intinya: Binance memisahkan statistik "top trader" (akun dengan margin
terbesar — proksi whale) dari "global account" (seluruh akun — didominasi
ritel). Ketika keduanya berlawanan arah, itu sinyal yang tidak terlihat dari
grafik harga saja.

Urutan sumber:

  1. Binance  — punya pemisahan top trader vs seluruh akun (terbaik)
  2. OKX      — punya pemisahan yang sama, jadi divergensi tetap utuh
  3. Bybit    — hanya rasio AGREGAT, jadi cuma sisi ritel yang pulih

Binance menolak IP runner GitHub Actions (451) dan Bybit menolak lewat
CloudFront (403), sehingga sebelum ada OKX sumber ini gagal pada setiap run.
Apa pun yang tidak berhasil diambil tetap dilaporkan sebagai kosong, bukan
ditambal angka dari sisi lain yang maknanya berbeda.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..utils.http import HttpError, get_json
from . import bybit, okx
from .binance import _ringkas

log = logging.getLogger(__name__)

FUTURES_BASE = "https://fapi.binance.com"

# Ambang divergensi posisi whale vs ritel sebelum dianggap layak dilaporkan.
AMBANG_DIVERGENSI = 0.15


def _ambil_rasio(path: str, symbol: str, period: str, limit: int) -> List[Dict[str, Any]]:
    return get_json(
        f"{FUTURES_BASE}/futures/data/{path}",
        params={"symbol": symbol, "period": period, "limit": limit},
        timeout=30,
    )


def _persen_long(baris: Dict[str, Any], kunci_long: str) -> Optional[float]:
    try:
        return float(baris[kunci_long])
    except (KeyError, TypeError, ValueError):
        return None


def _tren(deret: List[float]) -> Optional[float]:
    """Selisih nilai terakhir terhadap nilai awal jendela, dalam poin persen."""
    if len(deret) < 2:
        return None
    return round((deret[-1] - deret[0]) * 100, 2)


def collect(symbol: str, period: str = "1h", limit: int = 24) -> Dict[str, Any]:
    """Kumpulkan posisi whale vs ritel + aliran taker.

    Return: {"data": {...}, "failed": [...]}
    """
    data: Dict[str, Any] = {
        "whale_long_pct": None,
        "whale_short_pct": None,
        "whale_ratio": None,
        "whale_tren_long_pp": None,
        "ritel_long_pct": None,
        "ritel_short_pct": None,
        "ritel_ratio": None,
        "ritel_tren_long_pp": None,
        "divergensi": None,
        "divergensi_label": None,
        "taker_buy_sell_ratio": None,
        "taker_tren": None,
        "periode": period,
        "jam_dipantau": limit,
        "sumber_whale": "binance",
        "sumber_ritel": "binance",
    }
    failed: List[str] = []

    # -- posisi top trader (proksi whale) --------------------------------
    try:
        rows = _ambil_rasio("topLongShortPositionRatio", symbol, period, limit)
        seri = [p for p in (_persen_long(r, "longAccount") for r in rows) if p is not None]
        if seri:
            data["whale_long_pct"] = round(seri[-1] * 100, 2)
            data["whale_short_pct"] = round((1 - seri[-1]) * 100, 2)
            data["whale_ratio"] = round(float(rows[-1]["longShortRatio"]), 3)
            data["whale_tren_long_pp"] = _tren(seri)
    except (HttpError, KeyError, ValueError, TypeError, IndexError) as exc:
        log.warning("Rasio posisi whale gagal: %s", _ringkas(exc))
        failed.append("whale_posisi")

    # -- posisi seluruh akun (proksi ritel) ------------------------------
    try:
        rows = _ambil_rasio("globalLongShortAccountRatio", symbol, period, limit)
        seri = [p for p in (_persen_long(r, "longAccount") for r in rows) if p is not None]
        if seri:
            data["ritel_long_pct"] = round(seri[-1] * 100, 2)
            data["ritel_short_pct"] = round((1 - seri[-1]) * 100, 2)
            data["ritel_ratio"] = round(float(rows[-1]["longShortRatio"]), 3)
            data["ritel_tren_long_pp"] = _tren(seri)
    except (HttpError, KeyError, ValueError, TypeError, IndexError) as exc:
        log.warning("Rasio posisi ritel gagal: %s", _ringkas(exc))
        failed.append("ritel_posisi")

    # -- aliran taker (agresor beli vs jual) -----------------------------
    try:
        rows = _ambil_rasio("takerlongshortRatio", symbol, period, limit)
        seri = [float(r["buySellRatio"]) for r in rows if r.get("buySellRatio")]
        if seri:
            data["taker_buy_sell_ratio"] = round(seri[-1], 3)
            awal = sum(seri[: max(1, len(seri) // 2)]) / max(1, len(seri) // 2)
            akhir = sum(seri[len(seri) // 2 :]) / max(1, len(seri) - len(seri) // 2)
            data["taker_tren"] = (
                "beli menguat" if akhir > awal * 1.03
                else "jual menguat" if akhir < awal * 0.97
                else "seimbang"
            )
    except (HttpError, KeyError, ValueError, TypeError, IndexError) as exc:
        log.warning("Rasio taker gagal: %s", _ringkas(exc))
        failed.append("taker_flow")

    # -- cadangan 1: OKX --------------------------------------------------
    # OKX memisahkan top trader dari seluruh akun persis seperti Binance, jadi
    # divergensi whale-vs-ritel bisa pulih utuh. Tiap sisi diisi sendiri-sendiri
    # supaya sumber yang sebagian berhasil tetap berguna.
    if data["whale_long_pct"] is None or data["ritel_long_pct"] is None:
        cadangan_okx = okx.fetch_rasio_posisi()
        for awalan, nama_gagal in (("whale", "whale_posisi"), ("ritel", "ritel_posisi")):
            if data[f"{awalan}_long_pct"] is not None:
                continue
            if cadangan_okx.get(f"{awalan}_long_pct") is None:
                continue
            for akhiran in ("long_pct", "short_pct", "ratio", "tren_long_pp"):
                data[f"{awalan}_{akhiran}"] = cadangan_okx.get(f"{awalan}_{akhiran}")
            data["sumber_ritel" if awalan == "ritel" else "sumber_whale"] = "okx"
            log.info("Rasio posisi %s diambil dari OKX", awalan)
            if nama_gagal in failed:
                failed.remove(nama_gagal)

    if data["taker_buy_sell_ratio"] is None:
        cadangan_taker = okx.fetch_taker_ratio()
        if cadangan_taker["taker_buy_sell_ratio"] is not None:
            data.update(cadangan_taker)
            log.info("Rasio taker diambil dari OKX")
            if "taker_flow" in failed:
                failed.remove("taker_flow")

    # -- cadangan 2: Bybit ------------------------------------------------
    # Bybit hanya punya rasio agregat seluruh akun, jadi yang bisa dipulihkan
    # cuma sisi "ritel". Divergensi whale-vs-ritel tetap tidak tersedia, dan
    # itu dilaporkan apa adanya ketimbang ditambal angka yang beda maknanya.
    if data["ritel_long_pct"] is None:
        cadangan = bybit.fetch_account_ratio(symbol)
        if cadangan["long_pct"] is not None:
            data["ritel_long_pct"] = cadangan["long_pct"]
            data["ritel_short_pct"] = cadangan["short_pct"]
            data["ritel_tren_long_pp"] = cadangan["tren_long_pp"]
            data["sumber_ritel"] = "bybit"
            log.info("Rasio posisi agregat diambil dari Bybit")
            if "ritel_posisi" in failed:
                failed.remove("ritel_posisi")

    # -- divergensi whale vs ritel ---------------------------------------
    whale = data["whale_long_pct"]
    ritel = data["ritel_long_pct"]
    if whale is not None and ritel is not None:
        selisih = (whale - ritel) / 100
        data["divergensi"] = round(selisih * 100, 2)
        if abs(selisih) < AMBANG_DIVERGENSI:
            data["divergensi_label"] = "selaras"
        elif selisih < 0:
            # Whale lebih sedikit long dibanding ritel.
            data["divergensi_label"] = "whale_distribusi"
        else:
            data["divergensi_label"] = "whale_akumulasi"

    return {"data": data, "failed": failed}
