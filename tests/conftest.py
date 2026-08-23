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

#: Chromium yang sudah tersedia di lingkungan pengembangan, dipakai apa
#: adanya supaya uji tidak perlu mengunduh browser sendiri.
#:
#: Nilai ini TIDAK boleh dipaksakan: di runner CI, Playwright memasang
#: browsernya sendiri di tempat lain, dan menunjuk ke jalur yang tidak ada
#: membuat seluruh uji halaman gagal dengan "Executable doesn't exist" —
#: kegagalan yang sama sekali tidak berhubungan dengan yang sedang diuji.
#: `_jalur_chromium()` di bawah karena itu memulangkan None kalau berkasnya
#: memang tidak ada, dan Playwright mencari sendiri.
CHROMIUM = os.environ.get("CHROMIUM_PATH", "/opt/pw-browsers/chromium-1194/chrome-linux/chrome")


def _jalur_chromium():
    """Jalur chromium kalau memang ada di sana; None supaya Playwright memilih."""
    return CHROMIUM if CHROMIUM and Path(CHROMIUM).exists() else None

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

    # SEMUA halaman disiapkan, bukan cuma index.html: satu halaman yang
    # tag CDN-nya lolos akan diam-diam mengambil dari jaringan saat diuji,
    # dan uji yang bergantung jaringan gagal karena sebab yang tidak ada
    # hubungannya dengan yang sedang diperiksa.
    halaman = sorted(tujuan.glob("*.html"))
    assert halaman, "tidak ada halaman HTML di docs/"
    for berkas in halaman:
        html = berkas.read_text(encoding="utf-8")
        for pola, ganti in _GANTI:
            # Tidak semua halaman memuat semua pustaka — cost.html tidak
            # memakai Chart.js. Yang dijaga: setiap tag CDN yang MEMANG ADA
            # tertukar, bukan bahwa setiap pustaka hadir di tiap halaman.
            html, jumlah = re.subn(pola, ganti, html)
            assert jumlah <= 1, f"tag CDN ganda di {berkas.name}: {pola}"
        assert "https://cdn." not in html and "https://unpkg." not in html, (
            f"masih ada tag CDN yang belum ditukar di {berkas.name}"
        )
        html = _POLA_KONFIG_TW.sub("", html)
        berkas.write_text(html, encoding="utf-8")
    return tujuan


@pytest.fixture
def asal(situs: Path):
    """Sajikan salinan situs lewat HTTP lokal, bukan `file://`.

    `app.js` mengambil datanya dengan fetch(), dan fetch dari halaman
    `file://` ditolak browser sebagai pelanggaran CORS (origin "null") —
    halamannya lalu berhenti di layar "Data belum bisa dimuat", jauh sebelum
    hal yang sebenarnya diuji sempat dirender.

    Memulangkan asal (origin)-nya saja; tiap halaman menyusun alamatnya
    sendiri di atas ini.
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
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        http.server.SimpleHTTPRequestHandler.log_message = handler_log


@pytest.fixture
def alamat(asal: str) -> str:
    """Halaman brief."""
    return f"{asal}/index.html"


@pytest.fixture
def alamat_biaya(asal: str) -> str:
    """Halaman biaya per run."""
    return f"{asal}/cost.html"


@pytest.fixture
def tulis_telemetri(situs: Path):
    """Ganti isi data/telemetri.json dengan ringkasan yang sudah dimodifikasi."""
    def _tulis(ringkasan: dict) -> None:
        (situs / "data" / "telemetri.json").write_text(
            json.dumps(ringkasan, ensure_ascii=False), encoding="utf-8"
        )
    return _tulis


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
        browser = p.chromium.launch(executable_path=_jalur_chromium())
        yield browser
        browser.close()
