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
5. Seluruh teks naratif ditulis dalam bahasa Indonesia."""

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
def filter_relevansi(
    client: LLMClient,
    models: List[str],
    articles: List[Dict[str, Any]],
    min_score: int = 40,
    max_keep: int = 25,
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
    daftar = [
        {"id": a["id"], "judul": a["judul"], "ringkasan": a["ringkasan"][:250]}
        for a in articles
    ]
    user = "Nilai relevansi setiap artikel berikut:\n\n" + json.dumps(daftar, ensure_ascii=False)

    try:
        hasil = client.chat_json(models, system, user, step="filter", max_tokens=6000)
    except (LLMError, BudgetExceeded) as exc:
        log.warning("Filter relevansi gagal (%s), pakai skor prioritas kata kunci", exc)
        # Fallback deterministik: pakai skor kata kunci yang sudah dihitung kode.
        fallback = sorted(articles, key=lambda a: a["skor_prioritas"], reverse=True)[:max_keep]
        for a in fallback:
            a["relevansi_btc"] = a["skor_prioritas"]
        return fallback

    skor = {}
    for item in hasil if isinstance(hasil, list) else []:
        try:
            skor[str(item["id"])] = int(item["relevansi_btc"])
        except (KeyError, TypeError, ValueError):
            continue

    terpilih = []
    for a in articles:
        nilai = skor.get(a["id"])
        if nilai is None or nilai < min_score:
            continue
        a["relevansi_btc"] = nilai
        terpilih.append(a)

    terpilih.sort(key=lambda a: a["relevansi_btc"], reverse=True)
    log.info("Filter relevansi: %d dari %d artikel lolos", len(terpilih), len(articles))
    return terpilih[:max_keep]


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
        "  kekuatan: 1-5 (seberapa besar potensi dampaknya)\n"
        f"  horizon: salah satu dari {HORIZON}\n"
        f"  status_kepastian: salah satu dari {STATUS_KEPASTIAN}\n"
        "  entitas: array nama lembaga/perusahaan/orang yang disebut (maksimal 4)\n"
        f"  sudah_priced_in: salah satu dari {PRICED_IN}\n"
        f"  tipe_klaim: salah satu dari {TIPE_KLAIM}\n\n"
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

    return {
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
                max_tokens=4000,
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
                "tipe_klaim": None,
            }
        keluaran.append({**a, **klas, "mekanisme": None, "jalur_transmisi": None})

    log.info("Klasifikasi selesai: %d artikel", len(hasil_per_id))
    return keluaran


# --------------------------------------------------------------------------
# LLM #3 — mekanisme transmisi
# --------------------------------------------------------------------------
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
        "  - Berita lama yang diulang tanpa perkembangan baru -> relevansi_btc 0\n\n"
        "Kalau ADA pernyataannya, isi field berikut.\n\n"
        "Balas array JSON, satu objek per item:\n"
        "  id (string, sama persis dengan input)\n"
        "  tokoh: nama orang yang menyatakan (null kalau tidak jelas)\n"
        "  kutipan: kutipan langsung kalau ada di teks, null kalau hanya parafrase\n"
        "  ringkasan_id: satu kalimat isi pernyataan, dalam bahasa Indonesia\n"
        f"  topik: salah satu dari {TOPIK_PERNYATAAN}\n"
        f"  sikap_kripto: salah satu dari {SIKAP_PERNYATAAN} (sikap terhadap kripto/aset berisiko)\n"
        "  dampak_btc: bullish | bearish | netral\n"
        "  kekuatan: 1-5 (potensi dampak ke harga BTC)\n"
        f"  status: salah satu dari {STATUS_PERNYATAAN}\n"
        "  jalur_transmisi: likuiditas | risk_appetite | supply_demand | regulasi\n"
        "  mekanisme: satu kalimat rantai sebab-akibat menuju harga BTC\n"
        "  relevansi_btc: 0-100\n\n"
        "PEMBEDAAN YANG WAJIB KAMU JAGA:\n"
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
            relevansi = int(analisa.get("relevansi_btc") or 0)
        except (TypeError, ValueError):
            relevansi = 0
        if relevansi < min_relevansi:
            continue
        try:
            kekuatan = max(1, min(5, int(analisa.get("kekuatan"))))
        except (TypeError, ValueError):
            kekuatan = None

        keluaran.append({
            "id": k["id"],
            "tokoh": (str(analisa["tokoh"])[:80] if analisa.get("tokoh") else k.get("tokoh")),
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
        "Yang harus kamu jelaskan:\n"
        "  - Apa yang sedang diberitahukan struktur harga di ketiga timeframe\n"
        "  - Di mana timeframe saling MENGUATKAN dan di mana saling BERTENTANGAN\n"
        "  - Apa arti kondisi momentum dan volatilitas saat ini\n"
        "  - Apakah volume mengonfirmasi pergerakan harga atau tidak\n"
        "  - Kondisi konkret apa yang akan MEMBATALKAN pembacaan ini\n\n"
        "Balas objek JSON:\n"
        "  ringkasan: 2-3 kalimat inti kondisi teknikal\n"
        "  per_timeframe: {\"1d\": \"...\", \"4h\": \"...\", \"1h\": \"...\"} — 2-3 kalimat tiap timeframe\n"
        "  konfluensi: array string, hal yang saling menguatkan antar timeframe\n"
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
            temperature=0.3,
            max_tokens=6000,
        )
    except (LLMError, BudgetExceeded) as exc:
        log.warning("Interpretasi teknikal gagal: %s", exc)
        return None

    if not isinstance(hasil, dict) or not hasil.get("ringkasan"):
        log.warning("Interpretasi teknikal: balasan tanpa field 'ringkasan', dilewati")
        return None

    per_tf = hasil.get("per_timeframe")
    kualitas = hasil.get("kualitas_tren")
    return {
        "ringkasan": str(hasil["ringkasan"]).strip(),
        "per_timeframe": {
            k: str(v)[:800] for k, v in per_tf.items() if isinstance(v, str)
        } if isinstance(per_tf, dict) else {},
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
            temperature=0.3,
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
        "Balas objek JSON:\n"
        "  ringkasan: 2-3 kalimat pandangan umum ke depan\n"
        "  skenario_naik: {\"pemicu\": [array faktor], \"kondisi\": \"level/kondisi dari data yang harus bertahan\"}\n"
        "  skenario_turun: {\"pemicu\": [array faktor], \"kondisi\": \"level/kondisi dari data yang harus bertahan\"}\n"
        "  faktor_geopolitik: array string, isu geopolitik yang relevan bagi BTC (array kosong kalau tidak ada di data)\n"
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
            temperature=0.4,
            max_tokens=7000,
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
        "skenario_naik": skenario("skenario_naik"),
        "skenario_turun": skenario("skenario_turun"),
        "faktor_geopolitik": [str(x)[:300] for x in (hasil.get("faktor_geopolitik") or [])[:6]],
        "keputusan_besar": keputusan,
        "risiko_utama": [str(x)[:300] for x in (hasil.get("risiko_utama") or [])[:6]],
        "horizon": str(hasil.get("horizon", ""))[:100],
    }


# --------------------------------------------------------------------------
# LLM #4 — sintesis narasi
# --------------------------------------------------------------------------
def sintesis(
    client: LLMClient, models: List[str], konteks: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    system = (
        "Kamu penulis analisa pasar Bitcoin untuk pembaca Indonesia.\n\n"
        "Tulis analisa MENDALAM 6-9 paragraf. Ini bukan ringkasan singkat — pembaca "
        "ingin MENGERTI apa yang terjadi dan MENGAPA.\n\n"
        "Struktur yang harus kamu ikuti:\n"
        "  1. Apa yang terjadi pada harga (arah, besaran, dari data)\n"
        "  2. MENGAPA — ini bagian terpenting. Kalau pasar turun, jelaskan apa yang "
        "     menyebabkannya; kalau naik, jelaskan pendorongnya. Rangkai dari berita, "
        "     makro, posisi derivatif, dan aliran dana yang ada di data. Bedakan sebab "
        "     yang DIDUKUNG data dari yang sekadar bertepatan waktu.\n"
        "  3. Apa kata struktur teknikal terhadap cerita itu — apakah menguatkan atau "
        "     justru bertentangan\n"
        "  4. Apa yang dilakukan pemain besar versus ritel, kalau datanya tersedia\n"
        "  5. Konteks makro dan geopolitik yang membingkai semuanya\n"
        "  6. Apa yang berubah dibanding brief sebelumnya\n\n"
        "ATURAN SEBAB-AKIBAT (kritikal):\n"
        "  - Setiap klaim sebab harus bisa ditelusuri ke item di data yang diberikan\n"
        "  - Kalau penyebabnya tidak jelas, TULIS BEGITU: \"pergerakan ini tidak punya "
        "    pemicu tunggal yang jelas di data hari ini\"\n"
        "  - Korelasi bukan sebab-akibat. Jangan menyimpulkan A menyebabkan B hanya "
        "    karena keduanya terjadi bersamaan\n"
        "  - Sebutkan angka konkret dari data. JANGAN mengarang angka baru\n\n"
        "Balas objek JSON dengan field:\n"
        "  narrative: string, 6-9 paragraf dipisah \\n\\n, bahasa Indonesia\n"
        "  penyebab_pergerakan: array objek {\"faktor\": \"...\", \"arah\": \"naik|turun|netral\", "
        "\"keyakinan\": \"tinggi|sedang|rendah\", \"dasar\": \"data apa yang mendukung\"}, "
        "urut dari yang paling berpengaruh, maksimal 5\n"
        "  dominant_themes: array 2-3 string pendek\n"
        "  narrative_shift: string, apa yang berubah dibanding brief sebelumnya (null kalau tidak ada pembanding)\n"
        "  conflicts: array string, sinyal yang saling bertentangan (array kosong kalau tidak ada)\n\n"
        "Nada tulisan: tenang, deskriptif, tidak mengajak transaksi. Jangan menulis "
        "kalimat yang menyarankan pembaca membeli, menjual, atau menunggu harga tertentu.\n\n"
        + ATURAN_DASAR
    )
    try:
        hasil = client.chat_json(
            models,
            system,
            "Data hari ini:\n\n" + json.dumps(konteks, ensure_ascii=False, default=str),
            step="synthesis",
            temperature=0.4,
            max_tokens=10000,
        )
    except (LLMError, BudgetExceeded) as exc:
        log.warning("Sintesis narasi gagal: %s", exc)
        return None

    if not isinstance(hasil, dict) or not hasil.get("narrative"):
        log.warning("Sintesis mengembalikan struktur tak terduga")
        return None

    themes = hasil.get("dominant_themes")
    conflicts = hasil.get("conflicts")

    penyebab = []
    for p in (hasil.get("penyebab_pergerakan") or [])[:5]:
        if not isinstance(p, dict):
            continue
        arah = p.get("arah")
        keyakinan = p.get("keyakinan")
        penyebab.append({
            "faktor": str(p.get("faktor", ""))[:200],
            "arah": arah if arah in ("naik", "turun", "netral") else "netral",
            "keyakinan": keyakinan if keyakinan in ("tinggi", "sedang", "rendah") else "rendah",
            "dasar": str(p.get("dasar", ""))[:300],
        })

    return {
        "narrative": str(hasil["narrative"]).strip(),
        "penyebab_pergerakan": penyebab,
        "dominant_themes": [str(t)[:60] for t in themes[:3]] if isinstance(themes, list) else [],
        "narrative_shift": str(hasil["narrative_shift"])[:500] if hasil.get("narrative_shift") else "",
        "conflicts": [str(c)[:300] for c in conflicts[:5]] if isinstance(conflicts, list) else [],
    }


# --------------------------------------------------------------------------
# LLM #5 — critic
# --------------------------------------------------------------------------
def critic(
    client: LLMClient, models: List[str], teks_ai: Dict[str, str], data_mentah: Dict[str, Any]
) -> Dict[str, Any]:
    """Periksa SELURUH keluaran naratif AI terhadap data mentah sumbernya."""
    system = (
        "Kamu pemeriksa fakta yang ketat. Kamu diberi beberapa bagian teks analisa "
        "pasar beserta data mentah yang menjadi sumbernya. Periksa setiap bagian "
        "terhadap data.\n\n"
        "Cari empat jenis masalah:\n"
        "  1. angka_karangan: angka di teks yang tidak ada di data mentah\n"
        "  2. sebab_akibat: klaim sebab-akibat yang tidak didukung data\n"
        "  3. saran_investasi: rekomendasi beli/jual, target harga, atau ajakan transaksi\n"
        "  4. pengetahuan_luar: klaim faktual tentang peristiwa yang tidak ada di data "
        "     (model mengarang dari ingatan masa pelatihannya)\n\n"
        "PEMBEDAAN PENTING — jangan salah menandai:\n"
        "  - BOLEH: skenario kondisional yang merujuk level dari data, misalnya "
        "    \"selama bertahan di atas 116.500, kondisi X cenderung berlanjut\". "
        "    Level itu ADA di data, dan kalimatnya menjelaskan kondisi, bukan menyuruh.\n"
        "  - TIDAK BOLEH: target harga (\"menuju 130.000\"), ramalan arah "
        "    (\"akan naik minggu depan\"), atau ajakan (\"saatnya akumulasi\").\n"
        "  - BOLEH: menyatakan ketidakpastian (\"penyebabnya tidak jelas dari data hari ini\").\n"
        "  - BOLEH: menyebut pola whale sebagai kemungkinan berkeyakinan rendah, "
        "    selama tidak dinyatakan sebagai fakta pasti.\n\n"
        "Kategori keparahan:\n"
        "  fatal  = angka karangan, saran investasi, target harga, atau pengetahuan luar "
        "           yang disajikan sebagai fakta\n"
        "  minor  = klaim sebab-akibat yang terlalu percaya diri tapi tidak menyesatkan\n\n"
        "Balas objek JSON:\n"
        "  {\"passed\": bool, \"corrections\": [{\"jenis\": \"...\", \"keparahan\": \"fatal|minor\", "
        "\"bagian\": \"nama bagian yang bermasalah\", \"kutipan\": \"...\", \"alasan\": \"...\"}]}\n\n"
        "passed bernilai false HANYA kalau ada koreksi berkeparahan fatal.\n"
        "Kalau semua bagian bersih, balas {\"passed\": true, \"corrections\": []}.\n\n" + ATURAN_DASAR
    )
    bagian = "\n\n".join(
        f"=== BAGIAN: {nama} ===\n{isi}" for nama, isi in teks_ai.items() if isi
    )
    user = (
        "TEKS YANG DIPERIKSA:\n" + bagian + "\n\n"
        "DATA MENTAH SUMBERNYA:\n" + json.dumps(data_mentah, ensure_ascii=False, default=str)
    )

    try:
        hasil = client.chat_json(models, system, user, step="critic", temperature=0.0, max_tokens=3000)
    except (LLMError, BudgetExceeded) as exc:
        # Critic tidak jalan bukan berarti narasi salah, tapi juga belum terverifikasi.
        log.warning("Critic gagal dijalankan: %s", exc)
        return {"passed": True, "corrections": [], "dijalankan": False}

    if not isinstance(hasil, dict):
        return {"passed": True, "corrections": [], "dijalankan": False}

    corrections = hasil.get("corrections")
    corrections = corrections if isinstance(corrections, list) else []
    bersih = []
    for c in corrections[:10]:
        if not isinstance(c, dict):
            continue
        keparahan = c.get("keparahan")
        bersih.append(
            {
                "jenis": str(c.get("jenis", ""))[:60],
                "keparahan": keparahan if keparahan in ("fatal", "minor") else "minor",
                "bagian": str(c.get("bagian", ""))[:60],
                "kutipan": str(c.get("kutipan", ""))[:200],
                "alasan": str(c.get("alasan", ""))[:300],
            }
        )

    ada_fatal = any(c["keparahan"] == "fatal" for c in bersih)
    passed = bool(hasil.get("passed", True)) and not ada_fatal

    if not passed:
        log.warning("Critic menolak narasi: %d koreksi fatal", sum(c["keparahan"] == "fatal" for c in bersih))
    return {"passed": passed, "corrections": bersih, "dijalankan": True}
