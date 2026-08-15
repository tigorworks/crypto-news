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


def _potong(teks: str, maks: int) -> str:
    """Potong di batas kata supaya kalimat tidak terputus di tengah."""
    teks = (teks or "").strip()
    if len(teks) <= maks:
        return teks
    return teks[:maks].rsplit(" ", 1)[0] + "…"


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


def _blok_opsi(brief: Dict[str, Any]) -> List[str]:
    """Posisi opsi Deribit — cerminan taruhan institusional."""
    opsi = brief.get("options") or {}
    if not opsi:
        return []

    baris = ["", "🎯 <b>Opsi (Deribit)</b>"]

    potongan = []
    if opsi.get("dvol") is not None:
        arah = ""
        if opsi.get("dvol_perubahan_7h_pp") is not None:
            d = opsi["dvol_perubahan_7h_pp"]
            arah = f" ({'+' if d > 0 else ''}{_angka(d, 1)} pp/7h)"
        potongan.append(f"DVOL {_angka(opsi['dvol'], 1)}{arah}")
    if opsi.get("put_call_ratio_oi") is not None:
        potongan.append(f"Put/Call {_angka(opsi['put_call_ratio_oi'], 2)}")
    if potongan:
        baris.append(" · ".join(potongan))

    potongan = []
    if opsi.get("skew_put_call") is not None:
        s = opsi["skew_put_call"]
        makna = "proteksi turun lebih mahal" if s > 0 else "taruhan naik lebih mahal"
        potongan.append(f"Skew {'+' if s > 0 else ''}{_angka(s, 1)} ({makna})")
    if potongan:
        baris.append(" · ".join(potongan))

    if opsi.get("max_pain_expiry_terdekat"):
        baris.append(f"Max pain expiry terdekat: {_angka(opsi['max_pain_expiry_terdekat'], 0)}")
    return baris


def _blok_valuasi(brief: Dict[str, Any]) -> List[str]:
    """Valuasi on-chain — konteks jangka panjang, bukan sinyal harian."""
    oc = brief.get("onchain") or {}
    if not oc:
        return []

    baris = ["", "⛓ <b>Valuasi On-chain</b>"]
    potongan = []
    if oc.get("mvrv") is not None:
        zona = oc.get("mvrv_zona")
        label = f" ({zona.replace('_', ' ')})" if zona else ""
        potongan.append(f"MVRV {_angka(oc['mvrv'], 2)}{label}")
    if oc.get("nvt") is not None:
        potongan.append(f"NVT {_angka(oc['nvt'], 1)}")
    if potongan:
        baris.append(" · ".join(potongan))

    potongan = []
    if oc.get("alamat_aktif") is not None:
        ubah = oc.get("alamat_aktif_perubahan_30h_pct")
        tambahan = f" ({_persen(ubah, 1)}/30h)" if ubah is not None else ""
        potongan.append(f"Alamat aktif {_angka(oc['alamat_aktif'], 0)}{tambahan}")
    if oc.get("pasokan_diam_1thn_pct") is not None:
        potongan.append(f"Pasokan diam >1thn {_angka(oc['pasokan_diam_1thn_pct'], 1)}%")
    if potongan:
        baris.append(" · ".join(potongan))
    return baris if len(baris) > 2 else []


def _blok_aliran(brief: Dict[str, Any]) -> List[str]:
    """Premium Coinbase dan likuiditas stablecoin."""
    fl = brief.get("flows") or {}
    if not fl:
        return []

    baris = ["", "💵 <b>Aliran Dana</b>"]
    if fl.get("premium_coinbase_pct") is not None:
        p = fl["premium_coinbase_pct"]
        baris.append(
            f"Premium Coinbase {'+' if p > 0 else ''}{_angka(p, 3)}% "
            f"({esc(fl.get('premium_coinbase_label', ''))})"
        )
    if fl.get("stablecoin_cap_usd"):
        miliar = fl["stablecoin_cap_usd"] / 1e9
        ubah = fl.get("stablecoin_perubahan_24j_usd")
        tambahan = ""
        if ubah:
            juta = ubah / 1e6
            tambahan = f" ({'+' if juta > 0 else ''}${_angka(juta, 0)} jt/24j)"
        baris.append(f"Stablecoin ${_angka(miliar, 1)} miliar{tambahan}")
    return baris if len(baris) > 2 else []


def _blok_pernyataan(brief: Dict[str, Any], maks: int = 3) -> List[str]:
    """Pernyataan tokoh berpengaruh yang berpotensi menggerakkan pasar."""
    pernyataan = brief.get("statements") or []
    if not pernyataan:
        return []

    baris = ["", "🗣 <b>Pernyataan Berpengaruh</b>"]
    for s in pernyataan[:maks]:
        tokoh = esc(s.get("tokoh") or "Tidak disebutkan")
        isi = _potong(s.get("ringkasan") or s.get("kutipan") or "", 160)
        detail = []
        if s.get("dampak_btc"):
            detail.append(s["dampak_btc"])
        if s.get("kekuatan"):
            detail.append(f"kekuatan {s['kekuatan']}")
        # Rumor ditandai eksplisit supaya tidak terbaca seperti fakta.
        if s.get("status") == "rumor":
            detail.append("belum terkonfirmasi")
        akhiran = f" ({', '.join(detail)})" if detail else ""
        baris.append(f"• <b>{tokoh}</b>: {esc(isi)}{akhiran}")
    return baris


def _blok_whale(brief: Dict[str, Any]) -> List[str]:
    """Posisi whale vs ritel — angka mentah, belum ditafsirkan AI."""
    whale = brief.get("whale") or {}
    if whale.get("whale_long_pct") is None and whale.get("ritel_long_pct") is None:
        return []

    baris = ["", "🐋 <b>Posisi Besar vs Ritel</b>"]
    potongan = []
    if whale.get("whale_long_pct") is not None:
        potongan.append(f"Whale {_angka(whale['whale_long_pct'], 1)}% long")
    if whale.get("ritel_long_pct") is not None:
        potongan.append(f"Ritel {_angka(whale['ritel_long_pct'], 1)}% long")
    if potongan:
        baris.append(" · ".join(potongan))

    label = {
        "whale_distribusi": "Whale lebih defensif dari ritel",
        "whale_akumulasi": "Whale lebih agresif dari ritel",
        "selaras": "Posisi whale dan ritel selaras",
    }.get(whale.get("divergensi_label"))
    if label:
        baris.append(label)
    return baris


def _blok_sinyal_palsu(brief: Dict[str, Any], maks: int = 2) -> List[str]:
    """Pola manipulasi yang terdeteksi kode (bukan AI)."""
    sinyal = (brief.get("technical") or {}).get("sinyal_palsu") or []
    if not sinyal:
        return []
    baris = ["", "🎭 <b>Sinyal Perlu Diwaspadai</b>"]
    for s in sinyal[:maks]:
        baris.append(f"• {esc(s.get('keterangan', ''))}")
    return baris


def _blok_ai(brief: Dict[str, Any], paragraf_maks: int = 4) -> List[str]:
    ai = brief.get("ai") or {}
    critic = ai.get("critic") or {}

    baris = ["", PEMISAH]
    if not critic.get("passed", True):
        baris.append("⚠️ Analisa AI ditahan karena tidak lolos verifikasi.")
        baris.append(PEMISAH)
        return baris

    narasi = (ai.get("narrative_singkat") or "").strip()
    teknikal_ai = (ai.get("teknikal") or {}).get("ringkasan") or ""
    whale_ai = ai.get("whale") or {}
    outlook_ai = (ai.get("outlook") or {}).get("ringkasan") or ""

    if not any([narasi, teknikal_ai, whale_ai.get("ringkasan"), outlook_ai]):
        baris.append("✦ <b>ANALISA AI</b>")
        baris.append("<i>Analisa AI tidak tersedia pada run ini.</i>")
        baris.append(PEMISAH)
        return baris

    baris.append("✦ <b>ANALISA AI</b>")
    # Narasi lengkap dikirim beberapa paragraf, bukan cuma satu kalimat
    # pembuka — ruang 4096 karakter jauh lebih dari cukup, dan tangga
    # pemangkasan di bawah yang mengurus kalau ternyata kepanjangan.
    narasi_penuh = (ai.get("narrative") or "").strip()
    if narasi_penuh:
        paragraf = [p.strip() for p in narasi_penuh.split("\n\n") if p.strip()]
        for par in paragraf[:paragraf_maks]:
            baris.append(esc(_potong(par, 700)))
            baris.append("")
        if baris and baris[-1] == "":
            baris.pop()
    elif narasi:
        baris.append(esc(narasi))

    # Penyebab pergerakan — inti dari pertanyaan "kenapa naik/turun".
    penyebab = ai.get("penyebab_pergerakan") or []
    if penyebab:
        baris.append("")
        baris.append("<b>Penyebab utama:</b>")
        for p in penyebab[:4]:
            panah = {"naik": "↑", "turun": "↓"}.get(p.get("arah"), "·")
            keyakinan = p.get("keyakinan")
            tanda = {"tinggi": "", "sedang": " (keyakinan sedang)", "rendah": " (keyakinan rendah)"}.get(keyakinan, "")
            baris.append(f"{panah} <b>{esc(p.get('faktor', ''))}</b>{tanda}")
            if p.get("dasar"):
                baris.append(f"   <i>{esc(_potong(p['dasar'], 160))}</i>")

    tek = ai.get("teknikal") or {}
    if teknikal_ai:
        baris.append("")
        baris.append("<b>Teknikal:</b> " + esc(_potong(teknikal_ai, 500)))
        if tek.get("kontradiksi"):
            baris.append("⚠ " + esc(_potong(tek["kontradiksi"][0], 200)))
        if tek.get("pembatalan"):
            baris.append("<i>Batal bila: " + esc(_potong(tek["pembatalan"], 200)) + "</i>")

    if whale_ai.get("ringkasan"):
        waspada = whale_ai.get("tingkat_kewaspadaan")
        tanda = "⚠️ " if waspada == "tinggi" else ""
        baris.append("")
        baris.append(f"<b>Whale:</b> {tanda}" + esc(_potong(whale_ai["ringkasan"], 400)))
        for sp in (whale_ai.get("sinyal_palsu") or [])[:2]:
            if sp.get("keyakinan") in ("tinggi", "sedang"):
                baris.append(f"• {esc(sp.get('pola',''))}: {esc(_potong(sp.get('arti',''), 150))}")

    ol = ai.get("outlook") or {}
    if outlook_ai:
        baris.append("")
        horizon = f" ({esc(ol['horizon'])})" if ol.get("horizon") else ""
        baris.append(f"<b>Ke depan{horizon}:</b> " + esc(_potong(outlook_ai, 400)))
        for nama, kunci, panah in (("Menguat", "skenario_naik", "↑"), ("Melemah", "skenario_turun", "↓")):
            sk = ol.get(kunci) or {}
            pemicu = sk.get("pemicu") or []
            if pemicu:
                baris.append(f"{panah} <b>{nama}:</b> " + esc(_potong(", ".join(pemicu[:3]), 200)))
                if sk.get("kondisi"):
                    baris.append(f"   <i>syarat: {esc(_potong(sk['kondisi'], 130))}</i>")
        if ol.get("risiko_utama"):
            baris.append("⚠ <b>Risiko:</b> " + esc(_potong(ol["risiko_utama"][0], 200)))

    baris.append("")
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
    penutup = _blok_penutup(brief, site_url)

    # Tangga degradasi: yang dikorbankan lebih dulu adalah yang paling mudah
    # dibaca ulang di web. Blok AI dipertahankan sampai langkah terakhir
    # karena justru itu isi utama brief ini.
    # Tangga degradasi. Analisa AI adalah isi utama brief, jadi paragrafnya
    # dipertahankan lama; yang dikorbankan lebih dulu adalah daftar yang
    # gampang dibaca ulang di web.
    #  (berita, pernyataan, paragraf_ai, sinyal, whale, agenda, data_tambahan)
    tangga = [
        (4, 3, 4, True, True, True, True),
        (3, 3, 4, True, True, True, True),
        (3, 2, 3, True, True, True, True),
        (2, 2, 3, True, True, True, True),
        (2, 2, 2, True, True, True, True),
        (1, 1, 2, True, True, True, True),
        (0, 1, 2, False, True, True, True),
        (0, 1, 2, False, True, True, False),  # buang opsi/valuasi/aliran
        (0, 0, 2, False, False, True, False),
        (0, 0, 1, False, False, False, False),
    ]

    for berita_n, pernyataan_n, ai_n, sinyal, whale, agenda, tambahan in tangga:
        bagian = kepala + inti
        if tambahan:
            bagian += _blok_opsi(brief) + _blok_valuasi(brief) + _blok_aliran(brief)
        if whale:
            bagian += _blok_whale(brief)
        if sinyal:
            bagian += _blok_sinyal_palsu(brief)
        bagian += _blok_pernyataan(brief, pernyataan_n)
        bagian += _blok_berita(brief, berita_n)
        if agenda:
            bagian += _blok_agenda(brief)
        bagian += _blok_ai(brief, paragraf_maks=ai_n) + penutup

        pesan = "\n".join(bagian)
        if len(pesan) <= BATAS_KARAKTER:
            return pesan

    # Terakhir: pangkas isi blok AI sendiri, sisakan kepala + harga + penutup.
    dasar = "\n".join(kepala + _blok_harga(brief))
    sisa = BATAS_KARAKTER - len(dasar) - len("\n".join(penutup)) - 120
    ai_teks = "\n".join(_blok_ai(brief, paragraf_maks=1))
    if sisa > 200:
        ai_teks = ai_teks[:sisa].rsplit("\n", 1)[0] + f"\n…\n{PEMISAH}"
    else:
        ai_teks = ""
    pesan = "\n".join([dasar, ai_teks] + penutup)
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
