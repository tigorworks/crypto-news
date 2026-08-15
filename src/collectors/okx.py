"""Rasio posisi long/short dari OKX.

Alasan file ini ada: sumber posisi whale-vs-ritel selalu gagal di produksi.
Binance menolak IP runner GitHub Actions (451) dan Bybit menolak lewat
CloudFront (403), sehingga sumber `whale` gagal pada setiap run dan menyeret
skor kualitas data ke "sedang" terus-menerus.

OKX menyediakan statistik yang setara secara publik tanpa API key, dan yang
terpenting: OKX memisahkan "top trader" dari "seluruh akun" — pemisahan yang
justru hilang saat memakai Bybit. Jadi divergensi whale vs ritel bisa pulih
sepenuhnya, bukan cuma separuh.

Nama endpoint statistik OKX pernah berganti bentuk (versi `ccy=` lama dan versi
`instId=` baru hidup berdampingan). Karena itu setiap metrik dicoba lewat
beberapa kandidat URL sampai ada yang menjawab, ketimbang bertaruh pada satu
bentuk yang bisa saja sudah pensiun.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from ..utils.http import HttpError, get_json

log = logging.getLogger(__name__)

BASE = "https://www.okx.com"
INST_ID = "BTC-USDT-SWAP"

# Kandidat endpoint per metrik, dicoba berurutan.
_KANDIDAT_WHALE = [
    ("/api/v5/rubik/stat/contracts/long-short-account-ratio-contract-top-trader",
     {"instId": INST_ID, "period": "1H"}),
    ("/api/v5/rubik/stat/contracts/long-short-position-ratio-contract-top-trader",
     {"instId": INST_ID, "period": "1H"}),
]
_KANDIDAT_RITEL = [
    ("/api/v5/rubik/stat/contracts/long-short-account-ratio-contract",
     {"instId": INST_ID, "period": "1H"}),
    ("/api/v5/rubik/stat/contracts/long-short-account-ratio",
     {"ccy": "BTC", "period": "1H"}),
]
_KANDIDAT_TAKER = [
    ("/api/v5/rubik/stat/taker-volume-contract", {"instId": INST_ID, "period": "1H"}),
    ("/api/v5/rubik/stat/taker-volume",
     {"ccy": "BTC", "instType": "CONTRACTS", "period": "1H"}),
]


def _angka(nilai: Any) -> Optional[float]:
    try:
        angka = float(nilai)
    except (TypeError, ValueError):
        return None
    return angka if angka == angka else None  # buang NaN


def _baris(data: Any) -> List[List[Any]]:
    """Seragamkan bentuk baris OKX.

    OKX mengembalikan array-of-array (`[[ts, ratio], ...]`) pada endpoint lama
    dan array-of-object pada sebagian endpoint baru. Keduanya diterima supaya
    perubahan bentuk di sisi OKX tidak langsung mematikan sumbernya.
    """
    if not isinstance(data, list):
        return []
    hasil: List[List[Any]] = []
    for baris in data:
        if isinstance(baris, list):
            hasil.append(baris)
        elif isinstance(baris, dict):
            # Urutan dict Python mengikuti urutan field pada respons JSON, dan
            # OKX selalu menaruh timestamp lebih dulu.
            hasil.append(list(baris.values()))
    return hasil


def _ambil(kandidat: List[Tuple[str, Dict[str, Any]]], nama: str) -> List[List[Any]]:
    """Coba tiap kandidat endpoint sampai ada yang memberi data."""
    for path, params in kandidat:
        try:
            resp = get_json(BASE + path, params=params, timeout=25)
        except (HttpError, ValueError) as exc:
            log.debug("OKX %s (%s) gagal: %s", nama, path, exc)
            continue
        if not isinstance(resp, dict):
            continue
        if str(resp.get("code", "0")) != "0":
            log.debug("OKX %s (%s) menolak: %s", nama, path, resp.get("msg"))
            continue
        baris = _baris(resp.get("data"))
        if baris:
            log.info("OKX %s diambil dari %s", nama, path)
            return baris
    return []


def _urut_lama_ke_baru(baris: List[List[Any]]) -> List[List[Any]]:
    """OKX mengirim data terbaru lebih dulu; kode di sini butuh urutan naik."""
    if len(baris) < 2:
        return baris
    awal, akhir = _angka(baris[0][0]), _angka(baris[-1][0])
    if awal is not None and akhir is not None and awal > akhir:
        return list(reversed(baris))
    return baris


def _rasio_ke_persen_long(rasio: Optional[float]) -> Optional[float]:
    """Rasio long/short (mis. 1,25) menjadi porsi long dalam persen."""
    if rasio is None or rasio <= 0:
        return None
    return round(rasio / (1 + rasio) * 100, 2)


def _seri_persen_long(baris: List[List[Any]]) -> List[float]:
    seri = []
    for b in baris:
        if len(b) < 2:
            continue
        persen = _rasio_ke_persen_long(_angka(b[1]))
        if persen is not None:
            seri.append(persen)
    return seri


def fetch_rasio_posisi() -> Dict[str, Any]:
    """Porsi long top trader dan seluruh akun, plus trennya.

    Nilai yang tidak berhasil diambil dikembalikan sebagai None — tidak pernah
    ditambal angka dari sisi lain, karena "top trader" dan "seluruh akun"
    bermakna berbeda dan menyamakannya akan memalsukan divergensi.
    """
    hasil: Dict[str, Any] = {
        "whale_long_pct": None, "whale_short_pct": None,
        "whale_ratio": None, "whale_tren_long_pp": None,
        "ritel_long_pct": None, "ritel_short_pct": None,
        "ritel_ratio": None, "ritel_tren_long_pp": None,
    }

    for nama, kandidat, awalan in (
        ("rasio top trader", _KANDIDAT_WHALE, "whale"),
        ("rasio seluruh akun", _KANDIDAT_RITEL, "ritel"),
    ):
        baris = _urut_lama_ke_baru(_ambil(kandidat, nama))
        seri = _seri_persen_long(baris)
        if not seri:
            continue
        hasil[f"{awalan}_long_pct"] = seri[-1]
        hasil[f"{awalan}_short_pct"] = round(100 - seri[-1], 2)
        rasio_terakhir = _angka(baris[-1][1])
        hasil[f"{awalan}_ratio"] = round(rasio_terakhir, 3) if rasio_terakhir else None
        if len(seri) >= 2:
            hasil[f"{awalan}_tren_long_pp"] = round(seri[-1] - seri[0], 2)

    return hasil


def fetch_taker_ratio() -> Dict[str, Any]:
    """Rasio volume taker beli terhadap taker jual, plus arah trennya."""
    baris = _urut_lama_ke_baru(_ambil(_KANDIDAT_TAKER, "volume taker"))
    seri: List[float] = []
    for b in baris:
        # Bentuk baris: [ts, volume_jual, volume_beli]
        if len(b) < 3:
            continue
        jual, beli = _angka(b[1]), _angka(b[2])
        if jual and beli and jual > 0:
            seri.append(beli / jual)

    if not seri:
        return {"taker_buy_sell_ratio": None, "taker_tren": None}

    tengah = max(1, len(seri) // 2)
    awal = sum(seri[:tengah]) / tengah
    akhir = sum(seri[tengah:]) / max(1, len(seri) - tengah)
    return {
        "taker_buy_sell_ratio": round(seri[-1], 3),
        "taker_tren": (
            "beli menguat" if akhir > awal * 1.03
            else "jual menguat" if akhir < awal * 0.97
            else "seimbang"
        ),
    }
