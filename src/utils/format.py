"""Format angka gaya Indonesia: titik ribuan, koma desimal.

Dipakai untuk teks yang dilihat pengguna dan dirakit di sisi Python
(keterangan sinyal, ringkasan diff, pesan Telegram). Sisi web memformat
angkanya sendiri lewat toLocaleString('id-ID').
"""

from __future__ import annotations

from typing import Optional


def angka_id(nilai: Optional[float], desimal: int = 0) -> str:
    """Contoh: 167806.21 -> '167.806,21'. None -> '—'."""
    if nilai is None:
        return "—"
    try:
        teks = f"{float(nilai):,.{desimal}f}"
    except (TypeError, ValueError):
        return "—"
    # Tukar pemisah: ',' dan '.' bergantian lewat penanda sementara.
    return teks.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def persen_id(nilai: Optional[float], desimal: int = 2, pakai_tanda: bool = False) -> str:
    """Contoh: -1.234 -> '-1,23%'. pakai_tanda menambah '+' untuk nilai positif."""
    if nilai is None:
        return "—"
    try:
        tanda = "+" if (pakai_tanda and float(nilai) > 0) else ""
    except (TypeError, ValueError):
        return "—"
    return f"{tanda}{angka_id(nilai, desimal)}%"
