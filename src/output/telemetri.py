"""Metrik per run yang BERTAHAN lintas hari, di luar arsip brief.

Kenapa terpisah dari `docs/data/archive/`: arsip dipangkas oleh retensi dan
ikut terhapus setiap kali data direset, dan bersamanya hilang kemampuan
melihat TREN. Pertanyaan paling penting tentang kesehatan pipeline ini —
"seberapa sering critic menahan narasi?", "berapa biaya rata-rata per run?",
"sumber mana yang paling sering gagal?", "apakah siaga jendela benar-benar
mendahului penurunan harga?" — semuanya tidak bisa dijawab dari satu berkas
brief, sekaya apa pun isinya.

Bentuknya JSONL: satu baris per run, ditambahkan di ujung. Sederhana, tahan
terhadap tulis yang terpotong (baris rusak dilewati, sisanya tetap terbaca),
dan mudah dibaca ulang tanpa parsing seluruh berkas ke satu objek raksasa.

Dua berkas dihasilkan:

  state/telemetri.jsonl   — catatan mentah, TIDAK ikut terbit ke web
  docs/data/telemetri.json — ringkasan siap baca untuk halaman + operator

Ringkasannya sengaja tidak memuat apa pun yang bersifat pribadi: hanya
angka biaya, token, durasi, status critic, sumber yang gagal, dan tingkat
siaga beserta harga saat itu.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from dateutil import parser as date_parser

from ..config import DATA_DIR, STATE_DIR
from ..utils.timezone import iso_utc, now_utc, to_utc

log = logging.getLogger(__name__)

CATATAN_PATH = STATE_DIR / "telemetri.jsonl"
RINGKASAN_PATH = DATA_DIR / "telemetri.json"

#: Baris yang disimpan. 400 run harian ≈ lebih dari setahun; cukup panjang
#: untuk melihat tren musiman, cukup pendek untuk tetap ringan dibaca dan
#: di-commit tiap hari.
MAKS_BARIS = 400

#: Jendela yang dipakai untuk semua ringkasan. Lebih panjang dari ini,
#: perubahan konfigurasi lama (model, plafon biaya) mulai mencemari rata-rata.
JENDELA_RINGKASAN = 60

#: Jarak minimum sebuah run berikutnya boleh dianggap "sehari kemudian" saat
#: mengukur apakah siaga jendela diikuti pergerakan harga. Brief terbit sekali
#: sehari, tapi run manual bisa menyelip beberapa jam setelahnya — dan
#: membandingkan harga dengan selisih dua jam bukan menguji apa pun.
_MIN_JAM_LANJUTAN = 18


def _baca_baris(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    catatan: List[Dict[str, Any]] = []
    for baris in path.read_text(encoding="utf-8").splitlines():
        baris = baris.strip()
        if not baris:
            continue
        try:
            isi = json.loads(baris)
        except json.JSONDecodeError:
            # Satu baris rusak (run yang mati di tengah tulis) tidak boleh
            # menghanguskan seluruh riwayat.
            log.warning("Baris telemetri rusak dilewati")
            continue
        if isinstance(isi, dict):
            catatan.append(isi)
    return catatan


def _waktu(catatan: Dict[str, Any]):
    try:
        return to_utc(date_parser.parse(str(catatan.get("waktu_utc"))))
    except (ValueError, TypeError):
        return None


def rekam(
    *,
    brief: Dict[str, Any],
    ringkasan_llm: Optional[Dict[str, Any]] = None,
    panggilan_llm: Optional[List[Dict[str, Any]]] = None,
    budget_maks_usd: float = 0.0,
    feed_gagal: Optional[List[str]] = None,
    path: Path = CATATAN_PATH,
) -> Dict[str, Any]:
    """Tambahkan satu baris metrik untuk run ini; kembalikan barisnya."""
    kualitas = brief.get("data_quality") or {}
    ai = brief.get("ai") or {}
    critic = ai.get("critic") or {}
    agen = brief.get("agen_kebijakan") or {}

    # Biaya per langkah — inti dari pertanyaan "boros di sebelah mana".
    # Sebelum ini hanya total per run yang tersimpan, jadi setiap usaha
    # menghemat berjalan di atas tebakan.
    per_langkah: Dict[str, float] = {}
    # TOKEN per langkah, bukan cuma biaya. Tanpa ini pertanyaan "kenapa
    # sintesis mahal" cuma bisa dijawab dengan tebakan: biaya $0,156 bisa
    # berarti konteks masuk yang kegemukan, keluaran yang kepanjangan, atau
    # token penalaran yang tidak terlihat di brief sama sekali — dan
    # ketiganya menuntut perbaikan yang sama sekali berbeda.
    #
    # `keluar_tak_terlihat` adalah selisih antara token keluaran yang
    # DITAGIH dan yang benar-benar mendarat di brief. Pada model penalar,
    # selisih itulah token penalarannya.
    token_langkah: Dict[str, Dict[str, int]] = {}
    # MODEL per langkah. Sebelum ini tidak ada satu berkas pun yang menyimpan
    # model apa yang BENAR-BENAR melayani tiap langkah: `models_used` hanya
    # hidup di memori (LLMClient.ringkasan()) dan tidak pernah ikut ke
    # latest.json maupun ke sini. Akibatnya jatuhnya sebuah langkah ke model
    # cadangan tidak terlihat di mana pun.
    #
    # Itu bukan risiko teoretis. Perpindahan langkah penyiapan data ke
    # DeepSeek menurunkan harga campurannya dari ~$2,40 jadi ~$0,30 per juta
    # token — delapan kali lipat. Kalau suatu hari DeepSeek menolak dan
    # semuanya diam-diam jatuh ke cadangan Haiku, biayanya naik sebesar itu
    # juga, dan satu-satunya pertanda adalah tagihan di akhir bulan.
    #
    # Disimpan sebagai daftar, bukan satu nilai: satu langkah bisa dibatch
    # (classify 5 panggilan, statements ~5) dan sebagian batch bisa jatuh ke
    # cadangan sementara sisanya tidak.
    model_langkah: Dict[str, list] = {}
    for panggilan in panggilan_llm or []:
        langkah = str(panggilan.get("step") or "?")
        per_langkah[langkah] = round(
            per_langkah.get(langkah, 0.0) + float(panggilan.get("cost_usd") or 0.0), 6
        )
        t = token_langkah.setdefault(
            langkah, {"masuk": 0, "keluar": 0, "panggilan": 0, "prompt_char": 0}
        )
        t["masuk"] += int(panggilan.get("tokens_in") or 0)
        t["keluar"] += int(panggilan.get("tokens_out") or 0)
        # Panjang prompt yang dikirim, pendamping `masuk` yang ditagih.
        # Rasio prompt_char/masuk yang wajar untuk teks Indonesia ~3,5-4;
        # jauh di bawah itu berarti selisihnya bukan dari payload kita.
        t["prompt_char"] += int(panggilan.get("prompt_chars") or 0)
        t["panggilan"] += 1
        model = panggilan.get("model")
        if model and model not in model_langkah.setdefault(langkah, []):
            model_langkah[langkah].append(str(model))

    biaya = float(kualitas.get("llm_cost_usd") or 0.0)
    catatan = {
        "waktu_utc": brief.get("generated_at") or iso_utc(now_utc()),
        "run_type": brief.get("run_type"),
        "harga": (brief.get("price") or {}).get("last"),
        "biaya_usd": round(biaya, 5),
        "biaya_per_langkah": per_langkah,
        "token_per_langkah": token_langkah,
        "model_per_langkah": model_langkah,
        "budget_maks_usd": round(float(budget_maks_usd or 0.0), 4),
        "budget_terpakai_pct": (
            round(biaya / budget_maks_usd * 100, 1) if budget_maks_usd else None
        ),
        "panggilan_llm": int((ringkasan_llm or {}).get("jumlah_panggilan") or 0),
        "token_masuk": int(kualitas.get("llm_token_masuk") or 0),
        "token_keluar": int(kualitas.get("llm_token_keluar") or 0),
        "durasi_detik": kualitas.get("durasi_detik"),
        "corong_berita": kualitas.get("berita_corong") or {},
        "sumber_gagal": list(kualitas.get("failed_sources") or []),
        "feed_gagal": list(feed_gagal or []),
        "critic": {
            "dijalankan": bool(critic.get("dijalankan", False)),
            "lolos": bool(critic.get("passed", True)),
            "bagian_ditahan": list(ai.get("bagian_ditahan") or []),
            # Jenis temuan yang menahan — supaya "sering ditahan" bisa
            # dipecah jadi "karena angka karangan" vs sebab lain.
            "jenis_temuan": sorted({
                str(c.get("jenis"))
                for c in (critic.get("corrections") or [])
                if c.get("jenis")
            }),
        },
        # Risiko jendela + kerapuhan, keduanya hitungan kode. Dipakai menguji
        # apakah alarmnya benar-benar mendahului pergerakan harga.
        "siaga": {
            "risiko_jendela": (agen.get("risiko_jendela") or {}).get("tingkat"),
            "fase": (agen.get("jendela") or {}).get("fase"),
            "kerapuhan": (agen.get("kerapuhan") or {}).get("tingkat"),
        },
    }

    lama = _baca_baris(path)
    lama.append(catatan)
    if len(lama) > MAKS_BARIS:
        lama = lama[-MAKS_BARIS:]

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(b, ensure_ascii=False) + "\n" for b in lama), encoding="utf-8"
    )
    return catatan


def _riwayat_siaga(catatan: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Tiap siaga beserta APA YANG TERJADI pada harga sesudahnya.

    Ini satu-satunya cara mengetahui apakah panel jendela risiko benar-benar
    berguna atau cuma terasa berguna: siaga yang tidak pernah diikuti apa-apa
    adalah alarm yang layak dimatikan, dan tanpa catatan lintas hari
    perbandingan itu mustahil dibuat.

    Pembandingnya run BERIKUTNYA yang jaraknya cukup jauh (>= 18 jam), bukan
    run terdekat: brief terbit sekali sehari, tapi run manual bisa menyelip
    beberapa jam setelahnya dan selisih harga dua jam tidak menguji apa pun.
    """
    hasil: List[Dict[str, Any]] = []
    for i, c in enumerate(catatan):
        tingkat = (c.get("siaga") or {}).get("risiko_jendela")
        harga = c.get("harga")
        waktu = _waktu(c)
        if not tingkat or not harga or waktu is None:
            continue

        sesudah = None
        for lanjutan in catatan[i + 1 :]:
            waktu_lanjutan = _waktu(lanjutan)
            if waktu_lanjutan is None or not lanjutan.get("harga"):
                continue
            if (waktu_lanjutan - waktu).total_seconds() >= _MIN_JAM_LANJUTAN * 3600:
                sesudah = lanjutan
                break

        baris = {
            "waktu_utc": c["waktu_utc"],
            "tingkat": tingkat,
            "kerapuhan": (c.get("siaga") or {}).get("kerapuhan"),
            "fase": (c.get("siaga") or {}).get("fase"),
            "harga": harga,
            "harga_sesudah": None,
            "perubahan_pct": None,
        }
        if sesudah:
            baris["harga_sesudah"] = sesudah["harga"]
            baris["perubahan_pct"] = round(
                (float(sesudah["harga"]) - float(harga)) / float(harga) * 100, 2
            )
        hasil.append(baris)
    return hasil


def _rata(nilai: List[float]) -> Optional[float]:
    bersih = [float(x) for x in nilai if x is not None]
    return round(sum(bersih) / len(bersih), 4) if bersih else None


def ringkas(path: Path = CATATAN_PATH, keluaran: Path = RINGKASAN_PATH) -> Dict[str, Any]:
    """Rangkum catatan mentah jadi berkas ringkas untuk web dan operator."""
    semua = _baca_baris(path)
    catatan = semua[-JENDELA_RINGKASAN:]
    if not catatan:
        return {}

    biaya = [c.get("biaya_usd") for c in catatan if c.get("biaya_usd")]
    durasi = [c.get("durasi_detik") for c in catatan if c.get("durasi_detik")]

    # Critic: berapa sering ia BENAR-BENAR memeriksa, dan berapa sering
    # pemeriksaan itu berujung menahan sebagian analisa. Keduanya dibedakan
    # karena critic yang gagal dijalankan dilaporkan sebagai "lolos".
    diperiksa = [c for c in catatan if (c.get("critic") or {}).get("dijalankan")]
    menahan = [c for c in diperiksa if not (c["critic"] or {}).get("lolos")]
    bagian_ditahan: Dict[str, int] = {}
    for c in menahan:
        for bagian in (c.get("critic") or {}).get("bagian_ditahan") or []:
            bagian_ditahan[bagian] = bagian_ditahan.get(bagian, 0) + 1

    langkah: Dict[str, List[float]] = {}
    token: Dict[str, Dict[str, List[int]]] = {}
    for c in catatan:
        for nama, nilai in (c.get("biaya_per_langkah") or {}).items():
            langkah.setdefault(nama, []).append(float(nilai))
        for nama, t in (c.get("token_per_langkah") or {}).items():
            simpul = token.setdefault(nama, {"masuk": [], "keluar": []})
            simpul["masuk"].append(int(t.get("masuk") or 0))
            simpul["keluar"].append(int(t.get("keluar") or 0))

    gagal: Dict[str, int] = {}
    for c in catatan:
        for sumber in list(c.get("sumber_gagal") or []) + [
            f"feed:{f}" for f in (c.get("feed_gagal") or [])
        ]:
            gagal[sumber] = gagal.get(sumber, 0) + 1

    riwayat = _riwayat_siaga(catatan)
    per_tingkat: Dict[str, List[float]] = {}
    for r in riwayat:
        if r["perubahan_pct"] is not None:
            per_tingkat.setdefault(r["tingkat"], []).append(r["perubahan_pct"])

    ambang_peringatan = [
        c for c in catatan
        if (c.get("budget_terpakai_pct") or 0) >= 85
    ]

    ringkasan = {
        "dibuat": iso_utc(now_utc()),
        "jumlah_run": len(catatan),
        "total_run_tersimpan": len(semua),
        "sejak": catatan[0].get("waktu_utc"),
        "biaya": {
            "rata_usd": _rata(biaya),
            "maks_usd": max(biaya) if biaya else None,
            "terakhir_usd": catatan[-1].get("biaya_usd"),
            "run_di_atas_85_persen_budget": len(ambang_peringatan),
        },
        # Diurutkan dari yang paling mahal: ini daftar yang dilihat lebih dulu
        # saat mencari di mana penghematan berikutnya masuk akal.
        "biaya_per_langkah": sorted(
            (
                {
                    "langkah": nama,
                    "rata_usd": _rata(nilai),
                    "run": len(nilai),
                    "total_usd": round(sum(nilai), 5),
                    "rata_token_masuk": _rata(token.get(nama, {}).get("masuk", [])),
                    "rata_token_keluar": _rata(token.get(nama, {}).get("keluar", [])),
                }
                for nama, nilai in langkah.items()
            ),
            key=lambda x: x["total_usd"],
            reverse=True,
        ),
        "durasi": {
            "rata_detik": _rata(durasi),
            "maks_detik": max(durasi) if durasi else None,
        },
        "critic": {
            "dijalankan": len(diperiksa),
            "menahan": len(menahan),
            "persen_menahan": (
                round(len(menahan) / len(diperiksa) * 100, 1) if diperiksa else None
            ),
            "bagian_tersering": dict(
                sorted(bagian_ditahan.items(), key=lambda x: x[1], reverse=True)
            ),
        },
        "sumber_paling_sering_gagal": [
            {"sumber": nama, "run": jumlah}
            for nama, jumlah in sorted(gagal.items(), key=lambda x: x[1], reverse=True)[:8]
        ],
        # Riwayat siaga: alarm apa yang menyala, dan apa yang terjadi setelahnya.
        "riwayat_siaga": riwayat[-30:],
        "siaga_vs_harga": [
            {
                "tingkat": tingkat,
                "jumlah": len(nilai),
                "rata_perubahan_pct": _rata(nilai),
                "turun_lebih_2_persen": len([x for x in nilai if x <= -2]),
            }
            for tingkat, nilai in sorted(per_tingkat.items())
        ],
    }

    keluaran.parent.mkdir(parents=True, exist_ok=True)
    with keluaran.open("w", encoding="utf-8") as fh:
        json.dump(ringkasan, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return ringkasan
