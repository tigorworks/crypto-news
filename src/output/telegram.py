"""Render dan kirim ringkasan ke Telegram (parse_mode HTML)."""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional

from ..utils.http import HttpError, post_json

log = logging.getLogger(__name__)

BATAS_KARAKTER = 4096

# Emoji penanda blok, dikumpulkan di satu tempat supaya himpunannya bisa
# diperiksa sekaligus, bukan tersebar di belasan f-string.
#
# ATURANNYA: tiap emoji harus punya ARTI di konteks pasar — bukan hiasan.
# Yang dibuang karena cuma dekoratif: 🌊 (ombak) untuk posisi pasar, 🌍
# (bola dunia) untuk makro dan geopolitik, 🎯 (panah dart) untuk opsi, 🗣
# (kepala bicara) untuk pernyataan, 🎭 (topeng teater) untuk sinyal palsu,
# 🕐 (jam) untuk timestamp, dan 👋 (lambaian) untuk sapaan pelanggan.
#
# Yang dipertahankan justru yang paling kripto: 🐋 whale sudah jadi istilah
# baku di pasar ini, dan ⛓ rantai adalah lambang on-chain itu sendiri.
EMOJI = {
    "merek":       "📊",   # grafik batang — identitas pasar
    "harga":       "💰",
    "teknikal":    "📈",
    "posisi":      "⚖️",   # keseimbangan long vs short, funding, open interest
    "makro":       "🏦",   # bank sentral, suku bunga, dolar
    "opsi":        "💹",   # papan harga — derivatif
    "onchain":     "⛓",
    "aliran":      "💵",
    "whale":       "🐋",   # istilah baku pasar kripto
    "jebakan":     "🎣",   # umpan — bull trap / bear trap
    "agenda":      "📅",   # kalender ekonomi
    "pernyataan":  "📣",   # pengumuman yang menggerakkan harga
    "berita":      "📰",
    "regulasi":    "🏛",   # lembaga & kebijakan
    "mendesak":    "🚨",
    "dampak":      "🔴",
    "risiko":      "⚠",
    "tautan":      "🔗",
}
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


def _uang_bertanda(value: Optional[float], desimal: int = 1, suffix: str = "") -> str:
    """Uang dengan tanda +/- DI DEPAN simbol mata uang.

    Menempelkan angka negatif langsung ke "$" menghasilkan "$-57,6 jt" —
    terbaca seperti salah ketik, bukan arus keluar. Muncul di dua tempat
    berbeda (arus ETF dan perubahan kapitalisasi stablecoin), jadi dijadikan
    satu helper supaya tidak terulang di tempat ketiga.
    """
    if value is None:
        return "—"
    tanda = "+" if value > 0 else ("-" if value < 0 else "")
    return f"{tanda}${_angka(abs(value), desimal)}{suffix}"


def _persen(value: Optional[float], desimal: int = 2) -> str:
    if value is None:
        return "—"
    tanda = "+" if value > 0 else ""
    return f"{tanda}{_angka(value, desimal)}%"


# Istilah internal -> bahasa yang dimengerti pembaca umum. "kekuatan 4"
# tidak berarti apa-apa bagi orang yang tidak membaca dokumentasi kita.
DAMPAK = {
    1: "dampak kecil", 2: "dampak terbatas", 3: "dampak sedang",
    4: "dampak besar", 5: "dampak sangat besar",
}
ARAH_HARGA = {
    "bullish": "cenderung mengangkat harga",
    "bearish": "cenderung menekan harga",
    "netral": "dampak dua arah",
}
STATUS_PERNYATAAN = {
    "verbatim": "pernyataan langsung",
    "dilaporkan_media": "dilaporkan media",
    "rumor": "belum terkonfirmasi",
}
KEPASTIAN = {
    "rumor": "masih rumor",
    "belum_dikonfirmasi": "belum dikonfirmasi",
    "dikonfirmasi": "sudah dikonfirmasi",
    "sudah_terjadi": "sudah terjadi",
    "terjadwal": "terjadwal",
}


def _label_dampak(kekuatan: Optional[int]) -> str:
    return DAMPAK.get(kekuatan or 0, "")


def _label_arah(sentimen: Optional[str]) -> str:
    return ARAH_HARGA.get(sentimen or "", "")


def _potong(teks: str, maks: int) -> str:
    """Potong di batas kata supaya kalimat tidak terputus di tengah."""
    teks = (teks or "").strip()
    if len(teks) <= maks:
        return teks
    return teks[:maks].rsplit(" ", 1)[0] + "…"


# Sisipan bergaya "(jam lagi 66,2)" adalah NAMA FIELD internal (`jam_lagi`)
# yang bocor ke prosa model. Dibersihkan di kode, bukan lewat prompt: pola ini
# muncul berulang dengan angka berbeda, dan penegakan lewat prompt tidak pernah
# konvergen di repo ini.
_POLA_BOCOR_FIELD = re.compile(
    r"\s*\((?:jam|hari)[ _]lagi[^)]*\)", re.IGNORECASE
)


def _bersihkan_kapan(teks: str) -> str:
    """Buang sisipan nama field dari keterangan waktu agenda."""
    return _POLA_BOCOR_FIELD.sub("", teks or "").strip()


def _rapikan_kosong(baris: List[str]) -> List[str]:
    """Ciutkan baris kosong beruntun jadi satu, dan buang yang di ujung.

    Blok AI dirakit dari belasan bagian yang masing-masing boleh absen (ditahan
    critic, gagal dihasilkan, atau memang kosong). Tiap bagian menambahkan
    pemisah kosongnya sendiri, jadi begitu satu bagian absen pemisahnya
    bertumpuk — di produksi ini tampil sebagai dua baris kosong menganga tepat
    setelah baris pergerakan saat narasi ditahan. Dibereskan sekali di sini,
    bukan ditambal di tiap tempat yang menambahkan "".
    """
    hasil: List[str] = []
    for b in baris:
        if b == "" and (not hasil or hasil[-1] == ""):
            continue
        hasil.append(b)
    while hasil and hasil[-1] == "":
        hasil.pop()
    return hasil


def _blok_harga(brief: Dict[str, Any]) -> List[str]:
    price = brief.get("price") or {}
    levels = (brief.get("technical") or {}).get("key_levels") or {}
    support = levels.get("support") or []
    resistance = levels.get("resistance") or []

    baris = [
        f"{EMOJI['harga']} <b>Harga</b>",
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
    baris = ["", f"{EMOJI['posisi']} <b>Posisi Pasar</b>"]

    potongan = []
    funding = market.get("funding_rate")
    if funding is not None:
        persen_funding = funding * 100
        # Funding rate kerap sangat kecil (mendekati 0,0001%). Dibulatkan ke 3
        # desimal, itu tampil sebagai "0,000%" — angka yang benar tapi
        # kelihatan seperti bug. Di bawah ambang itu, tulis apa adanya:
        # mendekati nol, dan sisi pembayarnya memang tidak berarti apa-apa.
        if round(abs(persen_funding), 3) < 0.001:
            potongan.append("Funding mendekati 0% (netral, tidak ada tekanan dominan)")
        else:
            sisi = "pemegang long yang membayar" if funding > 0 else "pemegang short yang membayar"
            potongan.append(f"Funding {_persen(persen_funding, 3)} ({sisi})")
    if teknikal.get("oi_change_pct") is not None:
        arah = "naik" if teknikal["oi_change_pct"] > 0 else "turun"
        potongan.append(f"OI {arah} {_angka(abs(teknikal['oi_change_pct']), 1)}%")
    if potongan:
        baris.append(" · ".join(potongan))

    potongan = []
    if market.get("etf_flow_usd") is not None:
        juta = market["etf_flow_usd"] / 1_000_000
        potongan.append("ETF flow " + _uang_bertanda(juta, 1, " jt"))
    fg = market.get("fear_greed") or {}
    if fg.get("value") is not None:
        potongan.append(f"Fear &amp; Greed {fg['value']} ({esc(fg.get('label'))})")
    if potongan:
        baris.append(" · ".join(potongan))

    # Likuidasi 24 jam. Sisinya diterjemahkan ("posisi beli/jual"), dan
    # bursanya disebut — angka satu bursa tidak boleh terbaca seperti
    # likuidasi seluruh pasar.
    if market.get("likuidasi_total_usd"):
        juta = market["likuidasi_total_usd"] / 1_000_000
        baris.append(
            f"Likuidasi 24j ${_angka(juta, 1)} jt · "
            f"beli ${_angka(market.get('likuidasi_long_usd', 0) / 1_000_000, 1)} jt vs "
            f"jual ${_angka(market.get('likuidasi_short_usd', 0) / 1_000_000, 1)} jt "
            f"<i>({esc(market.get('likuidasi_sumber') or 'satu bursa')})</i>"
        )

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
    if macro.get("usdjpy") is not None:
        potongan.append(f"USD/JPY {_angka(macro['usdjpy'], 1)}")
    if not potongan:
        return []
    return ["", f"{EMOJI['makro']} <b>Makro</b>", " · ".join(potongan)]


def _blok_berita(brief: Dict[str, Any], maks: int = 5) -> List[str]:
    berita = brief.get("news") or []
    berperingkat = [n for n in berita if n.get("kekuatan")]
    berperingkat.sort(
        key=lambda n: (n.get("kekuatan") or 0) * (n.get("relevansi_btc") or 0), reverse=True
    )
    # `maks <= 0` berarti tangga degradasi memutuskan blok ini dikorbankan.
    # Tanpa penjagaan ini, judulnya tetap tercetak dengan isi kosong —
    # tampil di produksi sebagai "📰 Berita Utama" yang menggantung tanpa
    # satu pun berita di bawahnya.
    if not berperingkat or maks <= 0:
        return []

    baris = ["", f"{EMOJI['berita']} <b>Berita Utama</b>"]
    for i, n in enumerate(berperingkat[:maks], 1):
        # Judul terjemahan dipakai kalau ada, supaya seluruh pesan satu bahasa.
        judul_berita = n.get("judul_id") or n.get("judul") or ""
        baris.append(f"{i}. {esc(judul_berita[:110])}")
        detail = [t for t in (_label_arah(n.get("sentimen")), _label_dampak(n.get("kekuatan"))) if t]
        if n.get("status_kepastian") in ("rumor", "belum_dikonfirmasi"):
            detail.append(KEPASTIAN[n["status_kepastian"]])
        if detail:
            baris.append("   <i>" + esc(" · ".join(detail)) + "</i>")
    return baris


def _notice_agenda_mendesak(brief: Dict[str, Any], maks: int = 3) -> List[str]:
    """Notice mencolok kalau ada agenda BERDAMPAK BESAR dalam <24 jam.

    Ditaruh di kepala pesan (lihat render_terpisah), bukan di _blok_agenda
    biasa — supaya tidak mungkin tenggelam di tengah pesan, ikut terpotong
    tangga degradasi saat pesan kepanjangan, atau ditata ulang oleh stylist.
    """
    agenda = brief.get("calendar") or []
    mendesak = [
        a for a in agenda
        if a.get("jam_lagi") is not None and a["jam_lagi"] < 24
        and (a.get("relevansi_kripto") or 0) >= 4
    ]
    if not mendesak:
        return []
    baris = [f"{EMOJI['mendesak']} <b>AGENDA PENTING &lt;24 JAM</b>"]
    for a in mendesak[:maks]:
        baris.append(f"• <b>{esc(a['nama'])}</b> — {esc(a.get('waktu_wib', ''))}")
    return baris


def _blok_agenda(brief: Dict[str, Any], maks: int = 4) -> List[str]:
    """Agenda terdekat. Horizonnya 30 hari, tapi yang dikirim ke Telegram
    hanya beberapa yang paling dekat — sisanya bisa dilihat di web."""
    agenda = brief.get("calendar") or []
    if not agenda:
        return []
    baris = ["", f"{EMOJI['agenda']} <b>Agenda Terdekat</b>"]
    for acara in agenda[:maks]:
        tanda = "~" if acara.get("perkiraan") else ""
        # Acara berdampak besar ke kripto diberi penanda supaya tidak
        # tenggelam di antara rilis data rutin yang nyaris tidak berpengaruh.
        relevansi = acara.get("relevansi_kripto") or 0
        awalan = f"{EMOJI['dampak']} " if relevansi >= 4 else ""
        baris.append(f"{awalan}{esc(acara['waktu_wib'])} · {tanda}{esc(acara['nama'])}")
        # Jalur transmisinya hanya ditulis untuk yang benar-benar berdampak —
        # kalau semua acara diberi penjelasan, blok ini jadi terlalu panjang
        # dan justru mengaburkan mana yang penting.
        if relevansi >= 4 and acara.get("jalur"):
            baris.append(f"   <i>{esc(_potong(acara['jalur'], 180))}</i>")
    sisa = len(agenda) - maks
    if sisa > 0:
        baris.append(f"<i>+{sisa} agenda lain dalam 30 hari</i>")
    return baris


def _blok_opsi(brief: Dict[str, Any]) -> List[str]:
    """Posisi opsi Deribit — cerminan taruhan institusional."""
    opsi = brief.get("options") or {}
    if not opsi:
        return []

    baris = ["", f"{EMOJI['opsi']} <b>Opsi (Deribit)</b>"]

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
        baris.append(
            f"Max pain {_angka(opsi['max_pain_expiry_terdekat'], 0)} "
            "<i>(harga yang paling merugikan pemegang opsi saat jatuh tempo)</i>"
        )
    pc = opsi.get("put_call_ratio_oi")
    if pc is not None:
        arti = ("lebih banyak posisi proteksi turun" if pc > 1
                else "lebih banyak taruhan naik")
        baris.append(f"<i>Rasio put/call {_angka(pc, 2)}: {arti}.</i>")
    return baris


def _blok_valuasi(brief: Dict[str, Any]) -> List[str]:
    """Valuasi on-chain — konteks jangka panjang, bukan sinyal harian."""
    oc = brief.get("onchain") or {}
    if not oc:
        return []

    baris = ["", f"{EMOJI['onchain']} <b>Valuasi On-chain</b>"]
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
        ubah = oc.get("alamat_aktif_perubahan_30hari_pct")
        tambahan = f" ({_persen(ubah, 1)} per 30 hari)" if ubah is not None else ""
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

    baris = ["", f"{EMOJI['aliran']} <b>Aliran Dana</b>"]
    if fl.get("premium_coinbase_pct") is not None:
        p = fl["premium_coinbase_pct"]
        # Label bisa kosong kalau sumbernya tidak memberi keterangan; tanpa
        # penjagaan ini barisnya berakhir dengan tanda kurung kosong.
        label = (fl.get("premium_coinbase_label") or "").strip()
        keterangan = f" ({esc(label)})" if label else ""
        baris.append(
            f"Premium Coinbase {'+' if p > 0 else ''}{_angka(p, 3)}%{keterangan}"
        )
    if fl.get("stablecoin_cap_usd"):
        miliar = fl["stablecoin_cap_usd"] / 1e9
        ubah = fl.get("stablecoin_perubahan_24j_usd")
        tambahan = ""
        if ubah:
            juta = ubah / 1e6
            tambahan = f" ({_uang_bertanda(juta, 0, ' jt/24j')})"
        baris.append(f"Stablecoin ${_angka(miliar, 1)} miliar{tambahan}")
    return baris if len(baris) > 2 else []


def _blok_pernyataan(brief: Dict[str, Any], maks: int = 3) -> List[str]:
    """Pernyataan tokoh berpengaruh yang berpotensi menggerakkan pasar."""
    # Pernyataan tanpa tokoh yang teridentifikasi tidak punya nilai: pembaca
    # tidak bisa menimbang bobotnya kalau tidak tahu siapa yang bicara.
    pernyataan = [
        p for p in (brief.get("statements") or [])
        if p.get("tokoh") and str(p["tokoh"]).strip().lower() not in
        ("", "tidak disebutkan", "tidak diketahui", "null", "none")
    ]
    # Sama seperti _blok_berita: maks<=0 berarti blok ini dikorbankan tangga
    # degradasi, jadi judulnya pun tidak boleh ikut tercetak.
    if not pernyataan or maks <= 0:
        return []

    baris = ["", f"{EMOJI['pernyataan']} <b>Pernyataan Berpengaruh</b>"]
    for s in pernyataan[:maks]:
        isi = _potong(s.get("ringkasan") or s.get("kutipan") or "", 170)
        baris.append(f"• <b>{esc(s['tokoh'])}</b>: {esc(isi)}")
        detail = [t for t in (_label_arah(s.get("dampak_btc")), _label_dampak(s.get("kekuatan"))) if t]
        status = STATUS_PERNYATAAN.get(s.get("status") or "")
        if status and s.get("status") != "verbatim":
            detail.append(status)
        if detail:
            baris.append("   <i>" + esc(" · ".join(detail)) + "</i>")
    return baris


def _blok_whale(brief: Dict[str, Any]) -> List[str]:
    """Posisi whale vs ritel — angka mentah, belum ditafsirkan AI."""
    whale = brief.get("whale") or {}
    if whale.get("whale_long_pct") is None and whale.get("ritel_long_pct") is None:
        return []

    baris = ["", f"{EMOJI['whale']} <b>Posisi Besar vs Ritel</b>"]
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
    baris = ["", f"{EMOJI['jebakan']} <b>Sinyal Perlu Diwaspadai</b>"]
    for s in sinyal[:maks]:
        baris.append(f"• {esc(s.get('keterangan', ''))}")
    return baris


def _baris_pergerakan(brief: Dict[str, Any], penuh: bool) -> List[str]:
    """Satu-dua baris arah + jenis pergerakan 24 jam, dari hitungan KODE.

    Selalu ikut dikirim, termasuk saat analisa AI gagal atau ditahan — arah
    dan sifat pergerakan adalah pertanyaan pertama pembaca, dan jawabannya
    tidak bergantung pada model mana pun. `penuh` dipakai saat tidak ada
    prosa AI yang menjelaskannya, jadi kalimat lengkapnya yang dikirim.
    """
    p = ((brief.get("technical") or {}).get("pergerakan_24j")) or {}
    if not p.get("arah"):
        return []

    if penuh:
        return [esc(p.get("ringkas") or ""), ""]

    panah = {"naik": "📈", "turun": "📉"}.get(p["arah"], "➖")
    if p["arah"] == "datar":
        inti = "Praktis datar dalam 24 jam"
    else:
        kata = "Naik" if p["arah"] == "naik" else "Turun"
        angka = f"{abs(p.get('perubahan_pct') or 0):.2f}".replace(".", ",")
        inti = f"{kata} {angka}% dalam 24 jam"
    besaran = {
        "tipis": "tipis", "wajar": "wajar", "besar": "besar", "ekstrem": "sangat besar",
    }.get(p.get("besaran"))
    if besaran:
        inti += f" · pergerakan {besaran}"
    hasil = [f"{panah} <b>{esc(inti)}</b>"]
    # Penjelasannya, bukan istilahnya: pembaca Telegram tidak punya chip
    # maupun panel rinci untuk menebus label yang terlalu padat.
    if p.get("jenis_arti"):
        hasil.append(f"   <i>{esc(p['jenis_arti'])}</i>")
    hasil.append("")
    return hasil


#: Batas panjang tiap bagian blok AI, dalam dua mode.
#:
#: Mode LEGA dipakai selama pesannya masih muat. Mode RINGKAS dipakai begitu
#: tangga degradasi kehabisan cara lain — dan itu sering terjadi: pada brief
#: produksi 17 Agustus, blok AI sendirian mencapai 4.302 karakter, MELEBIHI
#: seluruh batas Telegram (4.096), sementara semua blok lain digabung cuma
#: 2.780. Akibatnya pesan jatuh ke jalur pemangkasan terakhir yang membuang
#: seluruh data pasar — termasuk arus ETF — dan menyisakan analisa AI yang
#: terpotong di tengah kalimat.
#:
#: Tiap batas di bawah dulunya masuk akal sendiri-sendiri; yang tidak pernah
#: diperiksa adalah JUMLAHNYA. Mode ringkas memangkas bagian yang paling
#: mudah dibaca ulang di web (skenario, keputusan besar, prosa panjang) dan
#: mempertahankan yang paling menentukan (arah, penyebab, geopolitik).
_BATAS_AI = {
    "lega": {
        "judul": 160, "narasi_par": 700, "outlook": 400, "geo_par": 700, "geo_jumlah": 3,
        "faktor": 200, "faktor_jumlah": 3, "keputusan": 200, "keputusan_jumlah": 2,
        "skenario": 200, "syarat": 130, "risiko": 200,
        "teknikal": 500, "whale": 400, "detail_tambahan": True,
        "penyebab_jumlah": 4, "penyebab_dasar": 160, "katalis": 250,
    },
    "ringkas": {
        "judul": 120, "narasi_par": 420, "outlook": 260, "geo_par": 420, "geo_jumlah": 1,
        "faktor": 120, "faktor_jumlah": 2, "keputusan": 130, "keputusan_jumlah": 1,
        "skenario": 140, "syarat": 0, "risiko": 130,
        "teknikal": 260, "whale": 220, "detail_tambahan": False,
        # `penyebab_pergerakan` sempat TERLEWAT dari anggaran ini: empat butir
        # dengan baris bukti 160 karakter masing-masing menyumbang ~1.000
        # karakter yang tidak pernah ikut menyusut. Butirnya dipertahankan
        # (itu jawaban atas "kenapa naik/turun") tapi baris buktinya dilepas —
        # buktinya ada lengkap di web.
        "penyebab_jumlah": 2, "penyebab_dasar": 0, "katalis": 0,
    },
}


def _blok_ai(
    brief: Dict[str, Any], paragraf_maks: int = 4, ringkas: bool = False
) -> List[str]:
    ai = brief.get("ai") or {}
    critic = ai.get("critic") or {}
    bat = _BATAS_AI["ringkas" if ringkas else "lega"]

    baris = ["", PEMISAH]

    # Bagian yang ditahan critic (kalau ada) sengaja TIDAK diberi tahu ke
    # pembaca — brief ini untuk pemakaian pribadi, dan membeberkan alasan
    # teknis critic ("angka_karangan", dst) cuma mengganggu tanpa berguna.
    # Bagian yang lolos tetap dikirim; kalau semuanya tertahan, pesan di
    # bawah ("tidak tersedia") yang tampil — sama seperti run tanpa analisa.
    narasi = (ai.get("narrative_singkat") or "").strip()
    teknikal_ai = (ai.get("teknikal") or {}).get("ringkasan") or ""
    whale_ai = ai.get("whale") or {}
    outlook_ai = (ai.get("outlook") or {}).get("ringkasan") or ""

    if not any([narasi, teknikal_ai, whale_ai.get("ringkasan"), outlook_ai]):
        baris.append("✦ <b>ULASAN LENGKAP</b>")
        # Arah dan sifat pergerakan tetap dikirim: itu hitungan kode, tidak
        # ikut hilang bersama analisa yang gagal.
        baris.extend(_baris_pergerakan(brief, penuh=True))
        baris.append("<i>Analisa AI tidak tersedia pada run ini.</i>")
        return _rapikan_kosong(baris) + [PEMISAH]

    baris.append("✦ <b>ULASAN LENGKAP</b>")
    baris.extend(_baris_pergerakan(brief, penuh=False))
    # Judul memuat temuan utamanya — itu yang paling ingin dibaca duluan.
    judul = ((ai.get("bagian") or {}).get("judul") or "").strip()
    if judul:
        baris.append(f"<b>{esc(_potong(judul, bat['judul']))}</b>")
        baris.append("")
    # Narasi lengkap dikirim beberapa paragraf, bukan cuma satu kalimat
    # pembuka — ruang 4096 karakter jauh lebih dari cukup, dan tangga
    # pemangkasan di bawah yang mengurus kalau ternyata kepanjangan.
    narasi_penuh = (ai.get("narrative") or "").strip()
    if narasi_penuh:
        paragraf = [p.strip() for p in narasi_penuh.split("\n\n") if p.strip()]
        for par in paragraf[:paragraf_maks]:
            baris.append(esc(_potong(par, bat["narasi_par"])))
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
        for p in penyebab[: bat["penyebab_jumlah"]]:
            panah = {"naik": "↑", "turun": "↓"}.get(p.get("arah"), "·")
            keyakinan = p.get("keyakinan")
            tanda = {"tinggi": "", "sedang": " (keyakinan sedang)", "rendah": " (keyakinan rendah)"}.get(keyakinan, "")
            baris.append(f"{panah} <b>{esc(p.get('faktor', ''))}</b>{tanda}")
            if p.get("dasar") and bat["penyebab_dasar"]:
                baris.append(f"   <i>{esc(_potong(p['dasar'], bat['penyebab_dasar']))}</i>")

    # Ke depan (makro, geopolitik, agenda) ditempatkan TEPAT SETELAH penyebab
    # pergerakan, sebelum teknikal dan whale — faktor-faktor ini sedang
    # sangat menentukan arah pasar belakangan ini, jadi tidak pantas terkubur
    # di bawah pembacaan yang levelnya lebih taktis. faktor_geopolitik dan
    # keputusan_besar sebelumnya malah tidak pernah ditulis ke Telegram sama
    # sekali meski datanya sudah dihasilkan outlook — sekarang ikut tampil.
    ol = ai.get("outlook") or {}
    if outlook_ai:
        baris.append("")
        # Horizon dari model kerap SUDAH memuat kurung sendiri: "1-3 minggu ke
        # depan (mencakup FOMC Minutes hingga rilis NFP awal September)".
        # Membungkusnya lagi menghasilkan kurung bersarang, dan memotongnya di
        # tengah meninggalkan kurung yang tidak pernah ditutup — dua-duanya
        # terlihat di produksi. Keterangan dalam kurung itu dibuang saja: ia
        # penjelas, bukan horizonnya, dan versi utuhnya ada di web.
        teks_horizon = (ol.get("horizon") or "").split("(")[0].strip(" ,;-")
        horizon = f" ({esc(_potong(teks_horizon, 60))})" if teks_horizon else ""
        baris.append(f"<b>Ke depan{horizon}:</b> " + esc(_potong(outlook_ai, bat["outlook"])))
        # Geopolitik ditulis sebagai paragraf utuh, bukan tempelan satu baris.
        # Pasar belakangan bergerak mengikuti isu ini, jadi ruangnya diberi
        # jauh lebih lega daripada butir pendukung lain.
        if ol.get("narasi_geopolitik"):
            baris.append("")
            baris.append(f"{EMOJI['regulasi']} <b>Geopolitik &amp; regulasi</b>")
            paragraf_geo = [p.strip() for p in ol["narasi_geopolitik"].split("\n\n") if p.strip()]
            for par in paragraf_geo[: bat["geo_jumlah"]]:
                baris.append(esc(_potong(par, bat["geo_par"])))
        if ol.get("faktor_geopolitik"):
            for g in ol["faktor_geopolitik"][: bat["faktor_jumlah"]]:
                baris.append("• " + esc(_potong(g, bat["faktor"])))
        # `keputusan_besar` tidak lagi dirender di sini — field-nya sudah
        # dihapus dari skema outlook. Isinya kalender yang sama dengan
        # `_blok_agenda`, yang dirakit KODE lengkap dengan jam WIB, penanda
        # dampak, dan jalur transmisinya. Dari lima butir yang dibayar tiap
        # run, paling banyak dua yang pernah muat di sini.
        for nama, kunci, panah in (("Menguat", "skenario_naik", "↑"), ("Melemah", "skenario_turun", "↓")):
            sk = ol.get(kunci) or {}
            pemicu = sk.get("pemicu") or []
            if pemicu:
                baris.append(
                    f"{panah} <b>{nama}:</b> "
                    + esc(_potong(", ".join(pemicu[:3]), bat["skenario"]))
                )
                if sk.get("kondisi") and bat["syarat"]:
                    baris.append(f"   <i>syarat: {esc(_potong(sk['kondisi'], bat['syarat']))}</i>")
        # `risiko_utama` juga dihapus dari skema: butir pertamanya —
        # satu-satunya yang pernah dirender — selalu mengulang kalimat
        # pembuka `yang_diwaspadai` yang sudah ada di blok narasi.

    tek = ai.get("teknikal") or {}
    if teknikal_ai:
        baris.append("")
        baris.append("<b>Teknikal:</b> " + esc(_potong(teknikal_ai, bat["teknikal"])))
        if bat["detail_tambahan"] and tek.get("kontradiksi"):
            baris.append("⚠ " + esc(_potong(tek["kontradiksi"][0], 200)))
        if bat["detail_tambahan"] and tek.get("pembatalan"):
            baris.append("<i>Batal bila: " + esc(_potong(tek["pembatalan"], 200)) + "</i>")

    if whale_ai.get("ringkasan"):
        waspada = whale_ai.get("tingkat_kewaspadaan")
        tanda = "⚠️ " if waspada == "tinggi" else ""
        baris.append("")
        baris.append(f"<b>Whale:</b> {tanda}" + esc(_potong(whale_ai["ringkasan"], bat["whale"])))
        for sp in (whale_ai.get("sinyal_palsu") or [])[:2] if bat["detail_tambahan"] else []:
            if sp.get("keyakinan") in ("tinggi", "sedang"):
                baris.append(f"• {esc(sp.get('pola',''))}: {esc(_potong(sp.get('arti',''), 150))}")

    bagian = ai.get("bagian") or {}
    # `narrative` sudah dirakit dari seluruh bagian TERMASUK kesimpulan, jadi
    # menuliskannya lagi di sini membuat pembaca membaca paragraf yang sama
    # dua kali. Baris terpisah ini hanya dipakai kalau narasi penuh tidak
    # sempat dirender (mis. cuma ringkasan pendek yang tersedia).
    if bagian.get("kesimpulan") and not narasi_penuh:
        baris.append("")
        baris.append("<b>Kesimpulan:</b> " + esc(_potong(bagian["kesimpulan"], 400)))
    # `katalis_berikutnya` dihapus dari skema sintesis: isinya salinan
    # agenda yang sudah dirakit kode, dan `_blok_agenda` di bawah merender
    # daftar yang sama dengan jam WIB serta jalur dampaknya.

    baris.append("")
    # Kalimat bernada anjuran TIDAK menahan analisa — cuma diberi keterangan,
    # karena keputusannya tetap di tangan pembaca.
    if ai.get("tanda_editorial"):
        baris.append(
            "<i>ℹ️ Sebagian kalimat terbaca menyerempet anjuran tindakan. "
            "Ini analisa, bukan instruksi.</i>"
        )
    if not critic.get("dijalankan", True):
        baris.append("<i>⚠️ Belum sempat diverifikasi — pemeriksa fakta gagal dijalankan.</i>")
    baris.append("<i>Dihasilkan AI, dapat keliru.</i>")
    # Ciutkan pemisah kosong yang bertumpuk akibat bagian yang absen; PEMISAH
    # ditambahkan setelahnya supaya tidak ikut terhapus sebagai "ujung kosong".
    baris = _rapikan_kosong(baris)
    baris.append(PEMISAH)
    return baris


def _blok_jendela_risiko(brief: Dict[str, Any]) -> List[str]:
    """Peringatan JENDELA: seberapa berbahaya jamnya, bukan isi kebijakannya.

    Isi kebijakan sudah pindah ke analisa AI sebagai sebab naik/turun harga,
    jadi blok ini tidak lagi mengulang pemicu maupun skenario. Yang tersisa
    hal yang tidak bisa diceritakan analisa: pasar AS sedang tutup, dan
    seberapa rapuh pasar menyambut kejutan.

    Diam pada risiko rendah. Peringatan yang berbunyi tiap hari akan
    diabaikan justru pada hari ia benar-benar berarti.
    """
    agen = brief.get("agen_kebijakan") or {}
    risiko = (agen.get("risiko_jendela") or {}).get("tingkat")
    if risiko not in ("sedang", "tinggi"):
        return []

    jendela = agen.get("jendela") or {}
    rapuh = agen.get("kerapuhan") or {}
    ikon = EMOJI["mendesak"] if risiko == "tinggi" else EMOJI["regulasi"]
    baris = ["", f"{ikon} <b>JENDELA RISIKO: {esc(risiko.upper())}</b>"]

    fase = jendela.get("fase")
    if fase == "jeda_akhir_pekan":
        # Sisa jam di sini SNAPSHOT, dan memang seharusnya begitu: pesan
        # Telegram dibaca dekat waktu kirim, jadi "7 jam lagi" benar saat
        # tiba. Yang perlu hitung mundur hidup cuma halaman web.
        jam = jendela.get("jam_sampai_buka")
        mulai = jendela.get("jeda_mulai")
        wib = jendela.get("buka_berikutnya_wib")
        awal = f" sejak {esc(mulai)}" if mulai else ""
        jangkar = f" — buka {esc(wib)}" if wib else ""
        baris.append(
            f"Bursa AS &amp; ETF tutup{awal}, masih {_angka(jam, 0)} jam lagi"
            f"{jangkar}. Kalau ada kejutan kebijakan sekarang, hanya harga kripto yang "
            "bereaksi — tidak ada transaksi ETF atau institusi AS yang bisa meredamnya."
        )
    elif fase == "jelang_tutup_pekan":
        baris.append(
            "Menjelang penutupan Jumat — berita yang mendarat sekarang tidak "
            "sempat dicerna pasar AS sebelum jeda akhir pekan."
        )

    if rapuh.get("tingkat") in ("sedang", "tinggi"):
        nama = ", ".join(f.get("nama", "") for f in (rapuh.get("faktor") or [])[:3])
        if nama:
            baris.append(f"<i>Kerapuhan {esc(rapuh['tingkat'])}: {esc(nama)}</i>")

    # Berapa sinyal kuat yang mendarat saat pasar AS tidak bisa menyerapnya.
    # Angka hitungan kode, bukan tafsir model.
    pend = agen.get("pendaratan") or {}
    if pend.get("ada_yang_tertahan"):
        n = pend.get("kuat_di_jendela_rawan")
        baris.append(
            f"<i>{n} sinyal kuat mendarat saat pasar AS tutup — efeknya masih menunggu.</i>"
        )
    return baris


def _blok_penutup(brief: Dict[str, Any], site_url: str) -> List[str]:
    baris = [""]
    conflicts = brief.get("conflicts") or []
    if conflicts:
        pertama = conflicts[0]
        teks = pertama.get("keterangan") if isinstance(pertama, dict) else str(pertama)
        baris.append(f"⚠️ Sinyal bertentangan: {esc(teks[:200])}")

    # Skor kualitas data sengaja TIDAK ditampilkan di Telegram: itu metrik
    # kesehatan pipeline, bukan informasi pasar, dan pembaca tidak bisa
    # berbuat apa-apa dengannya. Tetap tersimpan di `data_quality` pada
    # latest.json dan tampil di web untuk keperluan pemeriksaan.
    #
    # BEDA dengan catatan di bawah: kalau sebuah BAGIAN ANALISA gagal
    # dihasilkan, pembaca WAJIB tahu — kalau tidak, brief yang kehilangan
    # narasi utamanya terbaca seperti brief yang memang singkat hari itu.
    catatan_gagal = [
        c for c in ((brief.get("data_quality") or {}).get("catatan") or [])
        if "gagal dihasilkan" in c
    ]
    for c in catatan_gagal[:3]:
        baris.append(f"⚠️ {esc(c)}")

    if site_url:
        baris.append("")
        baris.append(f"{EMOJI['tautan']} Selengkapnya: {esc(site_url)}")
    baris.append("<i>Informasi, bukan saran investasi.</i>")
    return baris


def render_terpisah(
    brief: Dict[str, Any], site_url: str = "", batas: Optional[int] = None
) -> tuple:
    """Sama seperti render(), tapi judul+waktu dipisah dari sisa pesan.

    Dipakai supaya perapi LLM (stylist) hanya pernah melihat dan menata
    BADAN pesan — judul "Nawala" dan timestamp tidak pernah
    dikirim ke LLM sama sekali, jadi tidak mungkin hilang atau tertulis
    ulang biar pun modelnya lupa instruksi "pertahankan judul". Sebelumnya
    judul ini kadang hilang dari pesan yang sudah dirapikan karena
    verifikasinya tidak pernah mewajibkan judul itu ada.

    Return: (kepala_teks, badan_teks) — digabung apa adanya (tanpa pemisah
    tambahan) menghasilkan pesan yang identik dengan render().
    """
    batas_efektif = batas or BATAS_KARAKTER
    kepala = [
        f"{EMOJI['merek']} <b>Nawala</b> · <i>Ringkasan Pasar Kripto</i>",
        # Timestamp tanpa emoji: jam bukan penanda pasar, dan barisnya
        # sudah jelas tanpa hiasan.
        f"{esc(brief.get('generated_at_wib', ''))}",
    ]
    notice_mendesak = _notice_agenda_mendesak(brief)
    if notice_mendesak:
        kepala.append("")
        kepala.extend(notice_mendesak)
    kepala.append("")
    kepala_teks = "\n".join(kepala)
    inti = (
        _blok_harga(brief)
        # Siaga kebijakan masuk `inti`, bukan blok opsional: alarm yang
        # hilang begitu pesannya kepanjangan adalah alarm yang gagal justru
        # saat paling dibutuhkan. Ongkosnya kecil, dan ia diam sendiri pada
        # siaga rendah.
        + _blok_jendela_risiko(brief)
        + _blok_teknikal(brief)
        + _blok_pasar(brief)
        + _blok_makro(brief)
    )
    penutup = _blok_penutup(brief, site_url)

    # Tangga degradasi. Analisa AI adalah isi utama brief, jadi paragrafnya
    # dipertahankan lama; yang dikorbankan lebih dulu adalah daftar yang
    # gampang dibaca ulang di web.
    #
    # Kolom `ringkas` adalah sumbu degradasi KEDUA, ditambahkan setelah
    # menemukan bahwa memangkas jumlah paragraf saja tidak cukup: blok AI
    # punya belasan bagian berukuran tetap (geopolitik, keputusan besar,
    # skenario, teknikal, whale) yang tidak tersentuh `paragraf_ai` sama
    # sekali. Pada brief produksi 17 Agustus, blok AI mencapai 4.302 karakter
    # — melebihi seluruh batas Telegram — sehingga tangga ini kehabisan cara
    # dan pesan jatuh ke pemangkasan terakhir yang membuang SEMUA data pasar.
    #  (berita, pernyataan, paragraf_ai, sinyal, whale, agenda, data_tambahan, ringkas)
    tangga = [
        (4, 3, 5, True,  True,  True,  True,  False),
        (3, 3, 5, True,  True,  True,  True,  False),
        (3, 2, 4, True,  True,  True,  True,  False),
        (2, 2, 4, True,  True,  True,  True,  False),
        (2, 2, 2, True,  True,  True,  True,  False),
        (1, 1, 2, True,  True,  True,  True,  False),
        (0, 1, 2, False, True,  True,  True,  False),
        (0, 1, 2, False, True,  True,  False, False),  # buang opsi/valuasi/aliran
        # Mulai di sini blok AI sendiri yang diringkas, BUKAN data pasar yang
        # dibuang. Data pasar murah (seluruh blok pasar cuma ~144 karakter,
        # sudah termasuk arus ETF, funding, OI, dan Fear & Greed) dan tidak
        # bisa dibaca ulang di tempat lain dalam sekali lihat; prosa AI mahal
        # dan versi lengkapnya selalu tersedia di web.
        (2, 2, 4, True,  True,  True,  True,  True),
        (1, 1, 3, True,  True,  True,  True,  True),
        (0, 1, 3, False, True,  True,  True,  True),
        (0, 0, 2, False, True,  True,  False, True),
        (0, 0, 2, False, False, True,  False, True),
        (0, 0, 1, False, False, False, False, True),
    ]

    for berita_n, pernyataan_n, ai_n, sinyal, whale, agenda, tambahan, ringkas in tangga:
        bagian = kepala + inti
        if tambahan:
            bagian += _blok_opsi(brief) + _blok_valuasi(brief) + _blok_aliran(brief)
        if whale:
            bagian += _blok_whale(brief)
        if sinyal:
            bagian += _blok_sinyal_palsu(brief)
        # Agenda mendahului berita & pernyataan: apa yang AKAN terjadi lebih
        # menentukan posisi pembaca daripada apa yang sudah diberitakan.
        if agenda:
            bagian += _blok_agenda(brief)
        bagian += _blok_pernyataan(brief, pernyataan_n)
        bagian += _blok_berita(brief, berita_n)
        bagian += _blok_ai(brief, paragraf_maks=ai_n, ringkas=ringkas) + penutup

        # Dirapikan sekali di sini untuk SELURUH pesan, bukan per blok: tiap
        # blok menambahkan pemisah kosongnya sendiri, jadi sambungan antar
        # blok (dan blok yang kebetulan kosong) meninggalkan baris kosong
        # bertumpuk. `kepala` sengaja tidak ikut dirapikan — baris kosong di
        # ujungnya adalah pemisah yang disengaja, dan potongan `kepala_teks`
        # di bawah bergantung pada panjangnya tetap persis.
        pesan = "\n".join(kepala + _rapikan_kosong(bagian[len(kepala):]))
        if len(pesan) <= batas_efektif:
            return kepala_teks, pesan[len(kepala_teks):]

    # Jalur terakhir. Yang dipertahankan adalah `inti` UTUH — harga, teknikal,
    # posisi pasar (arus ETF, funding, OI, Fear & Greed), dan makro — bukan
    # cuma harga seperti sebelumnya. Itu perbaikan langsung dari kasus nyata:
    # pesan 17 Agustus sampai di sini dan pembaca kehilangan seluruh data
    # pasar, padahal semuanya cuma ~420 karakter dan justru bagian yang tidak
    # bisa direkonstruksi sendiri oleh pembaca. Prosa AI yang dipangkas,
    # karena versi lengkapnya ada di web dan tautannya ikut terkirim.
    panjang_dasar = len("\n".join(kepala + inti))
    sisa = batas_efektif - panjang_dasar - len("\n".join(penutup)) - 120
    ai_teks = "\n".join(_blok_ai(brief, paragraf_maks=1, ringkas=True))
    if sisa > 200:
        if len(ai_teks) > sisa:
            ai_teks = ai_teks[:sisa].rsplit("\n", 1)[0] + f"\n…\n{PEMISAH}"
    else:
        ai_teks = ""
    ekor = _rapikan_kosong(inti + ([ai_teks] if ai_teks else []) + penutup)
    pesan = "\n".join(kepala + ekor)
    if len(pesan) > batas_efektif:
        pesan = pesan[: batas_efektif - 60].rsplit("\n", 1)[0] + "\n\n…\n<i>Pesan dipotong.</i>"
    return kepala_teks, pesan[len(kepala_teks):]


def render(brief: Dict[str, Any], site_url: str = "", batas: Optional[int] = None) -> str:
    """Susun pesan HTML. Kalau kepanjangan, bagian berita dipangkas lebih dulu.

    `batas` memungkinkan pemanggil menyisakan ruang kepala. Perapi LLM
    menambah emoji dan jeda baris, jadi kalau pesan sudah mepet 4096 karakter
    hasil rapinya pasti melewati batas dan selalu ditolak — perapiannya jadi
    tidak pernah terpakai.
    """
    kepala_teks, badan_teks = render_terpisah(brief, site_url, batas)
    return kepala_teks + badan_teks


def broadcast(
    token: str,
    chat_ids: List[str],
    pesan: str,
    jeda: float = 0.06,
) -> Dict[str, Any]:
    """Kirim satu pesan ke banyak chat.

    Kegagalan satu penerima tidak menghentikan sisanya. Penerima yang jelas
    tidak valid lagi (memblokir bot, chat dihapus) dikembalikan lewat
    `gugur` supaya pemanggil bisa mengeluarkannya dari daftar — kalau
    dibiarkan, daftar akan terus menumpuk chat mati.
    """
    berhasil: List[str] = []
    gagal: List[str] = []
    gugur: List[str] = []

    for i, chat_id in enumerate(chat_ids):
        if i:
            time.sleep(jeda)  # Telegram membatasi sekitar 30 pesan per detik
        hasil = _kirim_satu(token, chat_id, pesan)
        if hasil["ok"]:
            berhasil.append(chat_id)
        else:
            gagal.append(chat_id)
            if hasil["permanen"]:
                gugur.append(chat_id)

    log.info(
        "Broadcast Telegram: %d berhasil, %d gagal (%d gugur permanen)",
        len(berhasil), len(gagal), len(gugur),
    )
    return {"berhasil": berhasil, "gagal": gagal, "gugur": gugur}


# Kode Telegram yang berarti penerima ini tidak akan pernah bisa dikirimi lagi.
_ALASAN_PERMANEN = (
    "bot was blocked by the user",
    "user is deactivated",
    "chat not found",
    "bot was kicked",
    "group chat was upgraded",
    "peer_id_invalid",
)


# Ciri penolakan Telegram yang penyebabnya MARKUP, bukan penerimanya.
# Untuk kasus ini pesan yang sama masih bisa dikirim tanpa parse_mode.
_CIRI_MASALAH_MARKUP = (
    "can't parse entities",
    "cant parse entities",
    "unsupported start tag",
    "unclosed start tag",
    "can't find end tag",
    "unexpected end tag",
    "bad request: can't parse",
)


def _tanpa_tag(teks: str) -> str:
    """Buang tag HTML dan kembalikan entity ke bentuk aslinya.

    Dipakai hanya sebagai upaya terakhir: lebih baik pembaca menerima pesan
    polos tanpa tebal/miring daripada tidak menerima apa pun.
    """
    polos = re.sub(r"<[^>]+>", "", teks)
    return (
        polos.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    )


def _kirim_satu(token: str, chat_id: str, pesan: str) -> Dict[str, Any]:
    """Kirim ke satu chat. Return {ok, permanen}.

    Kalau Telegram menolak karena MARKUP-nya (bukan karena penerimanya),
    pesan diulang sekali tanpa parse_mode. Tanpa jaring ini, satu tag rusak
    membuat SELURUH penerima tidak menerima apa pun — dan itu benar-benar
    terjadi di produksi: 0 dari 2 penerima, brief harian hilang sepenuhnya
    padahal isinya sudah jadi.
    """
    def _post(teks: str, pakai_html: bool) -> Dict[str, Any]:
        muatan = {
            "chat_id": chat_id,
            "text": teks,
            "disable_web_page_preview": True,
        }
        if pakai_html:
            muatan["parse_mode"] = "HTML"
        return post_json(
            f"https://api.telegram.org/bot{token}/sendMessage",
            muatan,
            timeout=30,
            retries=1,
        )

    def _coba_polos(keterangan: str) -> Optional[Dict[str, Any]]:
        if not any(c in keterangan for c in _CIRI_MASALAH_MARKUP):
            return None
        log.warning(
            "Telegram menolak markup untuk %s; dikirim ulang sebagai teks polos", chat_id
        )
        try:
            ulang = _post(_tanpa_tag(pesan), pakai_html=False)
        except HttpError as exc:
            log.warning("Kiriman teks polos ke %s juga gagal: %s", chat_id, str(exc)[:150])
            return None
        if ulang.get("ok"):
            return {"ok": True, "permanen": False}
        return None

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
            retries=1,
        )
        if hasil.get("ok"):
            return {"ok": True, "permanen": False}
        keterangan = str(hasil.get("description", "")).lower()
        pulih = _coba_polos(keterangan)
        if pulih:
            return pulih
        permanen = any(a in keterangan for a in _ALASAN_PERMANEN)
        log.warning("Telegram menolak kirim ke %s: %s", chat_id, hasil.get("description"))
        return {"ok": False, "permanen": permanen}
    except HttpError as exc:
        keterangan = str(exc).lower()
        pulih = _coba_polos(keterangan)
        if pulih:
            return pulih
        permanen = any(a in keterangan for a in _ALASAN_PERMANEN)
        log.warning("Gagal kirim ke %s: %s", chat_id, str(exc)[:150])
        return {"ok": False, "permanen": permanen}


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
