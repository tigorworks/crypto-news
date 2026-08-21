"""Corong berita: pemangkasan sebelum LLM, dan penyaring yang balasannya pendek."""

from __future__ import annotations

from datetime import timedelta

from src.analysis import news_analysis as na
from src.collectors import news
from src.utils.timezone import now_utc


def _feed_palsu(monkeypatch, artikel):
    """Ganti pengambilan feed dengan daftar artikel yang sudah jadi."""
    def _ambil(url):
        return [a for a in artikel if a["_feed"] == url]

    monkeypatch.setattr(news, "_fetch_feed", _ambil)


def _artikel(feed, judul, ringkasan="", menit_lalu=30):
    terbit = now_utc() - timedelta(minutes=menit_lalu)
    return {
        "_feed": feed,
        "id": news._article_id(judul, f"https://contoh/{judul}"),
        "judul": judul,
        "ringkasan": ringkasan,
        "url": f"https://contoh/{judul}",
        "sumber": "Contoh",
        "domain": "contoh.com",
        "waktu_utc": terbit.isoformat(),
        "_published": terbit,
    }


def test_artikel_tanpa_kata_kunci_dibuang_sebelum_llm(monkeypatch):
    artikel = [
        _artikel("f1", "Bitcoin ETF inflow melonjak"),
        _artikel("f1", "Resep rendang terbaik akhir pekan"),
        _artikel("f1", "Fed signals rate cut"),
    ]
    _feed_palsu(monkeypatch, artikel)

    hasil = news.collect(["f1"], min_skor_kandidat=1)
    judul = [a["judul"] for a in hasil["articles"]]

    assert "Resep rendang terbaik akhir pekan" not in judul
    assert len(judul) == 2
    # Corongnya melaporkan keduanya: berapa yang unik, dan berapa yang
    # benar-benar sampai ke model.
    assert hasil["jumlah"]["unik"] == 3
    assert hasil["jumlah"]["kandidat_llm"] == 2


def test_tanpa_pemangkasan_semua_kandidat_lolos(monkeypatch):
    artikel = [
        _artikel("f1", "Bitcoin ETF inflow melonjak"),
        _artikel("f1", "Resep rendang terbaik akhir pekan"),
    ]
    _feed_palsu(monkeypatch, artikel)

    hasil = news.collect(["f1"])
    assert hasil["jumlah"]["kandidat_llm"] == 2


def test_feed_diambil_paralel_tanpa_mengubah_urutan(monkeypatch):
    """Hasilnya mengikuti urutan feed yang diminta, bukan siapa yang selesai
    duluan.

    Feed kini ditarik berbarengan supaya run tidak menghabiskan waktu
    menunggu puluhan permintaan berurutan. Yang tidak boleh ikut berubah
    adalah urutan hasilnya: dedup menyimpan artikel yang PERTAMA terlihat,
    jadi urutan yang berayun berarti isi brief ikut berayun tanpa satu pun
    data yang berubah.
    """
    def _bikin():
        artikel = [
            _artikel("f1", "Bitcoin ETF inflow melonjak", menit_lalu=10),
            _artikel("f2", "Fed signals rate cut", menit_lalu=10),
        ]
        # Waktu terbit DISAMAKAN persis: dengan stempel berbeda urutannya
        # sudah ditentukan kesegaran, dan uji ini tidak akan menyentuh soal
        # urutan pengambilan sama sekali.
        artikel[1]["_published"] = artikel[0]["_published"]
        artikel[1]["waktu_utc"] = artikel[0]["waktu_utc"]
        return artikel

    for _ in range(5):
        # Dibuat ulang tiap putaran: collect() membuang field internal dari
        # dict yang diterimanya.
        _feed_palsu(monkeypatch, _bikin())
        hasil = news.collect(["f1", "f2"])
        assert [a["judul"] for a in hasil["articles"]] == [
            "Bitcoin ETF inflow melonjak",
            "Fed signals rate cut",
        ]


def test_feed_gagal_tidak_menjatuhkan_feed_lain(monkeypatch):
    def _ambil(url):
        if url == "rusak":
            raise ValueError("feed tidak bisa diparsing")
        return [_artikel(url, "Bitcoin ETF inflow melonjak")]

    monkeypatch.setattr(news, "_fetch_feed", _ambil)
    hasil = news.collect(["rusak", "https://baik.com/feed"])
    assert len(hasil["articles"]) == 1
    assert hasil["failed"] == ["rusak"]


class _KlienDiam:
    """Model yang menjawab dengan array kosong — sah, artinya tidak ada yang lolos."""

    def chat_json(self, models, system, user, **kw):
        return []


class _KlienGagal:
    def chat_json(self, models, system, user, **kw):
        raise na.LLMError("model tidak menjawab")


def test_balasan_kosong_berarti_tidak_ada_yang_relevan():
    """Model kini hanya menulis artikel yang lolos ambang.

    Array kosong karena itu adalah jawaban yang SAH, dan tidak boleh
    tertukar dengan langkah filter yang gagal — kalau tertukar, brief akan
    memuat 25 artikel yang baru saja dinilai tidak relevan.
    """
    artikel = [{"id": "a1", "judul": "x", "ringkasan": "", "skor_prioritas": 10}]
    assert na.filter_relevansi(_KlienDiam(), ["m"], artikel) == []


def test_langkah_filter_gagal_jatuh_ke_skor_kata_kunci():
    artikel = [
        {"id": "a1", "judul": "x", "ringkasan": "", "skor_prioritas": 10},
        {"id": "a2", "judul": "y", "ringkasan": "", "skor_prioritas": 90},
    ]
    hasil = na.filter_relevansi(_KlienGagal(), ["m"], artikel, max_keep=1)
    assert [a["id"] for a in hasil] == ["a2"]
    assert hasil[0]["relevansi_btc"] == 90
