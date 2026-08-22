"""Agenda ekonomi tambahan dari investing.com, diekstrak lewat LLM murah.

Kalender bawaan (`calendar.py`) menghitung tanggal dari pola bulanan tanpa
sumber luar sama sekali — akurat untuk FOMC (dari config), tapi CPI/NFP/PCE
di sana cuma DUGAAN ("Rabu ke-2", "Jumat pertama") karena tidak ada sumber
yang dibaca. investing.com menerbitkan kalender ekonomi sungguhan, tapi:

  1. Halamannya berat oleh proteksi anti-bot (mirip Farside), IP pusat data
     GitHub Actions kemungkinan besar ditolak.
  2. Tabelnya dirender lewat JavaScript dan markupnya rumit — regex biasa
     gampang rapuh terhadap perubahan kecil di struktur halaman.

Untuk (2), ekstraksinya diserahkan ke LLM murah: diberi teks mentah hasil
scrape, diminta mengembalikan daftar event terstruktur. LLM jauh lebih
tahan terhadap markup berantakan dibanding regex. Untuk (1), fungsi ini
selalu boleh gagal — kalau halaman diblokir atau LLM tidak tersedia,
kembali ke list kosong dan kalender bawaan tetap jalan tanpanya.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..analysis.llm import BudgetExceeded, LLMClient, LLMError
from ..utils.http import HttpError, get_text

log = logging.getLogger(__name__)

URL = "https://www.investing.com/economic-calendar/"

# Proteksi Cloudflare di investing.com menolak User-Agent skrip; header
# bergaya browser meningkatkan peluang lolos, meski tetap tidak dijamin.
HEADER_BROWSER = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

DAMPAK_VALID = ("tinggi", "menengah")


def _ambil_html() -> Optional[str]:
    """Timeout pendek, tanpa retry — sumber ini murni pelengkap, tidak boleh
    menahan pipeline kalau ternyata diblokir (lihat catatan di market.py
    soal Farside untuk pola yang sama)."""
    try:
        return get_text(URL, timeout=15, retries=0, headers=HEADER_BROWSER)
    except HttpError as exc:
        log.info("investing.com tidak terjangkau (wajar, sering diblokir IP pusat data): %s", exc)
        return None


def _teks_bersih(html: str, batas: int = 6000) -> str:
    """Buang script/style/tag, sisakan teks biasa yang bisa dibaca LLM."""
    html = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.IGNORECASE)
    html = re.sub(r"<style[\s\S]*?</style>", " ", html, flags=re.IGNORECASE)
    teks = re.sub(r"<[^>]+>", " ", html)
    teks = re.sub(r"&nbsp;", " ", teks)
    teks = re.sub(r"\s+", " ", teks).strip()
    return teks[:batas]


def _prompt() -> str:
    return (
        "Kamu mengekstrak jadwal rilis data ekonomi Amerika Serikat dari cuplikan "
        "teks hasil scrape halaman kalender ekonomi. Teksnya berantakan — banyak "
        "elemen navigasi, iklan, dan UI ikut terbawa. Abaikan semua itu, fokus "
        "HANYA pada baris yang benar-benar berisi jadwal event ekonomi.\n\n"
        "Ambil HANYA event yang: negaranya Amerika Serikat/USD, dan dampaknya "
        "tinggi atau menengah (buang yang dampaknya rendah atau hari libur).\n\n"
        "Balas array JSON, satu objek per event:\n"
        "  nama: nama event apa adanya, bahasa aslinya boleh Inggris "
        "(contoh \"CPI m/m\", \"Non-Farm Payrolls\", \"Fed Chair Powell Speaks\")\n"
        "  tanggal: format YYYY-MM-DD KALAU kamu benar-benar yakin bisa "
        "menyimpulkannya dari teks. Kalau ragu, isi null.\n"
        "  dampak: \"tinggi\" atau \"menengah\"\n\n"
        "Kalau kamu tidak yakin suatu baris benar-benar jadwal event (bukan "
        "navigasi/iklan/elemen UI lain), JANGAN masukkan. Kalau tidak menemukan "
        "event yang jelas sama sekali, balas array kosong []. JANGAN MENGARANG "
        "tanggal atau nama event yang tidak benar-benar ada di teks.\n\n"
        "Balas HANYA array JSON, tanpa penjelasan, tanpa pagar markdown."
    )


def collect(
    client: Optional[LLMClient], models: List[str], hari_ini_wib: str
) -> List[Dict[str, Any]]:
    """Event ekonomi tambahan hasil ekstraksi LLM dari investing.com.

    Return list kosong (bukan exception) kalau halaman tak terjangkau, teks
    hasil scrape terlalu pendek untuk diandalkan, atau LLM tidak tersedia —
    sumber ini murni pelengkap kalender bawaan, tidak pernah fatal.

    Setiap event yang dikembalikan diberi `waktu_utc` jam 00:00 UTC (tanggal
    saja, tanpa jam rilis persis) karena investing.com menampilkan jam dalam
    zona waktu sisi klien yang tidak bisa dipastikan dari teks hasil scrape
    saja — lebih aman melaporkan tanggalnya tanpa jam yang mungkin salah,
    daripada menebak jam dan menampilkannya seolah pasti.
    """
    if not client or not models:
        return []

    html = _ambil_html()
    if not html:
        return []

    teks = _teks_bersih(html)
    if len(teks) < 200:
        log.info("investing.com: teks hasil scrape terlalu pendek, dilewati")
        return []

    try:
        hasil = client.chat_json(
            models,
            _prompt(),
            f"Hari ini {hari_ini_wib}. Cuplikan teks halaman kalender ekonomi:\n\n{teks}",
            step="agenda",
            temperature=0.0,
            # Dinaikkan dari 2000: stealth/ox-alpha (PR #85) jauh lebih
            # verbose dari Haiku untuk tugas ekstraksi sederhana ini, dan
            # langkah ini tidak dibatch — lihat catatan serupa di
            # news_analysis.py step "filter".
            max_tokens=4000,
        )
    except (LLMError, BudgetExceeded) as exc:
        log.info("Ekstraksi agenda investing.com gagal: %s", exc)
        return []

    if not isinstance(hasil, list):
        return []

    keluaran: List[Dict[str, Any]] = []
    for item in hasil[:15]:
        if not isinstance(item, dict):
            continue
        nama = item.get("nama")
        dampak = item.get("dampak")
        if not nama or dampak not in DAMPAK_VALID:
            continue
        try:
            d = datetime.strptime(str(item.get("tanggal")), "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        except (ValueError, TypeError):
            continue
        keluaran.append(
            {
                "waktu_utc": d,
                "nama": str(nama)[:120],
                "kategori": "investing",
                "dampak": dampak,
                # Tanggal sungguhan dari kalender pihak ketiga, bukan dugaan
                # pola bulanan — beda dari event hasil hitungan calendar.py.
                "perkiraan": False,
            }
        )

    log.info("investing.com: %d event terambil", len(keluaran))
    return keluaran
