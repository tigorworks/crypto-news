"""Agregat likuidasi 24 jam lewat REST — tanpa WebSocket.

KENAPA BARU SEKARANG: likuidasi adalah satu-satunya butir yang bertahan
lama di daftar tugas repo ini, karena sumber yang lazim dipakai orang
(stream likuidasi bursa) menuntut koneksi WebSocket yang HIDUP TERUS,
sementara pipeline ini berjalan sekali lalu mati. Menyalakan WebSocket
selama beberapa detik per run hanya akan menangkap likuidasi yang kebetulan
terjadi dalam detik-detik itu — yang lebih buruk daripada tidak punya data,
karena angkanya terlihat sah padahal cuma cuplikan acak.

Jalan keluarnya endpoint REST yang menyimpan RIWAYAT order likuidasi: sekali
ambil, cakupannya jam-jaman ke belakang, dan hasilnya sama untuk siapa pun
yang memanggilnya.

BATAS YANG HARUS JUJUR DISEBUT: angka di sini berasal dari SATU bursa
(kontrak perpetual BTC di OKX), bukan seluruh pasar. Situs agregator
menjumlahkan belasan bursa dan angkanya akan jauh lebih besar; itu bukan
pertanda salah satunya keliru. Karena itu setiap field membawa `sumber` dan
`cakupan_jam`, dan tampilan/prosa wajib menyebut bursanya — bukan
menyodorkannya seolah likuidasi seluruh pasar.

Gagal di sini tidak menggagalkan apa pun: brief terbit tanpa blok likuidasi.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..utils.http import HttpError, get_json
from ..utils.timezone import now_utc

log = logging.getLogger(__name__)

BASE = "https://www.okx.com"
JALUR = "/api/v5/public/liquidation-orders"

#: Kontrak yang dibaca dan nilai satu kontraknya dalam BTC. OKX menghitung
#: ukuran order dalam KONTRAK, bukan BTC — mengalikan tanpa pengali ini
#: menghasilkan angka yang meleset seratus kali lipat.
INST_FAMILY = "BTC-USDT"
KONTRAK_KE_BTC = 0.01

#: Nama parameter untuk memilih kelompok kontrak sempat berganti (`uly` di
#: versi lama, `instFamily` di versi baru). Keduanya dicoba, seperti pola
#: yang sudah dipakai collectors/okx.py — katalog endpoint OKX memang
#: berubah tanpa pemberitahuan.
_VARIAN_PARAM = (
    {"instType": "SWAP", "instFamily": INST_FAMILY, "state": "filled", "limit": "100"},
    {"instType": "SWAP", "uly": INST_FAMILY, "state": "filled", "limit": "100"},
)

#: Berapa halaman riwayat ditelusuri. Tiap halaman 100 order; pada hari
#: normal 24 jam jauh di bawah itu, tapi hari likuidasi besar bisa
#: menghabiskan beberapa halaman. Dibatasi supaya satu sumber opsional tidak
#: pernah menahan pipeline lama-lama.
_MAKS_HALAMAN = 5

_JENDELA_JAM = 24


def _angka(nilai: Any) -> Optional[float]:
    try:
        hasil = float(nilai)
    except (TypeError, ValueError):
        return None
    return hasil if hasil == hasil else None


def _rincian(data: Any) -> List[Dict[str, Any]]:
    """Ratakan bentuk respons OKX jadi satu daftar order likuidasi.

    OKX mengelompokkan per instrumen: `[{instId, details: [...]}]`. Bentuk
    datar juga diterima kalau suatu saat responsnya disederhanakan.
    """
    keluar: List[Dict[str, Any]] = []
    for baris in data if isinstance(data, list) else []:
        if not isinstance(baris, dict):
            continue
        detail = baris.get("details")
        if isinstance(detail, list):
            for d in detail:
                if isinstance(d, dict):
                    keluar.append({**d, "instId": d.get("instId") or baris.get("instId")})
        elif baris.get("sz") is not None:
            keluar.append(baris)
    return keluar


def _ambil_halaman(params: Dict[str, str]) -> List[Dict[str, Any]]:
    resp = get_json(BASE + JALUR, params=params, timeout=10, retries=0)
    if not isinstance(resp, dict) or str(resp.get("code", "0")) != "0":
        pesan = resp.get("msg") if isinstance(resp, dict) else str(resp)[:120]
        raise ValueError(f"OKX menolak permintaan likuidasi: {pesan}")
    return _rincian(resp.get("data"))


def fetch_agregat(jendela_jam: int = _JENDELA_JAM) -> Dict[str, Any]:
    """Total likuidasi long dan short dalam jendela terakhir.

    Raise ValueError/HttpError kalau tidak ada varian endpoint yang menjawab.
    """
    batas_ms = (now_utc().timestamp() - jendela_jam * 3600) * 1000

    order: List[Dict[str, Any]] = []
    dipakai: Optional[Dict[str, str]] = None
    galat: List[str] = []
    for varian in _VARIAN_PARAM:
        try:
            halaman = _ambil_halaman(dict(varian))
        except (HttpError, ValueError) as exc:
            galat.append(str(exc)[:120])
            continue
        if halaman:
            order = halaman
            dipakai = dict(varian)
            break

    if dipakai is None:
        raise ValueError("; ".join(galat) or "tidak ada data likuidasi dari OKX")

    # Halaman berikutnya: OKX memakai `after` = timestamp order tertua yang
    # sudah didapat. Berhenti begitu ordernya lebih tua dari jendela, ATAU
    # begitu halaman baru tidak membawa order yang lebih tua — tanpa syarat
    # kedua itu, endpoint yang mengabaikan kursor akan dijumlahkan berkali-
    # kali dan totalnya membengkak sebanyak jumlah halaman yang diminta.
    def _tertua(daftar: List[Dict[str, Any]]) -> Optional[float]:
        stempel = [_angka(o.get("ts")) for o in daftar]
        return min([t for t in stempel if t is not None], default=None)

    tertua = _tertua(order)
    for _ in range(_MAKS_HALAMAN - 1):
        if tertua is None or tertua <= batas_ms:
            break
        lanjut = dict(dipakai)
        lanjut["after"] = str(int(tertua))
        try:
            halaman = _ambil_halaman(lanjut)
        except (HttpError, ValueError):
            break
        tertua_baru = _tertua(halaman)
        if not halaman or tertua_baru is None or tertua_baru >= tertua:
            break
        order.extend(halaman)
        tertua = tertua_baru

    long_usd = short_usd = 0.0
    jumlah_long = jumlah_short = 0
    terbesar: Optional[Dict[str, Any]] = None
    tertua_dipakai: Optional[float] = None

    for o in order:
        ts = _angka(o.get("ts"))
        ukuran = _angka(o.get("sz"))
        harga = _angka(o.get("bkPx")) or _angka(o.get("fillPx"))
        if ts is None or ts < batas_ms or not ukuran or not harga:
            continue
        nilai = ukuran * KONTRAK_KE_BTC * harga
        tertua_dipakai = ts if tertua_dipakai is None else min(tertua_dipakai, ts)

        # `side` adalah sisi ORDER LIKUIDASINYA, bukan sisi posisi yang kena.
        # Posisi beli (long) dilikuidasi dengan cara DIJUAL, jadi side "sell"
        # berarti long yang terlikuidasi. Tertukar di sini akan membalik
        # seluruh maknanya, jadi jangan disederhanakan.
        if str(o.get("side")).lower() == "sell":
            long_usd += nilai
            jumlah_long += 1
        else:
            short_usd += nilai
            jumlah_short += 1

        if terbesar is None or nilai > terbesar["nilai_usd"]:
            terbesar = {"nilai_usd": round(nilai, 0), "sisi": "long" if str(o.get("side")).lower() == "sell" else "short"}

    total = long_usd + short_usd
    if not total:
        raise ValueError(f"tidak ada order likuidasi dalam {jendela_jam} jam terakhir")

    dominan = "long" if long_usd > short_usd * 1.2 else "short" if short_usd > long_usd * 1.2 else "seimbang"
    return {
        "likuidasi_long_usd": round(long_usd, 0),
        "likuidasi_short_usd": round(short_usd, 0),
        "likuidasi_total_usd": round(total, 0),
        "likuidasi_jumlah_order": jumlah_long + jumlah_short,
        "likuidasi_sisi_dominan": dominan,
        "likuidasi_terbesar": terbesar,
        # Cakupan yang SEBENARNYA terpakai, bukan yang diminta: kalau riwayat
        # bursanya lebih pendek dari 24 jam, angka di atas menggambarkan
        # jendela yang lebih sempit dan pembaca berhak tahu.
        "likuidasi_cakupan_jam": (
            round((now_utc().timestamp() * 1000 - tertua_dipakai) / 3_600_000, 1)
            if tertua_dipakai else None
        ),
        "likuidasi_sumber": f"OKX {INST_FAMILY}-SWAP",
    }


def collect(jendela_jam: int = _JENDELA_JAM) -> Dict[str, Any]:
    """Bentuk seragam seperti collector lain: {"data": {...}, "failed": [...]}"""
    try:
        data = fetch_agregat(jendela_jam)
    except (HttpError, ValueError, KeyError, TypeError) as exc:
        log.warning("Agregat likuidasi gagal: %s", exc)
        return {"data": {}, "failed": ["likuidasi"]}

    log.info(
        "Likuidasi %sj: long $%.0f · short $%.0f (%s, %d order)",
        jendela_jam, data["likuidasi_long_usd"], data["likuidasi_short_usd"],
        data["likuidasi_sisi_dominan"], data["likuidasi_jumlah_order"],
    )
    return {"data": data, "failed": []}
