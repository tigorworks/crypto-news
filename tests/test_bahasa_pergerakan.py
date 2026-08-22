"""Bahasa jenis pergerakan: harus bisa dibaca orang yang tidak ikut pasar.

Baris ini sudah dua kali gagal dengan cara yang berbeda, dan berkas ini ada
supaya tidak ada kali ketiga:

  1. "penutupan posisi jual, bukan permintaan baru" — berhenti pada KONTRAS.
     Separuhnya benar, tapi pembaca tidak pernah diberi tahu apa bedanya dan
     kenapa itu penting.
  2. "pedagang yang bertaruh harga turun menutup posisinya dengan membeli" —
     mencoba menjelaskan mekanisme di dalam label, hasilnya panjang dan kaku,
     DAN memperkenalkan kosakata yang tidak dipakai orang. Model lalu
     menyalinnya ke judul brief 22 Agustus: "ditopang penutupan taruhan turun
     yang rapuh".
"""

from __future__ import annotations

import copy

import pytest

from src.analysis.technical import _ARTI_JENIS, _kalimat_pergerakan
from src.utils import istilah

#: Kata yang tidak boleh muncul di teks yang dibaca pengguna. "taruhan" dan
#: "bertaruh" bukan cara orang Indonesia bicara soal pasar; "pedagang" jadi
#: kata pengisi yang memanjangkan kalimat tanpa menambah makna.
KATA_TERLARANG = ("taruhan", "bertaruh", "pedagang", "short covering", "short-seller")


@pytest.mark.parametrize("jenis", sorted(_ARTI_JENIS))
def test_label_cukup_pendek_untuk_chip(jenis):
    label, _ = _ARTI_JENIS[jenis]
    assert len(label.split()) <= 3, (
        f"label '{label}' terlalu panjang untuk chip — kalimat yang dipaksa "
        "masuk ke chip selalu berakhir jadi frasa aneh"
    )
    assert not label.endswith("."), "label chip bukan kalimat"


@pytest.mark.parametrize("jenis", sorted(_ARTI_JENIS))
def test_penjelasan_memakai_kata_sehari_hari(jenis):
    label, arti = _ARTI_JENIS[jenis]
    for kata in KATA_TERLARANG:
        assert kata not in label.lower(), f"label '{label}' memakai kata '{kata}'"
        assert kata not in arti.lower(), f"penjelasan {jenis} memakai kata '{kata}'"


@pytest.mark.parametrize("jenis", sorted(_ARTI_JENIS))
def test_penjelasan_menyebut_akibatnya_bukan_cuma_kontras(jenis):
    """Kalimat kedua wajib ada: itulah bagian 'lalu kenapa'."""
    _, arti = _ARTI_JENIS[jenis]
    kalimat = [k for k in arti.split(". ") if k.strip()]
    assert len(kalimat) >= 2, (
        f"{jenis}: penjelasan cuma satu kalimat, jadi pembaca ditinggal "
        "tanpa tahu apa akibatnya"
    )
    # Kata-kata yang menandai akibat bagi pembaca, bukan sekadar mekanisme.
    petunjuk = ("cenderung", "biasanya", "gampang", "habis", "mereda", "kuat", "bertahan")
    assert any(x in arti.lower() for x in petunjuk), (
        f"{jenis}: penjelasannya tidak mengatakan apa artinya bagi pembaca"
    )


def test_frasa_kaku_dari_model_dibersihkan_kode():
    """Larangan lewat prompt saja terbukti tidak cukup.

    Judul brief 22 Agustus lolos dengan "penutupan taruhan turun" walaupun
    kosakata itu tidak pernah diminta — model menyalinnya dari contoh di
    prompt. Jaring pengamannya harus di kode.
    """
    hasil = istilah.manusiakan(
        "BTC melonjak 7% ditopang penutupan taruhan turun yang rapuh"
    )
    assert "taruhan" not in hasil
    assert "penutupan posisi jual" in hasil


def test_huruf_besar_awal_kalimat_dipertahankan():
    assert istilah.manusiakan("Taruhan turun bertambah.").startswith("Posisi jual")


def test_kalimat_yang_sudah_benar_tidak_disentuh():
    """Penggantian yang terlalu longgar lebih berbahaya daripada satu frasa
    kaku yang lolos — kalimat yang sudah benar tidak boleh ikut berubah."""
    utuh = "Harga naik 7,05% dalam 24 jam dengan volume di atas rata-rata."
    assert istilah.manusiakan(utuh) == utuh


def test_kartu_sorotan_menampilkan_penjelasan_bukan_istilah(
    peramban, alamat, tulis_data, brief_asli
):
    brief = copy.deepcopy(brief_asli)
    label, arti = _ARTI_JENIS["short_covering"]
    brief["technical"]["pergerakan_24j"].update({
        "arah": "naik", "jenis": "short_covering",
        "jenis_ringkas": label, "jenis_arti": arti,
        # Kalimat kode ikut dibangun ulang: brief lama di repo masih
        # menyimpan bentuk sebelum perbaikan, dan yang diuji di sini adalah
        # apa yang dihasilkan run BARU.
        "ringkas": _kalimat_pergerakan("naik", 7.05, "ekstrem", arti, "dikonfirmasi", []),
    })
    tulis_data(brief)

    page = peramban.new_page()
    page.goto(alamat)
    page.wait_for_selector(".baris-sorotan, #s-harga", timeout=10_000)
    teks = page.inner_text("body")

    # Penjelasannya tampil…
    assert "harus membeli lagi untuk menutup posisinya" in teks
    # …dan pembuka abstrak "Sifatnya:" tidak dipakai lagi.
    assert "Sifatnya:" not in teks
    page.close()
