"""Agenda: penanda "berlangsung hari ini", dan tidak ada daftar kembar.

Dua hal yang lahir dari audit halaman 22 Agustus:

  1. Chip hitung mundur tampil identik untuk "3 jam lagi" dan "7 hari lagi",
     jadi acara yang menuntut perhatian hari ini tidak pernah menonjol.
  2. Daftar agenda dirender DUA KALI di halaman yang sama — sekali sebagai
     "Katalis berikutnya" di dalam analisa AI, sekali sebagai Agenda 30 Hari.
     Diperiksa terhadap sembilan arsip: tidak satu pun butir "katalis
     berikutnya" membawa peristiwa yang tidak ada di agenda.
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

SEKARANG = datetime(2026, 8, 22, 6, 0, tzinfo=timezone.utc)


def _agenda(nama: str, jam_lagi: float, relevansi: int = 5) -> dict:
    waktu = SEKARANG + timedelta(hours=jam_lagi)
    return {
        "waktu_utc": waktu.isoformat(),
        "waktu_wib": waktu.strftime("%d %b · %H:%M WIB"),
        "hari": "Sabtu",
        "nama": nama,
        "kategori": "cpi",
        "dampak": "tinggi",
        "perkiraan": False,
        "jam_lagi": jam_lagi,
        "relevansi_kripto": relevansi,
        "jalur": "Inflasi lebih panas menunda pemangkasan suku bunga.",
        "arah": "dua_arah",
    }


def _buka(peramban, alamat):
    konteks = peramban.new_context()
    konteks.clock.install(time=SEKARANG)
    page = konteks.new_page()
    page.goto(alamat)
    page.wait_for_selector("#s-agenda", timeout=10_000)
    return konteks, page


def test_acara_hari_ini_ditandai(peramban, alamat, tulis_data, brief_asli):
    brief = copy.deepcopy(brief_asli)
    brief["generated_at"] = SEKARANG.isoformat().replace("+00:00", "Z")
    brief["calendar"] = [_agenda("Rilis CPI AS", 4), _agenda("Keputusan FOMC", 168)]
    tulis_data(brief)

    konteks, page = _buka(peramban, alamat)
    teks = page.inner_text("#s-agenda")
    assert "berlangsung hari ini" in teks
    # Hanya SATU acara yang menyandangnya — yang seminggu lagi tidak ikut.
    assert teks.count("berlangsung hari ini") == 1
    konteks.close()


def test_acara_jauh_tidak_ditandai(peramban, alamat, tulis_data, brief_asli):
    """Ambang 12 jam. Acara besok pagi bukan 'hari ini'."""
    brief = copy.deepcopy(brief_asli)
    brief["generated_at"] = SEKARANG.isoformat().replace("+00:00", "Z")
    brief["calendar"] = [_agenda("Rilis CPI AS", 20)]
    tulis_data(brief)

    konteks, page = _buka(peramban, alamat)
    assert "berlangsung hari ini" not in page.inner_text("#s-agenda")
    konteks.close()


def test_agenda_tidak_dirender_dua_kali(peramban, alamat, tulis_data, brief_asli):
    """Nama acara yang sama tidak boleh muncul di dua daftar berbeda.

    Sebelum perbaikan, "Rilis PCE inti AS" tampil sebagai butir "Katalis
    berikutnya" di dalam analisa AI DAN sebagai baris Agenda 30 Hari — dua
    daftar yang isinya sama, salah satunya tanpa hitung mundur maupun jalur
    dampak.
    """
    brief = copy.deepcopy(brief_asli)
    brief["generated_at"] = SEKARANG.isoformat().replace("+00:00", "Z")
    brief["calendar"] = [_agenda("Rilis PCE inti AS", 30)]
    # Brief lama masih menyimpan field yang sudah dihapus dari skema; halaman
    # harus mengabaikannya, bukan ikut merendernya.
    brief["ai"]["bagian"]["katalis_berikutnya"] = [
        "28 Agustus, 19:30 WIB — Rilis PCE inti AS",
    ]
    tulis_data(brief)

    konteks, page = _buka(peramban, alamat)
    assert "Katalis berikutnya" not in page.inner_text("body")
    # Analisa AI tidak boleh lagi memuat daftar agendanya sendiri.
    assert "Rilis PCE inti AS" not in page.inner_text("#s-ai")
    # Bagian Agenda menyebutnya sekali.
    assert page.inner_text("#s-agenda").count("Rilis PCE inti AS") == 1

    # Baris AGENDA BESAR di Sorotan BOLEH menyebutnya: itu satu butir
    # penunjuk yang menaut ke bagian Agenda — hubungan ringkasan-ke-rincian,
    # bukan daftar sejajar kedua. Yang dijaga: ia tetap satu butir.
    sorotan = page.query_selector_all(".baris-sorotan")
    baris_agenda = [b.inner_text() for b in sorotan if "AGENDA BESAR" in b.inner_text()]
    assert len(baris_agenda) == 1
    konteks.close()
