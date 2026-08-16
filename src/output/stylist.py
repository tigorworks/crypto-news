"""Perapi pesan Telegram dengan LLM murah.

Pesan yang dirakit kode benar isinya tapi terbaca kaku. Langkah ini
menatanya ulang supaya enak dibaca — menonjolkan harga dan analisa, menata
alur, menambah emoji yang relevan.

KENAPA ADA GERBANG VERIFIKASI

Memberi LLM kebebasan menulis ulang pesan berarti membuka jalan bagi angka
karangan lewat pintu belakang — persis hal yang dijaga ketat oleh critic di
langkah sebelumnya. Karena itu keluaran perapi TIDAK dipercaya begitu saja.
Sebelum dipakai, hasilnya diperiksa:

  1. Setiap angka di hasil harus sudah ada di pesan asli. Perapi boleh
     memindahkan dan memformat ulang angka, tidak boleh menciptakan.
  2. Hanya tag HTML yang didukung Telegram. Tag asing membuat kiriman
     ditolak API, dan brief-nya hilang sama sekali.
  3. Penanda AI dan disclaimer wajib bertahan.
  4. Panjang tetap di bawah batas Telegram.

Gagal satu saja -> pesan asli yang dikirim. Tampilan yang kurang cantik
jauh lebih baik daripada angka yang salah.
"""

from __future__ import annotations

import html
import logging
import re
from typing import Any, Dict, List, Optional, Set

from ..analysis.llm import BudgetExceeded, LLMClient, LLMError

log = logging.getLogger(__name__)

BATAS_KARAKTER = 4096

# Tag yang benar-benar didukung parse_mode HTML Telegram. Selain ini,
# API menolak seluruh pesan.
TAG_DIIZINKAN = {"b", "strong", "i", "em", "u", "s", "code", "pre", "a", "br", "blockquote"}

POLA_TAG = re.compile(r"</?([a-zA-Z][a-zA-Z0-9]*)")
# Angka termasuk desimal dan pemisah ribuan, dengan tanda opsional.
POLA_ANGKA = re.compile(r"-?\d[\d.,]*")

PENANDA_WAJIB = ("ANALISA AI", "bukan saran investasi")


def _angka_dinormalkan(teks: str) -> Set[str]:
    """Kumpulkan angka dalam bentuk yang tahan perbedaan format.

    '167.806', '167,806', dan '167806' dianggap angka yang sama supaya
    perapi bebas menyesuaikan gaya penulisan tanpa dianggap mengarang.
    """
    hasil: Set[str] = set()
    for cocok in POLA_ANGKA.findall(teks):
        bersih = cocok.replace(".", "").replace(",", "").lstrip("-").lstrip("0")
        if bersih:
            hasil.add(bersih)
    return hasil


def _tag_dipakai(teks: str) -> Set[str]:
    return {t.lower() for t in POLA_TAG.findall(teks)}


# Rasio minimal panjang hasil terhadap pesan asli. Perapi cuma boleh MENATA
# ulang, bukan MERINGKAS — kalau hasilnya jauh lebih pendek dari aslinya,
# yang paling mungkin terjadi bukan tata letak dirapikan, tapi seluruh
# bagian (penyebab, whale, outlook, kesimpulan, dst) ikut hilang saat
# ditulis ulang. Rasio 0,6 memberi ruang untuk pemadatan wajar (spasi dan
# pengulangan label dihapus) tapi menolak versi yang dipangkas jadi ringkasan.
RASIO_PANJANG_MINIMAL = 0.6


def periksa(asli: str, hasil: str) -> Optional[str]:
    """Return alasan penolakan, atau None kalau hasilnya layak pakai."""
    if not hasil or len(hasil.strip()) < 100:
        return "hasil kosong atau terlalu pendek"

    if len(hasil) > BATAS_KARAKTER:
        return f"melebihi batas Telegram ({len(hasil)} karakter)"

    if len(asli) >= 100 and len(hasil) < len(asli) * RASIO_PANJANG_MINIMAL:
        return (
            f"hasil jauh lebih pendek dari asli ({len(hasil)} vs {len(asli)} "
            "karakter) — kemungkinan sebagian isi ikut terhapus, bukan cuma dirapikan"
        )

    asing = _tag_dipakai(hasil) - TAG_DIIZINKAN
    if asing:
        return f"memakai tag HTML yang tidak didukung Telegram: {', '.join(sorted(asing))}"

    for penanda in PENANDA_WAJIB:
        if penanda.lower() not in hasil.lower():
            return f"penanda wajib hilang: '{penanda}'"

    # Disclaimer harus ada di dekat AKHIR pesan, bukan cuma di suatu tempat.
    # Kalau balasan LLM terpotong di tengah (token habis, reasoning model
    # menghabiskan budget sebelum sampai ke isi) tapi kebetulan sudah
    # menyebut "bukan saran investasi" lebih awal, pemeriksaan penanda di
    # atas tidak akan menangkapnya — pesan yang terpotong bisa lolos dan
    # terkirim ke pembaca berhenti di tengah kalimat. Disclaimer memang
    # selalu ditulis sebagai baris penutup di pesan asli, jadi kalau tidak
    # ada di ~300 karakter terakhir, kemungkinan besar isinya kepotong.
    ekor = hasil[-300:].lower()
    if "bukan saran investasi" not in ekor:
        return "disclaimer tidak berada di dekat akhir pesan (kemungkinan terpotong)"

    # Inti pemeriksaan: tidak boleh ada angka yang tidak ada di pesan asli.
    angka_asli = _angka_dinormalkan(asli)
    angka_baru = _angka_dinormalkan(hasil) - angka_asli
    # Angka satu digit sering muncul dari penomoran daftar yang ditata ulang;
    # itu bukan klaim data, jadi tidak dihitung sebagai karangan.
    angka_baru = {a for a in angka_baru if len(a) > 1}
    if angka_baru:
        return "memunculkan angka yang tidak ada di pesan asli: " + ", ".join(
            sorted(angka_baru)[:5]
        )

    return None


def _prompt() -> str:
    return (
        "Kamu penata pesan Telegram untuk brief pasar Bitcoin harian. Kamu "
        "menerima pesan yang isinya SUDAH BENAR tapi tata letaknya kaku.\n\n"

        "TUGASMU HANYA MENATA ULANG. Ini pekerjaan tata letak, bukan penulisan.\n\n"

        "LARANGAN MUTLAK:\n"
        "  - JANGAN mengubah angka apa pun. Satu digit pun tidak.\n"
        "  - JANGAN menambah angka baru, termasuk perhitungan sendiri.\n"
        "  - JANGAN menambah fakta, klaim, atau kesimpulan yang tidak ada.\n"
        "  - JANGAN menghapus angka penting, penanda ANALISA AI, atau disclaimer.\n"
        "  - JANGAN memberi saran beli/jual atau target harga.\n"
        "  - JANGAN meringkas atau memendekkan isi. Setiap bagian di pesan asli "
        "(penyebab pergerakan, pandangan ke depan, teknikal, whale, kesimpulan, "
        "dst) WAJIB tetap ada di hasil, dengan kalimat yang boleh dirapikan tapi "
        "TIDAK dipotong maknanya. Hasil yang jauh lebih pendek dari aslinya "
        "akan ditolak otomatis dan pesan asli yang dikirim — jadi lebih baik "
        "menata semuanya dengan rapi daripada memilih-milih bagian mana yang "
        "disertakan.\n\n"

        "YANG BOLEH KAMU LAKUKAN:\n"
        "  - Menata urutan supaya mengalir: harga dan analisa AI paling menonjol\n"
        "  - Menambah emoji yang relevan dan tidak berlebihan\n"
        "  - Merapikan spasi, jeda baris, dan pengelompokan\n"
        "  - Memperbaiki kalimat yang kaku TANPA mengubah maknanya\n"
        "  - Memakai <b> untuk penekanan dan <i> untuk catatan\n\n"

        "STRUKTUR YANG DIINGINKAN:\n"
        "  1. Harga BTC di paling atas, ditulis besar dan jelas, dengan arah "
        "     perubahannya\n"
        "  2. Inti analisa AI — judul temuan dan penjelasannya\n"
        "  3. Data pendukung dikelompokkan rapi\n"
        "  4. Penutup: kualitas data, link, disclaimer\n\n"

        "FORMAT TELEGRAM:\n"
        "  - HANYA tag ini yang boleh: <b> <i> <u> <s> <code> <a>\n"
        "  - JANGAN pakai <div> <span> <p> <h1> <br> atau markdown\n"
        "  - Maksimal 3900 karakter\n"
        "  - Pisahkan bagian dengan baris kosong, bukan garis panjang berulang\n\n"

        "Balas HANYA teks pesan yang sudah ditata. Tanpa penjelasan, tanpa "
        "pagar markdown, tanpa komentar apa pun."
    )


def rapikan(
    client: LLMClient,
    models: List[str],
    pesan: str,
    brief: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Rapikan pesan. Selalu mengembalikan pesan yang aman dikirim.

    Return: {"pesan": str, "dirapikan": bool, "alasan": str}
    """
    gagal = {"pesan": pesan, "dirapikan": False, "alasan": ""}
    if not models:
        gagal["alasan"] = "tidak ada model perapi terkonfigurasi"
        return gagal

    try:
        # 3900 karakter ~ 1000 token keluaran, tapi beberapa model menghitung
        # token penalaran dari budget yang sama sebelum sampai ke isi —
        # ruang ekstra ini jaga-jaga supaya isi sungguhannya tidak kepotong.
        hasil = client.chat(
            models,
            _prompt(),
            "Tata ulang pesan berikut:\n\n" + pesan,
            step="format",
            temperature=0.3,
            max_tokens=6000,
        )
    except (LLMError, BudgetExceeded) as exc:
        log.warning("Perapian pesan gagal (%s), pakai format asli", exc)
        gagal["alasan"] = str(exc)[:200]
        return gagal

    hasil = (hasil or "").strip()
    # Model kadang tetap membungkus dengan pagar meski sudah dilarang.
    if hasil.startswith("```"):
        hasil = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", hasil).strip()
    # Entitas ganda muncul kalau model meng-escape ulang teks yang sudah aman.
    if "&amp;amp;" in hasil:
        hasil = hasil.replace("&amp;amp;", "&amp;")

    alasan = periksa(pesan, hasil)
    if alasan:
        log.warning("Hasil perapian ditolak (%s), pakai format asli", alasan)
        return {"pesan": pesan, "dirapikan": False, "alasan": alasan}

    log.info(
        "Pesan Telegram dirapikan LLM: %d -> %d karakter", len(pesan), len(hasil)
    )
    return {"pesan": hasil, "dirapikan": True, "alasan": ""}
