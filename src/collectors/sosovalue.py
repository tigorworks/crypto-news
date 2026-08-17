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


def fetch_etf_flow(api_key: str) -> Dict[str, Any]:
    """Total arus ETF BTC AS harian terakhir yang SUDAH settle.

    Return {"etf_flow_usd": float, "etf_flow_date": "YYYY-MM-DD"}.
    Raise ValueError kalau tidak ada baris yang settle dalam jendela yang
    diambil — itu berarti API-nya mengembalikan data, cuma semuanya masih
    menunggu settlement, bukan kegagalan biasa yang layak retry.
    """
    baris = get_json(
        SUMMARY_URL,
        params={"symbol": "BTC", "country_code": "US", "limit": _LIMIT},
        headers={"x-soso-api-key": api_key},
        timeout=20,
        retries=1,
    )
    if not isinstance(baris, list):
        raise ValueError(f"respons summary-history bukan array: {type(baris).__name__}")

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
