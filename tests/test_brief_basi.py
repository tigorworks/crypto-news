"""Peringatan brief basi muncul tepat pada ambangnya, tidak lebih cepat.

Kalau cron gagal beberapa hari, halaman tetap menampilkan brief lama dengan
label "3 hari lalu" yang mudah terlewat — nadanya sama persis dengan "20
menit lalu". Uji ini menjaga dua sisi sekaligus: peringatannya benar-benar
muncul saat basi, dan TIDAK muncul pada keterlambatan cron yang wajar.
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

SEKARANG = datetime(2026, 8, 21, 6, 0, tzinfo=timezone.utc)


def _brief_berumur(brief: dict, jam: float) -> dict:
    brief = copy.deepcopy(brief)
    dibuat = SEKARANG - timedelta(hours=jam)
    brief["generated_at"] = dibuat.isoformat().replace("+00:00", "Z")
    return brief


def _teks_header(peramban, alamat):
    konteks = peramban.new_context()
    konteks.clock.install(time=SEKARANG)
    page = konteks.new_page()
    page.goto(alamat)
    page.wait_for_selector("header", timeout=10_000)
    teks = page.inner_text("header")
    konteks.close()
    return teks


def test_tidak_ada_peringatan_saat_cron_telat_wajar(
    peramban, alamat, tulis_data, brief_asli
):
    # 26 jam: cron GitHub kerap tertunda; ini masih terbit normal.
    tulis_data(_brief_berumur(brief_asli, 26))
    assert "Jadwal terbit harian tampaknya terlewat" not in _teks_header(peramban, alamat)


def test_peringatan_muncul_saat_brief_lewat_36_jam(
    peramban, alamat, tulis_data, brief_asli
):
    tulis_data(_brief_berumur(brief_asli, 40))
    teks = _teks_header(peramban, alamat)
    assert "Jadwal terbit harian tampaknya terlewat" in teks
    assert "sudah 1 hari lalu" in teks


def test_peringatan_masih_ada_setelah_beberapa_hari(
    peramban, alamat, tulis_data, brief_asli
):
    tulis_data(_brief_berumur(brief_asli, 24 * 4))
    teks = _teks_header(peramban, alamat)
    assert "Jadwal terbit harian tampaknya terlewat" in teks
    assert "4 hari lalu" in teks
