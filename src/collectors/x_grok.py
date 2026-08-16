"""Postingan X (Twitter) tokoh berpengaruh, diambil lewat Grok.

KENAPA MODULNYA SEPARANOID INI
------------------------------
Seluruh proyek ini berdiri di atas satu aturan: **model tidak pernah
menghasilkan fakta, sumber, atau URL.** Modul ini adalah tempat aturan itu
paling gampang bocor — kita meminta sebuah LLM menyebutkan apa yang
diposting seseorang, dan LLM yang tidak tahu jawabannya cenderung mengarang
jawaban yang terdengar meyakinkan. Postingan presiden AS soal kripto yang
dikarang, lalu disiarkan ke Telegram sebagai intelijen pasar, adalah
kesalahan yang jauh lebih buruk daripada tidak punya data X sama sekali.

Jadi ada dua lapis pengaman, dan lapis KEDUA yang benar-benar dipegang:

  1. Pencarian langsung (Live Search xAI). Permintaan menyertakan
     `search_parameters` supaya Grok menjawab dari hasil pencarian X yang
     sungguhan. Ini menaikkan peluang jawabannya berdasar, TAPI tidak bisa
     kita verifikasi dari sisi kita — kalau OpenRouter tidak meneruskan
     parameternya, model diam-diam kembali menjawab dari ingatan.

  2. Verifikasi KODE atas tiap item (fungsi `_sah`). Ini yang menentukan.
     Item tanpa URL status X yang berbentuk sah, atau yang akunnya tidak
     sama dengan akun yang diminta, atau yang waktunya di luar jangkauan,
     DIBUANG — tidak peduli seberapa meyakinkan teksnya.

Yang JUJUR perlu diakui: verifikasi bentuk URL membuktikan formatnya benar,
bukan bahwa postingannya ada. Model yang mengarang URL berformat rapi tetap
bisa lolos lapis ini. Karena itu item dari sini ditandai `jenis_sumber:
"x_grok"` dan TIDAK PERNAH diperlakukan sebagai sumber primer: langkah LLM
pernyataan berikutnya tetap wajib menilai `status` (verbatim / dilaporkan
media / rumor), dan `terkonfirmasi_media` menandai apakah isinya juga
muncul di kandidat dari sumber lain. Pemakai yang ingin jaminan penuh
sebaiknya mematikan modul ini (`statements.x_grok.aktif: false`) dan
bersandar pada laporan media, yang memang jalur default proyek ini.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

from ..analysis.llm import BudgetExceeded, LLMClient, LLMError
from ..utils.timezone import iso_utc, now_utc

log = logging.getLogger(__name__)

# URL status X yang sah: host x.com / twitter.com, lalu /{akun}/status/{id}.
# ID status X (snowflake) panjangnya belasan digit; batas 5-25 memberi ruang
# tanpa menerima "/status/1" yang jelas karangan.
_POLA_URL_X = re.compile(
    r"^https?://(?:www\.|mobile\.)?(?:x|twitter)\.com/"
    r"(?P<akun>[A-Za-z0-9_]{1,15})/status/(?P<id>\d{5,25})",
    re.IGNORECASE,
)

# Batas aman supaya satu balasan model tidak membanjiri kandidat pernyataan.
MAKS_ITEM = 15
PANJANG_TEKS_MAKS = 1200


def _waktu_dari_iso(nilai: Any) -> Optional[datetime]:
    if not isinstance(nilai, str) or not nilai.strip():
        return None
    teks = nilai.strip().replace("Z", "+00:00")
    try:
        waktu = datetime.fromisoformat(teks)
    except ValueError:
        return None
    if waktu.tzinfo is None:
        waktu = waktu.replace(tzinfo=timezone.utc)
    return waktu.astimezone(timezone.utc)


def _sah(
    item: Any, akun: str, batas_waktu: datetime, sekarang: datetime
) -> Optional[Dict[str, Any]]:
    """Kembalikan item yang sudah dibersihkan, atau None kalau tidak lolos.

    Semua penolakan di sini disengaja keras: lebih baik kehilangan postingan
    yang sebenarnya asli daripada meloloskan satu yang dikarang.
    """
    if not isinstance(item, dict):
        return None

    url = str(item.get("url") or "").strip()
    cocok = _POLA_URL_X.match(url)
    if not cocok:
        return None

    # Akun di URL harus akun yang kita minta. Ini menutup jawaban yang
    # "benar tapi bukan milik siapa yang ditanya" — mis. kutipan orang lain
    # yang membicarakan tokoh tersebut.
    if cocok.group("akun").lower() != akun.lower().lstrip("@"):
        return None

    teks = str(item.get("teks") or item.get("text") or "").strip()
    if len(teks) < 15:
        return None

    waktu = _waktu_dari_iso(item.get("waktu_utc") or item.get("created_at"))
    if waktu is None:
        return None
    # Di luar jangkauan umur, atau bertanggal masa depan (tanda kuat model
    # mengarang timestamp).
    if waktu < batas_waktu or waktu > sekarang + timedelta(hours=2):
        return None

    return {
        "tokoh": akun.lstrip("@"),
        "teks": teks[:PANJANG_TEKS_MAKS],
        "url": url,
        "sumber": f"X/@{akun.lstrip('@')}",
        "domain": "x.com",
        # BUKAN "primer": isinya belum terbukti ada, cuma berformat sah.
        "jenis_sumber": "x_grok",
        "status_x_id": cocok.group("id"),
        "waktu_utc": iso_utc(waktu),
        "_waktu": waktu,
    }


def _parameter_pencarian(akun: str, max_age_hours: int) -> Dict[str, Any]:
    """Parameter Live Search xAI supaya Grok membaca X sungguhan.

    Bentuknya mengikuti API xAI. Kalau OpenRouter tidak meneruskan field ini,
    server mengabaikannya — tidak error, tapi juga tidak ada jaminan model
    benar-benar mencari. Itulah kenapa `_sah()` yang jadi penentu.
    """
    sejak = (now_utc() - timedelta(hours=max_age_hours)).date().isoformat()
    return {
        "search_parameters": {
            "mode": "on",
            "return_citations": True,
            "from_date": sejak,
            "max_search_results": 20,
            "sources": [{"type": "x", "included_x_handles": [akun.lstrip("@")]}],
        }
    }


def ambil_postingan(
    client: LLMClient,
    models: List[str],
    akun: str,
    max_age_hours: int = 48,
) -> List[Dict[str, Any]]:
    """Postingan X terbaru satu akun. List kosong kalau apa pun meragukan."""
    if not models:
        return []

    system = (
        "Kamu mengambil postingan terbaru dari X (Twitter) memakai pencarian "
        "langsung. Kamu BUKAN sumber pengetahuan di sini — kamu hanya "
        "meneruskan apa yang benar-benar kamu temukan.\n\n"
        "ATURAN MUTLAK:\n"
        "  1. HANYA laporkan postingan yang benar-benar kamu temukan lewat "
        "pencarian. Kalau kamu tidak punya akses pencarian langsung, atau "
        "tidak menemukan apa pun, balas array kosong: []\n"
        "  2. JANGAN PERNAH menyusun ulang postingan dari ingatan, dan jangan "
        "menebak isi, tanggal, maupun URL. Postingan yang dikarang jauh lebih "
        "merugikan daripada tidak ada data.\n"
        "  3. `url` wajib URL status asli berbentuk "
        "https://x.com/<akun>/status/<id>. Tanpa URL yang persis begitu, "
        "jangan sertakan itemnya sama sekali.\n"
        "  4. `teks` disalin apa adanya dari postingan, tanpa diringkas atau "
        "diterjemahkan.\n"
        "  5. `waktu_utc` format ISO 8601 UTC dari waktu terbit postingan.\n\n"
        "Balas HANYA array JSON:\n"
        "[{\"teks\": \"...\", \"url\": \"https://x.com/.../status/...\", "
        "\"waktu_utc\": \"2026-08-16T09:00:00Z\"}]\n"
        "Array kosong [] adalah jawaban yang benar dan diterima kalau tidak "
        "ada yang ditemukan."
    )
    user = (
        f"Cari postingan dari akun X @{akun.lstrip('@')} dalam "
        f"{max_age_hours} jam terakhir. Utamakan yang menyinggung ekonomi, "
        "suku bunga, The Fed, tarif, regulasi, atau kripto. Sertakan hanya "
        "yang benar-benar ditemukan lewat pencarian."
    )

    try:
        hasil = client.chat_json(
            models,
            system,
            user,
            step="x_posts",
            temperature=0.0,
            max_tokens=4000,
            extra_body=_parameter_pencarian(akun, max_age_hours),
        )
    except (LLMError, BudgetExceeded) as exc:
        log.warning("Ambil postingan X @%s lewat Grok gagal: %s", akun, exc)
        return []

    if not isinstance(hasil, list):
        log.warning("Balasan Grok untuk @%s bukan array, diabaikan", akun)
        return []

    sekarang = now_utc()
    batas_waktu = sekarang - timedelta(hours=max_age_hours)
    keluaran: List[Dict[str, Any]] = []
    id_terlihat: set = set()
    ditolak = 0

    for item in hasil[: MAKS_ITEM * 3]:
        bersih = _sah(item, akun, batas_waktu, sekarang)
        if bersih is None:
            ditolak += 1
            continue
        if bersih["status_x_id"] in id_terlihat:
            continue
        id_terlihat.add(bersih["status_x_id"])
        keluaran.append(bersih)
        if len(keluaran) >= MAKS_ITEM:
            break

    log.info(
        "X/@%s lewat Grok: %d postingan lolos verifikasi, %d ditolak",
        akun.lstrip("@"), len(keluaran), ditolak,
    )
    if ditolak and not keluaran:
        log.warning(
            "Semua kandidat X @%s ditolak verifikasi — kemungkinan model "
            "menjawab tanpa pencarian langsung", akun.lstrip("@"),
        )
    return keluaran


def tandai_konfirmasi_media(
    item_x: List[Dict[str, Any]], kandidat_lain: List[Dict[str, Any]]
) -> None:
    """Tandai postingan X yang isinya juga muncul di sumber lain.

    Satu-satunya verifikasi isi yang benar-benar bisa dilakukan kode: kalau
    media juga melaporkan hal yang sama, postingannya hampir pasti nyata.
    Yang tidak terkonfirmasi tidak dibuang — cuma ditandai, supaya langkah
    LLM berikutnya dan pembaca tahu bobotnya berbeda.
    """
    teks_lain = [
        (i.get("teks") or "").lower() for i in kandidat_lain
        if i.get("jenis_sumber") != "x_grok"
    ]
    for item in item_x:
        teks = (item.get("teks") or "").lower()
        terkonfirmasi = False
        for pembanding in teks_lain:
            if not pembanding:
                continue
            # Rasio rendah disengaja: laporan media memparafrase, bukan
            # menyalin. Yang dicari cuma "membicarakan hal yang sama".
            if SequenceMatcher(None, teks[:300], pembanding[:300]).ratio() >= 0.45:
                terkonfirmasi = True
                break
        item["terkonfirmasi_media"] = terkonfirmasi
