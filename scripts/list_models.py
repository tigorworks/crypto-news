"""Daftar model OpenRouter yang tersedia sekarang, untuk memelihara config.yaml.

Katalog OpenRouter berubah cukup sering: model pensiun, slug berganti, harga
turun. Skrip ini menanyakan langsung ke API supaya kamu tidak perlu menebak.

Pakai:
    export OPENROUTER_API_KEY="sk-or-v1-..."

    python -m scripts.list_models                  # 40 model termurah
    python -m scripts.list_models --cari claude    # saring per nama/slug
    python -m scripts.list_models --maks-harga 1   # <= $1 per juta token input
    python -m scripts.list_models --cek            # periksa slug di config.yaml
    python -m scripts.list_models --urut output    # urut dari harga output

Endpoint /models bersifat publik, jadi tanpa API key pun tetap jalan.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
import yaml

MODELS_URL = "https://openrouter.ai/api/v1/models"
ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"


def ambil_model() -> List[Dict[str, Any]]:
    headers = {}
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    resp = requests.get(MODELS_URL, headers=headers, timeout=60)
    resp.raise_for_status()
    return resp.json().get("data", [])


def _harga(model: Dict[str, Any], jenis: str) -> Optional[float]:
    """Harga per JUTA token. OpenRouter mengembalikannya per token."""
    try:
        nilai = float((model.get("pricing") or {}).get(jenis))
    except (TypeError, ValueError):
        return None
    return nilai * 1_000_000


def _fmt(nilai: Optional[float]) -> str:
    if nilai is None:
        return "     ?"
    if nilai == 0:
        return "  0.00"
    return f"{nilai:6.3f}" if nilai < 10 else f"{nilai:6.2f}"


def tampilkan(models: List[Dict[str, Any]], batas: int) -> None:
    print(f"{'SLUG':<52} {'IN/Mtok':>8} {'OUT/Mtok':>9} {'KONTEKS':>9}")
    print("-" * 82)
    for m in models[:batas]:
        konteks = m.get("context_length") or 0
        print(
            f"{m.get('id', '?'):<52} "
            f"{_fmt(_harga(m, 'prompt')):>8} "
            f"{_fmt(_harga(m, 'completion')):>9} "
            f"{konteks:>9,}"
        )
    print(f"\n{len(models)} model cocok, {min(batas, len(models))} ditampilkan.")


def cek_config(models: List[Dict[str, Any]]) -> int:
    """Bandingkan slug di config.yaml dengan katalog yang sedang aktif."""
    if not CONFIG_PATH.exists():
        print(f"config.yaml tidak ditemukan di {CONFIG_PATH}", file=sys.stderr)
        return 1

    with CONFIG_PATH.open("r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}

    tersedia = {m.get("id") for m in models}
    llm = cfg.get("llm") or {}
    bermasalah = 0

    print(f"Memeriksa slug di config.yaml terhadap {len(tersedia)} model aktif\n")
    for step, daftar in llm.items():
        if not isinstance(daftar, list):
            continue
        status = []
        for slug in daftar:
            if str(slug).upper().startswith("ISI-"):
                status.append(f"  ○ {slug}  (masih placeholder, step dilewati)")
                bermasalah += 1
            elif slug in tersedia:
                status.append(f"  ✓ {slug}")
            else:
                status.append(f"  ✗ {slug}  TIDAK ADA di katalog")
                bermasalah += 1
        print(f"{step}:")
        print("\n".join(status))

    print()
    if bermasalah:
        print(f"{bermasalah} entri perlu diperbaiki.")
        print("Cari pengganti dengan: python -m scripts.list_models --cari <kata>")
    else:
        print("Semua slug valid.")
    return 1 if bermasalah else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Daftar model OpenRouter")
    parser.add_argument("--cari", default="", help="saring berdasarkan slug atau nama")
    parser.add_argument("--maks-harga", type=float, default=None,
                        help="harga input maksimum per juta token, dalam USD")
    parser.add_argument("--urut", choices=["input", "output", "konteks"], default="input",
                        help="dasar pengurutan (default: input)")
    parser.add_argument("--batas", type=int, default=40, help="jumlah baris ditampilkan")
    parser.add_argument("--gratis", action="store_true", help="hanya model berharga 0")
    parser.add_argument("--cek", action="store_true",
                        help="periksa slug di config.yaml, bukan menampilkan katalog")
    args = parser.parse_args()

    try:
        models = ambil_model()
    except requests.RequestException as exc:
        print(f"Gagal mengambil katalog OpenRouter: {exc}", file=sys.stderr)
        return 1

    if args.cek:
        return cek_config(models)

    kata = args.cari.lower()
    if kata:
        models = [
            m for m in models
            if kata in str(m.get("id", "")).lower() or kata in str(m.get("name", "")).lower()
        ]
    if args.maks_harga is not None:
        models = [
            m for m in models
            if (_harga(m, "prompt") is not None and _harga(m, "prompt") <= args.maks_harga)
        ]
    if args.gratis:
        models = [m for m in models if _harga(m, "prompt") == 0]

    kunci = {
        "input": lambda m: (_harga(m, "prompt") is None, _harga(m, "prompt") or 0),
        "output": lambda m: (_harga(m, "completion") is None, _harga(m, "completion") or 0),
        "konteks": lambda m: -(m.get("context_length") or 0),
    }[args.urut]
    models.sort(key=kunci)

    tampilkan(models, args.batas)
    return 0


if __name__ == "__main__":
    sys.exit(main())
