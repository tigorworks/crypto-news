"""Susun brief.json, hitung diff vs brief sebelumnya, dan kelola arsip."""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from dateutil import parser as date_parser

from ..config import ARCHIVE_DIR, DATA_DIR
from ..utils.timezone import (
    format_wib,
    format_wib_singkat,
    iso_utc,
    now_utc,
    run_type,
    slug_arsip,
    to_utc,
)

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
DISCLAIMER = "Konten ini bersifat informasional dan bukan saran investasi."

# Semua sumber yang dihitung dalam skor kualitas data.
SUMBER_DIPANTAU = [
    "price", "technical", "funding_oi", "fear_greed",
    "onchain", "etf_flow", "macro", "news",
]


def baca_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Gagal membaca %s: %s", path, exc)
        return None


def tulis_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


# --------------------------------------------------------------------------
# Kualitas data
# --------------------------------------------------------------------------
def hitung_kualitas(failed: List[str], llm_cost: float, catatan: List[str]) -> Dict[str, Any]:
    gagal = sorted(set(failed))
    total = len(SUMBER_DIPANTAU)
    ok = total - len([f for f in gagal if f in SUMBER_DIPANTAU])
    rasio = ok / total if total else 0

    if rasio >= 0.875:
        confidence = "baik"
    elif rasio >= 0.625:
        confidence = "sedang"
    else:
        confidence = "rendah"

    return {
        "sources_ok": ok,
        "sources_total": total,
        "failed_sources": gagal,
        "confidence": confidence,
        "llm_cost_usd": round(llm_cost, 5),
        "catatan": catatan,
    }


# --------------------------------------------------------------------------
# Diff vs brief sebelumnya
# --------------------------------------------------------------------------
def _bulat(value: float) -> float:
    """Pembulatan adaptif.

    Funding rate berada di orde 1e-4; membulatkannya ke 2 desimal akan
    mengubah semua nilainya jadi 0,0. Nilai kecil karena itu diberi
    presisi lebih.
    """
    if value != 0 and abs(value) < 1:
        return round(value, 6)
    return round(value, 2)


def _delta(sekarang: Optional[float], sebelum: Optional[float]) -> Optional[Dict[str, Any]]:
    if sekarang is None or sebelum is None:
        return None
    selisih = sekarang - sebelum
    pct = (selisih / sebelum * 100) if sebelum else None
    return {
        "sekarang": _bulat(sekarang),
        "sebelumnya": _bulat(sebelum),
        "selisih": _bulat(selisih),
        "selisih_pct": round(pct, 2) if pct is not None else None,
    }


def hitung_diff(baru: Dict[str, Any], lama: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Bandingkan brief baru dengan brief sebelumnya."""
    if not lama:
        return {"tersedia": False, "alasan": "Belum ada brief sebelumnya untuk dibandingkan."}

    diff: Dict[str, Any] = {"tersedia": True}
    diff["dibanding"] = lama.get("generated_at_wib") or lama.get("generated_at")

    diff["harga"] = _delta(
        (baru.get("price") or {}).get("last"), (lama.get("price") or {}).get("last")
    )
    diff["sentimen"] = _delta(
        (baru.get("aggregate") or {}).get("sentiment_score"),
        (lama.get("aggregate") or {}).get("sentiment_score"),
    )
    diff["funding_rate"] = _delta(
        (baru.get("market") or {}).get("funding_rate"),
        (lama.get("market") or {}).get("funding_rate"),
    )
    diff["fear_greed"] = _delta(
        ((baru.get("market") or {}).get("fear_greed") or {}).get("value"),
        ((lama.get("market") or {}).get("fear_greed") or {}).get("value"),
    )
    diff["rsi_1d"] = _delta(
        ((baru.get("technical") or {}).get("1d", {}).get("momentum") or {}).get("rsi"),
        ((lama.get("technical") or {}).get("1d", {}).get("momentum") or {}).get("rsi"),
    )

    tema_lama = set((lama.get("aggregate") or {}).get("dominant_themes") or [])
    tema_baru = set((baru.get("aggregate") or {}).get("dominant_themes") or [])
    diff["tema_baru"] = sorted(tema_baru - tema_lama)
    diff["tema_hilang"] = sorted(tema_lama - tema_baru)

    judul_lama = {n.get("judul") for n in (lama.get("news") or [])}
    diff["berita_baru"] = len([
        n for n in (baru.get("news") or []) if n.get("judul") not in judul_lama
    ])

    return diff


def ringkas_diff(diff: Dict[str, Any]) -> List[str]:
    """Ubah diff jadi daftar kalimat pendek untuk web dan Telegram."""
    if not diff.get("tersedia"):
        return []

    baris: List[str] = []
    harga = diff.get("harga")
    if harga and harga.get("selisih_pct"):
        arah = "naik" if harga["selisih"] > 0 else "turun"
        baris.append(f"Harga {arah} {abs(harga['selisih_pct']):.2f}% sejak brief sebelumnya.")

    sentimen = diff.get("sentimen")
    if sentimen and sentimen["selisih"]:
        arah = "menguat" if sentimen["selisih"] > 0 else "melemah"
        baris.append(
            f"Skor sentimen berita {arah} dari {sentimen['sebelumnya']:.0f} ke {sentimen['sekarang']:.0f}."
        )

    fg = diff.get("fear_greed")
    if fg and fg["selisih"]:
        arah = "naik" if fg["selisih"] > 0 else "turun"
        baris.append(f"Fear & Greed {arah} {abs(fg['selisih']):.0f} poin ke {fg['sekarang']:.0f}.")

    if diff.get("tema_baru"):
        baris.append("Tema baru muncul: " + ", ".join(diff["tema_baru"]) + ".")
    if diff.get("tema_hilang"):
        baris.append("Tema yang mereda: " + ", ".join(diff["tema_hilang"]) + ".")
    if diff.get("berita_baru"):
        baris.append(f"{diff['berita_baru']} berita baru masuk daftar pantauan.")

    return baris


# --------------------------------------------------------------------------
# Perakitan brief
# --------------------------------------------------------------------------
def bersihkan_berita(articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Buang field internal supaya JSON publik tetap ramping dan aman."""
    keluaran = []
    for a in articles:
        keluaran.append(
            {
                "id": a.get("id"),
                "judul": a.get("judul"),
                "sumber": a.get("sumber"),
                "url": a.get("url"),
                "waktu_utc": a.get("waktu_utc"),
                "kategori": a.get("kategori"),
                "relevansi_btc": a.get("relevansi_btc"),
                "sentimen": a.get("sentimen"),
                "kekuatan": a.get("kekuatan"),
                "horizon": a.get("horizon"),
                "status_kepastian": a.get("status_kepastian"),
                "jalur_transmisi": a.get("jalur_transmisi"),
                "mekanisme": a.get("mekanisme"),
                "entitas": a.get("entitas") or [],
                "sudah_priced_in": a.get("sudah_priced_in"),
                "tipe_klaim": a.get("tipe_klaim"),
                "kredibilitas_sumber": a.get("kredibilitas_sumber"),
                "jumlah_konfirmasi": a.get("jumlah_konfirmasi", 1),
                "reaksi_harga_1j": a.get("reaksi_harga_1j"),
                "catatan_reaksi": a.get("catatan_reaksi"),
            }
        )
    return keluaran


def build_brief(
    *,
    price: Dict[str, Any],
    technical: Dict[str, Any],
    market: Dict[str, Any],
    macro: Dict[str, Any],
    news: List[Dict[str, Any]],
    aggregate: Dict[str, Any],
    calendar: List[Dict[str, Any]],
    conflicts: List[Dict[str, Any]],
    ai: Dict[str, Any],
    data_quality: Dict[str, Any],
    price_series: Optional[List[Dict[str, Any]]] = None,
    previous: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    sekarang = now_utc()
    brief: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": iso_utc(sekarang),
        "generated_at_wib": format_wib(sekarang),
        "run_type": run_type(sekarang),
        "data_quality": data_quality,
        "price": price,
        # Deret harga ringkas khusus untuk grafik di halaman web.
        "price_series": price_series or [],
        "technical": technical,
        "market": market,
        "macro": macro,
        "news": bersihkan_berita(news),
        "aggregate": aggregate,
        "calendar": calendar,
        "conflicts": conflicts,
        "diff_vs_previous": {},
        "ai": ai,
        "disclaimer": DISCLAIMER,
    }

    diff = hitung_diff(brief, previous)
    diff["ringkasan"] = ringkas_diff(diff)
    brief["diff_vs_previous"] = diff
    return brief


# --------------------------------------------------------------------------
# Penulisan file
# --------------------------------------------------------------------------
def brief_sebelumnya(data_dir: Path = DATA_DIR) -> Optional[Dict[str, Any]]:
    return baca_json(data_dir / "latest.json")


def _perbarui_index(
    brief: Dict[str, Any], nama_file: str, retention_days: int, data_dir: Path, archive_dir: Path
) -> Dict[str, Any]:
    index = baca_json(data_dir / "index.json") or {"items": []}
    items = [i for i in index.get("items", []) if i.get("file") != f"archive/{nama_file}"]

    items.insert(
        0,
        {
            "file": f"archive/{nama_file}",
            "waktu_wib": format_wib_singkat(now_utc()),
            "waktu_utc": brief["generated_at"],
            "harga": (brief.get("price") or {}).get("last"),
            "sentimen": (brief.get("aggregate") or {}).get("sentiment_score"),
        },
    )

    # Buang entri yang lebih tua dari retensi, sekaligus hapus filenya.
    batas = now_utc() - timedelta(days=retention_days)
    disimpan = []
    for item in items:
        try:
            waktu = to_utc(date_parser.parse(item["waktu_utc"]))
        except (KeyError, ValueError, TypeError):
            disimpan.append(item)
            continue
        if waktu < batas:
            usang = archive_dir / Path(item["file"]).name
            if usang.exists():
                usang.unlink()
                log.info("Arsip usang dihapus: %s", usang.name)
            continue
        disimpan.append(item)

    disimpan.sort(key=lambda i: i.get("waktu_utc", ""), reverse=True)
    return {"updated_at": brief["generated_at"], "items": disimpan}


def tulis_output(
    brief: Dict[str, Any],
    retention_days: int = 90,
    data_dir: Path = DATA_DIR,
    archive_dir: Path = ARCHIVE_DIR,
    tulis_arsip: bool = True,
) -> Dict[str, str]:
    """Tulis latest.json, arsip, dan index.json. Return path yang ditulis."""
    data_dir.mkdir(parents=True, exist_ok=True)
    ditulis: Dict[str, str] = {}

    tulis_json(data_dir / "latest.json", brief)
    ditulis["latest"] = str(data_dir / "latest.json")

    if tulis_arsip:
        archive_dir.mkdir(parents=True, exist_ok=True)
        nama_file = f"{slug_arsip()}.json"
        tulis_json(archive_dir / nama_file, brief)
        ditulis["archive"] = str(archive_dir / nama_file)

        index = _perbarui_index(brief, nama_file, retention_days, data_dir, archive_dir)
        tulis_json(data_dir / "index.json", index)
        ditulis["index"] = str(data_dir / "index.json")

    return ditulis
