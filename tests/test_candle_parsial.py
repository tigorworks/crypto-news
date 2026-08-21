"""Indikator yang bertumpu pada VOLUME saat candle harian belum penuh.

Volume menumpuk sepanjang hari, jadi candle yang baru berjalan separuh
menyumbang separuh volumenya. Rasio volume sudah lama dijaga karena itu;
berkas ini menjaga dua indikator lain yang bertumpu pada hal yang sama dan
sebelumnya luput: arah OBV dan VWAP harian.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.analysis import technical


def _candle(waktu: datetime, tutup: float, volume: float, buka: float):
    return {
        "open_time": int(waktu.timestamp() * 1000),
        "open": buka,
        "high": max(buka, tutup) * 1.004,
        "low": min(buka, tutup) * 0.996,
        "close": tutup,
        "volume": volume,
    }


def _deret(jam_berjalan: float, volume_berjalan: float = 200.0):
    """60 candle harian selesai + satu candle hari ini yang belum penuh."""
    sekarang = datetime.now(timezone.utc)
    # `jam_berjalan` diwujudkan dengan menggeser awal candle terakhir, bukan
    # jamnya — jam sistem tidak bisa digeser dari dalam uji.
    mulai_terakhir = sekarang - timedelta(hours=jam_berjalan)

    klines = []
    harga = 60000.0
    for i in range(60, 0, -1):
        naik = i % 2 == 0
        tutup = harga * (1.01 if naik else 0.99)
        klines.append(_candle(mulai_terakhir - timedelta(days=i), tutup, 1000.0, harga))
        harga = tutup
    klines.append(_candle(mulai_terakhir, harga * 0.98, volume_berjalan, harga))
    return klines


def test_candle_berjalan_ditandai_parsial():
    volume = technical.analyze_timeframe(_deret(jam_berjalan=6))["volume"]
    assert volume["parsial"] is True
    assert 0.2 < volume["kelengkapan"] < 0.3
    assert volume["obv_arah_tanpa_candle_berjalan"] is True
    assert volume["vwap_harian_parsial"] is True


def test_candle_hampir_penuh_tidak_ditandai():
    volume = technical.analyze_timeframe(_deret(jam_berjalan=23.5))["volume"]
    assert volume["parsial"] is False
    assert volume["obv_arah_tanpa_candle_berjalan"] is False
    assert volume["vwap_harian_parsial"] is False


def test_arah_obv_tidak_berubah_karena_jam_menjalankan():
    """Inti perbaikannya.

    Arah OBV diukur dari kemiringan enam candle terakhir. Kalau candle hari
    berjalan ikut, sumbangannya bergantung pada SUDAH BERAPA JAUH hari itu
    berjalan — jadi jawabannya bisa berbeda antara run pukul 06.00 dan run
    pukul 23.00 tanpa satu pun harga berubah.
    """
    pagi = technical.analyze_timeframe(_deret(jam_berjalan=2, volume_berjalan=80.0))
    malam = technical.analyze_timeframe(_deret(jam_berjalan=20, volume_berjalan=900.0))
    assert pagi["volume"]["obv_arah"] == malam["volume"]["obv_arah"]
