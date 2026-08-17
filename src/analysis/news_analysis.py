"""Rangkaian panggilan LLM untuk analisa brief.

Urutannya:
    filter -> klasifikasi -> mekanisme -> interpretasi teknikal -> analisa whale
    -> sintesis -> outlook -> critic

Ini workflow deterministik, bukan agent. LLM tidak memilih langkah maupun tool;
seluruh urutan, batasan, dan perhitungan angka ditentukan kode di sini. Model
hanya MENAFSIRKAN angka yang sudah jadi — tidak pernah menghitungnya.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from dateutil import parser as date_parser

from ..utils.format import persen_id
from ..utils.timezone import now_utc, to_utc
from .llm import BudgetExceeded, LLMClient, LLMError

log = logging.getLogger(__name__)

# Aturan yang WAJIB ada di setiap system prompt.
ATURAN_DASAR = """ATURAN WAJIB:
1. Balas HANYA JSON valid sesuai skema. Tanpa preamble, tanpa penjelasan, tanpa pagar markdown.
2. Setiap penilaian harus merujuk isi artikel yang diberikan, bukan pengetahuan umummu.
3. DILARANG memberi target harga, rekomendasi beli/jual, atau prediksi arah harga.
4. Kalau informasi tidak cukup, isi null. JANGAN MENEBAK.
5. Seluruh teks naratif ditulis dalam bahasa Indonesia.
6. DI DALAM nilai string, pemisah paragraf ditulis sebagai \\n (dua karakter:
   backslash lalu huruf n) — JANGAN menekan enter/baris baru sungguhan.
   Baris baru mentah di dalam string membuat JSON tidak sah dan SELURUH
   balasanmu terbuang, bukan cuma satu field.
7. Tanda kutip ganda di dalam nilai string wajib di-escape (\\"). Kalau ragu,
   pakai tanda kutip tunggal ('begini') supaya tidak perlu di-escape."""

KATEGORI = ["regulasi", "makro", "etf", "onchain", "hack", "adopsi", "teknologi", "geopolitik"]
SENTIMEN = ["bullish", "bearish", "netral"]
HORIZON = ["langsung", "pendek", "struktural"]
STATUS_KEPASTIAN = ["rumor", "belum_dikonfirmasi", "dikonfirmasi", "sudah_terjadi", "terjadwal"]
JALUR_TRANSMISI = ["likuiditas", "risk_appetite", "supply_demand", "regulasi"]
PRICED_IN = ["ya", "tidak", "sebagian"]
TIPE_KLAIM = ["faktual", "prediktif", "opini"]

BOBOT_TIER = {1: 1.0, 2: 0.7, 3: 0.4}


# --------------------------------------------------------------------------
# LLM #1 — filter relevansi
# --------------------------------------------------------------------------
def _bonus_tier(tier: Optional[int]) -> float:
    """Pengali ringan untuk kredibilitas sumber: tier 1 ×1,30 … tier 3 ×1,00.

    Sengaja RINGAN, bukan BOBOT_TIER (1,0/0,7/0,4) yang dipakai skor sentimen.
    Brief ini soal Bitcoin, dan berita Bitcoin paling banyak datang dari media
    kripto yang kebanyakan tier 2-3; memakai bobot penuh akan membuat berita
    makro tier 1 yang cuma menyerempet BTC menggusur berita kripto yang justru
    jadi pokok laporan. Yang diinginkan di sini cuma pemecah imbang: pada
    relevansi setara, sumber yang lebih bisa dipertanggungjawabkan menang.
    """
    try:
        t = int(tier)
    except (TypeError, ValueError):
        t = 3
    return 1.0 + 0.15 * max(0, 3 - max(1, min(3, t)))


def filter_relevansi(
    client: LLMClient,
    models: List[str],
    articles: List[Dict[str, Any]],
    min_score: int = 40,
    max_keep: int = 25,
    batch_size: int = 60,
) -> List[Dict[str, Any]]:
    if not articles:
        return []

    system = (
        "Kamu penyaring berita untuk laporan pasar Bitcoin. Tugasmu hanya menilai "
        "seberapa relevan tiap artikel terhadap pergerakan harga Bitcoin dalam skala 0-100. "
        "Relevansi tinggi: kebijakan moneter, regulasi kripto, arus ETF, likuidasi besar, "
        "keamanan jaringan, adopsi institusional. Relevansi rendah: harga altcoin, NFT, "
        "gosip selebritas, siaran pers proyek kecil.\n\n"
        "Balas array JSON: [{\"id\": \"...\", \"relevansi_btc\": 0-100}]\n\n" + ATURAN_DASAR
    )

    # Dibatch: satu panggilan untuk SELURUH artikel pernah nyaris menyentuh
    # batas max_tokens (132 artikel -> 3027 token keluaran). Dengan daftar feed
    # yang lebih panjang, sekali terpotong berarti seluruh penilaian hilang dan
    # brief jatuh ke fallback kata kunci. Batch juga membuat satu batch yang
    # gagal tidak menjatuhkan yang lain.
    skor: Dict[str, int] = {}
    total_batch = (len(articles) + batch_size - 1) // batch_size
    for i in range(0, len(articles), batch_size):
        batch = articles[i : i + batch_size]
        daftar = [
            {"id": a["id"], "judul": a["judul"], "ringkasan": a["ringkasan"][:250]}
            for a in batch
        ]
        try:
            hasil = client.chat_json(
                models,
                system,
                "Nilai relevansi setiap artikel berikut:\n\n"
                + json.dumps(daftar, ensure_ascii=False),
                step="filter",
                max_tokens=4000,
            )
        except BudgetExceeded as exc:
            log.warning("Filter relevansi berhenti di batch %d/%d: %s",
                        i // batch_size + 1, total_batch, exc)
            break
        except LLMError as exc:
            log.warning("Batch filter %d/%d gagal: %s", i // batch_size + 1, total_batch, exc)
            continue

        for item in hasil if isinstance(hasil, list) else []:
            try:
                skor[str(item["id"])] = int(item["relevansi_btc"])
            except (KeyError, TypeError, ValueError):
                continue

    if not skor:
        log.warning("Filter relevansi tidak menghasilkan skor, pakai skor prioritas kata kunci")
        # Fallback deterministik: pakai skor kata kunci yang sudah dihitung kode.
        fallback = sorted(articles, key=lambda a: a["skor_prioritas"], reverse=True)[:max_keep]
        for a in fallback:
            a["relevansi_btc"] = a["skor_prioritas"]
        return fallback

    terpilih = []
    for a in articles:
        nilai = skor.get(a["id"])
        if nilai is None or nilai < min_score:
            continue
        a["relevansi_btc"] = nilai
        terpilih.append(a)

    # Urutan pakai skor gabungan (relevansi × bonus tier), tapi relevansi_btc
    # yang dilaporkan tetap penilaian mentah model — supaya angka yang tampil
    # di web/critic tidak berbeda dari yang dinilai.
    terpilih.sort(
        key=lambda a: a["relevansi_btc"] * _bonus_tier(a.get("kredibilitas_sumber")),
        reverse=True,
    )

    # Isi bergiliran per domain: ronde 0 mengambil artikel terbaik dari tiap
    # domain, ronde 1 yang terbaik kedua, dan seterusnya. Urutan skor tetap
    # dihormati DI DALAM tiap ronde, tapi satu outlet yang rajin menerbitkan
    # (Blockworks pernah 50 artikel dalam satu tarikan) tidak bisa memborong
    # kuota semata-mata karena jumlahnya banyak. Cara ini juga tidak pernah
    # menyisakan slot kosong: kalau kandidat memang terkonsentrasi di sedikit
    # outlet, ronde berikutnya mengambil lagi dari outlet yang sama.
    hasil_akhir: List[Dict[str, Any]] = []
    per_domain: Dict[str, int] = {}
    diambil: set = set()
    ronde = 0
    while len(hasil_akhir) < max_keep:
        maju = False
        for idx, a in enumerate(terpilih):
            if len(hasil_akhir) >= max_keep:
                break
            if idx in diambil:
                continue
            domain = a.get("domain") or ""
            if per_domain.get(domain, 0) != ronde:
                continue
            per_domain[domain] = ronde + 1
            diambil.add(idx)
            hasil_akhir.append(a)
            maju = True
        if not maju:
            break
        ronde += 1

    log.info(
        "Filter relevansi: %d dari %d artikel lolos, %d dipakai dari %d outlet",
        len(terpilih), len(articles), len(hasil_akhir), len(per_domain),
    )
    return hasil_akhir


# --------------------------------------------------------------------------
# LLM #2 — klasifikasi
# --------------------------------------------------------------------------
def _prompt_klasifikasi() -> str:
    return (
        "Kamu analis berita pasar Bitcoin. Klasifikasikan setiap artikel yang diberikan.\n\n"
        "Balas array JSON, satu objek per artikel, dengan field:\n"
        "  id (string, sama persis dengan input)\n"
        f"  kategori: salah satu dari {KATEGORI}\n"
        f"  sentimen: salah satu dari {SENTIMEN} (dampak terhadap harga BTC)\n"
        "  kekuatan: angka Arab 1-5 (seberapa besar potensi dampaknya) — tulis 3, JANGAN III\n"
        f"  horizon: salah satu dari {HORIZON}\n"
        f"  status_kepastian: salah satu dari {STATUS_KEPASTIAN}\n"
        "  entitas: array nama lembaga/perusahaan/orang yang disebut (maksimal 4)\n"
        f"  sudah_priced_in: salah satu dari {PRICED_IN}\n"
        f"  tipe_klaim: salah satu dari {TIPE_KLAIM}\n"
        "  judul_id: judul artikel diterjemahkan ke bahasa Indonesia, maksimal 120 karakter\n"
        "  ringkasan_id: 1-2 kalimat isi artikel dalam bahasa Indonesia, maksimal 220 karakter\n\n"
        "Aturan penerjemahan:\n"
        "  - Terjemahkan maknanya, bukan kata per kata. Judul harus terbaca wajar "
        "sebagai judul berita berbahasa Indonesia.\n"
        "  - Nama diri, nama lembaga, ticker, dan istilah pasar yang memang dipakai "
        "apa adanya di Indonesia (Bitcoin, ETF, Fed, SEC, halving, futures) "
        "JANGAN diterjemahkan.\n"
        "  - Jangan menambah, menghilangkan, atau melunakkan isi. Angka disalin persis.\n"
        "  - Kalau judulnya memang sudah berbahasa Indonesia, salin apa adanya.\n\n"
        "Bedakan dengan tegas berita yang SUDAH terjadi dari yang baru RUMOR atau "
        "sekadar OPINI analis. Berita berjadwal (rilis data yang belum keluar) "
        "berstatus terjadwal.\n\n" + ATURAN_DASAR
    )


def _validasi_klasifikasi(item: Dict[str, Any]) -> Dict[str, Any]:
    """Paksa nilai enum tetap dalam daftar; yang di luar daftar jadi None."""
    def enum(key: str, allowed: List[str]) -> Optional[str]:
        value = item.get(key)
        return value if value in allowed else None

    try:
        kekuatan = int(item.get("kekuatan"))
        kekuatan = max(1, min(5, kekuatan))
    except (TypeError, ValueError):
        kekuatan = None

    entitas = item.get("entitas")
    if not isinstance(entitas, list):
        entitas = []

    def teks(key: str, batas: int) -> Optional[str]:
        nilai = item.get(key)
        if not isinstance(nilai, str) or not nilai.strip():
            return None
        return nilai.strip()[:batas]

    return {
        "judul_id": teks("judul_id", 160),
        "ringkasan_id": teks("ringkasan_id", 280),
        "kategori": enum("kategori", KATEGORI),
        "sentimen": enum("sentimen", SENTIMEN),
        "kekuatan": kekuatan,
        "horizon": enum("horizon", HORIZON),
        "status_kepastian": enum("status_kepastian", STATUS_KEPASTIAN),
        "entitas": [str(e)[:60] for e in entitas[:4]],
        "sudah_priced_in": enum("sudah_priced_in", PRICED_IN),
        "tipe_klaim": enum("tipe_klaim", TIPE_KLAIM),
    }


def klasifikasi(
    client: LLMClient, models: List[str], articles: List[Dict[str, Any]], batch_size: int = 5
) -> List[Dict[str, Any]]:
    if not articles:
        return []

    system = _prompt_klasifikasi()
    hasil_per_id: Dict[str, Dict[str, Any]] = {}

    for i in range(0, len(articles), batch_size):
        batch = articles[i : i + batch_size]
        payload = [
            {
                "id": a["id"],
                "judul": a["judul"],
                "ringkasan": a["ringkasan"][:400],
                "sumber": a["sumber"],
                "waktu_utc": a["waktu_utc"],
            }
            for a in batch
        ]
        try:
            hasil = client.chat_json(
                models,
                system,
                "Klasifikasikan artikel berikut:\n\n" + json.dumps(payload, ensure_ascii=False),
                step="classify",
                # Naik dari 4000: langkah ini sekarang juga menerjemahkan judul
                # dan ringkasan tiap artikel, jadi keluarannya jauh lebih panjang.
                # Terpotong di tengah berarti seluruh batch hilang.
                max_tokens=9000,
            )
        except BudgetExceeded as exc:
            log.warning("Klasifikasi berhenti di batch %d: %s", i // batch_size + 1, exc)
            break
        except LLMError as exc:
            log.warning("Batch klasifikasi %d gagal: %s", i // batch_size + 1, exc)
            continue

        for item in hasil if isinstance(hasil, list) else []:
            if isinstance(item, dict) and item.get("id"):
                hasil_per_id[str(item["id"])] = _validasi_klasifikasi(item)

    keluaran = []
    for a in articles:
        klas = hasil_per_id.get(a["id"])
        if klas is None:
            # Tanpa klasifikasi, artikel tetap ditampilkan tapi tidak ikut skor sentimen.
            klas = {
                "kategori": None, "sentimen": None, "kekuatan": None, "horizon": None,
                "status_kepastian": None, "entitas": [], "sudah_priced_in": None,
                "tipe_klaim": None, "judul_id": None, "ringkasan_id": None,
            }
        keluaran.append({**a, **klas, "mekanisme": None, "jalur_transmisi": None})

    log.info("Klasifikasi selesai: %d artikel", len(hasil_per_id))
    return keluaran


# --------------------------------------------------------------------------
# LLM #3 — mekanisme transmisi
# --------------------------------------------------------------------------
def analisa_agenda(
    client: LLMClient,
    models: List[str],
    agenda: List[Dict[str, Any]],
    konteks_pasar: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Nilai seberapa besar tiap agenda berdampak ke kripto, dan lewat jalur apa.

    Kalender menghasilkan daftar mentah: CPI, NFP, expiry opsi, pidato ECB,
    penjualan ritel. Semuanya "acara ekonomi", tapi dampaknya ke BTC jauh dari
    seragam — expiry opsi bulanan bergerak lewat mekanisme yang sama sekali
    berbeda dari rilis CPI, dan sebagian acara nyaris tidak berpengaruh.

    Model TIDAK boleh menambah atau membuang acara. Ia hanya memberi anotasi,
    dan pencocokannya memakai indeks yang dikirim kode — anotasi dengan indeks
    yang tidak dikenali dibuang. Jadi tidak ada peluang mengarang agenda.
    """
    if not agenda or not models:
        return agenda

    system = (
        "Kamu analis makro yang menilai dampak agenda ekonomi terhadap harga "
        "Bitcoin. Kamu menerima daftar acara terjadwal beserta kondisi pasar "
        "saat ini.\n\n"

        "Untuk SETIAP acara, nilai:\n"
        "  - Seberapa besar potensi dampaknya ke BTC secara spesifik (bukan ke "
        "pasar saham atau ekonomi umum)\n"
        "  - Lewat JALUR APA dampak itu sampai ke harga BTC\n\n"

        "Jalur transmisi yang lazim:\n"
        "  - Rilis inflasi/tenaga kerja -> ekspektasi suku bunga Fed -> "
        "likuiditas dolar -> aset berisiko termasuk BTC\n"
        "  - Keputusan FOMC -> biaya modal dan selera risiko\n"
        "  - Expiry opsi besar -> tarikan harga ke max pain, volatilitas "
        "meningkat menjelang dan sesudahnya\n"
        "  - Keputusan regulator (SEC/CFTC) -> premi risiko regulasi kripto\n\n"

        "Balas array JSON, satu objek per acara:\n"
        "  id: integer, SAMA PERSIS dengan id pada input\n"
        "  relevansi_kripto: 1-5 (1 = nyaris tidak berpengaruh ke BTC, "
        "5 = berpotensi menggerakkan harga secara signifikan)\n"
        "  jalur: satu kalimat rantai transmisi menuju harga BTC, memakai "
        "tanda panah. Contoh: \"CPI lebih panas dari perkiraan -> ekspektasi "
        "pemangkasan suku bunga mundur -> dolar menguat -> BTC tertekan\"\n"
        "  arah: salah satu dari \"naik\", \"turun\", \"dua_arah\" — pakai "
        "\"dua_arah\" kalau hasilnya bisa menggerakkan ke mana saja "
        "tergantung angkanya (ini yang PALING SERING benar untuk rilis data)\n\n"

        "DILARANG memprediksi hasil rilisnya, menyebut target harga, atau "
        "menyarankan tindakan. Yang kamu jelaskan adalah MEKANISME, bukan "
        "ramalan. Untuk acara yang memang tidak punya kaitan jelas dengan "
        "kripto, beri relevansi 1 dan katakan terus terang di `jalur`.\n\n"

        "JANGAN menambah acara yang tidak ada di input, dan jangan membuang "
        "acara yang ada. Jumlah objek balasanmu harus sama dengan jumlah "
        "acara yang dikirim.\n\n" + ATURAN_DASAR
    )

    payload = [
        {
            "id": i,
            "nama": a.get("nama"),
            "waktu_wib": a.get("waktu_wib"),
            "dampak_umum": a.get("dampak"),
            "tanggal_perkiraan": a.get("perkiraan"),
        }
        for i, a in enumerate(agenda)
    ]
    user = (
        "Kondisi pasar:\n" + json.dumps(konteks_pasar, ensure_ascii=False, default=str)
        + "\n\nAgenda terjadwal:\n" + json.dumps(payload, ensure_ascii=False)
    )

    try:
        hasil = client.chat_json(
            models, system, user, step="agenda_dampak", temperature=0.2, max_tokens=4000
        )
    except (LLMError, BudgetExceeded) as exc:
        log.warning("Analisa dampak agenda gagal: %s", exc)
        return agenda

    if not isinstance(hasil, list):
        log.warning("Analisa dampak agenda: balasan bukan array, dilewati")
        return agenda

    anotasi: Dict[int, Dict[str, Any]] = {}
    for item in hasil:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item["id"])
        except (KeyError, TypeError, ValueError):
            continue
        # Indeks di luar jangkauan berarti model mengarang acara — dibuang.
        if not 0 <= idx < len(agenda):
            continue
        try:
            relevansi = max(1, min(5, int(item.get("relevansi_kripto"))))
        except (TypeError, ValueError):
            relevansi = None
        arah = item.get("arah")
        anotasi[idx] = {
            "relevansi_kripto": relevansi,
            "jalur": str(item.get("jalur", ""))[:400] or None,
            "arah": arah if arah in ("naik", "turun", "dua_arah") else "dua_arah",
        }

    keluaran = []
    for i, acara in enumerate(agenda):
        keluaran.append({**acara, **(anotasi.get(i) or {})})

    log.info("Dampak agenda dinilai: %d dari %d acara", len(anotasi), len(agenda))
    return keluaran


def analisa_mekanisme(
    client: LLMClient, models: List[str], articles: List[Dict[str, Any]], top_n: int = 10
) -> List[Dict[str, Any]]:
    def bobot(a: Dict[str, Any]) -> float:
        return (a.get("kekuatan") or 0) * (a.get("relevansi_btc") or 0) / 100

    terpilih = sorted(articles, key=bobot, reverse=True)[:top_n]
    terpilih = [a for a in terpilih if bobot(a) > 0]
    if not terpilih:
        return articles

    system = (
        "Kamu analis makro yang menjelaskan jalur transmisi berita ke harga Bitcoin.\n\n"
        "Untuk setiap artikel, jelaskan SATU kalimat rantai sebab-akibat konkret dari "
        "isi artikel menuju harga BTC. Gunakan format berantai dengan tanda panah.\n"
        "Contoh yang benar: \"Yield UST 10Y naik -> biaya modal naik -> tekanan pada aset "
        "tanpa imbal hasil termasuk BTC.\"\n"
        "Contoh yang salah: \"Berita ini negatif untuk Bitcoin.\" (tidak menjelaskan mekanisme)\n\n"
        "Balas array JSON dengan field:\n"
        "  id (string, sama persis dengan input)\n"
        "  mekanisme: satu kalimat rantai sebab-akibat, bahasa Indonesia\n"
        f"  jalur_transmisi: salah satu dari {JALUR_TRANSMISI}\n\n"
        "Kalau artikel tidak punya jalur transmisi yang jelas ke harga BTC, isi kedua "
        "field dengan null.\n\n" + ATURAN_DASAR
    )
    payload = [
        {
            "id": a["id"],
            "judul": a["judul"],
            "ringkasan": a["ringkasan"][:400],
            "kategori": a.get("kategori"),
            "sentimen": a.get("sentimen"),
        }
        for a in terpilih
    ]

    try:
        hasil = client.chat_json(
            models,
            system,
            "Jelaskan mekanisme untuk artikel berikut:\n\n" + json.dumps(payload, ensure_ascii=False),
            step="mechanism",
            max_tokens=4000,
        )
    except (LLMError, BudgetExceeded) as exc:
        log.warning("Analisa mekanisme gagal: %s", exc)
        return articles

    per_id = {}
    for item in hasil if isinstance(hasil, list) else []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        jalur = item.get("jalur_transmisi")
        per_id[str(item["id"])] = {
            "mekanisme": (str(item["mekanisme"])[:400] if item.get("mekanisme") else None),
            "jalur_transmisi": jalur if jalur in JALUR_TRANSMISI else None,
        }

    for a in articles:
        if a["id"] in per_id:
            a.update(per_id[a["id"]])

    log.info("Mekanisme terisi untuk %d artikel", len(per_id))
    return articles


# --------------------------------------------------------------------------
# Cross-check berita vs harga (kode murni)
# --------------------------------------------------------------------------
def _reaksi_harga(waktu_utc: str, klines_1h: List[Dict[str, Any]]) -> Optional[float]:
    """Perubahan harga pada candle 1H pertama setelah berita terbit."""
    if not waktu_utc or not klines_1h:
        return None
    try:
        waktu = to_utc(date_parser.parse(waktu_utc))
    except (ValueError, TypeError, OverflowError):
        return None

    target_ms = int(waktu.timestamp() * 1000)
    for candle in klines_1h:
        if candle["open_time"] >= target_ms:
            if not candle["open"]:
                return None
            return round((candle["close"] - candle["open"]) / candle["open"] * 100, 2)
    return None


def cross_check(
    articles: List[Dict[str, Any]],
    klines_1h: List[Dict[str, Any]],
    funding_rate: Optional[float],
) -> Dict[str, Any]:
    """Bandingkan sentimen berita kuat dengan pergerakan harga 1 jam setelahnya."""
    conflicts: List[Dict[str, Any]] = []

    for a in articles:
        if (a.get("kekuatan") or 0) < 4:
            continue
        reaksi = _reaksi_harga(a.get("waktu_utc"), klines_1h)
        a["reaksi_harga_1j"] = reaksi
        if reaksi is None or not a.get("sentimen"):
            continue

        arah_berita = {"bullish": 1, "bearish": -1}.get(a["sentimen"], 0)
        if arah_berita == 0:
            continue

        if abs(reaksi) < 0.3:
            a["catatan_reaksi"] = "Pasar praktis mengabaikan berita ini."
        elif (reaksi > 0) == (arah_berita > 0):
            a["catatan_reaksi"] = "Harga bergerak searah sentimen; kemungkinan sudah tercermin di harga."
        else:
            a["catatan_reaksi"] = "Harga bergerak berlawanan dengan sentimen berita."
            conflicts.append(
                {
                    "tipe": "anomali_reaksi",
                    "keterangan": (
                        f"Berita {a['sentimen']} berkekuatan {a['kekuatan']} "
                        f"(\"{a['judul'][:80]}\") diikuti pergerakan harga {persen_id(reaksi, 2, pakai_tanda=True)} "
                        "pada jam berikutnya — berlawanan arah."
                    ),
                }
            )

    # Berita bullish kuat + funding tinggi = risiko long squeeze.
    if funding_rate is not None and funding_rate > 0.0005:
        bullish_kuat = [
            a for a in articles
            if a.get("sentimen") == "bullish" and (a.get("kekuatan") or 0) >= 4
        ]
        if bullish_kuat:
            conflicts.append(
                {
                    "tipe": "risiko_long_squeeze",
                    "keterangan": (
                        f"Ada {len(bullish_kuat)} berita bullish kuat sementara funding rate "
                        f"sudah tinggi ({persen_id(funding_rate * 100, 3)} per 8 jam). Posisi long padat "
                        "membuat pasar rentan terhadap pembalikan tajam."
                    ),
                }
            )

    return {"conflicts": conflicts}


# --------------------------------------------------------------------------
# Skor sentimen agregat (kode murni)
# --------------------------------------------------------------------------
def _decay(waktu_utc: Optional[str], sekarang: datetime) -> float:
    """1.0 untuk berita < 6 jam, turun linear ke 0.3 pada 36 jam."""
    if not waktu_utc:
        return 0.3
    try:
        waktu = to_utc(date_parser.parse(waktu_utc))
    except (ValueError, TypeError, OverflowError):
        return 0.3
    umur_jam = (sekarang - waktu).total_seconds() / 3600
    if umur_jam <= 6:
        return 1.0
    if umur_jam >= 36:
        return 0.3
    return 1.0 - (umur_jam - 6) / 30 * 0.7


def skor_sentimen(articles: List[Dict[str, Any]], tier_lookup) -> Dict[str, Any]:
    """Skor tertimbang -100..+100.

    skor = Σ (arah × kekuatan × relevansi/100 × bobot_kredibilitas × decay_waktu)
    dinormalisasi terhadap total bobot maksimum, bukan rata-rata sederhana —
    supaya satu berita kuat tidak tenggelam oleh banyak berita lemah.
    """
    sekarang = now_utc()
    total = 0.0
    bobot_maks = 0.0
    faktual = 0
    berklaim = 0

    for a in articles:
        kekuatan = a.get("kekuatan") or 0
        sentimen = a.get("sentimen")
        if not kekuatan or sentimen not in SENTIMEN:
            continue

        arah = {"bullish": 1, "bearish": -1, "netral": 0}[sentimen]
        relevansi = (a.get("relevansi_btc") or 0) / 100
        tier = tier_lookup(a.get("domain", ""))
        kredibilitas = BOBOT_TIER.get(tier, 0.4)
        # Cerita yang dikonfirmasi banyak outlet diberi bonus kecil, dibatasi 1.3x.
        konfirmasi = min(1.0 + (a.get("jumlah_konfirmasi", 1) - 1) * 0.1, 1.3)
        decay = _decay(a.get("waktu_utc"), sekarang)

        bobot = kekuatan * relevansi * kredibilitas * konfirmasi * decay
        total += arah * bobot
        bobot_maks += bobot

        if a.get("tipe_klaim"):
            berklaim += 1
            if a["tipe_klaim"] == "faktual":
                faktual += 1

    skor = round(total / bobot_maks * 100, 1) if bobot_maks > 0 else 0.0
    skor = max(-100.0, min(100.0, skor))

    if skor >= 40:
        label = "bullish kuat"
    elif skor >= 15:
        label = "bullish"
    elif skor > -15:
        label = "netral"
    elif skor > -40:
        label = "bearish"
    else:
        label = "bearish kuat"

    return {
        "sentiment_score": skor,
        "sentiment_label": label,
        "rasio_faktual": round(faktual / berklaim, 2) if berklaim else None,
        "jumlah_dinilai": berklaim,
    }


def tema_dominan(articles: List[Dict[str, Any]], top_n: int = 3) -> List[str]:
    """Tema dihitung dari kategori berbobot, bukan dari LLM."""
    bobot: Dict[str, float] = {}
    for a in articles:
        kategori = a.get("kategori")
        if not kategori:
            continue
        nilai = (a.get("kekuatan") or 1) * ((a.get("relevansi_btc") or 50) / 100)
        bobot[kategori] = bobot.get(kategori, 0) + nilai
    return [k for k, _ in sorted(bobot.items(), key=lambda kv: kv[1], reverse=True)[:top_n]]


# --------------------------------------------------------------------------
# LLM — pernyataan tokoh berpengaruh
# --------------------------------------------------------------------------
TOPIK_PERNYATAAN = ["kripto", "moneter", "tarif", "regulasi", "geopolitik", "fiskal", "lainnya"]
SIKAP_PERNYATAAN = ["mendukung", "menentang", "netral"]
STATUS_PERNYATAAN = ["verbatim", "dilaporkan_media", "rumor"]


def agen_kebijakan(
    client: LLMClient,
    models: List[str],
    pernyataan: List[Dict[str, Any]],
    berita: List[Dict[str, Any]],
    jendela: Dict[str, Any],
    rapuh: Dict[str, Any],
    maks_item: int = 10,
) -> Optional[Dict[str, Any]]:
    """Agen pemantau kebijakan AS: seberapa berbahaya kalau kejutan datang SEKARANG.

    Ini bukan pengumpul data — pengumpulannya sudah dikerjakan collectors
    (whitehouse.gov, Google News, X lewat Grok). Tugas langkah ini
    mempertemukan tiga hal yang selama ini tidak pernah disandingkan:

      1. sinyal kebijakan/pernyataan AS yang sudah tersaring,
      2. POSISI WAKTUNYA terhadap jam bursa AS (dihitung kode), dan
      3. KERAPUHAN pasar saat ini (dihitung kode).

    Poin 2 yang paling sering terlewat. Kebijakan yang sama berdampak jauh
    berbeda tergantung kapan ia mendarat: pada Rabu siang pasar AS bisa
    langsung mencernanya lewat ETF dan lindung nilai; pada Jumat malam
    kripto menanggungnya sendirian sampai Senin.

    Angka jendela dan kerapuhan TIDAK dihitung ulang model — keduanya
    disodorkan sudah jadi, dan model hanya menafsirkan artinya.
    """
    if not jendela:
        return None

    kandidat = [
        {
            "tokoh": p.get("tokoh"),
            "ringkasan": p.get("ringkasan"),
            "topik": p.get("topik"),
            "dampak_btc": p.get("dampak_btc"),
            "kekuatan": p.get("kekuatan"),
            "status": p.get("status"),
            "waktu_utc": p.get("waktu_utc"),
        }
        for p in (pernyataan or [])[:maks_item]
    ]
    # Berita kebijakan/geopolitik ikut disertakan: kebijakan besar sering
    # muncul sebagai berita lebih dulu sebelum ada pernyataan resminya.
    kandidat_berita = [
        {
            "judul": b.get("judul_id") or b.get("judul"),
            "kategori": b.get("kategori"),
            "sentimen": b.get("sentimen"),
            "kekuatan": b.get("kekuatan"),
            "status_kepastian": b.get("status_kepastian"),
        }
        for b in (berita or [])
        if b.get("kategori") in ("regulasi", "geopolitik", "makro")
        and (b.get("kekuatan") or 0) >= 3
    ][:maks_item]

    if not kandidat and not kandidat_berita:
        return None

    system = (
        "Kamu pemantau risiko kebijakan AS untuk pasar kripto. Tugasmu SATU: "
        "menilai seberapa besar risiko kejutan kebijakan bagi pemegang BTC "
        "pada JENDELA WAKTU saat ini.\n\n"

        "KENAPA WAKTU PENTING — pahami ini sebelum menilai:\n"
        "Kripto diperdagangkan 24/7, tapi bursa saham AS dan ETF spot Bitcoin "
        "tutup Jumat 16.00 waktu New York dan baru buka Senin 09.30 — jeda "
        "sekitar 65 jam. Di dalam jeda itu tidak ada penciptaan/penebusan unit "
        "ETF, meja institusi AS tutup, dan likuiditas order book menipis. "
        "Kejutan kebijakan yang mendarat di jendela itu ditanggung SENDIRIAN "
        "oleh pasar kripto sampai Senin — itu sebabnya sebagian akhir pekan "
        "berakhir dengan penurunan tajam.\n\n"

        "Angka jendela dan kerapuhan SUDAH DIHITUNG KODE dan diberikan "
        "kepadamu. JANGAN menghitung ulang, jangan membantahnya, dan jangan "
        "mengarang angka baru. Tugasmu menafsirkan artinya.\n\n"

        "ATURAN PENILAIAN\n"
        "  - Kalau tidak ada sinyal kebijakan yang berarti, katakan begitu "
        "terus terang dan beri siaga 'rendah'. Menakut-nakuti tanpa dasar "
        "lebih merugikan daripada diam.\n"
        "  - Bedakan yang SUDAH TERJADI dari yang BARU WACANA. Wacana yang "
        "belum jadi kebijakan bukan alasan siaga tinggi.\n"
        "  - Siaga 'tinggi' hanya kalau KETIGANYA bertemu: ada sinyal "
        "kebijakan berdampak, waktunya di jendela rawan, dan pasarnya rapuh.\n"
        "  - Dilarang menyebut target harga atau ajakan bertindak.\n\n"

        "Balas objek JSON:\n"
        "  siaga: 'rendah' | 'sedang' | 'tinggi'\n"
        "  ringkasan: 2-3 kalimat. Apa yang sedang dipantau dan kenapa "
        "tingkat siaganya segitu. Sebut posisi waktunya secara eksplisit.\n"
        "  pemicu: array maksimal 3 string — sinyal kebijakan spesifik yang "
        "sedang dipantau, masing-masing satu kalimat pendek. Array kosong "
        "kalau memang tidak ada.\n"
        "  yang_diperhatikan: satu kalimat — apa yang paling layak diawasi "
        "sampai jendela ini lewat.\n\n"
        + ATURAN_DASAR
    )

    konteks = {
        "jendela_pasar_as": jendela,
        "kerapuhan_pasar": rapuh,
        "pernyataan_tokoh": kandidat,
        "berita_kebijakan": kandidat_berita,
    }
    try:
        hasil = client.chat_json(
            models,
            system,
            "Data:\n\n" + json.dumps(konteks, ensure_ascii=False, default=str),
            step="agen_kebijakan",
            temperature=0.2,
            max_tokens=2000,
        )
    except (LLMError, BudgetExceeded) as exc:
        log.warning("Agen kebijakan gagal: %s", exc)
        return None

    if not isinstance(hasil, dict):
        log.warning("Agen kebijakan mengembalikan struktur tak terduga")
        return None

    siaga = hasil.get("siaga")
    if siaga not in ("rendah", "sedang", "tinggi"):
        siaga = "rendah"

    # Siaga TIDAK boleh melebihi apa yang dibenarkan kondisi terhitung.
    # Tanpa penjagaan ini model bisa berteriak "tinggi" pada Rabu siang di
    # pasar yang tebal — persis jenis alarm palsu yang lama-lama membuat
    # alarm sungguhan ikut diabaikan.
    if siaga == "tinggi" and not (
        jendela.get("dalam_jendela_rawan")
        and rapuh.get("tingkat") in ("sedang", "tinggi")
    ):
        log.info(
            "Siaga agen diturunkan tinggi->sedang (jendela rawan=%s, kerapuhan=%s)",
            jendela.get("dalam_jendela_rawan"), rapuh.get("tingkat"),
        )
        siaga = "sedang"

    return {
        "siaga": siaga,
        "ringkasan": str(hasil.get("ringkasan") or "").strip()[:700],
        "pemicu": [str(x)[:200] for x in (hasil.get("pemicu") or [])[:3]],
        "yang_diperhatikan": str(hasil.get("yang_diperhatikan") or "").strip()[:300],
        # Disalin apa adanya supaya konsumen (web, Telegram) tidak perlu
        # menghitung ulang dan tidak bisa menyimpang dari yang dinilai agen.
        "jendela": jendela,
        "kerapuhan": rapuh,
    }


def analisa_pernyataan(
    client: LLMClient,
    models: List[str],
    kandidat: List[Dict[str, Any]],
    batch_size: int = 6,
    min_relevansi: int = 35,
    maks_hasil: int = 12,
) -> List[Dict[str, Any]]:
    """Saring kandidat jadi pernyataan yang benar-benar bisa menggerakkan pasar.

    Sebagian besar kandidat dari pencarian berita hanya MENYEBUT nama tokoh
    tanpa memuat pernyataan apa pun. Membuang item semacam itu adalah tugas
    utama langkah ini — tanpanya daftar akan penuh derau.
    """
    if not kandidat:
        return []

    system = (
        "Kamu analis yang melacak pernyataan tokoh berpengaruh yang bisa menggerakkan "
        "pasar kripto dan aset berisiko.\n\n"
        "Untuk setiap item, tentukan lebih dulu: APAKAH item ini benar-benar memuat "
        "pernyataan, kebijakan, atau keputusan dari seorang tokoh?\n"
        "  - Artikel yang hanya MENYEBUT nama tokoh tanpa pernyataannya -> relevansi_btc 0\n"
        "  - Analisa jurnalis tentang tokoh, bukan ucapan tokohnya -> relevansi_btc 0\n"
        "  - Berita lama yang diulang tanpa perkembangan baru -> relevansi_btc 0\n"
        "  - Pernyataan yang tidak jelas siapa yang mengucapkannya -> relevansi_btc 0, "
        "karena pembaca tidak bisa menimbang bobotnya\n\n"
        "Kalau ADA pernyataannya, isi field berikut.\n\n"
        "Balas array JSON, satu objek per item:\n"
        "  id (string, sama persis dengan input)\n"
        "  tokoh: nama orang yang menyatakan (null kalau tidak jelas)\n"
        "  kutipan: kutipan langsung kalau ada di teks, null kalau hanya parafrase\n"
        "  ringkasan_id: satu kalimat isi pernyataan, dalam bahasa Indonesia\n"
        f"  topik: salah satu dari {TOPIK_PERNYATAAN}\n"
        f"  sikap_kripto: salah satu dari {SIKAP_PERNYATAAN} (sikap terhadap kripto/aset berisiko)\n"
        "  dampak_btc: bullish | bearish | netral\n"
        "  kekuatan: angka Arab 1-5 (potensi dampak ke harga BTC) — tulis 3, JANGAN III\n"
        f"  status: salah satu dari {STATUS_PERNYATAAN}\n"
        "  jalur_transmisi: likuiditas | risk_appetite | supply_demand | regulasi\n"
        "  mekanisme: satu kalimat rantai sebab-akibat menuju harga BTC\n"
        "  relevansi_btc: 0-100\n\n"
        "PEMBEDAAN YANG WAJIB KAMU JAGA:\n"
        "  - jenis_sumber 'x_grok' berarti teksnya SALINAN postingan X yang "
        "keberadaannya BELUM bisa diverifikasi kode. Perlakukan sebagai "
        "'verbatim' hanya kalau memang berbentuk postingan langsung, dan "
        "turunkan relevansi_btc kalau isinya terbaca janggal atau tidak "
        "sejalan dengan berita mana pun di data.\n"
        "  - status 'verbatim' hanya kalau teks memuat ucapan/postingan langsung\n"
        "  - status 'dilaporkan_media' kalau media melaporkan tokoh mengatakan sesuatu\n"
        "  - status 'rumor' kalau bersumber dari 'orang dalam' atau belum dikonfirmasi\n"
        "Jangan menaikkan status hanya karena beritanya terdengar meyakinkan.\n\n"
        "Pernyataan soal suku bunga, tarif, regulasi kripto, cadangan Bitcoin negara, "
        "dan independensi bank sentral biasanya berkekuatan tinggi. Komentar politik "
        "umum tanpa kaitan ekonomi biasanya berkekuatan rendah.\n\n" + ATURAN_DASAR
    )

    hasil_per_id: Dict[str, Dict[str, Any]] = {}
    for i in range(0, len(kandidat), batch_size):
        batch = kandidat[i : i + batch_size]
        payload = [
            {
                "id": k["id"],
                "teks": k["teks"][:900],
                "sumber": k["sumber"],
                "jenis_sumber": k["jenis_sumber"],
                "waktu_utc": k["waktu_utc"],
            }
            for k in batch
        ]
        try:
            hasil = client.chat_json(
                models,
                system,
                "Analisa item berikut:\n\n" + json.dumps(payload, ensure_ascii=False),
                step="statements",
                max_tokens=5000,
            )
        except BudgetExceeded as exc:
            log.warning("Analisa pernyataan berhenti di batch %d: %s", i // batch_size + 1, exc)
            break
        except LLMError as exc:
            log.warning("Batch pernyataan %d gagal: %s", i // batch_size + 1, exc)
            continue

        for item in hasil if isinstance(hasil, list) else []:
            if isinstance(item, dict) and item.get("id"):
                hasil_per_id[str(item["id"])] = item

    def enum(nilai: Any, daftar: List[str]) -> Optional[str]:
        return nilai if nilai in daftar else None

    keluaran: List[Dict[str, Any]] = []
    for k in kandidat:
        analisa = hasil_per_id.get(k["id"])
        if not analisa:
            continue
        try:
            # Dijepit ke 0-100 seperti `kekuatan`: model pernah mengembalikan
            # -85 untuk berita yang jelas-jelas relevan (salah tanda), dan
            # nilai di luar jangkauan ikut terbawa ke pembobotan di hilir.
            relevansi = max(0, min(100, int(analisa.get("relevansi_btc") or 0)))
        except (TypeError, ValueError):
            relevansi = 0
        if relevansi < min_relevansi:
            continue

        # Tanpa tokoh yang teridentifikasi, sebuah "pernyataan" tidak bisa
        # ditimbang pembaca — siapa yang bicara menentukan bobotnya.
        tokoh = analisa.get("tokoh") or k.get("tokoh")
        if not tokoh or str(tokoh).strip().lower() in (
            "", "tidak disebutkan", "tidak diketahui", "null", "none", "unknown"
        ):
            continue
        try:
            kekuatan = max(1, min(5, int(analisa.get("kekuatan"))))
        except (TypeError, ValueError):
            kekuatan = None

        keluaran.append({
            "id": k["id"],
            "tokoh": str(tokoh)[:80],
            "kutipan": str(analisa["kutipan"])[:500] if analisa.get("kutipan") else None,
            "ringkasan": str(analisa.get("ringkasan_id", ""))[:400] or None,
            "topik": enum(analisa.get("topik"), TOPIK_PERNYATAAN),
            "sikap_kripto": enum(analisa.get("sikap_kripto"), SIKAP_PERNYATAAN),
            "dampak_btc": enum(analisa.get("dampak_btc"), SENTIMEN),
            "kekuatan": kekuatan,
            "status": enum(analisa.get("status"), STATUS_PERNYATAAN),
            "jalur_transmisi": enum(analisa.get("jalur_transmisi"), JALUR_TRANSMISI),
            "mekanisme": str(analisa["mekanisme"])[:400] if analisa.get("mekanisme") else None,
            "relevansi_btc": relevansi,
            "url": k["url"],
            "sumber": k["sumber"],
            "jenis_sumber": k["jenis_sumber"],
            # Hanya terisi untuk item dari X lewat Grok: apakah isinya juga
            # muncul di sumber lain. Dipakai pembaca (dan langkah hilir) untuk
            # menimbang bobotnya, karena postingan X tidak bisa diverifikasi
            # keberadaannya oleh kode.
            "terkonfirmasi_media": k.get("terkonfirmasi_media"),
            "waktu_utc": k["waktu_utc"],
        })

    keluaran.sort(
        key=lambda s: (s["kekuatan"] or 0) * (s["relevansi_btc"] or 0), reverse=True
    )
    log.info("Pernyataan relevan: %d dari %d kandidat", len(keluaran), len(kandidat))
    return keluaran[:maks_hasil]


# --------------------------------------------------------------------------
# LLM — interpretasi teknikal
# --------------------------------------------------------------------------
def interpretasi_teknikal(
    client: LLMClient, models: List[str], teknikal: Dict[str, Any], harga: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Tafsirkan indikator yang SUDAH dihitung kode.

    Model tidak pernah diminta menghitung apa pun. Semua angka dikirim jadi,
    dan prompt melarangnya menurunkan angka baru — kalau model butuh angka
    yang tidak ada, jawabannya harus null.
    """
    system = (
        "Kamu analis teknikal Bitcoin. Kamu menerima indikator yang SUDAH dihitung "
        "secara terprogram dari data candle asli. Tugasmu MENAFSIRKAN, bukan menghitung.\n\n"
        "DILARANG KERAS menghitung, memperkirakan, atau menyebut angka yang tidak ada "
        "dalam data yang diberikan. Kalau sebuah angka tidak tersedia, katakan tidak "
        "tersedia — jangan mengarang.\n\n"
        "Laporan ini terbit sekali sehari, jadi acuannya CANDLE HARIAN (1D). "
        "Jangan menyebut timeframe lain — datanya tidak dikirim kepadamu.\n\n"

        "PERINGATAN — DUA ANGKA VOLUME YANG BERBEDA, JANGAN TERTUKAR:\n"
        "  - `harga_terkini.volume_24h`: SATU-SATUNYA angka yang boleh kamu sebut "
        "sebagai \"volume 24 jam\". Ini volume bergulir (rolling) dari ticker bursa.\n"
        "  - `indikator_terhitung.volume.terakhir` dan `.rata_20`: volume PER CANDLE "
        "HARIAN yang dipakai untuk menghitung rasio dan OBV — angkanya BISA JAUH "
        "BERBEDA dari volume_24h karena batas waktu candle (00:00 UTC) tidak sama "
        "dengan jendela 24 jam bergulir. Sebut ini \"volume candle harian\" atau "
        "\"volume hari ini\", JANGAN \"volume 24 jam\".\n\n"

        "Yang harus kamu jelaskan:\n"
        "  - Apa yang sedang diberitahukan struktur harga harian\n"
        "  - Di mana kelompok indikator saling MENGUATKAN dan di mana saling "
        "BERTENTANGAN (tren vs momentum vs volume vs volatilitas)\n"
        "  - Apa arti kondisi momentum dan volatilitas saat ini\n"
        "  - Apakah volume mengonfirmasi pergerakan harga atau tidak\n"
        "  - Kondisi konkret apa yang akan MEMBATALKAN pembacaan ini\n\n"
        "Balas objek JSON:\n"
        "  ringkasan: 2-3 kalimat inti kondisi teknikal harian\n"
        "  struktur: 2-4 kalimat tentang tren dan posisi harga terhadap EMA\n"
        "  momentum_volume: 2-4 kalimat tentang RSI/MACD dan konfirmasi volume\n"
        "  konfluensi: array string, indikator yang saling menguatkan\n"
        "  kontradiksi: array string, sinyal yang saling bertentangan\n"
        "  kualitas_tren: salah satu dari \"kuat\", \"melemah\", \"tidak_jelas\", \"berbalik\"\n"
        "  pembatalan: string, kondisi harga yang membatalkan pembacaan di atas\n\n"
        "Tulis untuk pembaca yang paham dasar trading tapi bukan ahli. Jelaskan "
        "istilah teknis secara singkat saat pertama muncul.\n\n" + ATURAN_DASAR
    )
    konteks = {"harga_terkini": harga, "indikator_terhitung": teknikal}

    try:
        hasil = client.chat_json(
            models,
            system,
            "Tafsirkan kondisi teknikal berikut:\n\n" + json.dumps(konteks, ensure_ascii=False, default=str),
            step="technical",
            # Disamakan dengan sintesis/outlook: pembacaan indikator yang sama
            # harus menghasilkan kesimpulan yang sama.
            temperature=0.2,
            max_tokens=6000,
        )
    except (LLMError, BudgetExceeded) as exc:
        log.warning("Interpretasi teknikal gagal: %s", exc)
        return None

    if not isinstance(hasil, dict) or not hasil.get("ringkasan"):
        log.warning("Interpretasi teknikal: balasan tanpa field 'ringkasan', dilewati")
        return None

    kualitas = hasil.get("kualitas_tren")
    return {
        "ringkasan": str(hasil["ringkasan"]).strip(),
        "struktur": str(hasil["struktur"])[:900] if hasil.get("struktur") else "",
        "momentum_volume": (
            str(hasil["momentum_volume"])[:900] if hasil.get("momentum_volume") else ""
        ),
        "konfluensi": [str(x)[:250] for x in (hasil.get("konfluensi") or [])[:6]],
        "kontradiksi": [str(x)[:250] for x in (hasil.get("kontradiksi") or [])[:6]],
        "kualitas_tren": kualitas if kualitas in ("kuat", "melemah", "tidak_jelas", "berbalik") else None,
        "pembatalan": str(hasil["pembatalan"])[:400] if hasil.get("pembatalan") else "",
    }


# --------------------------------------------------------------------------
# LLM — analisa whale & sinyal palsu
# --------------------------------------------------------------------------
def analisa_whale(
    client: LLMClient,
    models: List[str],
    posisi: Dict[str, Any],
    sinyal_palsu: List[Dict[str, Any]],
    teknikal: Dict[str, Any],
    harga: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Tafsirkan posisi whale vs ritel dan pola candle yang mencurigakan."""
    if not posisi.get("whale_long_pct") and not sinyal_palsu:
        return None

    system = (
        "Kamu analis mikrostruktur pasar. Kamu menilai apakah pergerakan harga "
        "belakangan ini tulus atau kemungkinan hasil rekayasa pemain besar.\n\n"
        "Data yang kamu terima:\n"
        "  - posisi_whale: statistik posisi top trader Binance (proksi pemain besar)\n"
        "  - posisi_ritel: statistik seluruh akun (didominasi ritel)\n"
        "  - divergensi: selisih persentase long whale dikurangi long ritel, dalam poin persen\n"
        "  - sinyal_terdeteksi: pola candle dan volume yang sudah dideteksi kode\n\n"
        "Prinsip pembacaan:\n"
        "  - Whale net short sementara ritel net long = pola distribusi klasik\n"
        "  - Whale net long sementara ritel net short = pola akumulasi\n"
        "  - Sapuan likuiditas = level dipicu lalu harga tidak bertahan; sering "
        "    berarti likuidasi dipanen, bukan arah baru\n"
        "  - Volume besar tanpa perpindahan harga = ada pihak menyerap order\n"
        "  - Breakout dengan volume menurun = partisipasi tidak mengonfirmasi\n\n"
        "JUJUR soal ketidakpastian. Pola-pola ini adalah petunjuk probabilistik, "
        "bukan bukti manipulasi. Kalau datanya lemah atau ambigu, katakan begitu. "
        "JANGAN mengarang cerita konspirasi dari sinyal yang tipis.\n\n"
        "Balas objek JSON:\n"
        "  ringkasan: 2-4 kalimat kondisi posisi pemain besar vs ritel\n"
        "  sinyal_palsu: array objek {\"pola\": \"...\", \"arti\": \"...\", \"keyakinan\": \"tinggi|sedang|rendah\"}\n"
        "  posisi_whale_vs_ritel: string, satu kalimat kesimpulan\n"
        "  tingkat_kewaspadaan: salah satu dari \"tinggi\", \"sedang\", \"rendah\"\n"
        "  catatan: string, hal yang perlu diperhatikan pembaca (boleh null)\n\n"
        + ATURAN_DASAR
    )
    konteks = {
        "harga_terkini": harga,
        "posisi_whale": {
            "whale_long_pct": posisi.get("whale_long_pct"),
            "whale_short_pct": posisi.get("whale_short_pct"),
            "whale_tren_long_poin_persen": posisi.get("whale_tren_long_pp"),
        },
        "posisi_ritel": {
            "ritel_long_pct": posisi.get("ritel_long_pct"),
            "ritel_short_pct": posisi.get("ritel_short_pct"),
            "ritel_tren_long_poin_persen": posisi.get("ritel_tren_long_pp"),
        },
        "divergensi_poin_persen": posisi.get("divergensi"),
        "divergensi_label": posisi.get("divergensi_label"),
        "aliran_taker": {
            "buy_sell_ratio": posisi.get("taker_buy_sell_ratio"),
            "tren": posisi.get("taker_tren"),
        },
        "funding_dan_oi": {
            "sinyal_oi": teknikal.get("oi_price_signal"),
            "perubahan_oi_pct": teknikal.get("oi_change_pct"),
        },
        "sinyal_terdeteksi": sinyal_palsu,
    }

    try:
        hasil = client.chat_json(
            models,
            system,
            "Analisa kondisi berikut:\n\n" + json.dumps(konteks, ensure_ascii=False, default=str),
            step="whale",
            temperature=0.2,
            max_tokens=4000,
        )
    except (LLMError, BudgetExceeded) as exc:
        log.warning("Analisa whale gagal: %s", exc)
        return None

    if not isinstance(hasil, dict) or not hasil.get("ringkasan"):
        log.warning("Analisa whale: balasan tanpa field 'ringkasan', dilewati")
        return None

    daftar = []
    for s in (hasil.get("sinyal_palsu") or [])[:6]:
        if not isinstance(s, dict):
            continue
        keyakinan = s.get("keyakinan")
        daftar.append({
            "pola": str(s.get("pola", ""))[:120],
            "arti": str(s.get("arti", ""))[:400],
            "keyakinan": keyakinan if keyakinan in ("tinggi", "sedang", "rendah") else "rendah",
        })

    waspada = hasil.get("tingkat_kewaspadaan")
    return {
        "ringkasan": str(hasil["ringkasan"]).strip(),
        "sinyal_palsu": daftar,
        "posisi_whale_vs_ritel": str(hasil.get("posisi_whale_vs_ritel", ""))[:400],
        "tingkat_kewaspadaan": waspada if waspada in ("tinggi", "sedang", "rendah") else "rendah",
        "catatan": str(hasil["catatan"])[:400] if hasil.get("catatan") else "",
    }


# --------------------------------------------------------------------------
# LLM — outlook ke depan
# --------------------------------------------------------------------------
# Panjang minimum sebelum sebuah blok prosa layak dipecah. Di bawah ini,
# memecahnya justru menghasilkan paragraf sepotong-sepotong.
_MIN_KARAKTER_PECAH = 420
_MIN_KALIMAT_PECAH = 4
# Panjang paragraf yang enak dibaca di ponsel: sekitar 10 baris.
_TARGET_KARAKTER_PARAGRAF = 400

_POLA_AKHIR_KALIMAT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'\u201c])")


def _pecah_paragraf(teks: str, maks_paragraf: int = 3) -> str:
    """Pecah satu blok prosa panjang jadi 2-3 paragraf di batas kalimat.

    Prompt sudah meminta model memisahkan paragraf sendiri, tapi permintaan
    format seperti ini tidak pernah konvergen di repo ini — yang bertahan
    selalu penegakan di sisi kode. Hasil nyatanya satu dinding teks berisi
    semua isu sekaligus: melelahkan dibaca dan menyamarkan mana isu yang mana.

    Tidak menyentuh apa pun yang sudah dipecah model, dan tidak memecah blok
    yang memang pendek. Pemisahnya batas kalimat, jadi tidak ada kalimat yang
    terbelah.
    """
    teks = (teks or "").strip()
    if not teks or "\n\n" in teks or len(teks) < _MIN_KARAKTER_PECAH:
        return teks

    kalimat = [k.strip() for k in _POLA_AKHIR_KALIMAT.split(teks) if k.strip()]
    if len(kalimat) < _MIN_KALIMAT_PECAH:
        return teks

    # Jumlah paragraf ditentukan PANJANG, bukan cacah kalimat: kalimat
    # analisa pasar sering sangat panjang, dan aturan "<=5 kalimat jadi 2
    # paragraf" menghasilkan paragraf 947 karakter pada narasi produksi —
    # masih dinding teks. Sasarannya sekitar 400 karakter per paragraf.
    jumlah = min(maks_paragraf, max(2, round(len(teks) / _TARGET_KARAKTER_PARAGRAF)))
    jumlah = min(jumlah, len(kalimat))

    # Titik potong dipilih dari panjang KUMULATIF: untuk tiap batas ke-j,
    # ambil batas kalimat yang panjang kumulatifnya PALING DEKAT ke j/jumlah
    # bagian teks. Memotong per cacah kalimat menaruh semua kalimat panjang
    # di satu paragraf; cara ini menyeimbangkan berdasarkan yang benar-benar
    # dilihat pembaca, yaitu panjang teksnya.
    kumulatif, jalan = [], 0
    for k in kalimat:
        jalan += len(k) + 1
        kumulatif.append(jalan)
    total = kumulatif[-1]

    potong: List[int] = []
    for j in range(1, jumlah):
        sasaran = total * j / jumlah
        # Kandidat batas: setelah kalimat ke-i (i+1 kalimat terpakai), selalu
        # menyisakan minimal satu kalimat untuk tiap paragraf berikutnya.
        kandidat = [
            i for i in range(len(kalimat) - 1)
            if (not potong or i > potong[-1]) and (len(kalimat) - 1 - i) >= (jumlah - j - 1)
        ]
        if not kandidat:
            break
        potong.append(min(kandidat, key=lambda i: abs(kumulatif[i] - sasaran)))

    paragraf: List[str] = []
    mulai = 0
    for i in potong + [len(kalimat) - 1]:
        paragraf.append(" ".join(kalimat[mulai:i + 1]))
        mulai = i + 1

    log.info("Narasi panjang dipecah jadi %d paragraf", len(paragraf))
    return "\n\n".join(paragraf)


def outlook(
    client: LLMClient, models: List[str], konteks: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Analisa ke depan: teknikal + makro + geopolitik + agenda.

    Skenario ditulis KONDISIONAL terhadap level yang sudah dihitung kode
    ("kalau bertahan di atas X, kondisi Y menguat"), bukan sebagai ramalan
    harga. Target harga dan ajakan transaksi tetap dilarang.
    """
    system = (
        "Kamu analis pasar yang menyusun pandangan ke depan untuk Bitcoin.\n\n"
        "Susun analisa forward-looking yang menggabungkan: kondisi teknikal, situasi "
        "makro, faktor geopolitik, agenda ekonomi yang akan datang, dan keputusan besar "
        "yang sedang berlangsung (regulasi, kebijakan bank sentral, arus institusional).\n\n"
        "CARA MENULIS SKENARIO — penting:\n"
        "  - Skenario ditulis KONDISIONAL terhadap level yang ADA di data: "
        "\"selama bertahan di atas [level dari data], kondisi X cenderung berlanjut\"\n"
        "  - DILARANG menyebut target harga, proyeksi angka, atau ramalan arah\n"
        "  - DILARANG menyarankan pembaca membeli, menjual, menunggu, atau masuk posisi\n"
        "  - Yang kamu jelaskan adalah FAKTOR dan KONDISI, bukan apa yang harus dilakukan\n\n"
        "GEOPOLITIK & MAKRO — bagian yang paling sering dilewatkan:\n"
        "  - `makro` (DXY, yield, minyak, emas, VIX, USD/JPY) dan `agenda_mendatang` "
        "harus benar-benar ditelusuri, bukan diabaikan karena narasinya lebih "
        "mudah ditulis dari sisi teknikal saja.\n"
        "  - Yen menguat tajam = indikasi pelepasan carry trade dolar-yen, jalur "
        "likuiditas yang menekan aset berisiko termasuk BTC.\n"
        "  - Setiap berita/pernyataan bertopik regulasi, kebijakan bank sentral, "
        "atau geopolitik yang ADA di data masuk ke `faktor_geopolitik` — jangan "
        "kosongkan array ini kalau datanya sebenarnya memuat isu semacam itu.\n"
        "  - Agenda dari `agenda_mendatang` yang berjarak dekat dan berdampak besar "
        "(FOMC, rilis CPI/NFP) masuk ke `keputusan_besar`.\n\n"
        "JANGAN MENGARANG DETAIL DARI JUDUL BERITA: tiap berita hanya punya judul "
        "dan ringkasan singkat, bukan isi artikel lengkap. Kalau ringkasannya "
        "tidak menyebut angka atau nama pihak tertentu, jangan kamu isi sendiri. "
        "Ini berlaku juga untuk SUMBERNYA — kalau ringkasan menyebut 'Morgan "
        "Stanley menaikkan kepemilikan ETF 23%', jangan tambahkan bingkai seperti "
        "'filing kuartal kedua menunjukkan...' kecuali kata itu memang ada di "
        "ringkasannya. Angka boleh benar tapi mengarang DARI MANA asalnya tetap "
        "karangan.\n\n"
        "Balas objek JSON:\n"
        "  ringkasan: 2-3 kalimat pandangan umum ke depan\n"
        "  narasi_geopolitik: 4-8 kalimat MENGALIR (bukan daftar) yang "
        "menjelaskan kaitan situasi geopolitik/regulasi/makro saat ini dengan "
        "pasar kripto. Ini bagian yang paling dibaca.\n"
        "    PECAH JADI 2-3 PARAGRAF, dipisah baris kosong (\\n\\n di dalam "
        "string JSON) — satu isu per paragraf. Satu blok panjang berisi "
        "semua isu sekaligus melelahkan dibaca dan menyamarkan mana isu "
        "yang mana. Tetap prosa mengalir di dalam tiap paragraf, bukan "
        "potongan poin. Wajib menelusuri RANTAI TRANSMISI sampai "
        "ke harga BTC, bukan sekadar menyebut peristiwanya: bukan 'ada "
        "pertemuan Gedung Putih soal kripto', tapi 'pertemuan Gedung Putih "
        "soal kripto berpotensi menurunkan premi risiko regulasi yang selama "
        "ini ditanggung institusi AS, dan itu jalur yang sama yang menggerakkan "
        "arus ETF'. Kalau data memang tidak memuat isu geopolitik sama sekali, "
        "katakan begitu terus terang dalam satu kalimat — jangan mengarang.\n"
        "  skenario_naik: {\"pemicu\": [array faktor], \"kondisi\": \"level/kondisi dari data yang harus bertahan\"}\n"
        "  skenario_turun: {\"pemicu\": [array faktor], \"kondisi\": \"level/kondisi dari data yang harus bertahan\"}\n"
        "  faktor_geopolitik: array string PENDEK (maksimal 5), masing-masing "
        "satu isu — ini penopang butir dari narasi_geopolitik di atas, bukan "
        "pengulangannya. Jangan menyalin kalimat yang sama.\n"
        "  keputusan_besar: array objek {\"apa\": \"...\", \"kapan\": \"...\", \"kenapa_penting\": \"...\"}\n"
        "  risiko_utama: array string, hal yang paling bisa mengubah gambaran\n"
        "  horizon: string, rentang waktu yang dibahas (contoh \"1-2 minggu ke depan\")\n\n"
        "Kalau data yang tersedia tidak mendukung suatu bagian, isi array kosong. "
        "JANGAN mengisi dengan pengetahuan umum dari masa pelatihanmu — pembaca "
        "mengira semua yang kamu tulis berasal dari data hari ini.\n\n" + ATURAN_DASAR
    )
    try:
        hasil = client.chat_json(
            models,
            system,
            "Data terkini:\n\n" + json.dumps(konteks, ensure_ascii=False, default=str),
            step="outlook",
            # Sama seperti synthesis: pandangan ke depan tidak boleh berayun
            # hanya karena sampling model, harus berayun karena datanya berubah.
            temperature=0.2,
            # Dinaikkan dari 7000. Keluaran terpantau di produksi sudah
            # menyentuh 6016 — margin 14% dari plafon, dan itu PERSIS pola
            # yang menjatuhkan synthesis: keluarannya merangkak naik seiring
            # bertambahnya konteks sampai akhirnya terpotong dan SELURUH
            # bagian hilang. Batas longgar di sini praktis gratis (yang
            # ditagih token yang benar-benar dipakai, bukan plafonnya).
            max_tokens=12000,
        )
    except (LLMError, BudgetExceeded) as exc:
        log.warning("Analisa outlook gagal: %s", exc)
        return None

    if not isinstance(hasil, dict) or not hasil.get("ringkasan"):
        log.warning("Analisa outlook: balasan tanpa field 'ringkasan', dilewati")
        return None

    def skenario(kunci: str) -> Dict[str, Any]:
        s = hasil.get(kunci)
        if not isinstance(s, dict):
            return {"pemicu": [], "kondisi": ""}
        pemicu = s.get("pemicu")
        return {
            "pemicu": [str(p)[:250] for p in pemicu[:5]] if isinstance(pemicu, list) else [],
            "kondisi": str(s.get("kondisi", ""))[:300],
        }

    keputusan = []
    for k in (hasil.get("keputusan_besar") or [])[:6]:
        if not isinstance(k, dict):
            continue
        keputusan.append({
            "apa": str(k.get("apa", ""))[:200],
            "kapan": str(k.get("kapan", ""))[:100],
            "kenapa_penting": str(k.get("kenapa_penting", ""))[:400],
        })

    return {
        "ringkasan": str(hasil["ringkasan"]).strip(),
        "narasi_geopolitik": (
            # Dipecah di kode, bukan cuma diminta lewat prompt — lihat
            # _pecah_paragraf() untuk alasannya.
            _pecah_paragraf(str(hasil["narasi_geopolitik"]).strip()[:1500])
            if hasil.get("narasi_geopolitik") else ""
        ),
        "skenario_naik": skenario("skenario_naik"),
        "skenario_turun": skenario("skenario_turun"),
        "faktor_geopolitik": [str(x)[:300] for x in (hasil.get("faktor_geopolitik") or [])[:5]],
        "keputusan_besar": keputusan,
        "risiko_utama": [str(x)[:300] for x in (hasil.get("risiko_utama") or [])[:6]],
        "horizon": str(hasil.get("horizon", ""))[:100],
    }


def revisi_narasi(
    client: LLMClient,
    models: List[str],
    narasi: str,
    koreksi: List[Dict[str, Any]],
    konteks: Dict[str, Any],
) -> Optional[str]:
    """Perbaiki narasi berdasarkan temuan critic, satu putaran saja.

    Menahan seluruh analisa hanya karena beberapa kalimat bermasalah itu
    merugikan pembaca: bagian yang benar ikut hilang. Memperbaiki jauh lebih
    murah daripada kehilangan seluruh keluaran yang sudah dibayar.
    """
    if not narasi or not koreksi:
        return None

    daftar = "\n".join(
        f"- [{k.get('keparahan')}] {k.get('jenis')}: \"{k.get('kutipan', '')}\" "
        f"— {k.get('alasan', '')}"
        for k in koreksi[:10]
    )
    system = (
        "Kamu editor yang memperbaiki analisa pasar. Kamu menerima sebuah narasi, "
        "daftar temuan pemeriksa fakta, dan data mentah sumbernya.\n\n"
        "Perbaiki HANYA bagian yang bermasalah:\n"
        "  - Angka yang tidak ada di data: hapus, atau ganti dengan angka yang "
        "    benar-benar ada di data\n"
        "  - Saran investasi atau target harga: tulis ulang jadi pernyataan "
        "    kondisi, tanpa mengajak bertransaksi\n"
        "  - Klaim sebab-akibat tanpa dukungan: turunkan jadi pengamatan, atau "
        "    nyatakan terus terang bahwa penyebabnya tidak jelas dari data\n\n"
        "Pertahankan struktur, panjang, gaya, dan seluruh bagian yang tidak "
        "bermasalah PERSIS seperti aslinya. Jangan menulis ulang dari nol.\n\n"
        "Balas objek JSON: {\"narrative\": \"teks yang sudah diperbaiki\"}\n\n"
        + ATURAN_DASAR
    )
    user = (
        "TEMUAN PEMERIKSA:\n" + daftar + "\n\n"
        "NARASI YANG DIPERBAIKI:\n" + narasi + "\n\n"
        "DATA MENTAH:\n" + json.dumps(konteks, ensure_ascii=False, default=str)
    )
    try:
        # Keluarannya adalah SELURUH narasi hasil sintesis (400-700 kata) yang
        # ditulis ulang, bukan cuma bagian yang diperbaiki — jadi butuh ruang
        # sebanyak langkah sintesis sendiri (10000). 10000 pernah terpotong di
        # produksi; dinaikkan dengan margin ekstra.
        hasil = client.chat_json(
            models, system, user, step="revisi", temperature=0.2, max_tokens=16000
        )
    except (LLMError, BudgetExceeded) as exc:
        log.warning("Revisi narasi gagal: %s", exc)
        return None

    if not isinstance(hasil, dict) or not hasil.get("narrative"):
        log.warning("Revisi mengembalikan struktur tak terduga")
        return None
    return str(hasil["narrative"]).strip()


# --------------------------------------------------------------------------
# LLM #4 — sintesis narasi
# --------------------------------------------------------------------------
def sintesis(
    client: LLMClient, models: List[str], konteks: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    system = (
        "PERAN\n"
        "Kamu analis pasar crypto senior yang menulis untuk investor ritel serius. "
        "Pembacamu bukan trader harian — mereka punya posisi jangka menengah dan "
        "butuh memahami MENGAPA harga bergerak, bukan sekadar BAHWA harga bergerak.\n\n"
        "Tugasmu bukan meramal harga. Tugasmu menjelaskan rantai sebab-akibat yang "
        "menggerakkan pasar, memisahkan sinyal dari kebisingan, lalu menutup dengan "
        "kesimpulan yang tenang.\n\n"

        "PRINSIP INTI\n\n"
        "1. Jelaskan rantai transmisi, bukan korelasi permukaan.\n"
        "   Jangan berhenti di 'BTC turun karena sentimen negatif'. Telusuri:\n"
        "   minyak naik -> ekspektasi inflasi naik -> peluang Fed menaikkan suku "
        "bunga naik -> yield naik -> dolar menguat -> aset berisiko tertekan.\n"
        "   Rantai yang perlu kamu kuasai: energi->inflasi->Fed->aset berisiko; "
        "data tenaga kerja lemah->ekspektasi hike turun->BTC naik; saham AI/chip->"
        "selera risiko->crypto; dolar menguat->BTC tertekan; arus ETF->permintaan "
        "marginal->harga.\n\n"

        "2. Angka spesifik, bukan kata sifat.\n"
        "   Buruk: 'ETF mencatat inflow yang kuat'.\n"
        "   Baik: 'ETF mencatat inflow $265,7 juta, hari positif pertama setelah "
        "11 sesi jual berturut-turut'.\n"
        "   Setiap klaim penting wajib berangka, dan angkanya HARUS dari data yang "
        "diberikan.\n"
        "   AWAS dua angka volume yang berbeda: `harga.volume_24h` adalah "
        "volume bergulir 24 jam sungguhan. `teknikal_1d.volume.terakhir`/"
        "`.rata_20` adalah volume PER CANDLE HARIAN — bisa jauh berbeda "
        "angkanya. Sebut yang kedua sebagai \"volume candle harian\", JANGAN "
        "\"volume 24 jam\".\n\n"

        "3. Bedakan 'sudah tercermin di harga' dari 'kejutan'.\n"
        "   Pasar bergerak pada selisih antara kenyataan dan ekspektasi, bukan pada "
        "baik/buruknya berita. Berita buruk yang sudah diantisipasi sering direspons "
        "kecil atau malah naik. Bandingkan aktual vs konsensus, bukan vs bulan lalu.\n\n"

        "4. PERTANYAAN PERTAMA YANG WAJIB DIJAWAB: 24 jam ini NAIK atau TURUN, "
        "kenapa, dan itu kenaikan/penurunan JENIS APA.\n"
        "   Ini yang paling ingin diketahui pembaca dan harus terjawab tuntas, "
        "bukan tersirat. Field `pergerakan_24j` sudah berisi klasifikasi yang "
        "DIHITUNG KODE — pakai itu apa adanya, jangan hitung ulang dan jangan "
        "membantahnya:\n"
        "     `arah`            -> naik | turun | datar\n"
        "     `besaran`         -> tipis | wajar | besar | ekstrem, relatif "
        "terhadap kisaran harian normal (ATR), bukan angka mutlak\n"
        "     `jenis`           -> sifat pergerakannya dari arah open interest:\n"
        "         long_baru       = naik karena UANG BARU masuk ke sisi beli\n"
        "         short_covering  = naik karena POSISI JUAL DITUTUP, bukan "
        "permintaan baru — cenderung kehilangan tenaga saat posisi jual habis\n"
        "         short_baru      = turun karena POSISI JUAL BARU masuk\n"
        "         long_ditutup    = turun karena POSISI BELI keluar/dilikuidasi "
        "— tekanan mereda begitu posisi rapuh selesai keluar\n"
        "     `volume_konfirmasi` -> apakah volume mendukung pergerakan itu\n"
        "     `berita_pendukung`/`berita_berlawanan` -> kandidat pemicu yang "
        "sudah disaring kode; kamu yang merangkai sebab-akibatnya\n"
        "   Bedanya jenis ini PENTING dan harus dijelaskan artinya ke pembaca "
        "dengan bahasa manusia: naik 3% karena uang baru masuk tidak sama "
        "artinya dengan naik 3% karena short tertutup, walaupun angkanya sama. "
        "Kalau `jenis` bernilai null (data open interest tidak ada), katakan "
        "terus terang bahwa sifat pergerakannya belum bisa dipastikan — jangan "
        "menebak.\n\n"

        "4b. Bedakan pergerakan teknikal dari fundamental.\n"
        "   KALAU TIDAK ADA KATALIS BERITA, KATAKAN BEGITU — jangan mengarang narasi. "
        "Penyebab mekanis yang sering terjadi: short squeeze, likuidasi long "
        "beruntun, tarikan ke max pain menjelang expiry opsi, pengurangan posisi "
        "menjelang FOMC/CPI, pantulan oversold, rebalancing akhir bulan.\n"
        "   Frasa yang berguna: 'Tidak ada katalis berita spesifik dalam 24 jam "
        "terakhir; pergerakan ini konsisten dengan [mekanisme], bukan perubahan "
        "fundamental.'\n\n"

        "5. Selalu sajikan sisi lawan.\n"
        "   Kalau nadanya condong menguat, sebutkan apa yang bisa membatalkannya. "
        "Kalau melemah, sebutkan apa yang menahan penurunan.\n\n"

        "6. Rangkum SEMUA data yang diberikan, bukan cuma harga dan berita.\n"
        "   Field `makro` (DXY, yield UST 10Y, minyak, emas, Nasdaq, S&P 500, "
        "VIX, USD/JPY) dan `agenda_mendatang` (FOMC, rilis data ekonomi) WAJIB "
        "ikut dipertimbangkan di `penyebab` atau `yang_diwaspadai` KALAU datanya "
        "relevan dengan pergerakan hari ini — bukan sekadar disebut kalau "
        "kebetulan ada di data, tapi ditelusuri rantai transmisinya ke BTC. "
        "Yen yang menguat tajam (USD/JPY turun) mengindikasikan pelepasan carry "
        "trade dolar-yen, yang menekan aset berisiko lewat jalur likuiditas — "
        "sebutkan ini kalau datanya mendukung. Dolar yang menguat (DXY naik) "
        "dan yield yang naik punya jalur yang sama. Agenda besar yang belum "
        "terjadi (FOMC, CPI) masuk ke `katalis_berikutnya`, bukan `penyebab` — "
        "itu belum terjadi, jadi belum jadi penyebab.\n\n"

        "7. JANGAN mengarang detail dari judul berita — termasuk SUMBERNYA.\n"
        "   Tiap berita di `berita` hanya punya judul dan ringkasan 1-2 kalimat "
        "— BUKAN isi artikel lengkap. Kalau ringkasannya tidak menyebut angka "
        "atau nama pihak tertentu, JANGAN kamu isi sendiri. 'Bitcoin's $116M "
        "self-custody wake-up call' boleh kamu tulis ulang sebagai peringatan "
        "soal keamanan self-custody senilai sekitar $116 juta — TAPI TIDAK "
        "BOLEH kamu tulis sebagai 'eksploitasi terkonfirmasi' atau tambahkan "
        "detail (metode serangan, pelaku, tanggal pasti) yang tidak ada di "
        "judul/ringkasan itu sendiri. Kalau ringkasannya kosong, rujuk beritanya "
        "secara umum saja tanpa detail spesifik.\n"
        "   Ini berlaku juga untuk KATA KUNCI SUMBER: kalau ringkasan bilang "
        "'Morgan Stanley menaikkan kepemilikan ETF Bitcoin 23%', JANGAN kamu "
        "tambahkan bingkai seperti 'filing kuartal kedua menunjukkan...' atau "
        "'laporan 13F SEC mengonfirmasi...' kecuali kata-kata itu MEMANG ada di "
        "ringkasannya. Angka boleh benar tapi kalau kamu mengarang DARI MANA "
        "angka itu berasal, itu tetap karangan.\n\n"

        "8. SATU PERISTIWA DIBAHAS SATU KALI SAJA.\n"
        "   Tiap bagian punya tugas berbeda, jadi jangan menceritakan ulang "
        "berita yang sama di beberapa bagian. Bagi begini:\n"
        "     penyebab       -> peristiwa yang MENGGERAKKAN harga hari ini\n"
        "     data_pendukung -> ANGKA yang menopang klaim di penyebab, bukan "
        "menceritakan ulang peristiwanya\n"
        "     yang_diwaspadai-> hal yang BELUM tercermin di harga\n"
        "     kesimpulan     -> penilaian akhir, TANPA mengulang angka yang "
        "sudah disebut di atas\n"
        "   Kalau sebuah berita sudah dibahas di `penyebab`, di bagian lain "
        "cukup dirujuk singkat ('katalis regulasi tadi'), bukan diceritakan "
        "ulang dari awal. Pembaca sedang membaca satu tulisan utuh, bukan "
        "beberapa ringkasan terpisah yang kebetulan digabung.\n\n"

        "9. JANGAN MENULIS NAMA FIELD ATAU KODE INTERNAL.\n"
        "   Data yang kamu terima berbentuk JSON, tapi pembacamu tidak pernah "
        "melihat JSON itu. Tulis dalam bahasa manusia:\n"
        "     `short_covering`    -> \"penutupan posisi short\"\n"
        "     `invalidasi_turun`  -> \"batas pembatalan skenario turun\"\n"
        "     `buy_sell_ratio`    -> \"rasio beli-jual\"\n"
        "     `sinyal_oi`         -> \"sinyal open interest\"\n"
        "   Singkatan teknis (OBV, MVRV, NVT, DVOL, IV, ATR) dijelaskan sekali "
        "saat pertama muncul, lalu boleh dipakai singkat.\n\n"

        "STRUKTUR — isi setiap field ini:\n"
        "  judul: HEADLINE. Ini satu-satunya kalimat yang tampil paling atas "
        "halaman, dan sering satu-satunya yang sempat dibaca — perlakukan "
        "seperti judul utama koran keuangan, bukan label bagian.\n"
        "    Syaratnya: 8-14 kata; MEMUAT TEMUANNYA, bukan topiknya; "
        "sertakan angka atau level konkret kalau itu inti temuannya; "
        "sebutkan KETEGANGANNYA (apa lawan apa) kalau memang ada.\n"
        "    Buruk (label, bukan temuan): 'Update Harga BTC', 'Analisa "
        "Pasar Harian', 'BTC Bergerak Sideways'.\n"
        "    Baik: 'Rebound BTC ke $64.300 ditopang short yang tertutup, "
        "bukan pembeli baru'; 'Dua hari beruntun arus keluar ETF menahan "
        "BTC di bawah $65.000'; 'BTC datar menjelang FOMC Minutes sementara "
        "Bollinger menyempit ke level tersempit sebulan'.\n"
        "    Dilarang: kata hype ('meledak', 'roket', 'waspada!'), tanda "
        "seru, dan judul yang memancing tanpa menjawab ('Ada apa dengan "
        "BTC hari ini?'). Menarik di sini berarti PADAT ISI, bukan heboh.\n"
        "  posisi_harga: angka terkini, perubahan 24 jam, konteks jarak ke "
        "support/resistance kunci (2-3 kalimat)\n"
        "  karakter_pergerakan: WAJIB DIISI. 2-4 kalimat yang menjawab tuntas: "
        "24 jam ini naik atau turun dan berapa; itu pergerakan jenis apa "
        "(pakai `pergerakan_24j.jenis` — uang baru masuk, penutupan posisi "
        "jual, posisi jual baru, atau posisi beli dilikuidasi) DAN apa artinya "
        "bagi ketahanan pergerakan itu; apa pemicunya (berita tertentu, atau "
        "murni mekanis kalau tidak ada berita searah). Tulis dalam bahasa "
        "manusia, tanpa nama field. Ini bagian yang dibaca paling awal — "
        "jangan mengulanginya lagi kata per kata di `penyebab`, di sana "
        "telusuri rantai sebab-akibatnya lebih dalam.\n"
        "  penyebab: rantai sebab-akibat lengkap dengan angka pendukung. Kalau "
        "penyebabnya teknikal, katakan itu teknikal. (2-4 paragraf)\n"
        "  data_pendukung: array 2-4 poin berangka (arus ETF, likuidasi, on-chain, "
        "posisi opsi, sentimen, premium Coinbase)\n"
        "  peta_level: support & resistance konkret dari data, plus arti masing-"
        "masing kalau ditembus (1-2 paragraf)\n"
        "  yang_diwaspadai: argumen penyeimbang dan risiko yang belum tercermin di "
        "harga (1-2 paragraf)\n"
        "  katalis_berikutnya: array agenda dari data, sudah dalam WIB\n"
        "  kesimpulan: 2-3 kalimat. Apa artinya. Seringkali kesimpulan terbaik "
        "adalah 'belum ada yang perlu dilakukan'.\n"
        "  penyebab_pergerakan: array objek {\"faktor\", \"arah\": naik|turun|netral, "
        "\"keyakinan\": tinggi|sedang|rendah, \"dasar\": data pendukungnya}, "
        "maksimal 5, urut dari yang paling berpengaruh\n"
        "  dominant_themes: array 2-3 string pendek\n"
        "  narrative_shift: apa yang berubah dibanding brief sebelumnya (null kalau "
        "tidak ada pembanding)\n"
        "  conflicts: array string sinyal yang saling bertentangan\n\n"
        "Total panjang gabungan 400-700 kata.\n\n"

        "NADA\n"
        "Wajib: bahasa Indonesia jernih, jargon dijelaskan saat pertama muncul; "
        "jujur saat data tidak ada ('saya belum menemukan katalis spesifik untuk "
        "pergerakan ini' jauh lebih baik daripada mengarang); setiap probabilitas "
        "disebut sebagai estimasi; tutup dengan sikap tenang.\n"
        "Dilarang: kata hype ('meledak', 'roket', 'cuan besar', 'jangan sampai "
        "ketinggalan'); prediksi harga sebagai kepastian; rekomendasi beli/jual "
        "langsung; mengabaikan berita buruk demi narasi yang enak dibaca.\n"
        "Hindari pembuka basi seperti 'Pasar crypto kembali bergejolak hari ini'. "
        "Mulai langsung dari temuannya.\n\n"
        + ATURAN_DASAR
    )
    try:
        hasil = client.chat_json(
            models,
            system,
            "Data hari ini:\n\n" + json.dumps(konteks, ensure_ascii=False, default=str),
            step="synthesis",
            # Turun dari 0,4: pada 0,4 dua run dengan data yang nyaris sama
            # bisa menghasilkan penekanan dan kesimpulan yang terasa berbeda,
            # dan brief harian justru dibaca untuk melihat APA YANG BERUBAH.
            # Variasi gaya di sini tidak menambah nilai, cuma bikin ragu.
            temperature=0.2,
            max_tokens=16000,
        )
    except (LLMError, BudgetExceeded) as exc:
        log.warning("Sintesis narasi gagal: %s", exc)
        return None

    if not isinstance(hasil, dict):
        log.warning("Sintesis mengembalikan struktur tak terduga")
        return None

    def teks(kunci: str, batas: int = 4000) -> str:
        nilai = hasil.get(kunci)
        return str(nilai).strip()[:batas] if nilai else ""

    def daftar(kunci: str, batas_item: int, batas_teks: int = 400) -> List[str]:
        nilai = hasil.get(kunci)
        if not isinstance(nilai, list):
            return []
        return [str(x)[:batas_teks] for x in nilai[:batas_item] if x]

    bagian = {
        "judul": teks("judul", 200),
        "posisi_harga": teks("posisi_harga"),
        "karakter_pergerakan": teks("karakter_pergerakan", 1500),
        "penyebab": teks("penyebab"),
        "data_pendukung": daftar("data_pendukung", 4),
        "peta_level": teks("peta_level"),
        "yang_diwaspadai": teks("yang_diwaspadai"),
        "katalis_berikutnya": daftar("katalis_berikutnya", 5),
        "kesimpulan": teks("kesimpulan", 1000),
    }

    # Tanpa inti analisa, keluarannya tidak layak kirim.
    if not (bagian["penyebab"] or bagian["posisi_harga"]):
        log.warning("Sintesis tidak memuat bagian inti (penyebab/posisi harga)")
        return None

    # Narasi datar dirakit dari bagian-bagian di atas supaya konsumen lama
    # (Telegram, arsip) tetap punya satu blok teks yang utuh dan berurutan.
    potongan: List[str] = []
    if bagian["posisi_harga"]:
        potongan.append(bagian["posisi_harga"])
    if bagian["karakter_pergerakan"]:
        potongan.append(bagian["karakter_pergerakan"])
    if bagian["penyebab"]:
        potongan.append(bagian["penyebab"])
    if bagian["data_pendukung"]:
        potongan.append("Data pendukung: " + "; ".join(bagian["data_pendukung"]))
    if bagian["peta_level"]:
        potongan.append(bagian["peta_level"])
    if bagian["yang_diwaspadai"]:
        potongan.append("Yang perlu diwaspadai: " + bagian["yang_diwaspadai"])
    if bagian["kesimpulan"]:
        potongan.append(bagian["kesimpulan"])
    narrative = "\n\n".join(potongan)

    penyebab = []
    for pp in (hasil.get("penyebab_pergerakan") or [])[:5]:
        if not isinstance(pp, dict):
            continue
        arah = pp.get("arah")
        keyakinan = pp.get("keyakinan")
        penyebab.append({
            "faktor": str(pp.get("faktor", ""))[:200],
            "arah": arah if arah in ("naik", "turun", "netral") else "netral",
            "keyakinan": keyakinan if keyakinan in ("tinggi", "sedang", "rendah") else "rendah",
            "dasar": str(pp.get("dasar", ""))[:300],
        })

    themes = hasil.get("dominant_themes")
    conflicts = hasil.get("conflicts")
    return {
        "narrative": narrative,
        "bagian": bagian,
        "penyebab_pergerakan": penyebab,
        "dominant_themes": [str(t)[:60] for t in themes[:3]] if isinstance(themes, list) else [],
        "narrative_shift": teks("narrative_shift", 500),
        "conflicts": daftar("conflicts", 5, 300),
    }


# --------------------------------------------------------------------------
# LLM #5 — critic
# --------------------------------------------------------------------------
# Hanya dua jenis temuan yang boleh MENAHAN analisa: keduanya soal fakta yang
# dikarang. Sisanya (nada menyerempet saran, sebab-akibat yang terlalu percaya
# diri) cuma diberi tanda — analisanya tetap tampil.
#
# Alasannya: brief ini dibaca pemiliknya sendiri yang memutuskan sendiri.
# Menahan seluruh analisa karena satu kalimat bernada anjuran justru menghapus
# hal yang paling berguna, sementara bahaya sebenarnya — angka yang tidak
# pernah ada di data — tetap disaring, bahkan diperiksa ulang oleh kode.
JENIS_PENAHAN = {"angka_karangan", "pengetahuan_luar"}

_POLA_ANGKA = re.compile(r"\d[\d.,]*")

# Pola yang menandakan critic sendiri, di teks alasannya, MENGAKUI fakta yang
# dipersoalkan memang ada di data — dan cuma keberatan pada framing/kata
# sifat yang tidak eksplisit tertulis ("data menyebut X, tapi/namun/tanpa
# menyatakan secara eksplisit Y"). Menurut kriteria critic sendiri (lihat
# prompt di fungsi critic()), itu definisi sebab_akibat (minor), bukan
# pengetahuan_luar (fatal) — critic-nya berkontradiksi dengan instruksinya
# sendiri. Pola ini ditemukan berulang di beberapa run produksi berbeda,
# jadi dibantah lewat kode alih-alih terus menambal lewat prompt.
_POLA_AKUI_FAKTA_ADA = re.compile(
    r"data\s+(?:hanya\s+|memang\s+|juga\s+)?"
    r"(?:memuat|menyebut|menyertakan|menyampaikan|mencantumkan|berisi|memiliki)"
    # Bukan [^.]: alasan sering menyisipkan angka desimal ("63000.0") yang
    # titiknya salah dikira akhir kalimat dan memotong jendela pencarian
    # sebelum sampai ke tapi/namun/tanpa di baliknya.
    r".{0,220}?"
    r"(?:\btapi\b|\bnamun\b|\btanpa\b|tidak\s+ada\s+pernyataan|tidak\s+menyatakan|"
    r"tidak\s+menyebut|belum\s+menyebut)",
    re.IGNORECASE | re.DOTALL,
)

# Pembantah kedua, untuk keberatan yang intinya "faktanya ada, tapi TAFSIRNYA
# tidak tertulis". Dua penanda yang terbukti berulang di log produksi:
#
#   A. Kata "eksplisit" dalam konteks negatif — "tidak dinyatakan eksplisit",
#      "tidak ada secara eksplisit", "tidak tertulis eksplisit". Kalau
#      keberatannya soal EKSPLISITAS, critic sedang mengakui bahan faktanya
#      ada dan yang kurang cuma kalimat penegasnya. Itu persis definisi
#      sebab_akibat. pengetahuan_luar berarti faktanya TIDAK ADA sama sekali —
#      dan alasan untuk kasus itu tidak berbicara soal eksplisit, melainkan
#      "tidak ada satu pun berita yang menyebut ...".
#   B. Subjek kalimat alasannya sendiri sebuah kata benda tafsir
#      ("Interpretasi/Klaim/Penjelasan/Keterkaitan/... bahwa X ... tidak ...").
#      Kalau critic harus menyebut temuannya sebagai 'interpretasi' atau
#      'klaim', dia sedang menilai penalaran, bukan keberadaan fakta.
_POLA_KEBERATAN_TAFSIR = re.compile(
    r"tidak\s+(?:\w+\s+){0,3}eksplisit"
    r"|(?:^|\.\s+)\s*(?:interpretasi|klaim|penjelasan|keterkaitan|kesimpulan|"
    r"anggapan|penilaian|frasa|narasi|pernyataan\s+bahwa)\b[^.]{0,200}?\btidak\b",
    re.IGNORECASE,
)

# Angka pendek (≤2 digit) hampir selalu hasil turunan: persentase, jumlah
# butir, skor 1-5. Menuntutnya ada mentah-mentah di data akan menolak kalimat
# yang benar.
_MIN_DIGIT_DIPERIKSA = 3

# Narasi menyingkat angka besar ("$20,5 miliar") sementara data mentah
# menyimpan angka penuh (20497629840). Tanpa penyesuaian skala ini, kutipan
# yang sepenuhnya benar selalu gagal dicocokkan — persis yang terjadi di
# produksi saat critic menuduh "Volume candle harian $20,5 miliar" sebagai
# karangan padahal data memuat 20.497.629.840 (beda ~0,01%).
_SKALA_SUFFIKS = {
    "triliun": 1e12, "miliar": 1e9, "milyar": 1e9, "juta": 1e6, "ribu": 1e3,
}
_POLA_SUFFIKS_SKALA = re.compile(
    r"^\s*(triliun|miliar|milyar|juta|ribu)\b", re.IGNORECASE
)


def _kandidat_nilai(teks: str) -> List[float]:
    """Semua tafsir masuk akal dari satu angka tertulis.

    "63.226,18" bisa gaya Indonesia, "63,226.18" gaya Inggris, dan "63.226"
    ambigu antara keduanya. Semua tafsir dikumpulkan lalu dicocokkan; cukup
    satu yang cocok untuk menganggap angka itu ada.
    """
    bersih = teks.strip().strip(".,")
    if not bersih:
        return []
    kandidat = set()
    for ubah in (
        lambda s: s.replace(".", "").replace(",", "."),   # 63.226,18 -> 63226.18
        lambda s: s.replace(",", ""),                      # 63,226.18 -> 63226.18
    ):
        try:
            kandidat.add(float(ubah(bersih)))
        except ValueError:
            continue
    return sorted(kandidat)


def _angka_dalam_data(obj: Any, keluaran: Optional[List[float]] = None) -> List[float]:
    """Kumpulkan setiap angka di seluruh struktur data, termasuk dalam string."""
    if keluaran is None:
        keluaran = []
    if isinstance(obj, bool):
        return keluaran
    if isinstance(obj, (int, float)):
        keluaran.append(float(obj))
    elif isinstance(obj, str):
        for cocok in _POLA_ANGKA.findall(obj):
            keluaran.extend(_kandidat_nilai(cocok))
    elif isinstance(obj, dict):
        for nilai in obj.values():
            _angka_dalam_data(nilai, keluaran)
    elif isinstance(obj, (list, tuple)):
        for nilai in obj:
            _angka_dalam_data(nilai, keluaran)
    return keluaran


def _ada_di_data(nilai: float, nilai_data: List[float]) -> bool:
    """Cocok kalau selisihnya masih dalam batas pembulatan yang wajar."""
    for pembanding in nilai_data:
        if abs(nilai - pembanding) <= max(0.01, abs(pembanding) * 0.005):
            return True
    return False


def _semua_angka_didukung(kutipan: str, nilai_data: List[float]) -> bool:
    """True kalau setiap angka panjang pada kutipan memang ada di data.

    Ini pemeriksaan KODE, bukan penilaian model. Critic berkali-kali menuduh
    angka sebagai karangan padahal angkanya ada — cuma ditulis dengan pemisah
    ribuan yang berbeda. Pemeriksaan ini membatalkan tuduhan semacam itu tanpa
    melemahkan penyaringan angka yang benar-benar tidak ada.
    """
    kutipan = kutipan or ""
    for m in _POLA_ANGKA.finditer(kutipan):
        cocok = m.group()
        digit = sum(c.isdigit() for c in cocok)
        if digit < _MIN_DIGIT_DIPERIKSA:
            continue
        kandidat = _kandidat_nilai(cocok)
        # Kalau kata setelah angka ini adalah suffix skala ("miliar", dst),
        # ikutkan versi yang sudah dikalikan sebagai tafsir tambahan — tanpa
        # membuang tafsir apa adanya, kalau-kalau angkanya memang tidak
        # disingkat.
        suffiks = _POLA_SUFFIKS_SKALA.match(kutipan[m.end():])
        if suffiks:
            skala = _SKALA_SUFFIKS[suffiks.group(1).lower()]
            kandidat = kandidat + [k * skala for k in kandidat]
        if not any(_ada_di_data(n, nilai_data) for n in kandidat):
            return False
    # Tanpa angka panjang sama sekali, tuduhan "angka karangan" tidak berdasar.
    return True


# Keberatan yang bunyinya "ISTILAH/FRASA/SEBUTAN ini tidak muncul di data".
# Itu keberatan soal PILIHAN KATA, bukan keberadaan fakta: menurut prompt
# critic sendiri, pengetahuan_luar dipakai hanya kalau fakta/entitasnya sama
# sekali tidak ada. Ditemukan di produksi pada temuan yang bertahan sampai
# putaran kedua ("Istilah pertemuan Gedung Putih tidak muncul dalam ringkasan
# berita") padahal entitasnya jelas ada di judul berita yang disuplai.
_POLA_KEBERATAN_ISTILAH = re.compile(
    r"\b(?:istilah|frasa|sebutan|penyebutan|terminologi|kata|redaksi|"
    r"ungkapan|label)\b[^.]{0,160}?"
    r"\btidak\s+(?:muncul|ada|ditemukan|disebut\w*|tertulis|terdapat)\b",
    re.IGNORECASE,
)

# Entitas = rangkaian minimal DUA kata berawalan huruf besar ("Gedung Putih",
# "Federal Reserve", "Donald Trump"). Sengaja tidak menerima satu kata saja:
# kata pertama kalimat juga berhuruf besar, dan mencocokkannya akan membantah
# tuduhan yang justru benar.
_POLA_ENTITAS = re.compile(r"\b[A-Z][\wÀ-ÿ'’-]{2,}(?:\s+[A-Z][\wÀ-ÿ'’-]{2,})+")


def _teks_dalam_data(obj: Any, keluaran: Optional[List[str]] = None) -> List[str]:
    """Kumpulkan setiap potongan teks di seluruh struktur data."""
    if keluaran is None:
        keluaran = []
    if isinstance(obj, str):
        keluaran.append(obj)
    elif isinstance(obj, dict):
        for kunci, nilai in obj.items():
            keluaran.append(str(kunci))
            _teks_dalam_data(nilai, keluaran)
    elif isinstance(obj, (list, tuple)):
        for nilai in obj:
            _teks_dalam_data(nilai, keluaran)
    return keluaran


def _semua_entitas_didukung(kutipan: str, teks_data: str) -> bool:
    """True kalau SETIAP entitas multi-kata pada kutipan memang ada di data.

    False juga saat kutipan tidak memuat entitas sama sekali — tanpa entitas
    tidak ada yang bisa dibantah, jadi tuduhannya dibiarkan berdiri.
    """
    entitas = _POLA_ENTITAS.findall(kutipan or "")
    if not entitas:
        return False
    return all(" ".join(e.split()).lower() in teks_data for e in entitas)


def _persentase_level_tersirat(data_mentah: Dict[str, Any]) -> List[float]:
    """Angka turunan: persentase jarak harga saat ini ke tiap level kunci
    (support/resistance/invalidasi).

    Critic pernah menuduh persentase semacam ini ("0,44% di atas support")
    sebagai angka karangan padahal murni hasil hitung dari dua angka yang
    memang ada di data (harga.last dan level_kunci.*) — dan gagal
    diverifikasi kode karena persentasenya sendiri memang tidak muncul
    literal di mana pun. Dihitung sekali di sini (dua konvensi pembagi:
    persen dari level dan persen dari harga, supaya cocok dengan cara
    manapun modelnya membulatkan) dan digabung ke `nilai_data`.
    """
    hasil: List[float] = []
    try:
        harga = float((data_mentah.get("harga") or {}).get("last"))
    except (TypeError, ValueError):
        return hasil

    level_kunci = data_mentah.get("level_kunci") or {}
    kandidat_level: List[float] = []
    for kunci in ("support", "resistance"):
        for v in level_kunci.get(kunci) or []:
            try:
                kandidat_level.append(float(v))
            except (TypeError, ValueError):
                continue
    for kunci in ("invalidasi_naik", "invalidasi_turun"):
        v = level_kunci.get(kunci)
        if v is None:
            continue
        try:
            kandidat_level.append(float(v))
        except (TypeError, ValueError):
            continue

    for level in kandidat_level:
        if level == 0:
            continue
        selisih = abs(harga - level)
        hasil.append(selisih / abs(level) * 100)
        hasil.append(selisih / harga * 100 if harga else 0.0)
    return hasil


def pilih_model_critic(
    models: List[str], model_synthesis: Optional[str]
) -> List[str]:
    """Saring model critic supaya tidak sekeluarga dengan synthesis.

    Config boleh saja mendaftarkan cadangan yang sekeluarga dengan critic —
    yang penting adalah model yang AKHIRNYA dipakai berbeda. Kalau synthesis
    jatuh ke cadangannya, pilihan critic ikut digeser di sini.
    """
    if not model_synthesis:
        return models
    keluarga = model_synthesis.split("/")[0]
    tersaring = [m for m in models if m.split("/")[0] != keluarga]
    if not tersaring:
        log.warning(
            "Semua model critic sekeluarga dengan synthesis (%s); "
            "pemeriksaan jadi kurang independen",
            keluarga,
        )
        return models
    if len(tersaring) < len(models):
        log.info("Model critic disaring agar beda keluarga dari synthesis (%s)", keluarga)
    return tersaring


def critic(
    client: LLMClient, models: List[str], teks_ai: Dict[str, str], data_mentah: Dict[str, Any]
) -> Dict[str, Any]:
    """Periksa SELURUH keluaran naratif AI terhadap data mentah sumbernya."""
    system = (
        "Kamu pemeriksa fakta untuk laporan pasar. Kamu diberi beberapa bagian "
        "teks analisa beserta data mentah yang menjadi sumbernya. Tugasmu "
        "menemukan klaim yang TIDAK BISA didukung data itu.\n\n"

        "YANG SAH DAN TIDAK BOLEH KAMU TANDAI:\n"
        "  - Angka yang DITURUNKAN dari data: selisih, jumlah, rasio, persentase "
        "    perubahan antara dua nilai yang ada. Kalau data memuat 100 dan 110, "
        "    menulis 'naik 10%' itu benar, bukan karangan.\n"
        "  - Pembulatan dan format berbeda: 4,21 vs 4.2 vs 4,2% adalah angka yang sama.\n"
        "  - Angka yang muncul di BAGIAN MANA PUN dari data, termasuk di dalam "
        "    objek interpretasi_teknikal, analisa_whale, teknikal_1d, level_kunci, "
        "    opsi_deribit, valuasi_onchain, dan aliran_dana. Periksa SELURUH data "
        "    sebelum menyimpulkan sebuah angka tidak ada.\n"
        "  - PEMISAH RIBUAN DAN DESIMAL YANG BERBEDA. 64.371,18 dan 64,371.18 dan "
        "    64371.1839 adalah ANGKA YANG SAMA. Jangan pernah menandai angka hanya "
        "    karena cara penulisannya berbeda dari data.\n"
        "  - Skenario kondisional yang merujuk level dari data: 'selama bertahan "
        "    di atas 116.500, kondisi X cenderung berlanjut'.\n"
        "  - Menyebut support/resistance dari data sebagai kemungkinan tujuan "
        "    pergerakan. Itu pembacaan teknikal biasa, BUKAN saran investasi.\n"
        "  - Kalimat menunggu konfirmasi ('belum ada yang mendesak sampai level X "
        "    ditembus'). Itu penilaian kondisi, bukan ajakan bertransaksi.\n"
        "  - Pernyataan ketidakpastian: 'penyebabnya tidak jelas dari data hari ini'.\n"
        "  - Penyebutan pola sebagai kemungkinan berkeyakinan rendah.\n"
        "  - Istilah teknis umum yang tidak memerlukan angka.\n"
        "  - DUA ANGKA VOLUME BERBEDA YANG SAMA-SAMA VALID: `harga.volume_24h` "
        "    (volume bergulir 24 jam) dan `teknikal_1d.volume.terakhir`/`.rata_20` "
        "    (volume candle harian) adalah metrik yang BERBEDA — angkanya BOLEH "
        "    dan MEMANG SERING tidak sama satu sama lain. Kalau narasi menyebut "
        "    'volume candle harian' atau 'volume hari ini' dan angkanya cocok "
        "    dengan teknikal_1d.volume, itu BENAR — jangan menuntutnya sama "
        "    dengan volume_24h, itu memang seharusnya beda.\n"
        "  - MENGHUBUNGKAN beberapa data poin yang ADA menjadi satu penjelasan "
        "    (mis. 'BB squeeze + taker sell dominan + short buildup tipis "
        "    menjelaskan kenapa harga belum bergerak'), menilai apakah suatu "
        "    berita SUDAH TERCERMIN DI HARGA berdasarkan status/waktunya, atau "
        "    menilai dampak suatu agenda/pertemuan yang akan datang — semua ini "
        "    adalah PEKERJAAN UTAMA analis (menyusun sebab-akibat dari data "
        "    mentah), BUKAN pengetahuan_luar. Kalau setiap fakta/angka yang "
        "    dirujuk memang ada di data, ini paling banter sebab_akibat MINOR "
        "    (interpretasi tidak eksplisit), TIDAK PERNAH pengetahuan_luar.\n"
        "  - BAHASA BERPAGAR ('belum sepenuhnya', 'berpotensi', 'campur aduk', "
        "    'tidak memberi arah jelas', 'menular') pada kalimat yang meringkas "
        "    beberapa data poin yang ADA. Nada ragu-ragu itu justru CIRI "
        "    interpretasi (analis tidak yakin 100%), BUKAN tanda karangan — "
        "    jangan menafsirkannya sebagai klaim eksternal hanya karena tidak "
        "    ada satu angka pun yang dikutip persis. Contoh: 'makro hari ini "
        "    campur aduk dan tidak memberi arah jelas' saat data memuat DXY, "
        "    yield, VIX yang searah-tak-searah adalah RINGKASAN data itu "
        "    sendiri, sebab_akibat — bukan pengetahuan_luar.\n\n"

        "YANG HARUS KAMU TANDAI:\n"
        "  1. angka_karangan: angka yang setelah kamu telusuri SELURUH data "
        "     memang tidak ada dan tidak bisa diturunkan dari data\n"
        "  2. pengetahuan_luar: HANYA fakta, angka, peristiwa, atau ENTITAS "
        "     KONKRET yang disebut narasi tapi TIDAK ADA DI MANA PUN dalam "
        "     data — misalnya menyebut peristiwa yang tidak pernah muncul di "
        "     `berita`/`pernyataan_tokoh`, atau menyebut nilai numerik yang "
        "     benar-benar tidak bisa ditelusuri dari data manapun (kalau itu "
        "     soal angka, harusnya masuk angka_karangan, bukan sini). "
        "     Kategori ini BUKAN untuk kalimat yang MENAFSIRKAN atau "
        "     MENGHUBUNGKAN data yang sudah ada — itu sebab_akibat.\n"
        "  3. sebab_akibat: klaim sebab-akibat, kesimpulan, atau penilaian "
        "     ('kemungkinan besar', 'tampak lebih terkait', 'berpotensi "
        "     mempengaruhi') yang DIBANGUN dari data yang ada tapi hubungan "
        "     kausalnya tidak dinyatakan eksplisit di data itu sendiri. Ini "
        "     SELALU minor, tidak peduli seberapa spekulatif nadanya — "
        "     menyimpulkan dari data adalah tugas analis, bukan kesalahan.\n"
        "  4. saran_investasi: HANYA ajakan bertransaksi yang eksplisit — "
        "     'beli sekarang', 'segera jual', 'pasang stop loss di X'. "
        "     Menyebut level dan skenario TIDAK termasuk di sini.\n\n"

        "KEPARAHAN — pakai dengan hemat:\n"
        "  fatal = HANYA untuk angka_karangan dan pengetahuan_luar, dan hanya "
        "          setelah kamu benar-benar mencarinya di seluruh data\n"
        "  minor = semua sisanya, termasuk nada yang menyerempet anjuran dan "
        "          klaim yang terlalu percaya diri\n\n"
        "  jenis sebab_akibat TIDAK PERNAH fatal, berapa pun percaya dirinya "
        "  nada kalimatnya — kalau kamu ingin menahan sebuah kalimat sebab-"
        "  akibat, itu tandanya kamu salah memberi jenis. Cek ulang: apakah "
        "  SETIAP fakta yang dirujuk kalimat itu ada di data? Kalau ya, itu "
        "  sebab_akibat (minor). Hanya kalau ada fakta/angka yang BENAR-BENAR "
        "  tidak ada di data sama sekali, itu baru pengetahuan_luar/"
        "  angka_karangan (boleh fatal).\n\n"
        "KALAU RAGU, PILIH minor. Menahan analisa karena keraguan yang tidak "
        "pasti jauh lebih merugikan pembaca daripada membiarkan satu kalimat "
        "yang agak longgar lewat.\n\n"

        "Untuk setiap temuan, WAJIB sebutkan di field `dicari_di` bagian data mana "
        "yang sudah kamu periksa. Kalau kamu tidak bisa menyebutkannya, berarti "
        "kamu belum benar-benar mencari — turunkan jadi minor.\n\n"

        "Balas objek JSON:\n"
        "  {\"passed\": bool, \"corrections\": [{\"jenis\": \"...\", "
        "\"keparahan\": \"fatal|minor\", \"bagian\": \"nama bagian\", "
        "\"kutipan\": \"...\", \"alasan\": \"...\", \"dicari_di\": \"...\"}]}\n\n"
        "`alasan` maksimal satu kalimat pendek (~20 kata). PARAFRASEKAN, jangan "
        "mengutip ulang angka/frasa persis dengan tanda kutip di dalam alasan — "
        "kutip ganda bersarang di field JSON gampang membuat balasanmu sendiri "
        "gagal diparse dan seluruh temuanmu terbuang percuma.\n\n"
        "passed bernilai false HANYA kalau ada koreksi berkeparahan fatal.\n"
        "Kalau semua bersih, balas {\"passed\": true, \"corrections\": []}.\n\n"
        + ATURAN_DASAR
    )
    bagian = "\n\n".join(
        f"=== BAGIAN: {nama} ===\n{isi}" for nama, isi in teks_ai.items() if isi
    )
    user = (
        "TEKS YANG DIPERIKSA:\n" + bagian + "\n\n"
        "DATA MENTAH SUMBERNYA:\n" + json.dumps(data_mentah, ensure_ascii=False, default=str)
    )

    try:
        hasil = client.chat_json(models, system, user, step="critic", temperature=0.0, max_tokens=6000)
    except (LLMError, BudgetExceeded) as exc:
        # Critic tidak jalan bukan berarti narasi salah, tapi juga belum terverifikasi.
        log.warning("Critic gagal dijalankan: %s", exc)
        return {"passed": True, "corrections": [], "dijalankan": False}

    if not isinstance(hasil, dict):
        return {"passed": True, "corrections": [], "tanda": [], "dijalankan": False}

    corrections = hasil.get("corrections")
    corrections = corrections if isinstance(corrections, list) else []

    # Daftar angka dari data disiapkan sekali, dipakai untuk membantah tuduhan
    # "angka karangan" yang sebenarnya cuma beda format penulisan — plus
    # persentase jarak ke level kunci yang tidak muncul literal di data tapi
    # murni turunan dari dua angka yang memang ada (lihat _persentase_level_tersirat).
    nilai_data = _angka_dalam_data(data_mentah) + _persentase_level_tersirat(data_mentah)
    # Versi teks datar dari data yang sama, untuk membantah tuduhan "entitas
    # ini tidak ada di data" (lihat _semua_entitas_didukung).
    teks_data = " ".join(_teks_dalam_data(data_mentah)).lower()

    bersih = []
    for c in corrections[:10]:
        if not isinstance(c, dict):
            continue
        jenis = str(c.get("jenis", ""))[:60]
        kutipan = str(c.get("kutipan", ""))[:200]
        alasan_asli = str(c.get("alasan", ""))
        alasan = alasan_asli[:300]
        keparahan = c.get("keparahan")
        keparahan = keparahan if keparahan in ("fatal", "minor") else "minor"

        # 1. Bantahan kode: kalau setiap angka pada kutipan ternyata ADA di
        #    data, tuduhan mengarang angka tidak berdasar.
        if keparahan == "fatal" and jenis == "angka_karangan":
            if _semua_angka_didukung(kutipan, nilai_data):
                keparahan = "minor"
                alasan = "[dibantah kode: semua angka ditemukan di data] " + alasan
                log.info(
                    "Tuduhan angka karangan dibatalkan, angkanya ada di data: %s",
                    kutipan[:80],
                )

        # 1b. Bantahan kode: kalau critic sendiri MENGAKUI di teks alasannya
        #     bahwa faktanya ada di data (cuma keberatan framing/kata sifat
        #     yang tidak eksplisit), itu sebab_akibat menurut kriterianya
        #     sendiri — dicek pada teks alasan ASLI (sebelum dipotong 300
        #     karakter) supaya penanda "tapi/namun/tanpa" di ujung kalimat
        #     tidak ikut terpotong.
        if keparahan == "fatal" and jenis == "pengetahuan_luar":
            if _POLA_AKUI_FAKTA_ADA.search(alasan_asli):
                jenis = "sebab_akibat"
                keparahan = "minor"
                alasan = "[dibantah kode: alasan critic sendiri mengakui faktanya ada di data] " + alasan
                log.info(
                    "Tuduhan pengetahuan_luar dibatalkan, alasan critic sendiri mengakui faktanya ada di data: %s",
                    kutipan[:80],
                )
            elif _POLA_KEBERATAN_TAFSIR.search(alasan_asli):
                jenis = "sebab_akibat"
                keparahan = "minor"
                alasan = "[dibantah kode: keberatannya soal tafsir/eksplisitas, bukan keberadaan fakta] " + alasan
                log.info(
                    "Tuduhan pengetahuan_luar dibatalkan, keberatannya soal tafsir bukan fakta: %s",
                    kutipan[:80],
                )
            elif (
                _POLA_KEBERATAN_ISTILAH.search(alasan_asli)
                and _semua_entitas_didukung(kutipan, teks_data)
            ):
                # Keberatannya soal pilihan kata, dan kode sudah memastikan
                # setiap entitas yang disebut kutipan memang ada di data.
                jenis = "sebab_akibat"
                keparahan = "minor"
                alasan = "[dibantah kode: keberatan soal istilah, entitasnya ada di data] " + alasan
                log.info(
                    "Tuduhan pengetahuan_luar dibatalkan, entitas kutipan ditemukan di data: %s",
                    kutipan[:80],
                )

        # 2. Hanya kesalahan fakta yang boleh menahan. Sisanya jadi tanda:
        #    analisanya tetap tampil, cuma diberi keterangan.
        if keparahan == "fatal" and jenis not in JENIS_PENAHAN:
            keparahan = "tanda"

        bersih.append(
            {
                "jenis": jenis,
                "keparahan": keparahan,
                "bagian": str(c.get("bagian", ""))[:60],
                "kutipan": kutipan,
                "alasan": alasan,
                "dicari_di": str(c.get("dicari_di", ""))[:200],
            }
        )

    fatal = [c for c in bersih if c["keparahan"] == "fatal"]
    tanda = [c for c in bersih if c["keparahan"] == "tanda"]
    passed = not fatal

    if fatal:
        log.warning("Critic menahan narasi: %d kesalahan fakta", len(fatal))
        for c in fatal[:5]:
            log.warning(
                "  fatal [%s] %s | kutipan: %s | alasan: %s",
                c.get("bagian") or "?", c["jenis"], c["kutipan"][:80], c["alasan"][:120],
            )
    for c in tanda[:5]:
        log.info(
            "  ditandai [%s] %s | kutipan: %s",
            c.get("bagian") or "?", c["jenis"], c["kutipan"][:80],
        )

    return {"passed": passed, "corrections": bersih, "tanda": tanda, "dijalankan": True}


def longgarkan_setelah_revisi(hasil_critic: Dict[str, Any]) -> Dict[str, Any]:
    """Setelah SATU putaran revisi, hanya kesalahan ANGKA yang masih menahan.

    Alasannya empiris: temuan `pengetahuan_luar` yang bertahan sampai putaran
    kedua di produksi terbukti hampir selalu salah kategori — kalimat tafsir
    yang divonis sebagai fakta karangan. Ongkos dua jenis kesalahan ini tidak
    setara: menahan SELURUH analisa (narasi + outlook + skenario sekaligus)
    karena satu kalimat tafsir jauh lebih merugikan pembaca daripada
    membiarkan kalimat itu lewat dengan tanda editorial.

    Angka karangan TETAP menahan, tanpa pengecualian: angka yang salah
    menyesatkan secara langsung, dan kode sudah memverifikasinya sendiri
    lewat `_semua_angka_didukung` — jadi yang lolos ke sini memang angka yang
    tidak ditemukan di data mana pun.
    """
    fatal_tersisa = [
        c for c in hasil_critic.get("corrections") or []
        if c.get("keparahan") == "fatal"
    ]
    if not fatal_tersisa:
        return hasil_critic

    masih_menahan = [c for c in fatal_tersisa if c.get("jenis") == "angka_karangan"]
    diturunkan = [c for c in fatal_tersisa if c.get("jenis") != "angka_karangan"]
    if not diturunkan:
        return hasil_critic

    for c in diturunkan:
        c["keparahan"] = "tanda"
        c["alasan"] = "[diturunkan: bertahan setelah revisi, bukan kesalahan angka] " + (c.get("alasan") or "")
        log.info(
            "Temuan non-angka diturunkan jadi tanda setelah revisi: %s | %s",
            c.get("jenis"), (c.get("kutipan") or "")[:80],
        )

    hasil_critic["tanda"] = [
        c for c in hasil_critic.get("corrections") or [] if c.get("keparahan") == "tanda"
    ]
    hasil_critic["passed"] = not masih_menahan
    if masih_menahan:
        log.warning(
            "Masih ada %d kesalahan ANGKA setelah revisi; bagian terkait tetap ditahan",
            len(masih_menahan),
        )
    else:
        log.info(
            "Setelah revisi tidak ada kesalahan angka tersisa; analisa dikirim "
            "dengan %d tanda editorial", len(diturunkan),
        )
    return hasil_critic
