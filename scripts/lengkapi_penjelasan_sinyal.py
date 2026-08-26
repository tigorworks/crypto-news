"""Lengkapi penjelasan pola "sinyal palsu" pada brief yang sudah terbit.

Jalankan: python -m scripts.lengkapi_penjelasan_sinyal [berkas ...]

Tanpa argumen: `docs/data/latest.json` + seluruh arsip HARI INI (WIB).
Dengan `--semua-arsip`: seluruh arsip yang tersimpan.

KENAPA ADA: kartu "Sinyal Palsu" dulu cuma memuat satu kalimat berangka —
"Harga menembus swing high 79.500 hingga 80.000 lalu ditutup kembali di
78.993". Kalimat itu benar dan padat, tapi hanya bisa dibaca oleh orang yang
sudah tahu apa itu swing high dan kenapa penutupan di bawahnya penting.
Perbaikannya cuma berlaku untuk run berikutnya, sementara brief yang SEDANG
TAMPIL masih membawa kalimat telanjang itu — dan brief terbit sekali sehari,
jadi menunggu berarti pembaca melihat versi lama selama belasan jam.

YANG DIKERJAKAN: melampirkan `penjelasan` (paragraf: apa yang diukur, apa
artinya, catatan volume untuk sapuan, apa yang membatalkan) dan `arti_singkat`
ke tiap sinyal, dirakit `technical.penjelasan_pola()` — sumber yang sama persis
dengan yang dipakai pipeline. Tidak ada satu pun panggilan LLM di sini, dan
memang tidak perlu: teksnya ditentukan oleh `jenis` dan `kekuatan`, keduanya
sudah tersimpan di brief.

YANG TIDAK DIKERJAKAN: menghitung ulang deteksinya. Candle sudah bergerak
sejak brief itu terbit; pola yang tercatat hari itu tetap pola hari itu,
lengkap dengan angkanya. Yang ditambahkan hanya lapisan penjelas.

Aman diulang: penjelasan yang sudah ada ditimpa dengan nilai yang identik,
jadi jalannya idempoten.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.analysis.technical import arti_singkat_pola, penjelasan_pola  # noqa: E402
from src.config import ARCHIVE_DIR, DATA_DIR  # noqa: E402
from src.utils.timezone import to_wib  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("lengkapi-penjelasan")


def _berkas_hari_ini() -> List[Path]:
    """latest.json + arsip yang tanggalnya hari ini menurut WIB."""
    from datetime import datetime, timezone

    hari_ini = to_wib(datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    berkas = [DATA_DIR / "latest.json"]
    berkas += sorted(ARCHIVE_DIR.glob(f"{hari_ini}-*.json"))
    return [b for b in berkas if b.exists()]


def lengkapi(brief: Dict[str, Any]) -> List[str]:
    """Lampirkan penjelasan ke tiap sinyal. Memulangkan daftar perubahan."""
    sinyal = (brief.get("technical") or {}).get("sinyal_palsu") or []
    perubahan: List[str] = []

    for s in sinyal:
        jenis = s.get("jenis") or ""
        paragraf = penjelasan_pola(jenis, s.get("kekuatan"))
        if not paragraf:
            # Pola tanpa entri penjelasan. Dilewati, bukan diisi seadanya:
            # penjelasan pola lain jauh lebih buruk daripada tidak ada.
            log.warning("   ! pola tanpa penjelasan: %s", jenis or "(kosong)")
            continue
        arti = arti_singkat_pola(jenis)
        if s.get("penjelasan") == paragraf and s.get("arti_singkat") == arti:
            continue
        s["penjelasan"] = paragraf
        s["arti_singkat"] = arti
        perubahan.append(f"penjelasan dilampirkan ke `{jenis}` ({len(paragraf)} paragraf)")

    return perubahan


def proses(path: Path, tulis: bool = True) -> List[str]:
    brief = json.loads(path.read_text(encoding="utf-8"))
    perubahan = lengkapi(brief)
    if perubahan and tulis:
        with path.open("w", encoding="utf-8") as fh:
            json.dump(brief, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    return perubahan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("berkas", nargs="*", type=Path, help="brief JSON (default: hari ini)")
    parser.add_argument("--semua-arsip", action="store_true",
                        help="ikut melengkapi SELURUH arsip, bukan cuma hari ini")
    parser.add_argument("--periksa", action="store_true",
                        help="laporkan saja, jangan tulis")
    args = parser.parse_args()

    if args.berkas:
        berkas = args.berkas
    elif args.semua_arsip:
        berkas = [DATA_DIR / "latest.json"] + sorted(ARCHIVE_DIR.glob("*.json"))
    else:
        berkas = _berkas_hari_ini()

    if not berkas:
        log.error("Tidak ada berkas untuk dilengkapi.")
        return 1

    total = 0
    for path in berkas:
        perubahan = proses(path, tulis=not args.periksa)
        if perubahan:
            total += 1
            log.info("%s", path.name)
            for baris in perubahan:
                log.info("   - %s", baris)
        else:
            log.info("%s (sudah sesuai)", path.name)

    log.info("\n%d dari %d berkas %s.", total, len(berkas),
             "perlu dilengkapi" if args.periksa else "dilengkapi")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
