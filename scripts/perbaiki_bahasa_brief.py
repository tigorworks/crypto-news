"""Terapkan ulang pemetaan bahasa TERBARU ke brief yang sudah terbit.

Jalankan: python -m scripts.perbaiki_bahasa_brief [berkas ...]

Tanpa argumen: `docs/data/latest.json` + seluruh arsip HARI INI (WIB).

KENAPA ADA: perbaikan bahasa hanya berlaku untuk run berikutnya, sementara
brief yang sedang tampil di halaman masih membawa kata-kata lama. Brief
terbit sekali sehari, jadi menunggu berarti pembaca melihat versi lama
selama belasan jam — padahal seluruh perbaikannya DETERMINISTIK dan tidak
butuh satu pun panggilan model.

YANG DIKERJAKAN — hanya transformasi yang akan dilakukan pipeline sendiri
pada run berikutnya:

  1. Label dan penjelasan jenis pergerakan dirakit ulang dari `_ARTI_JENIS`
     (klasifikasinya TIDAK dihitung ulang — arah, jenis, dan besaran tetap
     apa adanya, karena itu hasil pengukuran hari itu).
  2. Frasa kaku dan nama field yang bocor diganti lewat `istilah`.

YANG TIDAK DIKERJAKAN: menulis ulang kalimat model. Judul dan analisa tetap
milik model yang menulisnya hari itu; yang berubah cuma istilah yang memang
diganti kode. Menyunting isinya dengan tangan berarti menerbitkan tulisan
manusia di bawah penanda AI, dan itu bukan perbaikan — itu pemalsuan.

Aman diulang: transformasinya idempoten.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.analysis.technical import _ARTI_JENIS, _kalimat_pergerakan  # noqa: E402
from src.config import ARCHIVE_DIR, DATA_DIR  # noqa: E402
from src.utils import istilah  # noqa: E402
from src.utils.timezone import to_wib  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("perbaiki-bahasa")


def _berkas_hari_ini() -> List[Path]:
    """latest.json + arsip yang tanggalnya hari ini menurut WIB."""
    from datetime import datetime, timezone

    hari_ini = to_wib(datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    berkas = [DATA_DIR / "latest.json"]
    berkas += sorted(ARCHIVE_DIR.glob(f"{hari_ini}-*.json"))
    return [b for b in berkas if b.exists()]


def _rakit_ulang_pergerakan(brief: Dict[str, Any]) -> List[str]:
    """Susun ulang kalimat pergerakan 24 jam dari label yang berlaku sekarang."""
    p = ((brief.get("technical") or {}).get("pergerakan_24j")) or {}
    jenis = p.get("jenis")
    if not p.get("arah"):
        return []
    if jenis not in _ARTI_JENIS:
        # Hari datar atau open interest tidak tersedia: tidak ada label jenis
        # yang perlu dirakit, dan menebaknya justru mengarang.
        return []

    label, arti = _ARTI_JENIS[jenis]
    sebelum = p.get("jenis_ringkas")
    p["jenis_ringkas"], p["jenis_arti"] = label, arti

    # Kalimat kode dirakit ulang dari komponen yang SUDAH tersimpan, bukan
    # dihitung ulang dari candle — candle-nya sudah bergerak sejak brief itu
    # terbit, dan angka di halaman harus tetap angka hari itu.
    pendukung = p.get("berita_pendukung") or []
    p["ringkas"] = _kalimat_pergerakan(
        p["arah"], float(p.get("perubahan_pct") or 0.0), p.get("besaran") or "wajar",
        arti, p.get("volume_konfirmasi"), pendukung,
    )
    return [] if sebelum == label else [f"jenis pergerakan: {sebelum!r} -> {label!r}"]


def _bersihkan_istilah(brief: Dict[str, Any]) -> List[str]:
    """Terapkan penyaring istilah ke bagian yang ditulis model."""
    perubahan: List[str] = []

    for kunci in ("ai", "aggregate", "diff_vs_previous"):
        sebelum = json.dumps(brief.get(kunci), ensure_ascii=False, sort_keys=True)
        brief[kunci] = istilah.manusiakan_dalam(brief.get(kunci))
        if json.dumps(brief[kunci], ensure_ascii=False, sort_keys=True) != sebelum:
            perubahan.append(f"istilah dibersihkan di `{kunci}`")

    diubah_berita = 0
    for artikel in brief.get("news") or []:
        for kunci in ("judul_id", "ringkasan_id", "mekanisme"):
            nilai = artikel.get(kunci)
            if not nilai:
                continue
            baru = istilah.manusiakan(nilai)
            if baru != nilai:
                artikel[kunci] = baru
                diubah_berita += 1
    if diubah_berita:
        perubahan.append(f"istilah dibersihkan di {diubah_berita} field berita")
    return perubahan


def perbaiki(path: Path, tulis: bool = True) -> List[str]:
    brief = json.loads(path.read_text(encoding="utf-8"))
    perubahan = _rakit_ulang_pergerakan(brief) + _bersihkan_istilah(brief)
    if perubahan and tulis:
        with path.open("w", encoding="utf-8") as fh:
            json.dump(brief, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    return perubahan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("berkas", nargs="*", type=Path, help="brief JSON (default: hari ini)")
    parser.add_argument("--semua-arsip", action="store_true",
                        help="ikut memperbaiki SELURUH arsip, bukan cuma hari ini")
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
        log.error("Tidak ada berkas untuk diperbaiki.")
        return 1

    total = 0
    for path in berkas:
        perubahan = perbaiki(path, tulis=not args.periksa)
        if perubahan:
            total += 1
            log.info("%s", path.name)
            for baris in perubahan:
                log.info("   - %s", baris)
        else:
            log.info("%s (sudah sesuai)", path.name)

    log.info("\n%d dari %d berkas %s.", total, len(berkas),
             "perlu diperbaiki" if args.periksa else "diperbaiki")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
