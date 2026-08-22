"""Stempel versi app.js di index.html tidak boleh basi.

`builder.segarkan_versi_aset()` menulis sidik jari app.js ke tag
`<script src="app.js?v=...">` supaya browser tidak memakai salinan lama dari
cache. Fungsi itu hanya dipanggil otomatis di dalam `main.py` saat brief
dibuat — PR yang mengedit `docs/app.js` langsung (tanpa menjalankan
pipeline) bisa lolos review dan CI padahal stempelnya tertinggal, dan
perubahannya lalu "tidak kelihatan" di browser pembaca sampai run
berikutnya menimpanya. Persis yang terjadi pada PR #81-#84: empat kali
app.js diedit manual, stempelnya tidak ikut diperbarui.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"

_POLA = re.compile(r'<script src="app\.js\?v=([0-9a-f]+)">')


def test_stempel_versi_cocok_dengan_isi_app_js():
    html = (DOCS / "index.html").read_text(encoding="utf-8")
    cocok = _POLA.search(html)
    assert cocok, 'tag <script src="app.js?v=...."> tidak ditemukan di index.html'

    versi_tertulis = cocok.group(1)
    versi_sebenarnya = hashlib.sha256((DOCS / "app.js").read_bytes()).hexdigest()[:8]
    assert versi_tertulis == versi_sebenarnya, (
        "stempel versi app.js di index.html basi — jalankan "
        "builder.segarkan_versi_aset() setelah mengedit docs/app.js"
    )
