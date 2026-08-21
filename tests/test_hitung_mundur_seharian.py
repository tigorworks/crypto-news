"""Uji "sehari penuh": panel jendela menyala dan PADAM pada saat yang tepat.

Hitung mundur di halaman ini tidak dibaca dari brief — ia dihitung ulang tiap
menit dari instant absolut, karena brief dibuat sekali sehari sementara
halamannya dibuka kapan saja. Yang sudah diuji sebelumnya cuma titik-titik
tertentu, dan titik-titik itu tidak bisa menangkap kesalahan yang paling
mahal di sini: panel yang LUPA PADAM setelah bursanya buka, atau padam
terlalu cepat.

Berkas ini menjalankan halamannya dengan JAM PALSU yang dimajukan dari pagi
ke malam. Tanpa itu, satu-satunya cara mengujinya adalah menunggu hari
berganti — yang berarti tidak pernah diuji sama sekali.
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

# Titik berangkat jam palsu: pagi WIB, jauh dari pergantian hari UTC supaya
# maju 14 jam tidak menyeberangi batas yang tidak ada hubungannya dengan uji.
MULAI = datetime(2026, 8, 21, 1, 0, tzinfo=timezone.utc)

#: Jendela dibuka lagi sepuluh jam setelah titik berangkat.
BUKA = MULAI + timedelta(hours=10)


def _brief_dengan_jendela(brief: dict) -> dict:
    """Brief yang menempatkan pembaca DI DALAM jendela rawan."""
    brief = copy.deepcopy(brief)
    brief["generated_at"] = MULAI.isoformat().replace("+00:00", "Z")
    brief["agen_kebijakan"] = {
        "jendela": {
            "fase": "jeda_akhir_pekan",
            "waktu_ny": "2026-08-20 21:00 EDT",
            "bursa_as_buka": False,
            "jam_sampai_buka": 10.0,
            "jeda_mulai": "Jumat 16.00 EDT",
            "jeda_berjalan_jam": 8.0,
            "dalam_jendela_rawan": True,
            "buka_berikutnya_utc": BUKA.isoformat(),
            "tutup_berikutnya_utc": None,
            "buka_berikutnya_wib": "Senin · 24 Agu · 20:30 WIB",
        },
        "kerapuhan": {"skor": 3, "maks": 5, "tingkat": "tinggi", "faktor": []},
        "risiko_jendela": {
            "tingkat": "tinggi",
            "fase": "jeda_akhir_pekan",
            "dalam_jendela_rawan": True,
            "kerapuhan": "tinggi",
        },
        "pendaratan": {"kuat": 4, "kuat_di_jendela_rawan": 2, "ada_yang_tertahan": True},
    }
    return brief


def _teks_baris_jendela(page) -> str:
    baris = page.query_selector_all(".baris-sorotan")
    for b in baris:
        teks = b.inner_text()
        if "JENDELA RISIKO" in teks:
            return teks
    return ""


def test_panel_jendela_menyala_lalu_padam_tepat_waktu(
    peramban, alamat, tulis_data, brief_asli
):
    tulis_data(_brief_dengan_jendela(brief_asli))

    konteks = peramban.new_context()
    konteks.clock.install(time=MULAI)
    page = konteks.new_page()
    page.goto(alamat)
    page.wait_for_selector("#s-siaga", timeout=10_000)

    # -- pagi: panel menyala, hitung mundur menyebut sisa jam yang benar ----
    assert "JENDELA RISIKO" in _teks_baris_jendela(page)
    assert "10 jam lagi" in _teks_baris_jendela(page)

    # -- sepanjang hari: sisa jam menyusut, panel tetap ada ----------------
    for jam in range(1, 10):
        konteks.clock.fast_forward(60 * 60 * 1000)
        page.wait_for_timeout(50)
        teks = _teks_baris_jendela(page)
        assert teks, f"baris jendela hilang terlalu cepat, {jam} jam setelah mulai"
        # Satu jam sebelum buka, satuannya berganti ke menit — itu benar, dan
        # yang diuji cuma bahwa panelnya masih hidup.
        if jam <= 8:
            assert f"{10 - jam} jam lagi" in teks, (
                f"jam ke-{jam}: hitung mundur tidak ikut menyusut — {teks!r}"
            )

    assert page.query_selector("#s-siaga"), "bagian rincian ikut hilang sebelum bursa buka"

    # -- lewat jam buka: baris DAN bagian rinciannya padam ------------------
    konteks.clock.fast_forward(2 * 60 * 60 * 1000)
    page.wait_for_timeout(50)
    assert not _teks_baris_jendela(page), (
        "baris jendela masih tampil setelah bursa AS buka"
    )
    assert page.query_selector("#s-siaga") is None, (
        "bagian rincian jendela masih tampil setelah bursa AS buka — "
        "premis seluruh prosanya sudah gugur"
    )
    konteks.close()


def test_baris_agenda_tetap_hidup_setelah_jendela_padam(
    peramban, alamat, tulis_data, brief_asli
):
    """Padamnya jendela tidak boleh ikut mematikan baris agenda.

    Keduanya berbagi satu kartu dan satu penjagaan `!b.mundur.lewat`, jadi
    ini persis tempat sebuah perbaikan bisa memadamkan hal yang salah.
    """
    brief = _brief_dengan_jendela(brief_asli)
    brief["calendar"] = [
        {
            "nama": "Rilis CPI AS",
            "waktu_utc": (BUKA + timedelta(hours=30)).isoformat(),
            "dampak": "tinggi",
            "relevansi_kripto": 5,
            "jalur_dampak": "Inflasi lebih panas menunda pemangkasan suku bunga.",
        }
    ]
    tulis_data(brief)

    konteks = peramban.new_context()
    konteks.clock.install(time=MULAI)
    page = konteks.new_page()
    page.goto(alamat)
    page.wait_for_selector(".baris-sorotan", timeout=10_000)

    konteks.clock.fast_forward(12 * 60 * 60 * 1000)
    page.wait_for_timeout(50)

    # Jendelanya sudah padam pada titik ini — itu yang membuat uji ini
    # berarti: agenda harus tetap berdiri sendiri sesudahnya.
    assert not _teks_baris_jendela(page), "prasyarat uji gagal: jendela belum padam"
    baris = [b.inner_text() for b in page.query_selector_all(".baris-sorotan")]
    assert any("AGENDA BESAR" in t for t in baris), (
        f"baris agenda ikut padam bersama jendela (baris tersisa: {baris})"
    )
    konteks.close()
