"""Riset sumber berita dinamis: LLM menentukan ARAH pencarian, kode mengambil.

Daftar feed di config.yaml bersifat tetap — bagus untuk cakupan dasar, tapi
tidak pernah menyesuaikan diri dengan apa yang sedang terjadi. Kalau hari ini
pasar bergerak karena kebijakan tarif atau likuidasi bursa tertentu, feed
tetap itu mungkin sama sekali tidak memuatnya.

Langkah ini menutup celah tersebut: model murah diminta mengusulkan beberapa
kueri pencarian berita berdasarkan kondisi hari ini, lalu KODE yang mengambil
artikelnya lewat Google News RSS.

PEMBAGIAN TUGAS ITU DISENGAJA. Model tidak pernah menghasilkan berita, judul,
atau URL — ia cuma menyarankan apa yang layak dicari. Seluruh artikel yang
masuk tetap berasal dari feed sungguhan dan melewati jalur yang sama persis
dengan feed tetap: penyaringan umur, dedup, skor prioritas, filter relevansi,
lalu critic. Jadi tidak ada jalan bagi model untuk mengarang sumber.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List
from urllib.parse import quote_plus

from .llm import BudgetExceeded, LLMClient, LLMError

log = logging.getLogger(__name__)

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"

MAKS_QUERY = 6
PANJANG_QUERY_MAKS = 90


def _prompt() -> str:
    return (
        "Kamu periset berita untuk laporan pasar Bitcoin harian. Tugasmu BUKAN "
        "menulis analisa, melainkan menentukan APA YANG LAYAK DICARI hari ini.\n\n"

        "Kamu diberi kondisi pasar terkini dan tema yang menonjol pada laporan "
        "sebelumnya. Usulkan kueri pencarian berita berbahasa Inggris yang "
        "berpeluang menemukan berita PENTING yang mungkin terlewat oleh feed "
        "tetap (CoinDesk, Reuters, BBC, Fed, SEC, dan sejenisnya).\n\n"

        "Yang dicari:\n"
        "  - Peristiwa yang sedang berkembang dan berpotensi menggerakkan harga\n"
        "  - Kebijakan, regulasi, atau geopolitik yang belum tentu tercakup feed umum\n"
        "  - Kejadian spesifik yang disinggung tema sebelumnya dan mungkin ada lanjutannya\n\n"

        "Aturan menulis kueri:\n"
        "  - Bahasa Inggris, 2-6 kata, spesifik. Bukan 'bitcoin news' (terlalu "
        "umum, hasilnya sampah), melainkan 'bitcoin ETF outflow' atau "
        "'Fed rate cut expectations'.\n"
        "  - Jangan mengulang kueri yang saling bertumpang tindih.\n"
        "  - Jangan memasukkan tanggal atau tahun; feed sudah disaring per waktu.\n"
        f"  - Maksimal {MAKS_QUERY} kueri. Lebih sedikit tapi tajam lebih baik "
        "daripada banyak tapi umum.\n\n"

        "Balas HANYA array JSON berisi string kueri, contoh:\n"
        '  ["bitcoin ETF outflow", "SEC crypto enforcement", "Fed rate cut odds"]\n\n'
        "Tanpa penjelasan, tanpa pagar markdown."
    )


def usulkan_query(
    client: LLMClient, models: List[str], konteks: Dict[str, Any]
) -> List[str]:
    """Kueri pencarian yang diusulkan model untuk kondisi hari ini.

    Selalu mengembalikan list (kosong kalau gagal) — langkah ini pelengkap,
    tidak pernah menggagalkan pengambilan berita.
    """
    if not models:
        return []

    try:
        hasil = client.chat_json(
            models,
            _prompt(),
            "Kondisi hari ini:\n\n" + json.dumps(konteks, ensure_ascii=False, default=str),
            step="riset",
            temperature=0.5,
            # Dinaikkan dari 800: stealth/ox-alpha (PR #85) jauh lebih verbose
            # dari Haiku, dan 800 nyaris tidak beri ruang sama sekali kalau
            # ada token penalaran ikut terhitung sebelum isi jawabannya.
            max_tokens=2000,
        )
    except (LLMError, BudgetExceeded) as exc:
        log.warning("Riset kueri berita gagal: %s", exc)
        return []

    if not isinstance(hasil, list):
        log.warning("Riset kueri: balasan bukan array, dilewati")
        return []

    bersih: List[str] = []
    terlihat = set()
    for item in hasil:
        if not isinstance(item, str):
            continue
        q = " ".join(item.split())[:PANJANG_QUERY_MAKS].strip()
        # Kueri satu kata hampir selalu terlalu umum ("bitcoin", "crypto") dan
        # hasilnya cuma menambah derau yang harus disaring lagi.
        if len(q) < 6 or len(q.split()) < 2:
            continue
        kunci = q.lower()
        if kunci in terlihat:
            continue
        terlihat.add(kunci)
        bersih.append(q)
        if len(bersih) >= MAKS_QUERY:
            break

    if bersih:
        log.info("Riset kueri berita: %s", "; ".join(bersih))
    return bersih


def feed_dari_query(queries: List[str]) -> List[str]:
    """Ubah kueri jadi URL Google News RSS.

    Hasilnya dipakai sebagai feed tambahan pada `news.collect()`, jadi seluruh
    penanganan lanjutan (umur, dedup, skor) identik dengan feed tetap.
    """
    return [GOOGLE_NEWS_RSS.format(q=quote_plus(q)) for q in queries]
