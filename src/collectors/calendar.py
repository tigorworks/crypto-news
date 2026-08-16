"""Agenda ekonomi 30 hari ke depan.

Sengaja tanpa scraping: FOMC dari config, rilis data AS memakai pola tanggal
yang stabil tiap bulan, dan expiry opsi Deribit selalu Jumat terakhir bulan.
Perkiraan tanggal ditandai `perkiraan: true` supaya tidak dianggap pasti.
"""

from __future__ import annotations

import calendar as _calendar
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List

from ..utils.timezone import format_tanggal_singkat, iso_utc, nama_hari, now_utc, to_wib

log = logging.getLogger(__name__)

# Jam rilis dalam UTC. CPI/PCE/NFP AS rilis 08:30 ET = 12:30/13:30 UTC
# tergantung DST; kita pakai 12:30 UTC (19:30 WIB) sebagai perkiraan.
RILIS_UTC = {"cpi": (12, 30), "pce": (12, 30), "nfp": (12, 30), "fomc": (18, 0)}


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """Tanggal ke-n dari suatu hari dalam seminggu (weekday: Senin=0)."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last_day = _calendar.monthrange(year, month)[1]
    d = date(year, month, last_day)
    while d.weekday() != weekday:
        d -= timedelta(days=1)
    return d


def _at(d: date, kind: str) -> datetime:
    hour, minute = RILIS_UTC.get(kind, (12, 30))
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=timezone.utc)


def _months_ahead(start: date, count: int = 2):
    """Yield (year, month) untuk bulan ini dan beberapa bulan berikutnya."""
    year, month = start.year, start.month
    for _ in range(count):
        yield year, month
        month += 1
        if month > 12:
            month = 1
            year += 1


def _candidates(today: date, fomc_dates: List[str]) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []

    for raw in fomc_dates:
        try:
            # TypeError ikut ditangkap: YAML memparsing `2026-01-28` jadi objek
            # date, bukan string. Loader kita sudah menormalkannya jadi string,
            # tapi satu tanggal aneh tidak pantas menggagalkan seluruh agenda.
            d = datetime.strptime(str(raw), "%Y-%m-%d").date()
        except (ValueError, TypeError):
            log.warning("Tanggal FOMC tidak valid di config: %s", raw)
            continue
        events.append(
            {
                "waktu_utc": _at(d, "fomc"),
                "nama": "Keputusan suku bunga FOMC",
                "kategori": "fomc",
                "dampak": "tinggi",
                "perkiraan": False,
            }
        )

    # Tiga bulan, bukan dua: horizon 30 hari dari akhir bulan bisa menembus
    # sampai bulan setelah berikutnya, dan tanpa itu agenda di ujung horizon
    # hilang diam-diam.
    for year, month in _months_ahead(today, 3):
        # CPI AS: umumnya sekitar hari kerja ke-10 bulan berjalan.
        cpi = _nth_weekday(year, month, 2, 2)  # Rabu ke-2 sebagai perkiraan
        events.append(
            {
                "waktu_utc": _at(cpi, "cpi"),
                "nama": "Rilis CPI AS",
                "kategori": "cpi",
                "dampak": "tinggi",
                "perkiraan": True,
            }
        )
        # NFP: Jumat pertama tiap bulan.
        nfp = _nth_weekday(year, month, 4, 1)
        events.append(
            {
                "waktu_utc": _at(nfp, "nfp"),
                "nama": "Rilis Non-Farm Payrolls AS",
                "kategori": "nfp",
                "dampak": "tinggi",
                "perkiraan": True,
            }
        )
        # PCE: umumnya Jumat terakhir bulan.
        pce = _last_weekday(year, month, 4)
        events.append(
            {
                "waktu_utc": _at(pce, "pce"),
                "nama": "Rilis PCE inti AS",
                "kategori": "pce",
                "dampak": "menengah",
                "perkiraan": True,
            }
        )
        # Expiry opsi bulanan Deribit: Jumat terakhir, 08:00 UTC.
        expiry = _last_weekday(year, month, 4)
        events.append(
            {
                "waktu_utc": datetime(expiry.year, expiry.month, expiry.day, 8, 0, tzinfo=timezone.utc),
                "nama": "Expiry opsi bulanan Deribit",
                "kategori": "opsi",
                "dampak": "menengah",
                "perkiraan": False,
            }
        )

    return events


# Kalau investing.com melaporkan tanggal SUNGGUHAN untuk kategori ini pada
# hari yang sama, dugaan pola bulanan kita untuk kategori itu dibuang —
# data konfirmasi menggantikan tebakan, bukan menduplikasinya. Kata kunci
# dicocokkan longgar terhadap nama event investing.com karena penamaannya
# tidak seragam ("CPI m/m", "Core CPI", dst).
_KATA_KUNCI_KATEGORI = {
    "cpi": ("cpi",),
    "nfp": ("non-farm", "nonfarm", "nfp", "payroll"),
    "pce": ("pce",),
}


def _gabung_konfirmasi(kandidat: List[Dict[str, Any]], konfirmasi: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Buang dugaan pola bulanan yang sudah dikonfirmasi oleh sumber luar."""
    if not konfirmasi:
        return kandidat

    hasil = []
    for e in kandidat:
        kata_kunci = _KATA_KUNCI_KATEGORI.get(e["kategori"])
        if kata_kunci:
            tergantikan = any(
                k["waktu_utc"].date() == e["waktu_utc"].date()
                and any(kw in k["nama"].lower() for kw in kata_kunci)
                for k in konfirmasi
            )
            if tergantikan:
                continue
        hasil.append(e)
    return hasil + konfirmasi


def collect(
    fomc_dates: List[str], days_ahead: int = 30, konfirmasi: List[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """Agenda dalam `days_ahead` hari ke depan, terurut dari yang terdekat.

    `konfirmasi` (opsional): event tambahan dari sumber luar dalam bentuk
    yang sama seperti hasil `_candidates()` — dipakai untuk MENGGANTIKAN
    dugaan pola bulanan (CPI/NFP/PCE) dengan tanggal sungguhan kalau ada,
    bukan sekadar ditambahkan sebagai baris duplikat. FOMC dan expiry opsi
    tidak pernah digantikan karena keduanya sudah pasti, bukan dugaan.
    """
    now = now_utc()
    today = now.date()
    horizon = now + timedelta(days=days_ahead)

    kandidat = _gabung_konfirmasi(_candidates(today, fomc_dates), konfirmasi or [])
    upcoming = [e for e in kandidat if now <= e["waktu_utc"] <= horizon]
    upcoming.sort(key=lambda e: e["waktu_utc"])

    out: List[Dict[str, Any]] = []
    for event in upcoming:
        dt = event["waktu_utc"]
        wib = to_wib(dt)
        delta = dt - now
        hours = delta.total_seconds() / 3600
        out.append(
            {
                "waktu_utc": iso_utc(dt),
                "waktu_wib": f"{format_tanggal_singkat(dt)} · {wib:%H:%M} WIB",
                "hari": nama_hari(dt),
                "nama": event["nama"],
                "kategori": event["kategori"],
                "dampak": event["dampak"],
                "perkiraan": event["perkiraan"],
                "jam_lagi": round(hours, 1),
            }
        )

    log.info("Agenda %d hari: %d acara", days_ahead, len(out))
    return out
