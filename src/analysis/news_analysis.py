"""Rangkaian panggilan LLM: filter -> klasifikasi -> mekanisme -> sintesis -> critic.

Ini workflow deterministik, bukan agent. LLM tidak memilih langkah maupun tool;
seluruh urutan, batasan, dan perhitungan angka ditentukan kode di sini.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from dateutil import parser as date_parser

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
        hasil = client.chat_json(models, system, user, step="filter", max_tokens=3000)
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
                max_tokens=2500,
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
            max_tokens=2500,
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
                        f"(\"{a['judul'][:80]}\") diikuti pergerakan harga {reaksi:+.2f}% "
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
                        f"sudah tinggi ({funding_rate * 100:.3f}% per 8 jam). Posisi long padat "
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
# LLM #4 — sintesis narasi
# --------------------------------------------------------------------------
def sintesis(
    client: LLMClient, models: List[str], konteks: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    system = (
        "Kamu penulis ringkasan pasar Bitcoin untuk pembaca Indonesia.\n\n"
        "Tulis narasi 3-5 paragraf yang MENJELASKAN kondisi pasar hari ini berdasarkan "
        "data yang diberikan. Rangkai teknikal, posisi pasar, makro, dan berita menjadi "
        "satu cerita yang nyambung. Sebutkan angka konkret dari data, jangan mengarang "
        "angka baru. Kalau ada sinyal yang bertentangan, sebut terus terang.\n\n"
        "Balas objek JSON dengan field:\n"
        "  narrative: string, 3-5 paragraf dipisah \\n\\n, bahasa Indonesia\n"
        "  dominant_themes: array 2-3 string pendek\n"
        "  narrative_shift: string, apa yang berubah dibanding brief sebelumnya (null kalau tidak ada data pembanding)\n"
        "  conflicts: array string, sinyal yang saling bertentangan (array kosong kalau tidak ada)\n\n"
        "Nada tulisan: tenang, deskriptif, tidak mengajak transaksi. "
        "Jangan menulis kalimat yang menyarankan pembaca membeli, menjual, atau menunggu "
        "harga tertentu.\n\n" + ATURAN_DASAR
    )
    try:
        hasil = client.chat_json(
            models,
            system,
            "Data hari ini:\n\n" + json.dumps(konteks, ensure_ascii=False, default=str),
            step="synthesis",
            temperature=0.4,
            max_tokens=2500,
        )
    except (LLMError, BudgetExceeded) as exc:
        log.warning("Sintesis narasi gagal: %s", exc)
        return None

    if not isinstance(hasil, dict) or not hasil.get("narrative"):
        log.warning("Sintesis mengembalikan struktur tak terduga")
        return None

    themes = hasil.get("dominant_themes")
    conflicts = hasil.get("conflicts")
    return {
        "narrative": str(hasil["narrative"]).strip(),
        "dominant_themes": [str(t)[:60] for t in themes[:3]] if isinstance(themes, list) else [],
        "narrative_shift": str(hasil["narrative_shift"])[:500] if hasil.get("narrative_shift") else "",
        "conflicts": [str(c)[:300] for c in conflicts[:5]] if isinstance(conflicts, list) else [],
    }


# --------------------------------------------------------------------------
# LLM #5 — critic
# --------------------------------------------------------------------------
def critic(
    client: LLMClient, models: List[str], narasi: str, data_mentah: Dict[str, Any]
) -> Dict[str, Any]:
    system = (
        "Kamu pemeriksa fakta yang ketat. Kamu diberi sebuah narasi pasar dan data mentah "
        "yang menjadi sumbernya. Periksa narasi terhadap data.\n\n"
        "Cari tiga jenis masalah:\n"
        "  1. angka_karangan: angka di narasi yang tidak ada di data mentah\n"
        "  2. sebab_akibat: klaim sebab-akibat yang tidak didukung data\n"
        "  3. saran_investasi: rekomendasi beli/jual, target harga, atau ajakan transaksi\n\n"
        "Kategori keparahan:\n"
        "  fatal  = angka karangan, atau saran investasi apa pun\n"
        "  minor  = klaim sebab-akibat yang terlalu percaya diri tapi tidak menyesatkan\n\n"
        "Balas objek JSON:\n"
        "  {\"passed\": bool, \"corrections\": [{\"jenis\": \"...\", \"keparahan\": \"fatal|minor\", "
        "\"kutipan\": \"...\", \"alasan\": \"...\"}]}\n\n"
        "passed bernilai false HANYA kalau ada koreksi berkeparahan fatal.\n"
        "Kalau narasi bersih, balas {\"passed\": true, \"corrections\": []}.\n\n" + ATURAN_DASAR
    )
    user = (
        "NARASI YANG DIPERIKSA:\n" + narasi + "\n\n"
        "DATA MENTAH SUMBERNYA:\n" + json.dumps(data_mentah, ensure_ascii=False, default=str)
    )

    try:
        hasil = client.chat_json(models, system, user, step="critic", temperature=0.0, max_tokens=1500)
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
                "kutipan": str(c.get("kutipan", ""))[:200],
                "alasan": str(c.get("alasan", ""))[:300],
            }
        )

    ada_fatal = any(c["keparahan"] == "fatal" for c in bersih)
    passed = bool(hasil.get("passed", True)) and not ada_fatal

    if not passed:
        log.warning("Critic menolak narasi: %d koreksi fatal", sum(c["keparahan"] == "fatal" for c in bersih))
    return {"passed": passed, "corrections": bersih, "dijalankan": True}
