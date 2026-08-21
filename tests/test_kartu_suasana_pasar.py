"""Kartu suasana pasar: Fear & Greed dulu, sentimen berita sesudahnya.

Keduanya pernah tertukar oleh pembaca — skor sentimen berita berdiri sebagai
angka terbesar di kartu ini dan terbaca sebagai "indeks suasana pasar",
padahal nama publik untuk itu adalah Fear & Greed dan angkanya bisa berlawanan
arah. Uji ini menjaga urutan dan penjelasannya supaya tidak tergeser lagi
tanpa sengaja.
"""

from __future__ import annotations

import copy


def _muat(peramban, alamat):
    page = peramban.new_page()
    page.goto(alamat)
    page.wait_for_selector("#s-harga", timeout=10_000)
    return page


def test_fear_greed_berada_di_atas_sentimen_berita(peramban, alamat, tulis_data, brief_asli):
    brief = copy.deepcopy(brief_asli)
    brief["market"]["fear_greed"] = {"value": 72, "label": "Keserakahan", "previous": 62}
    brief["aggregate"]["sentiment_score"] = -12.5
    brief["aggregate"]["sentiment_label"] = "netral"
    brief["aggregate"]["jumlah_dinilai"] = 25
    tulis_data(brief)

    page = _muat(peramban, alamat)
    teks = page.inner_text("#s-harga")

    assert "Fear & Greed Index" in teks
    assert "Sentimen berita hari ini" in teks
    assert teks.index("Fear & Greed Index") < teks.index("Sentimen berita hari ini"), (
        "sentimen berita kembali berdiri di atas Fear & Greed"
    )

    # Perbedaannya harus dinyatakan, bukan disiratkan lewat tata letak saja.
    assert "Bukan Fear & Greed" in teks
    assert "dari 25 berita" in teks
    assert "+10 poin dari kemarin" in teks
    # Skala sentimen tetap dua arah; tanpa keterangan ini angka negatif
    # terbaca seperti kesalahan.
    assert "-100..+100" in teks
    page.close()


def test_gauge_fear_greed_tidak_muncul_dua_kali(peramban, alamat, tulis_data, brief_asli):
    """Satu angka, satu tempat.

    Sebelumnya gauge-nya ada di kartu pasar SEKALIGUS hendak dipindah ke atas;
    dua salinan membuat pembaca mengira ada dua pengukuran berbeda.
    """
    tulis_data(brief_asli)
    page = _muat(peramban, alamat)
    # Dihitung dari TEKS TERLIHAT, bukan sumber HTML: template Alpine ikut
    # terserialisasi di `content()`, jadi menghitung di sana selalu
    # menemukan salinan yang tidak pernah dilihat siapa pun.
    assert page.inner_text("#s-harga").count("Fear & Greed Index") == 1
    assert "Fear & Greed Index" not in page.inner_text("#s-pasar"), (
        "gauge lama masih tertinggal di kartu pasar"
    )
    page.close()


def test_kartu_tetap_utuh_tanpa_fear_greed(peramban, alamat, tulis_data, brief_asli):
    """Sumber Fear & Greed boleh gagal; kartunya tidak boleh ikut rusak."""
    brief = copy.deepcopy(brief_asli)
    brief["market"]["fear_greed"] = None
    tulis_data(brief)

    page = _muat(peramban, alamat)
    teks = page.inner_text("#s-harga")
    assert "Fear & Greed Index" not in teks
    assert "Sentimen berita hari ini" in teks
    assert "Support terdekat" in teks
    page.close()
