"""Metrik on-chain bernilai tinggi dari Coin Metrics community API.

Gratis, tanpa API key, tanpa pendaftaran. Ini metrik valuasi yang biasanya
dijual berlangganan oleh penyedia analitik on-chain.

Apa artinya masing-masing:

  MVRV        Kapitalisasi pasar dibagi realized cap. Mengukur berapa besar
              keuntungan belum terealisasi yang dipegang pasar. Historisnya
              > 3,5 menandai zona euforia, < 1 menandai harga di bawah
              biaya perolehan rata-rata pemegangnya.
  Realized    Nilai seluruh koin dihargai saat terakhir berpindah — semacam
  cap         "biaya perolehan" agregat jaringan.
  NVT         Kapitalisasi dibagi nilai transaksi harian. Analog rasio P/E:
              tinggi berarti harga jauh di atas pemakaian jaringan.
  Alamat      Alamat aktif harian. Proksi permintaan nyata, bukan spekulasi
  aktif       derivatif.
  Pasokan     Porsi pasokan yang tidak bergerak setahun terakhir. Naik =
  1 tahun     akumulasi pemegang jangka panjang.

Angka mentah dikembalikan apa adanya; penafsirannya diserahkan ke langkah
LLM, dan ambang di atas hanya dipakai kode untuk memberi label zona.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from ..utils.http import HttpError, get_json

log = logging.getLogger(__name__)

BASE = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"

METRIK = {
    "CapMVRVCur": "mvrv",
    "CapRealUSD": "realized_cap_usd",
    "NVTAdj": "nvt",
    "AdrActCnt": "alamat_aktif",
    "SplyAct1yr": "pasokan_aktif_1thn",
    "SplyCur": "pasokan_beredar",
    "CapMrktCurUSD": "market_cap_usd",
}


def _zona_mvrv(nilai: Optional[float]) -> Optional[str]:
    """Label zona MVRV. Ambang dari rentang historis, bukan prediksi."""
    if nilai is None:
        return None
    if nilai >= 3.5:
        return "euforia"
    if nilai >= 2.0:
        return "keuntungan_tinggi"
    if nilai >= 1.0:
        return "wajar"
    return "di_bawah_biaya_perolehan"


# Tier community tidak menyediakan semua metrik, dan daftarnya berubah dari
# waktu ke waktu. API menolak SELURUH permintaan kalau satu metrik saja tidak
# tersedia, tapi pesan errornya menyebut metrik mana — jadi metrik itu dibuang
# lalu permintaan diulang. Lebih baik kehilangan satu metrik daripada semuanya.
_POLA_METRIK_DITOLAK = re.compile(r"metric '([A-Za-z0-9_]+)'")


def _ambil_dengan_degradasi(metrik: List[str], hari: int) -> tuple:
    """Minta metrik; buang yang ditolak lalu ulangi. Return (baris, terpakai)."""
    tersisa = list(metrik)
    dibuang: List[str] = []

    for _ in range(len(metrik)):
        if not tersisa:
            break
        try:
            hasil = get_json(
                BASE,
                params={
                    "assets": "btc",
                    "metrics": ",".join(tersisa),
                    "frequency": "1d",
                    "page_size": hari,
                    "sort": "time",
                },
                timeout=45,
            )
            if dibuang:
                log.info(
                    "Metrik on-chain tidak tersedia di tier ini, dilewati: %s",
                    ", ".join(dibuang),
                )
            return hasil.get("data") or [], tersisa
        except HttpError as exc:
            cocok = _POLA_METRIK_DITOLAK.search(str(exc))
            if not cocok or cocok.group(1) not in tersisa:
                raise
            ditolak = cocok.group(1)
            tersisa.remove(ditolak)
            dibuang.append(ditolak)

    return [], []


def collect(hari: int = 30) -> Dict[str, Any]:
    """Ambil metrik terkini plus perubahannya sebulan terakhir."""
    data: Dict[str, Any] = {}
    try:
        baris, terpakai = _ambil_dengan_degradasi(list(METRIK), hari)
        if not baris:
            raise ValueError("Coin Metrics tidak mengembalikan metrik yang tersedia")
        data["metrik_tersedia"] = terpakai

        terkini = baris[-1]
        terlama = baris[0]

        for kunci_cm, kunci_kita in METRIK.items():
            nilai = terkini.get(kunci_cm)
            if nilai is None:
                continue
            try:
                data[kunci_kita] = float(nilai)
            except (TypeError, ValueError):
                continue

        # Perubahan sebulan untuk metrik yang arahnya bermakna.
        for kunci_cm, kunci_kita in METRIK.items():
            baru, lama = terkini.get(kunci_cm), terlama.get(kunci_cm)
            if baru is None or lama is None:
                continue
            try:
                baru_f, lama_f = float(baru), float(lama)
            except (TypeError, ValueError):
                continue
            if lama_f:
                data[f"{kunci_kita}_perubahan_30h_pct"] = round(
                    (baru_f - lama_f) / lama_f * 100, 2
                )

        # Kalau MVRV langsung tidak tersedia tapi kedua penyusunnya ada,
        # hitung sendiri — rumusnya memang kapitalisasi dibagi realized cap.
        if data.get("mvrv") is None and data.get("realized_cap_usd"):
            cap = data.get("market_cap_usd")
            if cap:
                data["mvrv"] = round(cap / data["realized_cap_usd"], 3)
                data["mvrv_dihitung_sendiri"] = True

        data["mvrv_zona"] = _zona_mvrv(data.get("mvrv"))

        # Porsi pasokan yang diam setahun — dinyatakan sebagai persen supaya
        # langsung terbaca tanpa perlu membagi manual.
        if data.get("pasokan_aktif_1thn") and data.get("pasokan_beredar"):
            diam = 1 - data["pasokan_aktif_1thn"] / data["pasokan_beredar"]
            data["pasokan_diam_1thn_pct"] = round(diam * 100, 2)

        log.info(
            "On-chain: MVRV %s (%s), NVT %s, alamat aktif %s",
            data.get("mvrv"), data.get("mvrv_zona"),
            data.get("nvt"), data.get("alamat_aktif"),
        )
        return {"data": data, "failed": []}

    except (HttpError, ValueError, KeyError, TypeError, IndexError) as exc:
        log.warning("Metrik on-chain Coin Metrics gagal: %s", exc)
        return {"data": {}, "failed": ["onchain_valuasi"]}
