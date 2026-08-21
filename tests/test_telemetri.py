"""Telemetri lintas hari: catatan per run dan ringkasannya.

Yang dijaga di sini bukan formatnya, tapi tiga pertanyaan yang selama ini
tidak bisa dijawab: seberapa sering critic menahan narasi, langkah mana yang
paling boros, dan apa yang terjadi pada harga setelah siaga menyala.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from src.output import telemetri


def _brief(waktu: datetime, *, harga: float, biaya: float, lolos=True,
           dijalankan=True, ditahan=(), siaga="rendah"):
    return {
        "generated_at": waktu.isoformat().replace("+00:00", "Z"),
        "run_type": "pagi",
        "price": {"last": harga},
        "data_quality": {
            "llm_cost_usd": biaya,
            "llm_token_masuk": 1000,
            "llm_token_keluar": 500,
            "durasi_detik": 400.0,
            "failed_sources": ["etf_flow"],
            "berita_corong": {"terkumpul": 100, "dipakai": 25},
        },
        "ai": {
            "bagian_ditahan": list(ditahan),
            "critic": {
                "passed": lolos,
                "dijalankan": dijalankan,
                "corrections": [{"jenis": "angka_karangan", "keparahan": "fatal"}]
                if not lolos else [],
            },
        },
        "agen_kebijakan": {
            "risiko_jendela": {"tingkat": siaga},
            "jendela": {"fase": "jeda_akhir_pekan"},
            "kerapuhan": {"tingkat": "tinggi"},
        },
    }


def test_biaya_per_langkah_terkumpul(tmp_path):
    path = tmp_path / "telemetri.jsonl"
    telemetri.rekam(
        brief=_brief(datetime(2026, 8, 20, tzinfo=timezone.utc), harga=70000, biaya=0.5),
        panggilan_llm=[
            {"step": "synthesis", "cost_usd": 0.2},
            {"step": "synthesis", "cost_usd": 0.05},
            {"step": "filter", "cost_usd": 0.03},
        ],
        budget_maks_usd=0.75,
        path=path,
    )
    baris = json.loads(path.read_text().splitlines()[0])
    assert baris["biaya_per_langkah"] == {"synthesis": 0.25, "filter": 0.03}
    assert baris["budget_terpakai_pct"] == 66.7


def test_ringkasan_menghitung_seberapa_sering_critic_menahan(tmp_path):
    path = tmp_path / "telemetri.jsonl"
    keluaran = tmp_path / "telemetri.json"
    awal = datetime(2026, 8, 10, tzinfo=timezone.utc)

    # Empat run: dua diperiksa dan lolos, satu diperiksa dan menahan narasi,
    # satu tidak sempat diperiksa sama sekali.
    telemetri.rekam(brief=_brief(awal, harga=70000, biaya=0.4), path=path)
    telemetri.rekam(brief=_brief(awal + timedelta(days=1), harga=70500, biaya=0.5), path=path)
    telemetri.rekam(
        brief=_brief(awal + timedelta(days=2), harga=69000, biaya=0.6,
                     lolos=False, ditahan=["narasi"]),
        path=path,
    )
    telemetri.rekam(
        brief=_brief(awal + timedelta(days=3), harga=68000, biaya=0.7, dijalankan=False),
        path=path,
    )

    ringkas = telemetri.ringkas(path=path, keluaran=keluaran)
    # Critic yang GAGAL DIJALANKAN tidak boleh ikut dihitung sebagai lolos —
    # kalau ikut, angkanya akan menyamarkan analisa yang terbit tanpa pernah
    # diperiksa.
    assert ringkas["critic"]["dijalankan"] == 3
    assert ringkas["critic"]["menahan"] == 1
    assert ringkas["critic"]["persen_menahan"] == 33.3
    assert ringkas["critic"]["bagian_tersering"] == {"narasi": 1}
    assert ringkas["sumber_paling_sering_gagal"][0] == {"sumber": "etf_flow", "run": 4}
    assert keluaran.exists()


def test_riwayat_siaga_memakai_run_berikutnya_yang_cukup_jauh(tmp_path):
    path = tmp_path / "telemetri.jsonl"
    awal = datetime(2026, 8, 10, tzinfo=timezone.utc)

    telemetri.rekam(brief=_brief(awal, harga=70000, biaya=0.4, siaga="tinggi"), path=path)
    # Run manual dua jam kemudian: TIDAK boleh dipakai sebagai pembanding,
    # selisih harga dua jam tidak menguji apa pun.
    telemetri.rekam(
        brief=_brief(awal + timedelta(hours=2), harga=70100, biaya=0.4, siaga="tinggi"),
        path=path,
    )
    telemetri.rekam(
        brief=_brief(awal + timedelta(hours=24), harga=66500, biaya=0.4, siaga="rendah"),
        path=path,
    )

    ringkas = telemetri.ringkas(path=path, keluaran=tmp_path / "ringkas.json")
    pertama = ringkas["riwayat_siaga"][0]
    assert pertama["tingkat"] == "tinggi"
    assert pertama["harga_sesudah"] == 66500
    assert pertama["perubahan_pct"] == -5.0

    # Run terakhir belum punya pembanding; harus null, bukan 0.
    assert ringkas["riwayat_siaga"][-1]["perubahan_pct"] is None


def test_baris_rusak_tidak_menghanguskan_riwayat(tmp_path):
    path = tmp_path / "telemetri.jsonl"
    telemetri.rekam(brief=_brief(datetime(2026, 8, 10, tzinfo=timezone.utc),
                                 harga=70000, biaya=0.4), path=path)
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"waktu_utc": "2026-08-11T00:00:00Z", "harga":\n')  # terpotong

    ringkas = telemetri.ringkas(path=path, keluaran=tmp_path / "ringkas.json")
    assert ringkas["jumlah_run"] == 1


def test_catatan_dipangkas_ke_batas(tmp_path, monkeypatch):
    monkeypatch.setattr(telemetri, "MAKS_BARIS", 3)
    path = tmp_path / "telemetri.jsonl"
    awal = datetime(2026, 8, 10, tzinfo=timezone.utc)
    for i in range(6):
        telemetri.rekam(
            brief=_brief(awal + timedelta(days=i), harga=70000 + i, biaya=0.4), path=path
        )
    assert len(path.read_text().strip().splitlines()) == 3
