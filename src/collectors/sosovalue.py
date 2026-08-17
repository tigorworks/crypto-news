"""Arus ETF BTC lewat API resmi SoSoValue — pengganti scrape Farside.

Farside di belakang Cloudflare dan menolak IP pusat data (403 "Just a
moment..."), permanen dari GitHub Actions. SoSoValue punya API resmi
terdokumentasi (https://sosovalue.gitbook.io/soso-value-api-doc/) yang
langsung memberi TOTAL gabungan seluruh ETF BTC AS per hari — persis kolom
"Total" di Farside — tanpa perlu menjumlahkan sendiri per ticker.

Butuh API key (env `SOSO_KEY`, gratis lewat sosovalue.com/developer).
Kalau key kosong, langkah ini dilewati dan `market.py` jatuh ke Farside.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from ..utils.http import HttpError, get_json

log = logging.getLogger(__name__)

SUMMARY_URL = "https://openapi.sosovalue.com/api/v1/etfs/summary-history"

# Baris hari berjalan belum settle (T+1): total_net_inflow dkk masih null
# sampai hari berikutnya. Ambil beberapa baris ke belakang supaya selalu ada
# baris yang sudah settle untuk dipakai, bukan cuma baris pertama yang
# kemungkinan besar kosong pada run pagi.
_LIMIT = 5


# Kunci envelope yang lazim dipakai API bergaya ini (OKX, CoinGlass, dst
# membungkus array datanya di dalam objek {"code":..., "data":[...]}).
# Contoh respons di dokumentasi SoSoValue menampilkan array telanjang, tapi
# dokumentasi sering menyederhanakan dan cuma menunjukkan isi field data-nya
# — jadi keduanya ditangani, bukan diasumsikan salah satu.
_KUNCI_ENVELOPE = ("data", "result", "list", "items")


def _cari_array(mentah: Any) -> Optional[list]:
    """Temukan array baris di dalam respons, telanjang atau terbungkus envelope."""
    if isinstance(mentah, list):
        return mentah
    if isinstance(mentah, dict):
        for kunci in _KUNCI_ENVELOPE:
            nilai = mentah.get(kunci)
            if isinstance(nilai, list):
                return nilai
    return None


def _ringkas_respons(mentah: Any) -> str:
    """Ringkasan respons untuk pesan error — supaya kegagalan berikutnya
    langsung terbaca DARI PESAN LOG-nya, tanpa perlu menebak lagi.

    API bergaya ini biasanya menaruh alasan gagal di field `code`/`msg` atau
    `message`/`error` saat autentikasi salah atau limit habis. Field itu
    ditonjolkan lebih dulu kalau ada; sisanya dipotong supaya log tidak
    kebanjiran.
    """
    if isinstance(mentah, dict):
        petunjuk = {
            k: mentah[k] for k in ("code", "msg", "message", "error", "success")
            if k in mentah
        }
        if petunjuk:
            return f"{petunjuk} (bentuk lengkap: {str(mentah)[:300]})"
    return f"{type(mentah).__name__}: {str(mentah)[:300]}"


def fetch_etf_flow(api_key: str) -> Dict[str, Any]:
    """Total arus ETF BTC AS harian terakhir yang SUDAH settle.

    Return {"etf_flow_usd": float, "etf_flow_date": "YYYY-MM-DD"}.
    Raise ValueError kalau tidak ada baris yang settle dalam jendela yang
    diambil — itu berarti API-nya mengembalikan data, cuma semuanya masih
    menunggu settlement, bukan kegagalan biasa yang layak retry.
    """
    mentah = get_json(
        SUMMARY_URL,
        params={"symbol": "BTC", "country_code": "US", "limit": _LIMIT},
        headers={"x-soso-api-key": api_key},
        timeout=20,
        retries=1,
    )
    baris = _cari_array(mentah)
    if baris is None:
        raise ValueError(
            f"respons summary-history tidak memuat array yang dikenali: {_ringkas_respons(mentah)}"
        )

    # Data.sudah terurut terbaru dulu (dikonfirmasi dokumentasi), tapi
    # diurutkan ulang di sini juga supaya tidak bergantung pada urutan API
    # yang mungkin berubah tanpa pemberitahuan.
    for b in sorted(baris, key=lambda x: str(x.get("date") or ""), reverse=True):
        nilai = b.get("total_net_inflow")
        tanggal = b.get("date")
        if nilai is None or not tanggal:
            continue
        return {"etf_flow_usd": round(float(nilai), 0), "etf_flow_date": str(tanggal)}

    raise ValueError(
        f"tidak ada baris yang sudah settle dalam {_LIMIT} hari terakhir dari SoSoValue"
    )
