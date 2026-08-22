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
            {"step": "synthesis", "cost_usd": 0.15, "tokens_in": 11400, "tokens_out": 8100},
            {"step": "filter", "cost_usd": 0.02, "tokens_in": 9000, "tokens_out": 400},
            {"step": "filter", "cost_usd": 0.02, "tokens_in": 9000, "tokens_out": 400},
        ],
        path=path,
    )
    baris = json.loads(path.read_text().splitlines()[0])
    assert baris["token_per_langkah"]["synthesis"] == {
        "masuk": 11400, "keluar": 8100, "panggilan": 1,
    }
    assert baris["token_per_langkah"]["filter"]["panggilan"] == 2

    ringkas = telemetri.ringkas(path=path, keluaran=tmp_path / "ringkas.json")
    sintesis = next(x for x in ringkas["biaya_per_langkah"] if x["langkah"] == "synthesis")
    assert sintesis["rata_token_keluar"] == 8100
    assert sintesis["rata_token_masuk"] == 11400
