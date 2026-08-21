"""Isi telemetri awal dari arsip brief yang sudah ada.

Jalankan sekali: python -m scripts.telemetri_dari_arsip

Telemetri lintas hari baru mulai terisi pada run berikutnya, sementara arsip
brief yang sudah tersimpan memuat hampir semua angkanya — biaya, token,
durasi, status critic, sumber gagal, corong berita, dan tingkat siaga.
Menyalinnya membuat pertanyaan "seberapa sering critic menahan narasi" bisa
dijawab HARI INI, bukan dua bulan lagi.

Yang TIDAK bisa dipulihkan dari arsip: biaya per langkah. Rinciannya memang
tidak pernah ikut disimpan di brief (justru itu sebabnya telemetri dibuat),
jadi baris hasil isian awal punya `biaya_per_langkah` kosong dan ringkasan
per langkah baru terbentuk dari run-run berikutnya.

Aman diulang: berkas telemetri ditulis ulang dari arsip + baris yang sudah
ada, tanpa menduplikasi run yang waktunya sama.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import ARCHIVE_DIR, DATA_DIR  # noqa: E402
from src.output import telemetri  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("telemetri-arsip")


def _brief_dari_arsip() -> list:
    berkas = sorted(ARCHIVE_DIR.glob("*.json"))
    terbaru = DATA_DIR / "latest.json"
    briefs = []
    for path in berkas:
        try:
            briefs.append(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Arsip %s dilewati: %s", path.name, exc)
    # latest.json ikut kalau ia belum terwakili arsip (run terakhir bisa saja
    # dry-run, yang memang tidak menulis arsip).
    if terbaru.exists():
        try:
            isi = json.loads(terbaru.read_text(encoding="utf-8"))
            if isi.get("generated_at") not in {b.get("generated_at") for b in briefs}:
                briefs.append(isi)
        except (json.JSONDecodeError, OSError):
            pass
    briefs.sort(key=lambda b: str(b.get("generated_at") or ""))
    return briefs


def main() -> int:
    briefs = _brief_dari_arsip()
    if not briefs:
        log.error("Tidak ada arsip di %s", ARCHIVE_DIR)
        return 1

    sudah = {
        str(b.get("waktu_utc"))
        for b in telemetri._baca_baris(telemetri.CATATAN_PATH)  # noqa: SLF001
    }
    ditambah = 0
    for brief in briefs:
        if str(brief.get("generated_at")) in sudah:
            continue
        telemetri.rekam(
            brief=brief,
            ringkasan_llm={},
            panggilan_llm=[],
            # Plafon saat itu tidak ikut tersimpan di brief; dipakai nilai
            # yang berlaku sekarang supaya persentasenya tetap sebanding.
            budget_maks_usd=0.60,
            feed_gagal=[],
        )
        ditambah += 1

    ringkasan = telemetri.ringkas()
    log.info(
        "%d run diisi dari arsip. Total tersimpan %d, critic menahan %s%% dari %d pemeriksaan.",
        ditambah,
        ringkasan.get("total_run_tersimpan", 0),
        ringkasan.get("critic", {}).get("persen_menahan"),
        ringkasan.get("critic", {}).get("dijalankan", 0),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
