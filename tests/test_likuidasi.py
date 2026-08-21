"""Agregat likuidasi: parsing, arah sisi, dan cara ia ditampilkan.

Yang paling mudah salah di sini ada dua, dan keduanya gagal DIAM:

  1. Ukuran order OKX dihitung dalam KONTRAK, bukan BTC. Lupa pengalinya
     membuat angkanya meleset seratus kali lipat — dan tetap terlihat wajar.
  2. `side` adalah sisi ORDER LIKUIDASINYA. Posisi beli dilikuidasi dengan
     cara dijual, jadi "sell" berarti long yang kena. Tertukar di sini
     membalik seluruh maknanya tanpa satu pun error.
"""

from __future__ import annotations

import copy
import time

from src.collectors import likuidasi


def _respons(detail):
    return {"code": "0", "data": [{"instId": "BTC-USDT-SWAP", "details": detail}]}


def _pasang(monkeypatch, respons):
    monkeypatch.setattr(likuidasi, "get_json", lambda url, **kw: respons)


def test_sell_dihitung_sebagai_likuidasi_posisi_beli(monkeypatch):
    sekarang = time.time() * 1000
    _pasang(monkeypatch, _respons([
        {"side": "sell", "sz": "120", "bkPx": "70000", "ts": str(int(sekarang - 3_600_000))},
        {"side": "buy", "sz": "50", "bkPx": "71000", "ts": str(int(sekarang - 7_200_000))},
    ]))
    data = likuidasi.collect()["data"]

    # 120 kontrak x 0,01 BTC x $70.000 = $84.000
    assert data["likuidasi_long_usd"] == 84_000
    assert data["likuidasi_short_usd"] == 35_500
    assert data["likuidasi_sisi_dominan"] == "long"
    assert data["likuidasi_jumlah_order"] == 2
    assert data["likuidasi_sumber"] == "OKX BTC-USDT-SWAP"


def test_order_di_luar_jendela_tidak_ikut(monkeypatch):
    sekarang = time.time() * 1000
    _pasang(monkeypatch, _respons([
        {"side": "sell", "sz": "100", "bkPx": "70000", "ts": str(int(sekarang - 3_600_000))},
        {"side": "sell", "sz": "999", "bkPx": "70000", "ts": str(int(sekarang - 30 * 3_600_000))},
    ]))
    data = likuidasi.collect()["data"]
    assert data["likuidasi_total_usd"] == 70_000
    assert data["likuidasi_cakupan_jam"] == 1.0


def test_endpoint_yang_mengabaikan_kursor_tidak_dijumlah_berkali_kali(monkeypatch):
    """Penelusuran halaman berhenti kalau halaman baru tidak lebih tua.

    Bukan kasus karangan: endpoint yang mengabaikan parameter `after` akan
    memulangkan halaman yang sama terus-menerus, dan totalnya membengkak
    sebanyak jumlah halaman yang diminta — angka yang salah lima kali lipat
    tanpa satu pun error.
    """
    sekarang = time.time() * 1000
    dipanggil = []

    def _abaikan_kursor(url, **kw):
        dipanggil.append(kw.get("params", {}).get("after"))
        return _respons([
            {"side": "sell", "sz": "100", "bkPx": "70000", "ts": str(int(sekarang - 3_600_000))},
        ])

    monkeypatch.setattr(likuidasi, "get_json", _abaikan_kursor)
    data = likuidasi.collect()["data"]
    assert data["likuidasi_total_usd"] == 70_000
    assert data["likuidasi_jumlah_order"] == 1


def test_sumber_gagal_tidak_melempar(monkeypatch):
    def _menolak(url, **kw):
        return {"code": "50000", "msg": "endpoint tidak dikenal"}

    monkeypatch.setattr(likuidasi, "get_json", _menolak)
    hasil = likuidasi.collect()
    assert hasil["data"] == {}
    assert hasil["failed"] == ["likuidasi"]


def test_hari_tanpa_likuidasi_dilaporkan_gagal_bukan_nol(monkeypatch):
    """Nol dan "tidak tahu" tidak boleh tertukar.

    Blok likuidasi yang menampilkan $0 terbaca sebagai "pasar tenang hari
    ini", padahal yang terjadi bisa saja endpointnya tidak menjawab.
    """
    _pasang(monkeypatch, _respons([]))
    assert likuidasi.collect()["failed"] == ["likuidasi"]


def test_blok_likuidasi_tampil_di_halaman(peramban, alamat, tulis_data, brief_asli):
    brief = copy.deepcopy(brief_asli)
    brief["market"].update({
        "likuidasi_long_usd": 84_000_000.0,
        "likuidasi_short_usd": 21_000_000.0,
        "likuidasi_total_usd": 105_000_000.0,
        "likuidasi_jumlah_order": 312,
        "likuidasi_sisi_dominan": "long",
        "likuidasi_cakupan_jam": 24.0,
        "likuidasi_sumber": "OKX BTC-USDT-SWAP",
    })
    tulis_data(brief)

    page = peramban.new_page()
    page.goto(alamat)
    page.wait_for_selector("#s-pasar", timeout=10_000)
    teks = page.inner_text("#s-pasar")

    assert "Likuidasi 24 jam" in teks
    assert "posisi beli" in teks and "posisi jual" in teks
    # Cakupannya wajib disebut: pembaca yang membandingkan dengan agregator
    # lintas bursa harus tahu ini satu bursa.
    assert "bukan gabungan seluruh bursa" in teks
    assert "OKX BTC-USDT-SWAP" in teks
    page.close()


def test_blok_likuidasi_absen_kalau_sumbernya_gagal(peramban, alamat, tulis_data, brief_asli):
    brief = copy.deepcopy(brief_asli)
    for kunci in list(brief["market"]):
        if kunci.startswith("likuidasi_"):
            brief["market"].pop(kunci)
    tulis_data(brief)

    page = peramban.new_page()
    page.goto(alamat)
    page.wait_for_selector("#s-pasar", timeout=10_000)
    assert "Likuidasi 24 jam" not in page.inner_text("#s-pasar")
    page.close()
