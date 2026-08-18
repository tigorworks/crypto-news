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
_KANDIDAT_OI_HISTORY = [
    ("/api/v5/rubik/stat/contracts/open-interest-volume", {"ccy": "BTC", "period": "1H"}),
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
            # Cepat gagal: ini penjajakan beberapa kandidat URL, jadi kandidat
            # yang buntu harus segera menyerah. Dengan retry default, enam
            # kandidat yang menggantung bisa menahan pipeline bermenit-menit.
            resp = get_json(BASE + path, params=params, timeout=10, retries=0)
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


def fetch_open_interest_history(limit: int = 30) -> List[Dict[str, Any]]:
    """Riwayat OI per jam — cadangan KETIGA setelah Binance dan Bybit gagal.

    Sebelum ini ada, satu-satunya jalan saat kedua bursa itu gagal adalah
    membandingkan OI hari ini dengan OI di brief KEMARIN (lihat main.py) —
    valid tapi cuma satu titik pembanding, jadi tidak bisa mendeteksi tren
    dalam sehari. Endpoint ini memberi granularitas per jam.

    Satuan OI dari OKX (kontrak vs USD) tidak dipastikan sama dengan Binance/
    Bybit, dan itu TIDAK masalah di sini: oi_price_signal() cuma memakai
    persentase perubahan, bukan nilai mutlaknya, jadi konsisten antar-baris
    sudah cukup.
    """
    baris = _urut_lama_ke_baru(_ambil(_KANDIDAT_OI_HISTORY, "riwayat OI"))
    hasil: List[Dict[str, Any]] = []
    for b in baris[-limit:]:
        if len(b) < 2:
            continue
        ts, oi = _angka(b[0]), _angka(b[1])
        if ts is None or oi is None:
            continue
        hasil.append({"timestamp": int(ts), "open_interest": oi})
    return hasil


def fetch_funding_rate_history(limit: int = 24) -> List[Dict[str, Any]]:
    """Riwayat funding rate (fraksi, bukan persen), lama ke baru.

    Dipakai untuk membedakan funding yang persisten (long/short crowded
    berhari-hari — sinyal kuat) dari lonjakan satu kali yang nyaris tidak
    berarti. Endpoint publik OKX, tidak butuh API key.
    """
    try:
        resp = get_json(
            f"{BASE}/api/v5/public/funding-rate-history",
            params={"instId": INST_ID, "limit": min(limit, 100)},
            timeout=10,
            retries=1,
        )
    except (HttpError, ValueError) as exc:
        log.warning("Riwayat funding rate OKX gagal: %s", exc)
        return []
    if not isinstance(resp, dict) or str(resp.get("code", "0")) != "0":
        return []

    hasil: List[Dict[str, Any]] = []
    for r in resp.get("data") or []:
        if not isinstance(r, dict):
            continue
        rate = _angka(r.get("fundingRate"))
        waktu = _angka(r.get("fundingTime"))
        if rate is None or waktu is None:
            continue
        hasil.append({"timestamp": int(waktu), "funding_rate": rate})
    hasil.sort(key=lambda r: r["timestamp"])
    return hasil[-limit:]


def tren_funding(history: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Berapa lama funding bertahan di sisi yang sama — bukan cuma titik terakhir.

    Funding positif SATU KALI nyaris tidak berarti; funding positif yang
    bertahan berhari-hari (long crowded, dibayar terus-menerus) adalah sinyal
    yang jauh lebih kuat. OKX BTC funding tiap 8 jam, jadi count periode
    dikonversi ke perkiraan hari lewat pembagi 3.
    """
    if not history:
        return {"funding_persisten_jam": None, "funding_rata_7h_pct": None}

    tanda_terakhir = history[-1]["funding_rate"] >= 0
    berturut = 0
    for h in reversed(history):
        if (h["funding_rate"] >= 0) != tanda_terakhir:
            break
        berturut += 1

    tujuh_hari = history[-21:]  # ~7 hari pada interval 8 jam
    rata = sum(h["funding_rate"] for h in tujuh_hari) / len(tujuh_hari)

    return {
        # 8 jam/periode adalah interval funding BTC OKX.
        "funding_persisten_jam": berturut * 8,
        "funding_rata_7h_pct": round(rata * 100, 4),
    }


# --------------------------------------------------------------------------
# Harga & candle — cadangan SEBELUM CoinGecko
# --------------------------------------------------------------------------
# Binance menolak IP runner GitHub Actions (HTTP 451) pada setiap run, jadi
# harga selalu jatuh ke CoinGecko. Masalahnya CoinGecko tidak menyediakan
# OHLCV per interval: candle-nya di-RESAMPLE dari deret harga, sehingga
# high/low/volume tiap candle cuma perkiraan. Seluruh indikator teknikal
# (ATR, Bollinger, volume rata-rata, sapuan likuiditas) dihitung dari situ.
#
# OKX menyediakan OHLCV sungguhan dan terbukti tembus dari IP yang sama —
# rasio whale, taker, dan riwayat OI semuanya berhasil lewat sini. Jadi OKX
# ditaruh sebagai cadangan PERTAMA: candle asli jauh lebih baik daripada
# candle hasil resampling.
# Bar harian memakai "1Dutc", BUKAN "1D".
#
# Bar "1D" OKX diselaraskan ke waktu Hong Kong, sehingga candle hariannya
# dibuka pukul 16.00 UTC — bukan 00.00 UTC seperti Binance. Perbedaan itu
# tidak terlihat di harga, tapi merusak semua yang bergantung pada BATAS
# HARI, dan kegagalannya senyap.
#
# Terbukti pada brief 18 Agustus 06.33 WIB: saat Binance terblokir dan data
# jatuh ke OKX, candle "harian" terakhir dibuka 17 Agustus 16.00 UTC,
# sehingga pada jam cron (23.15 UTC) ia baru berjalan 7,3 jam — 31% terisi,
# bukan 97% seperti yang diasumsikan jadwalnya. Volume harian ikut terbaca
# 0,44x rata-rata padahal harinya belum berjalan sepertiga.
#
# Dengan "1Dutc" batas harinya sama dengan Binance, jadi jadwal cron dan
# penjaga kelengkapan candle berlaku untuk kedua sumber.
_INTERVAL_OKX = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1H", "4h": "4H", "1d": "1Dutc"}
SPOT_INST = "BTC-USDT"


def fetch_price() -> Dict[str, Any]:
    """Harga spot + statistik 24 jam dari ticker OKX."""
    resp = get_json(
        f"{BASE}/api/v5/market/ticker", params={"instId": SPOT_INST}, timeout=15, retries=1
    )
    if not isinstance(resp, dict) or str(resp.get("code", "0")) != "0":
        raise ValueError(f"ticker OKX menolak: {resp}")
    baris = (resp.get("data") or [None])[0]
    if not isinstance(baris, dict):
        raise ValueError("ticker OKX tidak mengembalikan data")

    terakhir = _angka(baris.get("last"))
    buka24 = _angka(baris.get("open24h"))
    if terakhir is None:
        raise ValueError("ticker OKX tidak memuat harga terakhir")

    return {
        "last": terakhir,
        "change_24h_pct": (
            round((terakhir - buka24) / buka24 * 100, 2) if buka24 else None
        ),
        "high_24h": _angka(baris.get("high24h")),
        "low_24h": _angka(baris.get("low24h")),
        # volCcy24h = volume dalam mata uang quote (USDT), setara volume_24h
        # Binance dalam USD — bukan volCcy dalam BTC.
        "volume_24h": _angka(baris.get("volCcy24h")),
    }


def fetch_klines(interval: str, limit: int) -> List[Dict[str, Any]]:
    """Candle OHLCV sungguhan, diurutkan lama ke baru.

    OKX membalas terbaru-dulu dan membatasi 300 baris per permintaan; kode
    lain di proyek ini mengasumsikan elemen terakhir yang terkini.
    """
    bar = _INTERVAL_OKX.get(interval)
    if bar is None:
        raise ValueError(f"interval '{interval}' tidak dikenali OKX")

    resp = get_json(
        f"{BASE}/api/v5/market/candles",
        params={"instId": SPOT_INST, "bar": bar, "limit": min(limit, 300)},
        timeout=15,
        retries=1,
    )
    if not isinstance(resp, dict) or str(resp.get("code", "0")) != "0":
        raise ValueError(f"candles OKX menolak: {resp}")

    hasil: List[Dict[str, Any]] = []
    for r in resp.get("data") or []:
        # [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
        if not isinstance(r, list) or len(r) < 7:
            continue
        nilai = [_angka(x) for x in r[:7]]
        if any(v is None for v in nilai[:5]):
            continue
        ts = int(nilai[0])
        hasil.append({
            "open_time": ts,
            "open": nilai[1], "high": nilai[2], "low": nilai[3], "close": nilai[4],
            # volCcy (indeks 6) = volume dalam USDT, sepadan dengan volume
            # quote Binance yang dipakai di seluruh analisa teknikal.
            "volume": nilai[6] if nilai[6] is not None else nilai[5],
            "close_time": ts - 1,
        })
    if not hasil:
        raise ValueError("candles OKX kosong")

    hasil.sort(key=lambda c: c["open_time"])
    # close_time baru bisa dihitung setelah urut: jaraknya = selisih antar bar.
    if len(hasil) >= 2:
        lebar = hasil[1]["open_time"] - hasil[0]["open_time"]
        for c in hasil:
            c["close_time"] = c["open_time"] + lebar - 1
    return hasil[-limit:]
