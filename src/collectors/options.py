"""Data opsi Deribit — posisi institusional yang tidak terlihat di grafik spot.

Deribit menguasai mayoritas volume opsi BTC, dan API publiknya gratis tanpa
key. Ini data yang biasanya dijual mahal oleh penyedia analitik, padahal
tersedia terbuka.

Kenapa penting untuk brief harian:

  DVOL              Indeks volatilitas implied BTC — "VIX"-nya Bitcoin.
                    Naik = pasar membayar mahal untuk proteksi.
  Put/call ratio    Berapa banyak proteksi turun dibanding taruhan naik.
                    Rasio tinggi = pelaku pasar melindungi diri.
  Skew 25 delta     Selisih IV put vs call pada delta yang sama. Positif
                    berarti put lebih mahal — ketakutan berbayar.
  Max pain          Strike yang membuat pemegang opsi paling rugi saat
                    expiry. Harga cenderung tertarik ke sana menjelang
                    expiry besar karena penulis opsi melindungi posisinya.

Semua angka di sini dihitung kode dari data mentah Deribit, bukan diambil
dari ringkasan pihak lain.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..utils.http import HttpError, get_json

log = logging.getLogger(__name__)

BASE = "https://www.deribit.com/api/v2/public"

# Format nama instrumen Deribit: BTC-26DEC25-100000-C
POLA_INSTRUMEN = re.compile(r"^BTC-(\d{1,2}[A-Z]{3}\d{2})-(\d+)-([CP])$")
BULAN = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def _parse_instrumen(nama: str) -> Optional[Tuple[datetime, float, str]]:
    """Pecah nama instrumen jadi (expiry, strike, tipe)."""
    cocok = POLA_INSTRUMEN.match(nama)
    if not cocok:
        return None
    tanggal, strike, tipe = cocok.groups()
    try:
        hari = int(tanggal[:-5])
        bulan = BULAN[tanggal[-5:-2]]
        tahun = 2000 + int(tanggal[-2:])
        # Opsi Deribit kedaluwarsa 08:00 UTC.
        return datetime(tahun, bulan, hari, 8, 0, tzinfo=timezone.utc), float(strike), tipe
    except (ValueError, KeyError):
        return None


def _dvol() -> Dict[str, Any]:
    """Indeks volatilitas implied Deribit, plus perubahannya sepekan."""
    sekarang = datetime.now(timezone.utc)
    data = get_json(
        f"{BASE}/get_volatility_index_data",
        params={
            "currency": "BTC",
            "start_timestamp": int((sekarang - timedelta(days=8)).timestamp() * 1000),
            "end_timestamp": int(sekarang.timestamp() * 1000),
            "resolution": "43200",  # 12 jam
        },
        timeout=30,
    )
    baris = (data.get("result") or {}).get("data") or []
    if not baris:
        raise ValueError("DVOL kosong")

    # Tiap baris: [timestamp, open, high, low, close]
    terkini = float(baris[-1][4])
    sepekan_lalu = float(baris[0][4])
    penutupan = [float(b[4]) for b in baris]

    return {
        "dvol": round(terkini, 2),
        "dvol_perubahan_7h_pp": round(terkini - sepekan_lalu, 2),
        "dvol_min_7h": round(min(penutupan), 2),
        "dvol_maks_7h": round(max(penutupan), 2),
    }


def _max_pain(strikes: Dict[float, Dict[str, float]], ) -> Optional[float]:
    """Strike yang membuat total nilai opsi in-the-money paling kecil.

    Untuk tiap kandidat harga penyelesaian K, hitung total pembayaran yang
    harus ditanggung penulis opsi. Titik dengan pembayaran terkecil adalah
    max pain — posisi yang paling menguntungkan penulis opsi.
    """
    if not strikes:
        return None
    kandidat = sorted(strikes)
    terbaik, nyeri_terkecil = None, None
    for k in kandidat:
        nyeri = 0.0
        for strike, oi in strikes.items():
            if strike < k:
                nyeri += oi["call"] * (k - strike)   # call ITM
            elif strike > k:
                nyeri += oi["put"] * (strike - k)    # put ITM
        if nyeri_terkecil is None or nyeri < nyeri_terkecil:
            nyeri_terkecil, terbaik = nyeri, k
    return terbaik


def _ringkasan_opsi() -> Dict[str, Any]:
    """Agregasi seluruh rantai opsi BTC yang aktif."""
    data = get_json(
        f"{BASE}/get_book_summary_by_currency",
        params={"currency": "BTC", "kind": "option"},
        timeout=45,
    )
    instrumen = data.get("result") or []
    if not instrumen:
        raise ValueError("rantai opsi kosong")

    sekarang = datetime.now(timezone.utc)
    oi_call = oi_put = 0.0
    iv_call: List[Tuple[float, float]] = []   # (jarak_ke_atm, iv)
    iv_put: List[Tuple[float, float]] = []
    per_expiry: Dict[datetime, float] = defaultdict(float)
    strikes_terdekat: Dict[float, Dict[str, float]] = defaultdict(lambda: {"call": 0.0, "put": 0.0})
    harga_dasar: Optional[float] = None
    expiry_terdekat: Optional[datetime] = None

    terurai = []
    for item in instrumen:
        info = _parse_instrumen(item.get("instrument_name", ""))
        if not info:
            continue
        expiry, strike, tipe = info
        if expiry <= sekarang:
            continue
        oi = float(item.get("open_interest") or 0)
        terurai.append((expiry, strike, tipe, oi, item))
        per_expiry[expiry] += oi
        if harga_dasar is None and item.get("underlying_price"):
            harga_dasar = float(item["underlying_price"])
        if expiry_terdekat is None or expiry < expiry_terdekat:
            expiry_terdekat = expiry

    if not terurai or harga_dasar is None:
        raise ValueError("tidak ada instrumen opsi yang bisa diurai")

    for expiry, strike, tipe, oi, item in terurai:
        if tipe == "C":
            oi_call += oi
        else:
            oi_put += oi
        # IV hanya diambil dari strike dekat ATM supaya rata-ratanya bermakna.
        iv = item.get("mark_iv")
        if iv:
            jarak = abs(strike - harga_dasar) / harga_dasar
            if jarak <= 0.15:
                (iv_call if tipe == "C" else iv_put).append((jarak, float(iv)))
        if expiry == expiry_terdekat:
            strikes_terdekat[strike]["call" if tipe == "C" else "put"] += oi

    def rata_iv(pasangan: List[Tuple[float, float]]) -> Optional[float]:
        if not pasangan:
            return None
        return round(sum(iv for _, iv in pasangan) / len(pasangan), 2)

    iv_c, iv_p = rata_iv(iv_call), rata_iv(iv_put)
    # Skew: IV put dikurangi IV call di sekitar ATM. Positif = proteksi mahal.
    skew = round(iv_p - iv_c, 2) if (iv_c is not None and iv_p is not None) else None

    expiry_besar = max(per_expiry.items(), key=lambda kv: kv[1]) if per_expiry else None

    return {
        "put_call_ratio_oi": round(oi_put / oi_call, 3) if oi_call else None,
        "oi_call_btc": round(oi_call, 1),
        "oi_put_btc": round(oi_put, 1),
        "iv_atm_call": iv_c,
        "iv_atm_put": iv_p,
        "skew_put_call": skew,
        "max_pain_expiry_terdekat": _max_pain(strikes_terdekat),
        "expiry_terdekat": expiry_terdekat.strftime("%Y-%m-%dT%H:%M:%SZ") if expiry_terdekat else None,
        "expiry_oi_terbesar": expiry_besar[0].strftime("%Y-%m-%dT%H:%M:%SZ") if expiry_besar else None,
        "oi_pada_expiry_terbesar_btc": round(expiry_besar[1], 1) if expiry_besar else None,
        "harga_dasar_deribit": round(harga_dasar, 2),
    }


def perp_funding_rate() -> Optional[float]:
    """Funding rate perpetual Deribit, dinormalkan ke basis 8 jam.

    Sumber ketiga setelah Binance dan Bybit. Keduanya memblokir IP runner
    GitHub Actions — Binance dengan 451, Bybit lewat CloudFront — sementara
    Deribit terbukti tetap bisa diakses.
    """
    try:
        data = get_json(
            f"{BASE}/ticker", params={"instrument_name": "BTC-PERPETUAL"}, timeout=30
        )
        hasil = data.get("result") or {}
        # funding_8h sudah dalam basis 8 jam, sama seperti Binance dan Bybit.
        nilai = hasil.get("funding_8h")
        if nilai is None:
            nilai = hasil.get("current_funding")
        return float(nilai) if nilai is not None else None
    except (HttpError, ValueError, KeyError, TypeError) as exc:
        log.warning("Funding perpetual Deribit gagal: %s", exc)
        return None


def perp_open_interest() -> Optional[float]:
    """Open interest perpetual Deribit, dikonversi ke BTC.

    Deribit melaporkan OI perpetual dalam USD, sedangkan Binance dan Bybit
    dalam BTC. Dibagi index price supaya satuannya seragam — kalau tidak,
    perbandingan antar run bisa melompat drastis saat sumbernya berganti.
    """
    try:
        data = get_json(
            f"{BASE}/ticker", params={"instrument_name": "BTC-PERPETUAL"}, timeout=30
        )
        hasil = data.get("result") or {}
        oi_usd = hasil.get("open_interest")
        index = hasil.get("index_price")
        if oi_usd is None or not index:
            return None
        return round(float(oi_usd) / float(index), 2)
    except (HttpError, ValueError, KeyError, TypeError, ZeroDivisionError) as exc:
        log.warning("Open interest perpetual Deribit gagal: %s", exc)
        return None


def collect() -> Dict[str, Any]:
    """Kumpulkan data opsi. Boleh gagal sebagian."""
    data: Dict[str, Any] = {}
    failed: List[str] = []

    try:
        data.update(_dvol())
    except (HttpError, ValueError, KeyError, TypeError, IndexError) as exc:
        log.warning("DVOL Deribit gagal: %s", exc)
        failed.append("dvol")

    try:
        data.update(_ringkasan_opsi())
    except (HttpError, ValueError, KeyError, TypeError) as exc:
        log.warning("Rantai opsi Deribit gagal: %s", exc)
        failed.append("opsi")

    if data:
        log.info(
            "Opsi: DVOL %s, put/call %s, max pain %s",
            data.get("dvol"), data.get("put_call_ratio_oi"), data.get("max_pain_expiry_terdekat"),
        )

    return {"data": data, "failed": ["options"] if len(failed) == 2 else []}
