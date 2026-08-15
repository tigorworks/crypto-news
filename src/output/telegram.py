"""Render dan kirim ringkasan ke Telegram (parse_mode HTML)."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..utils.http import HttpError, post_json

log = logging.getLogger(__name__)

BATAS_KARAKTER = 4096
PEMISAH = "━━━━━━━━━━━━━━"


def esc(text: Any) -> str:
    """Escape untuk parse_mode HTML Telegram: hanya &, <, > yang perlu."""
    if text is None:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _angka(value: Optional[float], desimal: int = 0, prefix: str = "", suffix: str = "") -> str:
    """Format angka gaya Indonesia: titik ribuan, koma desimal."""
    if value is None:
        return "—"
    try:
        teks = f"{float(value):,.{desimal}f}"
    except (TypeError, ValueError):
        return "—"
    teks = teks.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return f"{prefix}{teks}{suffix}"


def _persen(value: Optional[float], desimal: int = 2) -> str:
    if value is None:
        return "—"
    tanda = "+" if value > 0 else ""
    return f"{tanda}{_angka(value, desimal)}%"


def _blok_harga(brief: Dict[str, Any]) -> List[str]:
    price = brief.get("price") or {}
    levels = (brief.get("technical") or {}).get("key_levels") or {}
    support = levels.get("support") or []
    resistance = levels.get("resistance") or []

    baris = [
        "💰 <b>Harga</b>",
        f"{_angka(price.get('last'), 0, prefix='$')} ({_persen(price.get('change_24h_pct'), 1)} / 24j)",
    ]
    if support or resistance:
        bagian = []
        if support:
            bagian.append(f"Support {_angka(support[0], 0)}")
        if resistance:
            bagian.append(f"Resistance {_angka(resistance[0], 0)}")
        baris.append(" · ".join(bagian))
    return baris


def _blok_teknikal(brief: Dict[str, Any]) -> List[str]:
    tf = (brief.get("technical") or {}).get("1d") or {}
    if not tf:
        return []

    momentum = tf.get("momentum") or {}
    tren = tf.get("tren") or {}
    levels = (brief.get("technical") or {}).get("key_levels") or {}

    potongan = []
    if momentum.get("rsi") is not None:
        potongan.append(f"RSI {_angka(momentum['rsi'], 0)}")
    if momentum.get("macd_histogram") is not None:
        potongan.append(f"MACD {'positif' if momentum['macd_histogram'] > 0 else 'negatif'}")
    posisi = (tren.get("posisi") or {}).get("ema50")
    if posisi:
        potongan.append(f"{'Di atas' if posisi == 'di_atas' else 'Di bawah'} EMA50")

    baris = ["", "📈 <b>Teknikal (1D)</b>"]
    if potongan:
        baris.append(" · ".join(potongan))
    if levels.get("invalidasi_naik"):
        baris.append(f"Skenario naik batal di bawah {_angka(levels['invalidasi_naik'], 0)}")

    catatan = []
    if momentum.get("divergensi_rsi"):
        catatan.append(f"divergensi RSI {momentum['divergensi_rsi']}")
    if (tf.get("volatilitas") or {}).get("bb_squeeze"):
        catatan.append("Bollinger menyempit")
    if catatan:
        baris.append("Catatan: " + ", ".join(catatan))
    return baris


def _blok_pasar(brief: Dict[str, Any]) -> List[str]:
    market = brief.get("market") or {}
    teknikal = brief.get("technical") or {}
    baris = ["", "🌊 <b>Posisi Pasar</b>"]

    potongan = []
    funding = market.get("funding_rate")
    if funding is not None:
        potongan.append(f"Funding {_persen(funding * 100, 3)}")
    if teknikal.get("oi_change_pct") is not None:
        arah = "naik" if teknikal["oi_change_pct"] > 0 else "turun"
        potongan.append(f"OI {arah} {_angka(abs(teknikal['oi_change_pct']), 1)}%")
    if potongan:
        baris.append(" · ".join(potongan))

    potongan = []
    if market.get("etf_flow_usd") is not None:
        juta = market["etf_flow_usd"] / 1_000_000
        tanda = "+" if juta > 0 else ""
        potongan.append(f"ETF flow {tanda}${_angka(juta, 1)} jt")
    fg = market.get("fear_greed") or {}
    if fg.get("value") is not None:
        potongan.append(f"Fear &amp; Greed {fg['value']} ({esc(fg.get('label'))})")
    if potongan:
        baris.append(" · ".join(potongan))

    if market.get("hashrate"):
        baris.append(f"Hashrate {_angka(market['hashrate'], 0)} EH/s")
    return baris if len(baris) > 2 else []


def _blok_makro(brief: Dict[str, Any]) -> List[str]:
    macro = brief.get("macro") or {}
    potongan = []
    if macro.get("dxy") is not None:
        potongan.append(f"DXY {_angka(macro['dxy'], 1)}")
    if macro.get("ust10y") is not None:
        potongan.append(f"UST10Y {_angka(macro['ust10y'], 2)}%")
    if macro.get("wti") is not None:
        potongan.append(f"WTI ${_angka(macro['wti'], 1)}")
    if macro.get("vix") is not None:
        potongan.append(f"VIX {_angka(macro['vix'], 1)}")
    if not potongan:
        return []
    return ["", "🌍 <b>Makro</b>", " · ".join(potongan)]


def _blok_berita(brief: Dict[str, Any], maks: int = 5) -> List[str]:
    berita = brief.get("news") or []
    berperingkat = [n for n in berita if n.get("kekuatan")]
    berperingkat.sort(
        key=lambda n: (n.get("kekuatan") or 0) * (n.get("relevansi_btc") or 0), reverse=True
    )
    if not berperingkat:
        return []

    baris = ["", "📰 <b>Berita Utama</b>"]
    for i, n in enumerate(berperingkat[:maks], 1):
        judul = esc(n["judul"][:110])
        detail = []
        if n.get("sentimen"):
            detail.append(n["sentimen"])
        if n.get("kekuatan"):
            detail.append(f"kekuatan {n['kekuatan']}")
        keterangan = f" — {', '.join(detail)}" if detail else ""
        baris.append(f"{i}. {judul}{keterangan}")
    return baris


def _blok_agenda(brief: Dict[str, Any], maks: int = 3) -> List[str]:
    agenda = brief.get("calendar") or []
    if not agenda:
        return []
    baris = ["", "📅 <b>Agenda</b>"]
    for acara in agenda[:maks]:
        tanda = "~" if acara.get("perkiraan") else ""
        baris.append(f"{esc(acara['waktu_wib'])} · {tanda}{esc(acara['nama'])}")
    return baris


def _blok_ai(brief: Dict[str, Any]) -> List[str]:
    ai = brief.get("ai") or {}
    critic = ai.get("critic") or {}

    baris = ["", PEMISAH]
    if not critic.get("passed", True):
        baris.append("⚠️ Analisa AI ditahan karena tidak lolos verifikasi.")
        baris.append(PEMISAH)
        return baris

    narasi = (ai.get("narrative_singkat") or "").strip()
    if not narasi:
        baris.append("✦ <b>ANALISA AI</b>")
        baris.append("<i>Analisa AI tidak tersedia pada run ini.</i>")
        baris.append(PEMISAH)
        return baris

    baris.append("✦ <b>ANALISA AI</b>")
    baris.append(esc(narasi))
    baris.append("<i>Dihasilkan AI, dapat keliru.</i>")
    baris.append(PEMISAH)
    return baris


def _blok_penutup(brief: Dict[str, Any], site_url: str) -> List[str]:
    baris = [""]
    conflicts = brief.get("conflicts") or []
    if conflicts:
        pertama = conflicts[0]
        teks = pertama.get("keterangan") if isinstance(pertama, dict) else str(pertama)
        baris.append(f"⚠️ Sinyal bertentangan: {esc(teks[:200])}")

    dq = brief.get("data_quality") or {}
    baris.append(
        f"📊 Kualitas data: {esc(dq.get('confidence', '—'))} "
        f"({dq.get('sources_ok', 0)}/{dq.get('sources_total', 0)} sumber)"
    )
    if site_url:
        baris.append("")
        baris.append(f"🔗 Selengkapnya: {esc(site_url)}")
    baris.append("<i>Informasi, bukan saran investasi.</i>")
    return baris


def render(brief: Dict[str, Any], site_url: str = "") -> str:
    """Susun pesan HTML. Kalau kepanjangan, bagian berita dipangkas lebih dulu."""
    kepala = [
        "📊 <b>Ringkasan Pasar Bitcoin</b>",
        f"🕐 {esc(brief.get('generated_at_wib', ''))}",
        "",
    ]
    inti = (
        _blok_harga(brief)
        + _blok_teknikal(brief)
        + _blok_pasar(brief)
        + _blok_makro(brief)
    )
    ekor = _blok_agenda(brief) + _blok_ai(brief) + _blok_penutup(brief, site_url)

    # Bagian berita adalah satu-satunya yang boleh dipangkas.
    for jumlah_berita in (5, 3, 2, 1, 0):
        pesan = "\n".join(kepala + inti + _blok_berita(brief, jumlah_berita) + ekor)
        if len(pesan) <= BATAS_KARAKTER:
            return pesan

    # Kalau tanpa berita pun masih kepanjangan, potong keras di batas aman.
    pesan = "\n".join(kepala + inti + ekor)
    if len(pesan) > BATAS_KARAKTER:
        pesan = pesan[: BATAS_KARAKTER - 60].rsplit("\n", 1)[0] + "\n\n…\n<i>Pesan dipotong.</i>"
    return pesan


def kirim(token: str, chat_id: str, pesan: str) -> bool:
    """Kirim pesan. Return True kalau berhasil; kegagalan tidak melempar exception."""
    try:
        hasil = post_json(
            f"https://api.telegram.org/bot{token}/sendMessage",
            {
                "chat_id": chat_id,
                "text": pesan,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=30,
        )
        if hasil.get("ok"):
            log.info("Pesan Telegram terkirim (%d karakter)", len(pesan))
            return True
        log.error("Telegram menolak pesan: %s", hasil)
        return False
    except HttpError as exc:
        log.error("Gagal mengirim Telegram: %s", exc)
        return False
