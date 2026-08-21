"""Data posisi pasar: Fear & Greed, on-chain, dan arus ETF.

Semua sumber di sini boleh gagal. Kegagalan dicatat sebagai nama sumber yang
dikembalikan lewat `failed`, lalu pipeline lanjut.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from ..utils.http import HttpError, get_json, get_text
from . import sosovalue

log = logging.getLogger(__name__)

FNG_URL = "https://api.alternative.me/fng/?limit=2"
HASHRATE_URL = "https://mempool.space/api/v1/mining/hashrate/3d"
FEES_URL = "https://mempool.space/api/v1/fees/recommended"
FARSIDE_URL = "https://farside.co.uk/bitcoin-etf-flow-all-data/"
GLOBAL_URL = "https://api.coingecko.com/api/v3/global"

# Farside di belakang Cloudflare: User-Agent skrip ditolak, browser diterima.
HEADER_BROWSER = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

FNG_LABEL_ID = {
    "extreme fear": "Ketakutan Ekstrem",
    "fear": "Ketakutan",
    "neutral": "Netral",
    "greed": "Keserakahan",
    "extreme greed": "Keserakahan Ekstrem",
}


def _fear_greed() -> Dict[str, Any]:
    data = get_json(FNG_URL)
    items = data.get("data") or []
    if not items:
        raise ValueError("respons Fear & Greed kosong")
    current = items[0]
    value = int(current["value"])
    label_en = str(current.get("value_classification", "")).strip()
    previous = int(items[1]["value"]) if len(items) > 1 else None
    return {
        "value": value,
        "label": FNG_LABEL_ID.get(label_en.lower(), label_en),
        "previous": previous,
    }


def _hashrate() -> Dict[str, Any]:
    data = get_json(HASHRATE_URL)
    current = data.get("currentHashrate")
    difficulty = data.get("currentDifficulty")
    if current is None:
        raise ValueError("respons hashrate tidak memuat currentHashrate")
    return {
        # dikonversi ke EH/s supaya angkanya enak dibaca
        "hashrate_ehs": round(float(current) / 1e18, 2),
        "difficulty": float(difficulty) if difficulty is not None else None,
    }


def _fees() -> Dict[str, Any]:
    data = get_json(FEES_URL)
    return {
        "fee_fastest_sat_vb": data.get("fastestFee"),
        "fee_hour_sat_vb": data.get("hourFee"),
    }


def _parse_farside_amount(cell: str) -> Optional[float]:
    """Ubah sel tabel Farside ('142.3', '(58.1)', '-') jadi angka juta USD."""
    text = cell.strip().replace(",", "").replace("$", "")
    if not text or text in {"-", "—", "N/A"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    try:
        value = float(text)
    except ValueError:
        return None
    return -value if negative else value


#: Selisih relatif yang masih dianggap "sama" antara dua sumber arus ETF.
#: Keduanya menjumlahkan kumpulan ETF yang sama, jadi seharusnya nyaris
#: identik; 5% memberi ruang untuk pembulatan dan revisi kecil satu emiten
#: tanpa membiarkan perbedaan yang benar-benar berarti lolos diam-diam.
_TOLERANSI_SELISIH_ETF = 0.05

#: Di bawah nilai ini selisih relatif tidak bermakna: pada hari arus mendekati
#: nol ($3 juta vs -$1 juta), persentase selisihnya meledak tanpa satu pun
#: sumber benar-benar bermasalah.
_MIN_NILAI_BANDING_ETF = 20_000_000


def _verifikasi_silang_etf(utama: Dict[str, Any]) -> Dict[str, Any]:
    """Bandingkan angka arus ETF dengan sumber kedua.

    Arus ETF adalah salah satu angka yang paling sering dikutip pembaca dan
    salah satu yang paling rapuh cara pengambilannya: satu API berbayar dan
    satu scrape halaman HTML. Selama ini tidak ada pemeriksaan silang sama
    sekali — kalau salah satunya rusak (kolom bergeser, satuan berubah dari
    juta ke ribu), angkanya tetap terbit dengan percaya diri.

    Pembandingnya best-effort dan MURAH: timeout pendek, tanpa retry. Farside
    memang sering menolak IP pusat data, dan itu bukan kegagalan yang perlu
    dilaporkan — hasilnya cuma "tidak ada pembanding".

    Yang dikembalikan hanya CATATAN. Angka utamanya tidak pernah diganti
    diam-diam oleh pembanding: kalau keduanya berbeda jauh, yang benar adalah
    memberi tahu, bukan menebak siapa yang betul.
    """
    if utama.get("etf_flow_usd") is None:
        return {}
    try:
        # Timeout lebih pendek daripada saat Farside jadi sumber utama:
        # di sini ia cuma pembanding, dan pemeriksaan silang tidak boleh
        # menambah lama run untuk sumber yang biasanya menolak.
        pembanding = _etf_flow_farside(timeout=8)
    except (HttpError, ValueError, KeyError, TypeError) as exc:
        log.debug("Pembanding arus ETF tidak tersedia: %s", exc)
        return {"etf_flow_verifikasi": {"status": "tanpa_pembanding"}}

    a = float(utama["etf_flow_usd"])
    b = float(pembanding["etf_flow_usd"])
    selisih = a - b
    acuan = max(abs(a), abs(b))
    if acuan < _MIN_NILAI_BANDING_ETF:
        status = "terlalu_kecil_untuk_dibandingkan"
    elif abs(selisih) / acuan <= _TOLERANSI_SELISIH_ETF:
        status = "cocok"
    else:
        status = "berbeda"
        log.warning(
            "Arus ETF BERBEDA antar sumber: %s $%.0f vs Farside $%.0f (tanggal %s vs %s)",
            utama.get("etf_flow_sumber", "sumber utama"), a, b,
            utama.get("etf_flow_date"), pembanding.get("etf_flow_date"),
        )

    return {
        "etf_flow_verifikasi": {
            "status": status,
            "pembanding_sumber": "Farside",
            "pembanding_usd": b,
            "pembanding_tanggal": pembanding.get("etf_flow_date"),
            "selisih_usd": round(selisih, 0),
            "selisih_pct": round(selisih / acuan * 100, 1) if acuan else None,
            # Tanggal yang berbeda menjelaskan sebagian besar selisih besar:
            # satu sumber sudah memuat hari baru, satunya belum.
            "tanggal_sama": str(utama.get("etf_flow_date") or "")
            == str(pembanding.get("etf_flow_date") or ""),
        }
    }


def _etf_flow(soso_api_key: Optional[str] = None) -> Dict[str, Any]:
    """Total arus ETF BTC harian terakhir, plus hasil pemeriksaan silangnya.

    SoSoValue (API resmi) dicoba lebih dulu kalau key-nya tersedia — Farside
    di belakang Cloudflare dan menolak IP pusat data secara PERMANEN dari
    GitHub Actions (403 "Just a moment..."), jadi tanpa SoSoValue sumber ini
    gagal di hampir setiap run produksi. Farside tetap dipertahankan sebagai
    cadangan kalau SoSoValue sendiri sedang bermasalah atau key belum diisi.
    """
    if soso_api_key:
        try:
            hasil = sosovalue.fetch_etf_flow(soso_api_key)
            log.info(
                "Arus ETF diambil dari SoSoValue (tanggal %s)", hasil["etf_flow_date"]
            )
            hasil["etf_flow_sumber"] = "SoSoValue"
            hasil.update(_verifikasi_silang_etf(hasil))
            return hasil
        except (HttpError, ValueError, KeyError, TypeError) as exc:
            log.warning("SoSoValue gagal (%s), coba Farside", exc)

    hasil = _etf_flow_farside()
    hasil["etf_flow_sumber"] = "Farside"
    # Tidak ada pemeriksaan silang di sini: pembandingnya adalah sumber yang
    # sama, dan membandingkan angka dengan dirinya sendiri cuma menghasilkan
    # rasa aman yang palsu.
    hasil["etf_flow_verifikasi"] = {"status": "tanpa_pembanding"}
    return hasil


def _etf_flow_farside(timeout: int = 20) -> Dict[str, Any]:
    """Scrape total arus harian terakhir dari tabel Farside.

    Farside adalah halaman HTML biasa tanpa API. Struktur tabelnya bisa berubah
    kapan saja, jadi parsing di sini sengaja longgar: cari baris data terakhir
    yang tanggalnya valid, lalu ambil kolom total di ujung baris.

    Header di bawah meniru browser sungguhan. Farside berada di belakang
    Cloudflare, dan permintaan dengan User-Agent skrip dari IP pusat data
    ditolak 403 — itu yang membuat sumber ini gagal terus di produksi.
    """
    # Timeout pendek dan TANPA retry. Ini scrape pihak ketiga yang boleh gagal,
    # dan kalau koneksinya menggantung (bukan ditolak) retry default membuat
    # satu sumber opsional menahan seluruh pipeline sampai ~2 menit.
    html = get_text(FARSIDE_URL, timeout=timeout, retries=0, headers=HEADER_BROWSER)
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL | re.IGNORECASE)

    for row in reversed(rows):
        cells = [
            re.sub(r"<[^>]+>", "", c).strip()
            for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL | re.IGNORECASE)
        ]
        if len(cells) < 3:
            continue
        # Baris data diawali tanggal bergaya "15 Aug 2026" atau "2026-08-15".
        if not re.match(r"^\d{1,2}\s+\w{3}\s+\d{4}$|^\d{4}-\d{2}-\d{2}$", cells[0]):
            continue
        total = _parse_farside_amount(cells[-1])
        if total is None:
            continue
        return {
            "etf_flow_usd": round(total * 1_000_000, 0),
            "etf_flow_date": cells[0],
        }

    raise ValueError("tidak menemukan baris arus ETF yang bisa diparsing")


def _dominasi_btc() -> Dict[str, Any]:
    """Porsi kapitalisasi BTC dari total kapitalisasi seluruh kripto.

    Penanda rezim: dominance naik + harga BTC naik = uang mengalir KE BTC
    (altcoin melemah relatif). Dominance turun + harga BTC naik = risk-on
    lebih luas, uang mengalir ke seluruh pasar termasuk altcoin. Tanpa angka
    ini, brief tidak bisa membedakan "ini gerakan BTC" dari "ini gerakan
    seluruh kripto yang BTC ikut terbawa".
    """
    data = get_json(GLOBAL_URL, timeout=15, retries=1)
    persen = ((data.get("data") or {}).get("market_cap_percentage") or {}).get("btc")
    if persen is None:
        raise ValueError("respons CoinGecko /global tidak memuat market_cap_percentage.btc")
    return {"btc_dominance_pct": round(float(persen), 2)}


def collect(symbol: str, soso_api_key: Optional[str] = None) -> Dict[str, Any]:
    """Kumpulkan semua data pasar. Return dict + daftar sumber yang gagal."""
    result: Dict[str, Any] = {
        "fear_greed": None,
        "hashrate": None,
        "difficulty": None,
        "fee_fastest_sat_vb": None,
        "fee_hour_sat_vb": None,
        "etf_flow_usd": None,
        "etf_flow_date": None,
        # Sumber yang benar-benar melayani angka di atas, dan hasil
        # pemeriksaan silangnya terhadap sumber kedua (lihat
        # _verifikasi_silang_etf).
        "etf_flow_sumber": None,
        "etf_flow_verifikasi": {"status": "tanpa_pembanding"},
        # Ditandai True oleh pipeline kalau angkanya dipakai ulang dari brief
        # sebelumnya karena scrape hari ini gagal.
        "etf_flow_kedaluwarsa": False,
        "btc_dominance_pct": None,
    }
    failed: List[str] = []

    try:
        result["fear_greed"] = _fear_greed()
    except (HttpError, ValueError, KeyError, TypeError) as exc:
        log.warning("Fear & Greed gagal: %s", exc)
        failed.append("fear_greed")

    try:
        onchain = _hashrate()
        result["hashrate"] = onchain["hashrate_ehs"]
        result["difficulty"] = onchain["difficulty"]
    except (HttpError, ValueError, KeyError, TypeError) as exc:
        log.warning("Hashrate gagal: %s", exc)
        failed.append("onchain")

    try:
        result.update(_fees())
    except (HttpError, ValueError, KeyError, TypeError) as exc:
        log.warning("Fee mempool gagal: %s", exc)
        if "onchain" not in failed:
            failed.append("onchain_fees")

    try:
        result.update(_etf_flow(soso_api_key))
    except (HttpError, ValueError, KeyError, TypeError) as exc:
        log.warning("Arus ETF gagal diparsing: %s", exc)
        failed.append("etf_flow")

    try:
        result.update(_dominasi_btc())
    except (HttpError, ValueError, KeyError, TypeError) as exc:
        log.warning("Dominasi BTC gagal: %s", exc)
        failed.append("btc_dominance")

    return {"data": result, "failed": failed}
