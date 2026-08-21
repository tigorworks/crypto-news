"""Perkakas bersama untuk uji halaman.

Halaman produksi memuat Tailwind, Alpine, Chart.js, dan Lucide dari CDN.
Uji tidak boleh bergantung pada jaringan — hasilnya jadi tidak bisa diulang,
dan runner tanpa akses keluar akan gagal karena alasan yang sama sekali tidak
berhubungan dengan yang sedang diuji.

Karena itu halamannya disalin ke direktori sementara dengan tag CDN ditukar
ke berkas lokal hasil `python -m scripts.bangun_aset_uji`. Yang ditukar hanya
ALAMATNYA; struktur halaman, kelas, dan skrip aplikasinya tetap apa adanya.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
ASET = Path(__file__).resolve().parent / "aset"

#: Chromium bawaan lingkungan. Bisa ditimpa lewat env kalau Playwright
#: memasang browsernya sendiri di tempat lain.
CHROMIUM = os.environ.get("CHROMIUM_PATH", "/opt/pw-browsers/chromium-1194/chrome-linux/chrome")

_PETUNJUK_ASET = (
    "Aset uji belum dibangun. Jalankan: python -m scripts.bangun_aset_uji"
)

# Tag CDN -> berkas lokal. Cocokkan lewat potongan URL yang khas supaya
# perubahan atribut lain (defer, integrity) tidak membuat penukaran meleset.
_GANTI = (
    (r'<script src="https://cdn\.tailwindcss\.com"></script>',
     '<link rel="stylesheet" href="aset/tw.css">'),
    (r'<script src="https://cdn\.jsdelivr\.net/npm/chart\.js[^"]*"></script>',
     '<script src="aset/chart.js"></script>'),
    (r'<script src="https://unpkg\.com/lucide[^"]*"></script>',
     '<script src="aset/lucide.js"></script>'),
    (r'<script defer src="https://cdn\.jsdelivr\.net/npm/alpinejs[^"]*"></script>',
     '<script defer src="aset/alpine.js"></script>'),
)

# `tailwind.config = {...}` hanya dikenali skrip CDN. Tanpa CDN ia melempar
# ReferenceError yang menghentikan skrip inline berikutnya di blok yang sama.
_POLA_KONFIG_TW = re.compile(
    r"<script>\s*\n\s*tailwind\.config\s*=.*?</script>", re.DOTALL
)


@pytest.fixture(scope="session")
def aset_tersedia() -> bool:
    wajib = ["tw.css", "alpine.js", "chart.js", "lucide.js"]
    return all((ASET / n).exists() for n in wajib)


@pytest.fixture
def situs(tmp_path: Path, aset_tersedia: bool) -> Path:
    """Salinan docs/ yang bisa dibuka offline. Return direktorinya."""
    if not aset_tersedia:
        pytest.skip(_PETUNJUK_ASET)

    tujuan = tmp_path / "situs"
    shutil.copytree(DOCS, tujuan)
    shutil.copytree(ASET, tujuan / "aset")

    html = (tujuan / "index.html").read_text(encoding="utf-8")
    for pola, ganti in _GANTI:
        html, jumlah = re.subn(pola, ganti, html)
        assert jumlah == 1, f"tag CDN tidak ditemukan untuk pola: {pola}"
    html = _POLA_KONFIG_TW.sub("", html)
    (tujuan / "index.html").write_text(html, encoding="utf-8")
    return tujuan


@pytest.fixture
def alamat(situs: Path):
    """Sajikan salinan situs lewat HTTP lokal, bukan `file://`.

    `app.js` mengambil datanya dengan fetch(), dan fetch dari halaman
    `file://` ditolak browser sebagai pelanggaran CORS (origin "null") —
    halamannya lalu berhenti di layar "Data belum bisa dimuat", jauh sebelum
    hal yang sebenarnya diuji sempat dirender.
    """
    import functools
    import http.server
    import threading

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(situs))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    # Diam: SimpleHTTPRequestHandler menulis satu baris ke stderr per request,
    # dan satu halaman ini saja sudah puluhan permintaan.
    handler_log = http.server.SimpleHTTPRequestHandler.log_message
    http.server.SimpleHTTPRequestHandler.log_message = lambda *a, **k: None
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/index.html"
    finally:
        server.shutdown()
        server.server_close()
        http.server.SimpleHTTPRequestHandler.log_message = handler_log


@pytest.fixture
def tulis_data(situs: Path):
    """Ganti isi data/latest.json dengan brief yang sudah dimodifikasi."""
    def _tulis(brief: dict) -> None:
        (situs / "data" / "latest.json").write_text(
            json.dumps(brief, ensure_ascii=False), encoding="utf-8"
        )
    return _tulis


@pytest.fixture
def brief_asli() -> dict:
    """Brief produksi terakhir — dipakai sebagai basis lalu diubah seperlunya.

    Memakai data sungguhan, bukan fixture buatan tangan: fixture buatan
    tangan selalu ketinggalan bentuk data yang sebenarnya, dan itu justru
    kelas kesalahan yang paling sering lolos dari pengujian di repo ini.
    """
    return json.loads((DOCS / "data" / "latest.json").read_text(encoding="utf-8"))


@pytest.fixture
def peramban():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROMIUM)
        yield browser
        browser.close()
