"""Pengaman biaya LLM: parameter penalaran dan telemetri per langkah.

Dua hal yang dijaga di sini, keduanya lahir dari angka produksi:

  1. Langkah `synthesis` menelan $0,156/run padahal konteks masuknya ~11.400
     token dan keluaran yang mendarat di brief cuma ~1.350 token. Selisihnya
     token penalaran — dan satu-satunya cara mengendalikannya adalah
     parameter yang bentuknya berbeda antar provider. Tebakan yang salah
     TIDAK BOLEH menghanguskan narasi utama.
  2. Biaya per langkah saja tidak cukup untuk mendiagnosis: $0,156 bisa
     berarti konteks kegemukan, keluaran kepanjangan, atau penalaran yang
     tak terlihat — tiga sebab dengan tiga perbaikan yang berbeda.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from src.analysis import llm as modul_llm
from src.analysis.llm import LLMClient, LLMError
from src.output import telemetri
from src.utils.http import HttpError


class _Respons:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _balasan_ok(isi='{"hasil": "oke"}'):
    return _Respons({
        "model": "anthropic/claude-sonnet-5",
        "choices": [{"message": {"content": isi}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 2000, "cost": 0.01},
    })


def _klien(**kw):
    return LLMClient(api_key="x", max_cost_usd=1.0, **kw)


def test_effort_dikirim_untuk_langkah_yang_diatur(monkeypatch):
    dikirim = {}

    def _request(metode, url, **kwargs):
        dikirim.update(kwargs["json_body"])
        return _balasan_ok()

    monkeypatch.setattr(modul_llm, "request", _request)
    _klien(reasoning_effort={"synthesis": "medium"}).chat(
        ["m"], "sistem", "data", step="synthesis"
    )
    assert dikirim["reasoning"] == {"effort": "medium"}


def test_langkah_lain_tidak_ikut_terkena(monkeypatch):
    dikirim = {}

    def _request(metode, url, **kwargs):
        dikirim.update(kwargs["json_body"])
        return _balasan_ok()

    monkeypatch.setattr(modul_llm, "request", _request)
    _klien(reasoning_effort={"synthesis": "medium"}).chat(
        ["m"], "sistem", "data", step="filter"
    )
    assert "reasoning" not in dikirim


def test_provider_menolak_effort_langkahnya_tetap_jalan(monkeypatch):
    """Inti pengamannya.

    Bentuk parameter penalaran berbeda antar provider dan berubah seiring
    model baru. Kalau tebakannya salah, langkahnya harus tetap menghasilkan
    jawaban pada biaya penuh — bukan hilang bersama narasi utama brief.
    """
    percobaan = []

    def _request(metode, url, **kwargs):
        percobaan.append(dict(kwargs["json_body"]))
        if "reasoning" in kwargs["json_body"]:
            raise HttpError("HTTP 400: unsupported parameter", status_code=400)
        return _balasan_ok()

    monkeypatch.setattr(modul_llm, "request", _request)
    klien = _klien(reasoning_effort={"synthesis": "medium"})
    hasil = klien.chat(["m"], "sistem", "data", step="synthesis")

    assert hasil == '{"hasil": "oke"}'
    assert len(percobaan) == 2
    assert "reasoning" in percobaan[0] and "reasoning" not in percobaan[1]
    # Penolakannya dilaporkan, bukan cuma lewat di log.
    assert klien.ringkasan()["effort_ditolak"] == ["synthesis"]


def test_kegagalan_lain_tidak_ikut_dicoba_ulang(monkeypatch):
    """Hanya penolakan parameter (400/422) yang layak diulang.

    Rate limit dan error server sudah punya retry sendiri di lapis HTTP;
    mengulanginya lagi di sini cuma menggandakan beban saat provider
    sedang bermasalah.
    """
    percobaan = []

    def _request(metode, url, **kwargs):
        percobaan.append(1)
        raise HttpError("HTTP 429: rate limit", status_code=429)

    monkeypatch.setattr(modul_llm, "request", _request)
    with pytest.raises(LLMError):
        _klien(reasoning_effort={"synthesis": "medium"}).chat(
            ["m"], "sistem", "data", step="synthesis"
        )
    assert len(percobaan) == 1


def test_telemetri_mencatat_token_per_langkah(tmp_path):
    """Biaya saja tidak cukup untuk tahu HARUS memperbaiki apa."""
    brief = {
        "generated_at": datetime(2026, 8, 22, tzinfo=timezone.utc).isoformat(),
        "price": {"last": 78000},
        "data_quality": {"llm_cost_usd": 0.5},
        "ai": {"critic": {}},
        "agen_kebijakan": {},
    }
    path = tmp_path / "telemetri.jsonl"
    telemetri.rekam(
        brief=brief,
        panggilan_llm=[
            {
                "step": "synthesis", "cost_usd": 0.15,
                "tokens_in": 11400, "tokens_out": 8100,
                "prompt_chars": 41000, "model": "openai/gpt-5.1",
            },
            {
                "step": "filter", "cost_usd": 0.02,
                "tokens_in": 9000, "tokens_out": 400,
                "prompt_chars": 32000, "model": "deepseek/deepseek-v3.2",
            },
            {
                "step": "filter", "cost_usd": 0.02,
                "tokens_in": 9000, "tokens_out": 400,
                "prompt_chars": 32000, "model": "anthropic/claude-haiku-4.5",
            },
        ],
        path=path,
    )
    baris = json.loads(path.read_text().splitlines()[0])
    assert baris["token_per_langkah"]["synthesis"] == {
        "masuk": 11400, "keluar": 8100, "panggilan": 1, "prompt_char": 41000,
    }
    assert baris["token_per_langkah"]["filter"]["panggilan"] == 2
    assert baris["token_per_langkah"]["filter"]["prompt_char"] == 64000

    ringkas = telemetri.ringkas(path=path, keluaran=tmp_path / "ringkas.json")
    sintesis = next(x for x in ringkas["biaya_per_langkah"] if x["langkah"] == "synthesis")
    assert sintesis["rata_token_keluar"] == 8100
    assert sintesis["rata_token_masuk"] == 11400


def test_angka_biaya_dibatasi_sejak_tanggal_tertentu(tmp_path, monkeypatch):
    """Run dari konfigurasi lama tidak boleh mencemari rata-rata biaya.

    Sembilan run pertama diisi ulang dari arsip dan sebagian besar sisanya
    run pengembangan, semuanya sebelum langkah penyiapan data pindah ke
    DeepSeek. Merata-ratakannya bersama run sekarang menghasilkan angka yang
    tidak menggambarkan satu pun konfigurasi yang pernah berjalan.
    """
    monkeypatch.setattr(telemetri, "BIAYA_SEJAK_UTC", "2026-08-22T17:00:00Z")

    path = tmp_path / "telemetri.jsonl"
    baris = [
        # Sebelum batas: era lama, mahal.
        {"waktu_utc": "2026-08-20T08:42:36Z", "biaya_usd": 0.75,
         "budget_terpakai_pct": 126.5, "siaga": {"risiko_jendela": "sedang"}, "harga": 74000},
        {"waktu_utc": "2026-08-22T10:14:00Z", "biaya_usd": 0.33,
         "budget_terpakai_pct": 44.0, "siaga": {"risiko_jendela": "rendah"}, "harga": 75000},
        # Sesudah batas: konfigurasi sekarang.
        {"waktu_utc": "2026-08-22T23:43:00Z", "biaya_usd": 0.26,
         "budget_terpakai_pct": 65.0, "biaya_per_langkah": {"synthesis": 0.2},
         "siaga": {"risiko_jendela": "rendah"}, "harga": 77000},
        {"waktu_utc": "2026-08-23T23:42:12Z", "biaya_usd": 0.24,
         "budget_terpakai_pct": 60.0, "biaya_per_langkah": {"synthesis": 0.18},
         "siaga": {"risiko_jendela": "rendah"}, "harga": 78000},
    ]
    path.write_text("".join(json.dumps(b) + "\n" for b in baris), encoding="utf-8")

    hasil = telemetri.ringkas(path=path, keluaran=tmp_path / "ringkas.json")

    # Hanya dua run terakhir yang masuk ke angka biaya.
    assert hasil["jumlah_run_biaya"] == 2
    assert hasil["biaya"]["rata_usd"] == pytest.approx(0.25)
    assert hasil["biaya"]["maks_usd"] == 0.26
    assert hasil["biaya"]["run_di_atas_85_persen_budget"] == 0   # yang 126,5% jatuh di luar
    assert [r["waktu_utc"] for r in hasil["run"]] == [
        "2026-08-23T23:42:12Z", "2026-08-22T23:43:00Z",
    ]

    # Yang BUKAN biaya tetap memakai jendela penuh: panel riwayat siaga di
    # halaman brief bergantung pada rentang hari yang panjang, dan
    # memendekkannya adalah kemunduran yang tidak diminta siapa pun.
    assert hasil["jumlah_run"] == 4
    assert len(hasil["riwayat_siaga"]) == 4


def test_batas_biaya_tidak_mengosongkan_halaman(tmp_path, monkeypatch):
    """Kalau batasnya menyapu semua run, jendela penuh dipakai kembali.

    Halaman biaya yang kosong sama sekali lebih membingungkan daripada
    halaman berisi run lama — dan itu keadaan yang wajar terjadi setelah
    data direset.
    """
    monkeypatch.setattr(telemetri, "BIAYA_SEJAK_UTC", "2027-01-01T00:00:00Z")

    path = tmp_path / "telemetri.jsonl"
    path.write_text(json.dumps({
        "waktu_utc": "2026-08-22T23:43:00Z", "biaya_usd": 0.26,
    }) + "\n", encoding="utf-8")

    hasil = telemetri.ringkas(path=path, keluaran=tmp_path / "ringkas.json")
    assert hasil["jumlah_run_biaya"] == 1
    assert hasil["biaya"]["rata_usd"] == pytest.approx(0.26)


def test_telemetri_mencatat_model_per_langkah(tmp_path):
    """Jatuhnya sebuah langkah ke model cadangan harus terlihat.

    Tanpa catatan ini, `filter` yang diam-diam pindah dari DeepSeek ke Haiku
    menaikkan biayanya delapan kali lipat tanpa satu pun pertanda sampai
    tagihan bulanan datang.
    """
    brief = {
        "generated_at": datetime(2026, 8, 22, tzinfo=timezone.utc).isoformat(),
        "price": {"last": 78000},
        "data_quality": {"llm_cost_usd": 0.5},
        "ai": {"critic": {}},
        "agen_kebijakan": {},
    }
    path = tmp_path / "telemetri.jsonl"
    telemetri.rekam(
        brief=brief,
        panggilan_llm=[
            {"step": "synthesis", "model": "openai/gpt-5.1"},
            # Satu langkah yang dibatch boleh dilayani dua model berbeda:
            # batch pertama lolos ke model utama, batch kedua jatuh ke
            # cadangan. Keduanya harus tercatat, bukan cuma yang terakhir.
            {"step": "filter", "model": "deepseek/deepseek-v3.2"},
            {"step": "filter", "model": "anthropic/claude-haiku-4.5"},
            {"step": "filter", "model": "deepseek/deepseek-v3.2"},
        ],
        path=path,
    )
    baris = json.loads(path.read_text().splitlines()[0])
    assert baris["model_per_langkah"]["synthesis"] == ["openai/gpt-5.1"]
    assert baris["model_per_langkah"]["filter"] == [
        "deepseek/deepseek-v3.2", "anthropic/claude-haiku-4.5",
    ]


def test_panggilan_mencatat_panjang_prompt(monkeypatch):
    """`tokens_in` yang ditagih tidak bisa ditafsirkan tanpa pembanding.

    Rasio karakter-per-token adalah satu-satunya cara membedakan prompt yang
    memang gemuk dari penagihan di atas apa yang dikirim.
    """
    monkeypatch.setattr(modul_llm, "request", lambda *a, **k: _balasan_ok())
    klien = _klien()
    klien.chat(["m"], "sistem" * 10, "data" * 20, step="synthesis")
    assert klien.calls[0]["prompt_chars"] == len("sistem" * 10) + len("data" * 20)
