"""Kartu "Sinyal Palsu" harus menjelaskan dirinya, bukan cuma melaporkan angka.

Sebelumnya kartunya berbunyi persis satu kalimat: "Harga menembus swing high
79.500 hingga 80.000 lalu ditutup kembali di 78.993 — level dipicu tanpa
diikuti." Benar, padat, dan tidak bisa dibaca oleh siapa pun yang belum tahu
apa itu swing high. Yang dijaga di sini:

  1. Setiap pola yang BISA dideteksi punya penjelasannya — pola baru yang lupa
     diberi entri gagal di sini, bukan diam-diam terbit telanjang.
  2. Teks di halaman (docs/app.js, cadangan untuk arsip lama) identik huruf
     per huruf dengan teks di pipeline. Dua versi yang berbeda adalah kegagalan
     diam: tidak ada error, cuma arsip yang berbunyi lain dari brief hari ini.
  3. Paragraf penjelas TIDAK ikut dikirim ke LLM — isinya tetap dan sudah ada
     di prompt masing-masing langkah, jadi mengirimnya cuma menambah token.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from src.analysis import technical
from src.analysis.technical import (
    _ARTI_SINGKAT_POLA,
    _CATATAN_VOLUME_SAPUAN,
    _PENJELASAN_POLA,
    arti_singkat_pola,
    deteksi_sinyal_palsu,
    penjelasan_pola,
    sinyal_tanpa_penjelasan,
)

ROOT = Path(__file__).resolve().parent.parent
APP_JS = ROOT / "docs" / "app.js"

#: Semua nilai `"jenis": "..."` yang benar-benar ditulis deteksi_sinyal_palsu().
#: Dibaca dari sumbernya, bukan didaftar ulang dengan tangan: daftar tangan
#: ikut basi bersama pola yang ditambahkan nanti, dan justru pola BARU yang
#: paling mungkin lupa diberi penjelasan.
def _jenis_yang_dideteksi() -> set:
    sumber = (ROOT / "src" / "analysis" / "technical.py").read_text(encoding="utf-8")
    awal = sumber.index("def deteksi_sinyal_palsu(")
    akhir = sumber.index("\ndef ", awal + 1)
    return set(re.findall(r'"jenis":\s*"([a-z_]+)"', sumber[awal:akhir]))


def test_setiap_pola_yang_dideteksi_punya_penjelasan():
    jenis = _jenis_yang_dideteksi()
    assert jenis, "tidak ada pola terbaca dari deteksi_sinyal_palsu()"

    tanpa_paragraf = sorted(jenis - set(_PENJELASAN_POLA))
    assert not tanpa_paragraf, (
        f"pola tanpa entri di _PENJELASAN_POLA: {tanpa_paragraf}"
    )
    tanpa_ringkas = sorted(jenis - set(_ARTI_SINGKAT_POLA))
    assert not tanpa_ringkas, (
        f"pola tanpa entri di _ARTI_SINGKAT_POLA (dipakai Telegram): {tanpa_ringkas}"
    )


def test_penjelasan_urut_dan_lengkap():
    """Urutannya tetap: apa yang diukur, apa artinya, lalu apa yang membatalkan."""
    for jenis, bagian in _PENJELASAN_POLA.items():
        paragraf = penjelasan_pola(jenis, kekuatan=3)
        assert paragraf[0] == bagian["cara_ukur"], jenis
        assert paragraf[1] == bagian["arti"], jenis
        assert paragraf[-1] == bagian["pembatal"], jenis
        # Bagian ketiga yang paling sering hilang di penjelasan pola candle,
        # dan justru itu yang menjaga petunjuk tidak dibaca sebagai kepastian.
        assert "batal" in bagian["pembatal"].lower() or "gugur" in bagian["pembatal"].lower() \
            or "mereda" in bagian["pembatal"].lower() or "terbaca" in bagian["pembatal"].lower(), jenis


def test_catatan_volume_hanya_untuk_sapuan():
    """`kekuatan` cuma bervariasi pada sapuan; pola lain nilainya tetap."""
    kuat = penjelasan_pola("sapuan_likuiditas_atas", kekuatan=4)
    lemah = penjelasan_pola("sapuan_likuiditas_atas", kekuatan=3)
    assert _CATATAN_VOLUME_SAPUAN[4] in kuat
    assert _CATATAN_VOLUME_SAPUAN[3] in lemah
    assert len(kuat) == 4 and len(lemah) == 4

    # Pola lain tidak boleh membawa catatan volume: "kekuatan 4" di sana adalah
    # nilai tetap dan tidak menyatakan apa pun soal volume.
    lain = penjelasan_pola("posisi_padat", kekuatan=4)
    assert len(lain) == 3
    assert _CATATAN_VOLUME_SAPUAN[4] not in lain


def test_pola_tak_dikenal_tidak_dijelaskan_seadanya():
    assert penjelasan_pola("pola_yang_belum_ada", 4) == []
    assert arti_singkat_pola("pola_yang_belum_ada") == ""


def _klines_sapuan():
    """Candle sintetis yang pasti memicu sapuan likuiditas di atas.

    Empat puluh candle datar di 100, lalu satu candle yang menembus sampai 120
    tapi ditutup kembali di 99.
    """
    dasar = [
        {"open_time": i * 86_400_000, "open": 100.0, "high": 101.0,
         "low": 99.0, "close": 100.0, "volume": 10.0}
        for i in range(40)
    ]
    dasar[-1] = {**dasar[-1], "high": 120.0, "close": 99.0, "volume": 50.0}
    return dasar


def test_deteksi_melampirkan_penjelasan():
    sinyal = deteksi_sinyal_palsu(_klines_sapuan())
    sapuan = [s for s in sinyal if s["jenis"] == "sapuan_likuiditas_atas"]
    assert sapuan, "candle uji seharusnya memicu sapuan likuiditas di atas"

    s = sapuan[0]
    assert s["penjelasan"] == penjelasan_pola(s["jenis"], s["kekuatan"])
    assert s["arti_singkat"] == arti_singkat_pola(s["jenis"])
    # Angkanya tetap milik `keterangan`; paragraf penjelas tidak mengulangnya,
    # jadi tidak ada dua tempat yang bisa saling bertentangan.
    assert all("120" not in p for p in s["penjelasan"])


def test_penjelasan_tidak_ikut_dikirim_ke_llm():
    sinyal = deteksi_sinyal_palsu(_klines_sapuan())
    assert sinyal and "penjelasan" in sinyal[0]

    ramping = sinyal_tanpa_penjelasan(sinyal)
    assert all("penjelasan" not in s and "arti_singkat" not in s for s in ramping)
    # Fakta terukurnya harus utuh — yang dibuang cuma lapisan penjelas.
    assert ramping[0]["keterangan"] == sinyal[0]["keterangan"]
    assert ramping[0]["kekuatan"] == sinyal[0]["kekuatan"]
    # Salinan, bukan mutasi: sinyal aslinya masih dipakai halaman dan Telegram.
    assert "penjelasan" in sinyal[0]


def _blok_js(nama: str) -> dict:
    """Ambil satu konstanta objek dari app.js dan parse sebagai JSON.

    Blok-blok itu memang ditulis dalam bentuk yang bisa diparse apa adanya
    supaya perbandingannya bisa dilakukan huruf per huruf di sini.
    """
    sumber = APP_JS.read_text(encoding="utf-8")
    cocok = re.search(rf"^const {nama} = (\{{.*?^\}});$", sumber, re.DOTALL | re.MULTILINE)
    assert cocok, f"konstanta {nama} tidak ditemukan di docs/app.js"
    return json.loads(cocok.group(1))


def test_teks_halaman_identik_dengan_pipeline():
    """Cadangan arsip di app.js tidak boleh menyimpang dari sumbernya.

    Kalau uji ini gagal: ubah teksnya di src/analysis/technical.py, lalu
    regenerasi bloknya di docs/app.js — jangan menyunting keduanya terpisah.
    """
    assert _blok_js("PENJELASAN_POLA") == _PENJELASAN_POLA
    assert _blok_js("CATATAN_VOLUME_SAPUAN") == {
        str(k): v for k, v in _CATATAN_VOLUME_SAPUAN.items()
    }


def test_label_pola_di_halaman_meliputi_semua_pola():
    """Setiap pola yang punya penjelasan juga punya judulnya di kartu."""
    sumber = APP_JS.read_text(encoding="utf-8")
    awal = sumber.index("labelPola(jenis) {")
    blok = sumber[awal:sumber.index("},", awal)]
    hilang = sorted(j for j in _PENJELASAN_POLA if f"{j}:" not in blok)
    assert not hilang, f"pola tanpa label di labelPola(): {hilang}"


def test_kartu_merender_paragraf_penjelas():
    """Template kartu memanggil penjelasanSinyal(), bukan sekadar keterangan."""
    html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    assert "penjelasanSinyal(s)" in html, (
        "kartu Sinyal Palsu kembali cuma menampilkan kalimat berangka"
    )


def test_skrip_lengkapi_idempoten(tmp_path):
    from scripts.lengkapi_penjelasan_sinyal import lengkapi

    sinyal = {"jenis": "sapuan_likuiditas_bawah", "arah": "bullish",
              "keterangan": "...", "kekuatan": 3, "timeframe": "1d"}
    brief = {"technical": {"sinyal_palsu": [sinyal]}}

    assert lengkapi(brief), "brief lama seharusnya dilengkapi"
    assert brief["technical"]["sinyal_palsu"][0]["penjelasan"] == \
        penjelasan_pola("sapuan_likuiditas_bawah", 3)
    # Jalan kedua tidak boleh mengubah apa pun — skrip ini memang dijalankan
    # ulang setiap kali teksnya diperbaiki.
    assert lengkapi(brief) == []


def test_brief_terakhir_sudah_dilengkapi():
    """latest.json yang sedang tampil tidak boleh tertinggal dari kodenya.

    Menjaga langkah yang paling gampang lupa: teks di kode sudah diperbaiki,
    tapi brief yang dibaca pembaca hari ini masih membawa versi telanjangnya
    sampai run berikutnya belasan jam kemudian.
    """
    brief = json.loads((ROOT / "docs" / "data" / "latest.json").read_text(encoding="utf-8"))
    sinyal = (brief.get("technical") or {}).get("sinyal_palsu") or []
    for s in sinyal:
        if s.get("jenis") not in _PENJELASAN_POLA:
            continue
        assert s.get("penjelasan"), (
            f"sinyal `{s['jenis']}` di latest.json belum punya penjelasan — "
            "jalankan: python -m scripts.lengkapi_penjelasan_sinyal"
        )


def test_telegram_menyertakan_arti_bukan_cuma_angka():
    from src.output.telegram import _blok_sinyal_palsu

    brief = {"technical": {"sinyal_palsu": [{
        "jenis": "sapuan_likuiditas_atas", "arah": "bearish",
        "keterangan": "Harga menembus swing high 79.500.", "kekuatan": 4,
    }]}}
    baris = _blok_sinyal_palsu(brief)
    teks = "\n".join(baris)
    assert "79.500" in teks
    assert arti_singkat_pola("sapuan_likuiditas_atas") in teks, (
        "pesan Telegram kembali cuma memuat kalimat berangka"
    )


def test_telegram_merakit_arti_untuk_brief_lama():
    """Brief yang terbit sebelum perubahan ini tetap dapat kalimat artinya."""
    from src.output.telegram import _blok_sinyal_palsu

    brief = {"technical": {"sinyal_palsu": [{
        "jenis": "absorpsi_volume", "arah": "netral",
        "keterangan": "Volume 3,0x rata-rata.", "kekuatan": 4,
    }]}}
    teks = "\n".join(_blok_sinyal_palsu(brief))
    assert technical.arti_singkat_pola("absorpsi_volume") in teks


# --------------------------------------------------------------------------
# Halaman
# --------------------------------------------------------------------------
def _muat(peramban, alamat):
    page = peramban.new_page()
    page.goto(alamat)
    page.wait_for_selector("#s-whale", timeout=10_000)
    return page


def test_halaman_menampilkan_penjelasan(peramban, alamat, tulis_data, brief_asli):
    import copy

    brief = copy.deepcopy(brief_asli)
    brief["technical"]["sinyal_palsu"] = [{
        "jenis": "sapuan_likuiditas_atas", "arah": "bearish", "kekuatan": 4,
        "timeframe": "1d", "keterangan": "Harga menembus swing high 79.500.",
        "penjelasan": penjelasan_pola("sapuan_likuiditas_atas", 4),
        "arti_singkat": arti_singkat_pola("sapuan_likuiditas_atas"),
    }]
    tulis_data(brief)

    teks = _muat(peramban, alamat).inner_text("#s-whale")
    assert "Sapuan likuiditas di atas" in teks
    assert "79.500" in teks
    for paragraf in penjelasan_pola("sapuan_likuiditas_atas", 4):
        assert paragraf in teks, f"paragraf hilang dari kartu: {paragraf[:50]}…"


def test_arsip_lama_tetap_dijelaskan(peramban, alamat, tulis_data, brief_asli):
    """Brief tanpa field `penjelasan` dirakit di sisi halaman.

    Tanpa cadangan ini seluruh arsip yang terbit sebelum 26 Agustus 2026
    selamanya cuma memuat kalimat berangkanya.
    """
    import copy

    brief = copy.deepcopy(brief_asli)
    brief["technical"]["sinyal_palsu"] = [{
        "jenis": "absorpsi_volume", "arah": "netral", "kekuatan": 4,
        "timeframe": "1d", "keterangan": "Volume 3,0x rata-rata.",
    }]
    tulis_data(brief)

    teks = _muat(peramban, alamat).inner_text("#s-whale")
    for paragraf in penjelasan_pola("absorpsi_volume", 4):
        assert paragraf in teks, f"cadangan halaman tidak merakit: {paragraf[:50]}…"
