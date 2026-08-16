"""Kalender ekonomi dari feed JSON publik ForexFactory (faireconomy.media).

KENAPA ADA DI SAMPING investing.py

`investing.py` men-scrape halaman HTML lalu menyerahkan ekstraksinya ke LLM —
perlu karena tabelnya dirender JavaScript dan markupnya rumit. Tapi pendekatan
itu punya dua kelemahan: halamannya di belakang proteksi anti-bot (kerap
menolak IP pusat data), dan hasilnya bergantung kepatuhan model.

Feed ini menyelesaikan keduanya sekaligus: JSON terstruktur, tanpa API key,
tanpa proteksi anti-bot, dan parsing-nya deterministik — tidak ada LLM yang
terlibat sama sekali, jadi tidak ada yang bisa dikarang. Karena itu feed ini
dicoba LEBIH DULU; investing.py tetap dipertahankan sebagai cadangan kalau
feed ini suatu saat mati.

Bentuk tiap entri (nama field mengikuti ekspor ForexFactory):

    {"title": "CPI m/m", "country": "USD", "date": "2026-08-19T08:30:00-04:00",
     "impact": "High", "forecast": "0.2%", "previous": "0.3%"}
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..utils.http import HttpError, get_json

log = logging.getLogger(__name__)

BASE = "https://nfs.faireconomy.media"

# Tiga jendela dicoba semuanya lalu digabung: "thisweek" saja tidak cukup
# untuk horizon 30 hari, dan ketiganya saling melengkapi tanpa saling
# menggagalkan kalau salah satunya kosong.
BERKAS = (
    "ff_calendar_thisweek.json",
    "ff_calendar_nextweek.json",
    "ff_calendar_thismonth.json",
)

# Hanya mata uang yang benar-benar menggerakkan aset berisiko global.
# Agenda Selandia Baru tidak pernah menggerakkan BTC.
NEGARA_DIPANTAU = {"USD", "EUR", "CNY"}

# ForexFactory memakai High/Medium/Low; Low cuma menambah derau.
PETA_DAMPAK = {"high": "tinggi", "medium": "menengah"}


def _parse_waktu(nilai: Any) -> Optional[datetime]:
    """Terima ISO-8601 dengan offset zona waktu, kembalikan datetime UTC."""
    if not isinstance(nilai, str) or not nilai.strip():
        return None
    teks = nilai.strip()
    # Python < 3.11 tidak menerima akhiran "Z" pada fromisoformat.
    if teks.endswith("Z"):
        teks = teks[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(teks)
    except ValueError:
        return None
    # Entri tanpa zona waktu dianggap UTC; lebih baik meleset beberapa jam
    # daripada membuang agendanya sama sekali.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _ambil_berkas(nama: str) -> List[Dict[str, Any]]:
    try:
        data = get_json(f"{BASE}/{nama}", timeout=15, retries=0)
    except (HttpError, ValueError) as exc:
        log.debug("Feed kalender %s gagal: %s", nama, exc)
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def collect() -> List[Dict[str, Any]]:
    """Event ekonomi terstruktur, dalam bentuk yang sama seperti calendar.py.

    Selalu mengembalikan list (kosong kalau seluruh feed gagal) — sumber ini
    pelengkap, tidak pernah fatal.
    """
    mentah: List[Dict[str, Any]] = []
    for nama in BERKAS:
        isi = _ambil_berkas(nama)
        if isi:
            log.info("Kalender ekonomi: %d entri dari %s", len(isi), nama)
            mentah.extend(isi)

    if not mentah:
        log.info("Kalender ekonomi ForexFactory tidak terjangkau; dilewati")
        return []

    keluaran: List[Dict[str, Any]] = []
    terlihat = set()
    for item in mentah:
        negara = str(item.get("country") or "").upper()
        if negara not in NEGARA_DIPANTAU:
            continue

        dampak = PETA_DAMPAK.get(str(item.get("impact") or "").lower())
        if not dampak:
            continue

        waktu = _parse_waktu(item.get("date"))
        if waktu is None:
            continue

        judul = str(item.get("title") or "").strip()
        if not judul:
            continue

        # Feed mingguan dan bulanan saling tumpang tindih — event yang sama
        # muncul di dua berkas sekaligus.
        kunci = (judul.lower(), waktu.isoformat())
        if kunci in terlihat:
            continue
        terlihat.add(kunci)

        nama_lengkap = judul if negara == "USD" else f"{judul} ({negara})"
        keluaran.append(
            {
                "waktu_utc": waktu,
                "nama": nama_lengkap[:120],
                "kategori": "ekonomi",
                "dampak": dampak,
                # Tanggal dan jam sungguhan dari kalender resmi, bukan dugaan
                # pola bulanan seperti yang dihitung calendar.py.
                "perkiraan": False,
            }
        )

    log.info("Kalender ekonomi: %d event relevan setelah disaring", len(keluaran))
    return keluaran
