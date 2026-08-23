"""Bangun aset uji halaman: CSS Tailwind + pustaka yang di produksi dari CDN.

Jalankan: python -m scripts.bangun_aset_uji

MASALAH YANG DISELESAIKAN
-------------------------
Halaman produksi memuat Tailwind dari `cdn.tailwindcss.com`, yang membangkitkan
utility ON DEMAND untuk kelas apa pun yang muncul di halaman. Pengujian lokal
tidak bisa memakai CDN itu (butuh jaringan, dan hasilnya bisa berbeda antar
hari), jadi dipakai CSS yang di-vendor — dan CSS vendor yang dibuat SEKALI
lalu dibiarkan akan tertinggal begitu halamannya berkembang.

Itu bukan kekhawatiran teoretis: salinan vendor sebelumnya kehilangan
`.block`, `.line-clamp-2`, dan `.self-center`. Ketiganya gagal DIAM — tidak
ada error, cuma tata letak yang salah — dan tiga kali membuat perbaikan yang
benar tampak gagal (atau sebaliknya, verifikasi yang menyesatkan).

Skrip ini membangkitkan ulang CSS-nya DARI SUMBER HALAMAN SAAT INI, memakai
konfigurasi Tailwind yang sama persis dengan yang dipasang `index.html`
(`darkMode: 'class'`, font Inter). Selama ia dijalankan ulang sesudah halaman
berubah, CSS uji tidak bisa lagi tertinggal diam-diam.

`docs/app.js` ikut dipindai, bukan cuma `index.html`: banyak kelas hanya
muncul sebagai string di dalam getter Alpine (`kelasSentimen`,
`kelasKualitas`, dan kawan-kawan). Itu justru yang paling sering hilang dari
salinan vendor lama.

Pustaka lain (Alpine, Chart.js, Lucide) diambil dari registry npm dengan
versi yang DIKUNCI ke yang dipakai halaman, bukan dari CDN — supaya uji tidak
ikut berubah saat CDN memutakhirkan versi minornya.

Hasilnya masuk ke `tests/aset/` dan TIDAK di-commit (lihat .gitignore):
berkasnya besar, dibangkitkan, dan versinya sudah dikunci di sini.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import DOCS_DIR, ROOT  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("aset-uji")

ASET_DIR = ROOT / "tests" / "aset"

#: Versi Tailwind. Sengaja v3: itulah yang dilayani cdn.tailwindcss.com, dan
#: v4 memakai mesin serta sintaks konfigurasi yang berbeda.
TAILWIND = "tailwindcss@3.4.17"

#: Pustaka lain, versinya disalin dari tag <script> di index.html. Kalau
#: halamannya menaikkan versi, naikkan juga di sini — ketidakcocokan versi
#: adalah persis jenis perbedaan uji-vs-produksi yang mau dihindari berkas ini.
PUSTAKA = {
    "alpinejs@3.14.1": ("dist/cdn.min.js", "alpine.js"),
    "chart.js@4.4.1": ("dist/chart.umd.js", "chart.js"),
    "lucide@0.451.0": ("dist/umd/lucide.min.js", "lucide.js"),
}

KONFIG_TAILWIND = """
module.exports = {
  content: %s,
  darkMode: 'class',
  theme: { extend: { fontFamily: { sans: ['Inter', 'system-ui', 'sans-serif'] } } },
};
"""

MASUKAN_CSS = "@tailwind base;\n@tailwind components;\n@tailwind utilities;\n"


def _jalankan(perintah: list, cwd: Path) -> None:
    log.info("$ %s", " ".join(perintah))
    subprocess.run(perintah, cwd=cwd, check=True)


def bangun_css(kerja: Path) -> Path:
    # SELURUH halaman di docs/ ikut dipindai, bukan cuma index.html. Kelas
    # yang tidak ter-generate gagal DIAM: halamannya tetap render, cuma
    # tanpa tata letaknya — dan uji yang memeriksa posisi elemen lalu jatuh
    # karena sebab yang tidak ada hubungannya dengan yang sedang diuji.
    konten = [str(p) for p in sorted(DOCS_DIR.glob("*.html"))]
    konten.append(str(DOCS_DIR / "app.js"))
    (kerja / "tailwind.config.js").write_text(
        KONFIG_TAILWIND % json.dumps(konten), encoding="utf-8"
    )
    (kerja / "masukan.css").write_text(MASUKAN_CSS, encoding="utf-8")
    keluaran = ASET_DIR / "tw.css"
    _jalankan(
        [
            "npx", "--yes", TAILWIND,
            "-c", str(kerja / "tailwind.config.js"),
            "-i", str(kerja / "masukan.css"),
            "-o", str(keluaran),
        ],
        cwd=kerja,
    )
    return keluaran


def ambil_pustaka(kerja: Path) -> None:
    for paket, (di_dalam, nama_lokal) in PUSTAKA.items():
        hasil = subprocess.run(
            ["npm", "pack", paket, "--silent"],
            cwd=kerja, check=True, capture_output=True, text=True,
        )
        tarball = kerja / hasil.stdout.strip().splitlines()[-1]
        with tarfile.open(tarball) as tar:
            anggota = tar.extractfile(f"package/{di_dalam}")
            if anggota is None:
                raise FileNotFoundError(f"{di_dalam} tidak ada di {paket}")
            (ASET_DIR / nama_lokal).write_bytes(anggota.read())
        log.info("%s -> tests/aset/%s", paket, nama_lokal)


def periksa_utility_penting(css: Path) -> None:
    """Pagar terhadap kegagalan diam yang jadi alasan berkas ini ada.

    Ketiganya pernah hilang dari salinan vendor sebelumnya. Kalau salah satu
    tidak ada, CSS-nya tidak layak dipakai menguji tata letak — dan lebih
    baik ketahuan di sini daripada muncul sebagai "perbaikan yang gagal".
    """
    isi = css.read_text(encoding="utf-8")
    hilang = [
        kelas for kelas in (r"\.block", r"\.line-clamp-2", r"\.self-center")
        if not re.search(kelas + r"\s*\{", isi)
    ]
    if hilang:
        raise SystemExit(
            "CSS uji tidak memuat utility yang dibutuhkan: "
            + ", ".join(h.replace("\\", "") for h in hilang)
        )
    log.info("CSS uji: %d KB, utility penting lengkap", len(isi) // 1024)


def main() -> int:
    if not shutil.which("npx"):
        log.error("npx tidak tersedia. Pasang Node.js dulu.")
        return 1

    ASET_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        kerja = Path(tmp)
        css = bangun_css(kerja)
        ambil_pustaka(kerja)
    periksa_utility_penting(css)
    log.info("Selesai. Aset uji ada di %s", ASET_DIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
