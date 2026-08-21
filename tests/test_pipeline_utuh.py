"""Jalankan `main.jalankan()` utuh dengan SELURUH sumber ditiru.

Bukan uji kebenaran analisa — itu tugas berkas uji lain. Yang dijaga di sini
satu hal saja: pipeline-nya masih tersambung. Orkestratornya panjang, banyak
langkah saling mengoper dict, dan kesalahan yang paling mahal di sana bukan
salah hitung melainkan `NameError`/`KeyError` yang baru muncul saat run
produksi tengah malam — sesudah semua panggilan LLM dibayar.

Semua akses jaringan diganti data tiruan, LLM dimatikan (tanpa
`OPENROUTER_API_KEY` pipeline memang berjalan tanpa langkah AI), dan seluruh
tulisan diarahkan ke direktori sementara.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src import main as pipeline
from src.collectors import (
    binance, calendar as calendar_collector, ff_calendar, flows, likuidasi,
    macro, market, news, okx, onchain, options, statements as statements_collector,
    whale,
)
from src.config import load_config
from src.output import builder, telemetri


def _klines(jumlah: int = 120, menit: int = 1440):
    sekarang = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    keluar = []
    harga = 60000.0
    for i in range(jumlah, 0, -1):
        tutup = harga * (1.01 if i % 2 == 0 else 0.99)
        keluar.append({
            "open_time": int((sekarang - timedelta(minutes=menit * i)).timestamp() * 1000),
            "open": harga,
            "high": max(harga, tutup) * 1.005,
            "low": min(harga, tutup) * 0.995,
            "close": tutup,
            "volume": 1200.0,
        })
        harga = tutup
    return keluar


@pytest.fixture
def pipeline_tertiru(monkeypatch, tmp_path: Path):
    """Matikan seluruh jaringan dan arahkan semua tulisan ke tmp_path."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    monkeypatch.setattr(binance, "fetch_price_and_klines", lambda *a, **k: {
        "price": {
            "last": 70000.0, "change_24h_pct": 3.2, "high_24h": 71000.0,
            "low_24h": 68000.0, "volume_24h": 1.2e9,
        },
        "klines": {"1d": _klines(), "1h": _klines(200, 60)},
        "source": "binance",
    })
    monkeypatch.setattr(binance, "fetch_funding_rate", lambda *a, **k: 0.0001)
    monkeypatch.setattr(binance, "fetch_open_interest", lambda *a, **k: 82000.0)
    monkeypatch.setattr(binance, "fetch_open_interest_history", lambda *a, **k: [])

    monkeypatch.setattr(market, "collect", lambda *a, **k: {
        "data": {
            "fear_greed": {"value": 72, "label": "Keserakahan", "previous": 62},
            "hashrate": 700.0, "etf_flow_usd": 2.4e8, "etf_flow_date": "2026-08-20",
            "etf_flow_sumber": "SoSoValue",
            "etf_flow_verifikasi": {"status": "cocok"},
            "btc_dominance_pct": 58.1,
        },
        "failed": [],
    })
    monkeypatch.setattr(okx, "fetch_funding_rate_history", lambda *a, **k: [])
    monkeypatch.setattr(okx, "tren_funding", lambda *a, **k: {})
    monkeypatch.setattr(whale, "collect", lambda *a, **k: {
        "data": {"whale_long_pct": 48.0, "ritel_long_pct": 61.0, "divergensi": -13.0},
        "failed": [],
    })
    monkeypatch.setattr(options, "collect", lambda *a, **k: {"data": {"dvol": 45.0}, "failed": []})
    monkeypatch.setattr(onchain, "collect", lambda *a, **k: {"data": {"mvrv": 2.1}, "failed": []})
    monkeypatch.setattr(flows, "collect", lambda *a, **k: {"data": {"premium_coinbase_pct": 0.02}, "failed": []})
    monkeypatch.setattr(likuidasi, "collect", lambda *a, **k: {
        "data": {
            "likuidasi_long_usd": 8.4e7, "likuidasi_short_usd": 2.1e7,
            "likuidasi_total_usd": 1.05e8, "likuidasi_jumlah_order": 300,
            "likuidasi_sisi_dominan": "long", "likuidasi_cakupan_jam": 24.0,
            "likuidasi_sumber": "OKX BTC-USDT-SWAP",
        },
        "failed": [],
    })
    monkeypatch.setattr(macro, "collect", lambda *a, **k: {"data": {"dxy": 104.2}, "failed": []})
    monkeypatch.setattr(news, "collect", lambda *a, **k: {
        "articles": [{
            "id": "n1", "judul": "Bitcoin ETF inflow melonjak", "ringkasan": "Arus masuk",
            "url": "https://contoh/1", "sumber": "CoinDesk", "domain": "coindesk.com",
            "waktu_utc": datetime.now(timezone.utc).isoformat(), "skor_prioritas": 60,
            "jumlah_konfirmasi": 2,
        }],
        "failed": [],
        "jumlah": {"terkumpul": 300, "segar": 40, "unik": 38, "kandidat_llm": 20},
    })
    monkeypatch.setattr(statements_collector, "collect", lambda *a, **k: {
        "items": [], "failed": [], "sumber_gagal": [],
    })
    monkeypatch.setattr(ff_calendar, "collect", lambda *a, **k: [])
    monkeypatch.setattr(calendar_collector, "collect", lambda *a, **k: [])

    # Tulisan: brief ke tmp, telemetri ke tmp, stempel aset dimatikan.
    tulis_asli = builder.tulis_output
    monkeypatch.setattr(builder, "tulis_output", lambda brief, retention_days=90, **k: tulis_asli(
        brief, retention_days=retention_days,
        data_dir=tmp_path / "data", archive_dir=tmp_path / "data" / "archive",
        tulis_arsip=k.get("tulis_arsip", True),
    ))
    monkeypatch.setattr(builder, "brief_sebelumnya", lambda *a, **k: None)
    monkeypatch.setattr(builder, "segarkan_versi_aset", lambda *a, **k: None)

    rekam_asli = telemetri.rekam
    monkeypatch.setattr(telemetri, "rekam", lambda **kw: rekam_asli(
        **{**kw, "path": tmp_path / "telemetri.jsonl"}
    ))
    ringkas_asli = telemetri.ringkas
    monkeypatch.setattr(telemetri, "ringkas", lambda **kw: ringkas_asli(
        path=tmp_path / "telemetri.jsonl", keluaran=tmp_path / "telemetri.json"
    ))
    return tmp_path


def test_pipeline_berjalan_tanpa_llm(pipeline_tertiru):
    brief = pipeline.jalankan(load_config(), dry_run=True)

    assert brief["price"]["last"] == 70000.0
    # Blok jendela risiko kini murni hitungan kode: ia HARUS lengkap justru
    # pada run tanpa LLM sama sekali.
    agen = brief["agen_kebijakan"]
    assert agen["risiko_jendela"]["tingkat"] in ("rendah", "sedang", "tinggi")
    assert set(agen) == {"jendela", "kerapuhan", "risiko_jendela", "pendaratan"}
    # Likuidasi ikut mengalir ke blok pasar tanpa jalur baru.
    assert brief["market"]["likuidasi_total_usd"] == 1.05e8
    # Pergerakan 24 jam tetap dihitung kode.
    assert brief["technical"]["pergerakan_24j"]["arah"] == "naik"


def test_telemetri_tercatat_untuk_tiap_run(pipeline_tertiru):
    pipeline.jalankan(load_config(), dry_run=True)

    baris = [
        json.loads(b) for b in
        (pipeline_tertiru / "telemetri.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(baris) == 1
    assert baris[0]["harga"] == 70000.0
    assert baris[0]["corong_berita"]["kandidat_llm"] == 20
    assert (pipeline_tertiru / "telemetri.json").exists()
