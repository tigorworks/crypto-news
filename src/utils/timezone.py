"""Helper waktu WIB (UTC+7) dan format tanggal bahasa Indonesia."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

WIB = timezone(timedelta(hours=7), name="WIB")

BULAN = [
    "Januari", "Februari", "Maret", "April", "Mei", "Juni",
    "Juli", "Agustus", "September", "Oktober", "November", "Desember",
]

BULAN_SINGKAT = [
    "Jan", "Feb", "Mar", "Apr", "Mei", "Jun",
    "Jul", "Agu", "Sep", "Okt", "Nov", "Des",
]

HARI = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_wib() -> datetime:
    return datetime.now(WIB)


def to_wib(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(WIB)


def to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso_utc(dt: Optional[datetime] = None) -> str:
    """ISO 8601 UTC dengan sufiks Z."""
    dt = to_utc(dt or now_utc())
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def format_wib(dt: Optional[datetime] = None) -> str:
    """Contoh: '15 Agustus 2026, 07:12 WIB'."""
    d = to_wib(dt or now_utc())
    return f"{d.day} {BULAN[d.month - 1]} {d.year}, {d:%H:%M} WIB"


def format_wib_singkat(dt: Optional[datetime] = None) -> str:
    """Contoh: '15 Agu 2026, 07:12'."""
    d = to_wib(dt or now_utc())
    return f"{d.day} {BULAN_SINGKAT[d.month - 1]} {d.year}, {d:%H:%M}"


def format_tanggal_singkat(dt: datetime) -> str:
    """Contoh: '16 Agu'."""
    d = to_wib(dt)
    return f"{d.day} {BULAN_SINGKAT[d.month - 1]}"


def nama_hari(dt: datetime) -> str:
    return HARI[to_wib(dt).weekday()]


def run_type(dt: Optional[datetime] = None) -> str:
    """Jenis run berdasarkan jam WIB: pagi (< 12) atau sore."""
    return "pagi" if to_wib(dt or now_utc()).hour < 12 else "sore"


def slug_arsip(dt: Optional[datetime] = None) -> str:
    """Nama file arsip berbasis waktu WIB, contoh '2026-08-15-0700'."""
    d = to_wib(dt or now_utc())
    return d.strftime("%Y-%m-%d-%H%M")
