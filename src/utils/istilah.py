"""Ubah istilah internal jadi bahasa manusia.

MASALAHNYA: konteks yang dikirim ke LLM berbentuk JSON, jadi model melihat
nama field dan nilai enum apa adanya — `sinyal_oi`, `short_covering`,
`invalidasi_turun`, `buy_sell_ratio`. Model lalu menyalinnya bulat-bulat ke
dalam narasi, dan pembaca disuguhi potongan kode alih-alih kalimat:

    "Sinyal open interest menunjukkan pola short_covering"
    "invalidasi_turun di $64.314 adalah titik yang..."
    "buy_sell_ratio taker 1,785 (dominan buy)"

Melarangnya lewat prompt saja tidak cukup — model tetap tergelincir, dan
kalau gagal hasilnya baru ketahuan setelah terkirim ke pembaca. Karena itu
penggantian dilakukan KODE, setelah LLM selesai menulis: deterministik,
tidak bergantung kepatuhan model, dan tidak pernah mengubah angka.

Ini murni transformasi tampilan. Tidak ada fakta yang berubah — `62.790`
tetap `62.790`, cuma `invalidasi_turun` yang jadi "batas pembatalan
skenario turun".
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

# Istilah yang butuh terjemahan sungguhan, bukan sekadar hapus garis bawah.
# Diurutkan dari yang paling panjang saat dipakai supaya `taker_buy_sell_ratio`
# tidak keburu tertangkap pola `buy_sell_ratio`.
KAMUS: Dict[str, str] = {
    # Sinyal open interest vs harga
    # Kata Indonesianya didahulukan, istilah pasarnya menyusul dalam kurung:
    # pembaca yang belum kenal istilahnya tetap paham kalimatnya, dan yang
    # sudah kenal tidak kehilangan padanan aslinya.
    "short_covering": "penutupan posisi jual (short)",
    "long_liquidation": "likuidasi posisi beli (long)",
    "short_buildup": "penumpukan posisi jual (short)",
    "long_buildup": "penumpukan posisi beli (long)",
    # Level kunci
    "invalidasi_turun": "batas pembatalan skenario turun",
    "invalidasi_naik": "batas pembatalan skenario naik",
    "level_kunci": "level kunci",
    "key_levels": "level kunci",
    # Derivatif & aliran
    "taker_buy_sell_ratio": "rasio beli-jual taker",
    "buy_sell_ratio": "rasio beli-jual",
    "put_call_ratio_oi": "rasio put/call",
    "funding_rate": "funding rate",
    "open_interest": "open interest",
    "oi_change_pct": "perubahan open interest",
    "sinyal_oi": "sinyal open interest",
    "max_pain_expiry_terdekat": "max pain jatuh tempo terdekat",
    # Posisi whale vs ritel
    "whale_distribusi": "whale mengurangi posisi lebih cepat dari ritel",
    "whale_akumulasi": "whale menambah posisi lebih cepat dari ritel",
    "posisi_whale_vs_ritel": "posisi whale dibanding ritel",
    "whale_long_pct": "porsi long whale",
    "ritel_long_pct": "porsi long ritel",
    "divergensi_label": "arah divergensi",
    # Teknikal
    "bb_squeeze": "penyempitan Bollinger Band",
    "bb_bandwidth": "lebar Bollinger Band",
    "stoch_rsi_k": "Stochastic RSI %K",
    "stoch_rsi_d": "Stochastic RSI %D",
    "divergensi_rsi": "divergensi RSI",
    "macd_histogram": "histogram MACD",
    "rasio_vs_rata": "rasio terhadap rata-rata",
    "vwap_harian": "VWAP harian",
    "obv_arah": "arah OBV",
    "jenuh_beli": "jenuh beli",
    "jenuh_jual": "jenuh jual",
    "kualitas_tren": "kualitas tren",
    "sinyal_palsu": "sinyal yang perlu diwaspadai",
    "cross_50_200": "persilangan EMA 50 dan 200",
    "cross_20_50": "persilangan EMA 20 dan 50",
    # Harga & pasar
    "volume_24h": "volume 24 jam",
    "change_24h_pct": "perubahan 24 jam",
    "high_24h": "tertinggi 24 jam",
    "low_24h": "terendah 24 jam",
    "etf_flow_usd": "arus ETF",
    "fear_greed": "indeks Fear & Greed",
    "premium_coinbase_pct": "premium Coinbase",
    "stablecoin_cap_usd": "kapitalisasi stablecoin",
    # Valuasi on-chain
    "mvrv_zona": "zona MVRV",
    "realized_cap_usd": "realized cap",
    "alamat_aktif": "alamat aktif",
    "pasokan_diam_1thn_pct": "pasokan yang diam lebih dari setahun",
    # Klasifikasi berita
    "status_kepastian": "status kepastian",
    "jalur_transmisi": "jalur transmisi",
    "sudah_priced_in": "sudah priced in",
    "belum_dikonfirmasi": "belum dikonfirmasi",
    "sudah_terjadi": "sudah terjadi",
    "dilaporkan_media": "dilaporkan media",
    "tipe_klaim": "tipe klaim",
    "risk_appetite": "selera risiko",
    "supply_demand": "pasokan dan permintaan",
    # Struktur keluaran AI
    "penyebab_pergerakan": "penyebab pergerakan",
    "data_pendukung": "data pendukung",
    "peta_level": "peta level",
    "yang_diwaspadai": "yang perlu diwaspadai",
    "katalis_berikutnya": "katalis berikutnya",
    "posisi_harga": "posisi harga",
    "faktor_geopolitik": "faktor geopolitik",
    "keputusan_besar": "keputusan besar",
    "risiko_utama": "risiko utama",
    "skenario_naik": "skenario menguat",
    "skenario_turun": "skenario melemah",
    "narrative_shift": "pergeseran narasi",
    "dominant_themes": "tema dominan",
    "tingkat_kewaspadaan": "tingkat kewaspadaan",
    "momentum_volume": "momentum dan volume",
    "teknikal_1d": "teknikal harian",
    "sentimen_agregat": "sentimen agregat",
    "perubahan_vs_sebelumnya": "perubahan dibanding brief sebelumnya",
    "pernyataan_tokoh": "pernyataan tokoh",
    "valuasi_onchain": "valuasi on-chain",
    "aliran_dana": "aliran dana",
    "opsi_deribit": "opsi Deribit",
    "posisi_whale": "posisi whale",
}

# --------------------------------------------------------------------------
# Frasa kaku yang ditulis MODEL (bukan nama field)
# --------------------------------------------------------------------------
# Beda dari KAMUS di atas: yang ini bukan istilah internal yang bocor,
# melainkan bahasa Indonesia yang sah secara tata bahasa tapi tidak dipakai
# siapa pun untuk bicara soal pasar.
#
# Bukan kekhawatiran teoretis. Prompt `judul` sempat memberi contoh
# "pedagang yang bertaruh harga turun menutup posisinya" sebagai cara
# menghindari istilah 'short', dan model menyalin kosakata itu ke judul
# brief 22 Agustus: "ditopang penutupan taruhan turun yang rapuh". Contoh di
# prompt sudah dibetulkan, tapi pelajarannya sama dengan nama field —
# LARANGAN LEWAT PROMPT SAJA TIDAK CUKUP, karena gagalnya baru ketahuan
# setelah terkirim ke pembaca.
#
# Daftarnya sengaja PENDEK dan harfiah: hanya frasa yang benar-benar pernah
# muncul beserta variasi terdekatnya. Penggantian yang terlalu longgar akan
# merusak kalimat yang sudah benar, dan itu lebih buruk daripada satu frasa
# kaku yang lolos.
FRASA_KAKU = {
    "penutupan taruhan turun": "penutupan posisi jual",
    "penutupan taruhan naik": "penutupan posisi beli",
    "taruhan turun": "posisi jual",
    "taruhan naik": "posisi beli",
    # Varian yang muncul di terjemahan berita: "$1,21 miliar taruhan bearish".
    "taruhan bearish": "posisi jual",
    "taruhan bullish": "posisi beli",
    "pedagang yang bertaruh harga turun": "pihak yang tadinya menjual",
    "pedagang yang bertaruh harga naik": "pihak yang tadinya membeli",
    "bertaruh harga turun": "membuka posisi jual",
    "bertaruh harga naik": "membuka posisi beli",
    "short covering": "penutupan posisi jual",
    "short seller": "pemilik posisi jual",
    "short-seller": "pemilik posisi jual",
}

_POLA_FRASA = re.compile(
    r"(" + "|".join(sorted(map(re.escape, FRASA_KAKU), key=len, reverse=True)) + r")",
    re.IGNORECASE,
)


#: Nilai hasil penggantian — dipakai mengenali kurung yang jadi mubazir
#: SETELAH penggantian, bukan sebelumnya.
_NILAI_PENGGANTI = {v.lower() for v in FRASA_KAKU.values()} | {
    v.lower() for v in KAMUS.values()
}

_POLA_KURUNG_ISI = re.compile(r"\s*\(([^()]{1,60})\)")


def _buang_kurung_mubazir(teks: str) -> str:
    """Buang kurung yang mengulang frasa tepat di depannya.

    Dua penggantian bisa menembak kalimat yang sama. Contoh nyata dari brief
    22 Agustus:

        "Penutupan taruhan turun secara massal (short covering) memaksa..."
         -> taruhan turun  -> posisi jual
         -> short covering -> penutupan posisi jual
        = "Penutupan posisi jual secara massal (penutupan posisi jual) ..."

    Kurungnya semula berguna — ia memberi padanan istilah asing. Begitu isi
    kurung jadi sama dengan teks di depannya, ia tinggal pengulangan.

    Sengaja SEMPIT: hanya kurung yang isinya persis salah satu NILAI hasil
    penggantian, dan hanya kalau nilai itu memang sudah muncul tepat
    sebelumnya. Kurung yang menerangkan hal lain — "(short)", "(ATR 2,3%)" —
    tidak tersentuh.
    """
    def _ganti(m: "re.Match") -> str:
        isi = m.group(1).strip().lower()
        if isi not in _NILAI_PENGGANTI:
            return m.group(0)
        sebelum = teks[max(0, m.start() - 80): m.start()].lower()
        return "" if isi in sebelum else m.group(0)

    return _POLA_KURUNG_ISI.sub(_ganti, teks)


def _ganti_frasa(teks: str) -> str:
    """Ganti frasa kaku, dengan huruf besar awal kalimat dipertahankan."""
    def _ganti(m: "re.Match") -> str:
        asli = m.group(0)
        pengganti = FRASA_KAKU[asli.lower()]
        return pengganti[0].upper() + pengganti[1:] if asli[0].isupper() else pengganti

    return _POLA_FRASA.sub(_ganti, teks)


# Kata bergaris bawah apa pun yang tersisa. Ditangani generik: garis bawah
# jadi spasi. Lebih baik "sinyal terdeteksi" daripada "sinyal_terdeteksi",
# dan ini menangkap field baru yang belum sempat masuk kamus.
_POLA_SISA = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")

# Dibangun sekali: pola gabungan seluruh kunci kamus, terpanjang lebih dulu
# supaya `taker_buy_sell_ratio` menang atas `buy_sell_ratio`.
_POLA_KAMUS = re.compile(
    r"\b(" + "|".join(sorted(map(re.escape, KAMUS), key=len, reverse=True)) + r")\b"
)


def manusiakan(teks: Any) -> Any:
    """Ganti istilah internal dalam satu string dengan padanan manusiawi.

    Nilai non-string dikembalikan apa adanya supaya fungsi ini aman dipanggil
    pada struktur campuran tanpa perlu pengecekan tipe di sisi pemanggil.
    """
    if not isinstance(teks, str):
        return teks
    # Frasa kaku diperiksa SELALU. Pemeriksaan nama field di bawah masih
    # dibatasi ada-tidaknya garis bawah (itu memang penandanya), tapi frasa
    # seperti "taruhan turun" tidak punya penanda apa pun — membatasinya
    # dengan syarat yang sama berarti ia tidak akan pernah tertangkap.
    hasil = _ganti_frasa(teks)
    if "_" in hasil:
        hasil = _POLA_KAMUS.sub(lambda m: KAMUS[m.group(1)], hasil)
        hasil = _POLA_SISA.sub(lambda m: m.group(0).replace("_", " "), hasil)
    # Dijalankan paling akhir: pengulangannya baru terbentuk setelah semua
    # penggantian di atas selesai.
    return _buang_kurung_mubazir(hasil)


def manusiakan_dalam(obj: Any) -> Any:
    """Terapkan `manusiakan()` ke SETIAP string di dalam struktur bersarang.

    Dipakai pada objek `ai` sebelum brief disusun, jadi apa pun yang ditulis
    LLM — narasi, ringkasan, butir daftar, alasan critic — sudah bersih dari
    nama field sebelum sampai ke web maupun Telegram.
    """
    if isinstance(obj, str):
        return manusiakan(obj)
    if isinstance(obj, dict):
        return {k: manusiakan_dalam(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [manusiakan_dalam(v) for v in obj]
    return obj
