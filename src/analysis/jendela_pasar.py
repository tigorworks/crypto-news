"""Jendela risiko akibat BEDA JAM BUKA kripto dan pasar AS.

Ini menjawab satu pertanyaan yang tidak bisa dijawab data harga: seberapa
berbahaya sebuah kejutan kebijakan KALAU IA DATANG SEKARANG.

Alasannya struktural, bukan firasat. Kripto diperdagangkan 24/7, sementara
bursa saham AS dan ETF spot Bitcoin tutup Jumat pukul 16.00 waktu New York
dan baru buka lagi Senin 09.30 — jeda sekitar 65 jam. Di dalam jeda itu:

  - tidak ada penciptaan/penebusan unit ETF, jadi tidak ada arus institusi
    yang bisa menyerap tekanan jual;
  - meja institusi AS tutup, jadi lindung nilai baru praktis tidak ada;
  - likuiditas order book menipis, sehingga order berukuran sama
    menggerakkan harga lebih jauh.

Akibatnya kejutan kebijakan yang mendarat Jumat malam atau akhir pekan
ditanggung SENDIRIAN oleh pasar kripto sampai Senin. Itu yang membuat
sebagian akhir pekan berakhir berdarah, dan itu pula sebabnya waktu
kedatangan sebuah berita layak dihitung terpisah dari isinya.

Seluruh modul ini murni aritmetika kalender — tidak ada LLM, tidak ada
panggilan jaringan, dan hasilnya sama persis untuk masukan yang sama.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from ..utils.timezone import format_tanggal_singkat, nama_hari, now_utc, to_wib

log = logging.getLogger(__name__)

# Zona bursa AS. Memakai zoneinfo, bukan offset tetap: pergantian EST/EDT
# menggeser jam tutup sebesar satu jam penuh terhadap WIB, dan menuliskannya
# sebagai angka mati akan salah selama separuh tahun.
NY = ZoneInfo("America/New_York")

JAM_BUKA = (9, 30)    # 09.30 ET
JAM_TUTUP = (16, 0)   # 16.00 ET

#: Berapa jam sebelum penutupan Jumat sudah dihitung "jelang jeda". Kejutan
#: yang mendarat di jendela ini tidak sempat direspons pasar AS sebelum
#: tutup, jadi efeknya praktis sama dengan kejutan akhir pekan.
_JAM_JELANG_TUTUP = 3


def _tutup_hari(saat_ny: datetime) -> datetime:
    return saat_ny.replace(hour=JAM_TUTUP[0], minute=JAM_TUTUP[1], second=0, microsecond=0)


def _buka_hari(saat_ny: datetime) -> datetime:
    return saat_ny.replace(hour=JAM_BUKA[0], minute=JAM_BUKA[1], second=0, microsecond=0)


def _buka_berikutnya(saat_ny: datetime) -> datetime:
    """Pembukaan bursa AS berikutnya, melewati akhir pekan.

    Hari libur bursa AS TIDAK diperhitungkan: daftarnya berubah tiap tahun
    dan menebaknya akan salah diam-diam. Akibatnya jendela rawan sesekali
    dilaporkan lebih PENDEK dari kenyataan — arah kesalahan yang aman,
    karena tidak pernah membuat pasar terlihat lebih tenang dari
    sesungguhnya.
    """
    kandidat = _buka_hari(saat_ny)
    if saat_ny >= kandidat:
        kandidat += timedelta(days=1)
    while kandidat.weekday() >= 5:   # 5=Sabtu, 6=Minggu
        kandidat += timedelta(days=1)
    return _buka_hari(kandidat)


def _tutup_terakhir(saat_ny: datetime) -> datetime:
    """Penutupan bursa AS terakhir sebelum `saat_ny`."""
    kandidat = _tutup_hari(saat_ny)
    if saat_ny < kandidat:
        kandidat -= timedelta(days=1)
    while kandidat.weekday() >= 5:
        kandidat -= timedelta(days=1)
    return _tutup_hari(kandidat)


#: Jeda tutup normal antar hari bursa adalah 17,5 jam (16.00 -> 09.30). Jeda
#: yang lebih panjang dari ini berarti akhir pekan sedang berjalan.
_JEDA_NORMAL_JAM = 20

_HARI = ("Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu")


def _label_waktu(saat_ny: datetime) -> str:
    """Contoh: "Jumat 16.00 EDT" — dipakai pembaca untuk menempatkan jeda.

    Tanpa titik awal ini angka panjang jeda gampang disalahbaca sebagai sisa
    waktu: "jeda 65,5 jam" pada hari Senin terdengar seperti bursa baru buka
    tiga hari lagi, padahal 58 jam di antaranya sudah lewat.
    """
    return f"{_HARI[saat_ny.weekday()]} {saat_ny.strftime('%H.%M %Z')}"


def fase_pasar(saat: Optional[datetime] = None) -> Dict[str, Any]:
    """Fase pasar AS saat ini, panjang jeda, dan jarak ke pembukaan.

    Jendela rawan ditentukan dari PANJANG JEDA yang sedang berjalan, bukan
    dari nama harinya. Perbedaannya nyata: Senin pukul 04.00 waktu New York
    masih berada di dalam jeda 65 jam yang dimulai Jumat sore — bursa belum
    buka, ETF belum bisa menyerap apa pun — padahal hari itu sudah bukan
    Sabtu atau Minggu. Memeriksa nama hari melewatkan seluruh pagi Senin,
    justru saat kejutan akhir pekan biasanya mulai dihargai pasar.
    """
    saat = saat or now_utc()
    ny = saat.astimezone(NY)
    hari_kerja = ny.weekday() < 5
    buka, tutup = _buka_hari(ny), _tutup_hari(ny)
    sedang_buka = hari_kerja and buka <= ny < tutup

    # Instant absolut penanda batas fase berikutnya. INI yang dipakai web
    # untuk menghitung mundur sendiri; angka jam di bawah cuma cocok untuk
    # Telegram, yang dibaca dekat waktu kirim.
    tutup_berikutnya = None

    if sedang_buka:
        jam_sampai_buka = 0.0
        panjang_jeda = 0.0
        jeda_mulai = ""
        jeda_berjalan = 0.0
        buka_berikutnya = _buka_berikutnya(ny)
        tutup_berikutnya = tutup
        # Masih buka, tapi tinggal beberapa jam sebelum jeda panjang dimulai:
        # berita yang mendarat di sini tidak sempat dicerna pasar AS.
        jelang = (
            ny.weekday() == 4
            and (tutup - ny).total_seconds() / 3600 <= _JAM_JELANG_TUTUP
        )
        fase = "jelang_tutup_pekan" if jelang else "buka"
    else:
        berikut = _buka_berikutnya(ny)
        sebelum = _tutup_terakhir(ny)
        buka_berikutnya = berikut
        jam_sampai_buka = (berikut - ny).total_seconds() / 3600
        panjang_jeda = (berikut - sebelum).total_seconds() / 3600
        jeda_berjalan = (ny - sebelum).total_seconds() / 3600
        jeda_mulai = _label_waktu(sebelum)
        fase = "jeda_akhir_pekan" if panjang_jeda > _JEDA_NORMAL_JAM else "tutup_harian"

    return {
        "fase": fase,
        "waktu_ny": ny.strftime("%Y-%m-%d %H:%M %Z"),
        "bursa_as_buka": sedang_buka,
        "jam_sampai_buka": round(jam_sampai_buka, 1),
        # Panjang TOTAL jeda dari tutup terakhir sampai buka berikutnya —
        # ukuran "berapa lama pasar kripto menanggung sendirian". Ini BUKAN
        # sisa waktu; pasangkan selalu dengan `jeda_mulai` dan
        # `jeda_berjalan_jam`, karena angka ini sendirian menyesatkan pada
        # Senin pagi (65,5 jam total, tapi 58 di antaranya sudah lewat).
        "panjang_jeda_jam": round(panjang_jeda, 1),
        "jeda_mulai": jeda_mulai,
        "jeda_berjalan_jam": round(jeda_berjalan, 1),
        "dalam_jendela_rawan": fase in ("jeda_akhir_pekan", "jelang_tutup_pekan"),
        # Instant ABSOLUT, bukan selisih. Halaman web dibaca kapan saja —
        # brief pukul 06.12 masih dibuka jam 6 sore — jadi selisih yang
        # dibekukan saat brief dibuat akan berbohong sepanjang hari:
        # "masih 7 jam lagi" ketika jawabannya tinggal 2,5 jam, lalu tetap
        # mengaku bursa tutup setelah bursa buka. Yang absolut tidak bisa
        # basi; sisi web menghitung mundurnya sendiri dari sini.
        "buka_berikutnya_utc": buka_berikutnya.astimezone(timezone.utc).isoformat(),
        "tutup_berikutnya_utc": (
            tutup_berikutnya.astimezone(timezone.utc).isoformat()
            if tutup_berikutnya else None
        ),
        "buka_berikutnya_wib": _label_wib(buka_berikutnya),
    }


def _label_wib(saat: datetime) -> str:
    """Jangkar absolut dalam waktu pembaca: "Senin · 17 Agu · 20:30 WIB".

    Hitung mundur boleh basi kalau JavaScript mati; jangkar ini tidak.
    Karena itu keduanya selalu ditampilkan berdampingan.

    Formatnya SENGAJA sama persis dengan baris agenda, dan dirakit dari
    helper yang sama — dua penanda waktu bersebelahan dengan susunan berbeda
    membuat pembaca mengira keduanya mengukur hal yang berbeda. Tanggal ikut
    karena tanpa itu "Senin 20.30" ambigu begitu jaraknya lewat sehari.
    """
    return f"{nama_hari(saat)} · {format_tanggal_singkat(saat)} · {to_wib(saat):%H:%M} WIB"


def ringkas_untuk_llm(jendela: Dict[str, Any]) -> Dict[str, Any]:
    """Bentuk jendela yang aman disodorkan ke model.

    `panjang_jeda_jam` sengaja TIDAK ikut. Angka itu benar, tapi hanya
    bermakna kalau dipasangkan dengan titik awalnya, dan model terbukti
    memampatkannya jadi sisa waktu: pada run 17 Agustus dua langkah LLM yang
    berbeda sama-sama menulis "jeda 65,5 jam sampai Senin" di hari Senin
    pagi, padahal 58 jam di antaranya sudah lewat dan bursa tinggal tujuh
    jam lagi buka.

    Menutup lubangnya di hulu lebih murah daripada menambal tiap prosa di
    hilir: angka yang tidak pernah diberikan tidak bisa disalahkutip. Total
    jeda tetap tersedia lengkap di `fase_pasar()` untuk web dan Telegram,
    yang kalimatnya ditulis kode dan tidak bisa keliru.
    """
    if not jendela:
        return {}
    aman = {
        "fase": jendela.get("fase"),
        "bursa_as_buka": jendela.get("bursa_as_buka"),
        "sisa_jam_sampai_buka": jendela.get("jam_sampai_buka"),
        "dalam_jendela_rawan": jendela.get("dalam_jendela_rawan"),
        "waktu_ny": jendela.get("waktu_ny"),
    }
    mulai = jendela.get("jeda_mulai")
    if mulai:
        aman["jeda_dimulai"] = mulai
    return aman


def klasifikasi_sinyal(waktu_utc: Optional[str]) -> Dict[str, Any]:
    """Di fase pasar mana sebuah sinyal MENDARAT.

    Sampai sekarang pipeline hanya tahu di mana pasar berada SEKARANG, bukan
    di mana ia berada saat berita atau pernyataannya terbit. Padahal itu
    pembeda yang menentukan: tarif yang diumumkan Rabu siang langsung
    dicerna ETF dan meja institusi, sementara tarif yang sama diumumkan
    Sabtu sore ditanggung pasar kripto sendirian sampai Senin.

    Pada brief 17 Agustus keempat pernyataan Trump — satu di antaranya
    berkekuatan 4 — semuanya mendarat di dalam jeda akhir pekan, dan tidak
    ada satu pun bagian sistem yang mengetahuinya.

    Murni aritmetika kalender: `fase_pasar()` memang sudah menerima waktu
    sembarang, jadi tinggal dipanggil dengan stempel waktu sinyalnya.
    """
    if not waktu_utc:
        return {}
    try:
        saat = datetime.fromisoformat(str(waktu_utc).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return {}
    if saat.tzinfo is None:
        saat = saat.replace(tzinfo=timezone.utc)
    f = fase_pasar(saat)
    return {
        "fase": f["fase"],
        "dalam_jendela_rawan": f["dalam_jendela_rawan"],
        "keterangan": _ARTI_PENDARATAN.get(f["fase"], ""),
    }


_ARTI_PENDARATAN = {
    "buka": "mendarat saat bursa AS buka — bisa langsung dicerna ETF dan meja institusi",
    "tutup_harian": "mendarat setelah bursa tutup — tertunda sampai pembukaan besok",
    "jelang_tutup_pekan": "mendarat menjelang penutupan Jumat — tak sempat dicerna sebelum jeda",
    "jeda_akhir_pekan": "mendarat di dalam jeda akhir pekan — hanya harga kripto yang bereaksi, "
                        "belum diserap ETF atau institusi AS",
}

#: Sinyal selemah ini tidak dihitung dalam ringkasan pendaratan; yang menarik
#: hanya yang cukup kuat untuk benar-benar menggerakkan pasar.
_KEKUATAN_LAYAK_DIHITUNG = 4


def ringkas_pendaratan(sinyal: list) -> Dict[str, Any]:
    """Berapa sinyal KUAT yang mendarat saat pasar AS tidak bisa menyerapnya.

    Dihitung kode, bukan ditafsir model, supaya angkanya bisa dipakai
    critic dan tidak bisa dikarang.
    """
    rawan = [
        s for s in (sinyal or [])
        if (s.get("kekuatan") or 0) >= _KEKUATAN_LAYAK_DIHITUNG
        and (s.get("mendarat") or {}).get("dalam_jendela_rawan")
    ]
    kuat = [s for s in (sinyal or []) if (s.get("kekuatan") or 0) >= _KEKUATAN_LAYAK_DIHITUNG]
    return {
        "kuat": len(kuat),
        "kuat_di_jendela_rawan": len(rawan),
        "ada_yang_tertahan": bool(rawan),
    }


#: Ambang kerapuhan. Tiap butir bernilai satu poin; ambangnya dipilih supaya
#: yang tercatat hanya kondisi yang benar-benar menonjol, bukan fluktuasi
#: harian biasa.
_AMBANG = {
    "volume_tipis": 0.7,        # rasio volume candle harian vs rata-rata 20 hari
    "funding_persisten_jam": 24,
    "divergensi_ritel_pp": 8.0,  # ritel jauh lebih long daripada whale
    "oi_naik_pct": 3.0,
}


def kerapuhan(brief_sebagian: Dict[str, Any]) -> Dict[str, Any]:
    """Seberapa rapuh pasar terhadap kejutan, dari data yang SUDAH ada.

    Bukan ramalan arah — ini ukuran seberapa besar reaksi yang wajar
    diharapkan kalau kejutan datang. Pasar dengan leverage menumpuk, volume
    tipis, dan ritel yang crowded di satu sisi akan bergerak jauh lebih jauh
    untuk berita yang sama dibanding pasar yang tebal dan seimbang.

    Tiap faktor dihitung dari angka yang sudah dikumpulkan pipeline, jadi
    tidak ada panggilan tambahan dan tidak ada tafsiran model.
    """
    tek = brief_sebagian.get("technical") or {}
    harian = tek.get("1d") or {}
    pasar = brief_sebagian.get("market") or {}
    whale = brief_sebagian.get("whale") or {}

    faktor = []

    # Volume dari candle yang belum penuh TIDAK dipakai. Candle harian
    # menumpuk volumenya sepanjang hari, jadi yang baru berjalan separuh
    # otomatis terbaca "tipis" berapa pun ramainya pasar — dan faktor palsu
    # itu ikut menaikkan skor kerapuhan yang jadi gerbang siaga tertinggi.
    # Terjadi nyata pada run manual 17 Agustus 11.28 UTC: candle 11,5 jam,
    # rasio terbaca 0,67x, padahal lajunya menuju sekitar 1,40x.
    volume = harian.get("volume") or {}
    rasio_vol = None if volume.get("parsial") else volume.get("rasio_vs_rata")
    if rasio_vol is not None and rasio_vol < _AMBANG["volume_tipis"]:
        faktor.append({
            "nama": "volume tipis",
            "keterangan": f"volume candle harian {rasio_vol:.2f}x rata-rata 20 hari — "
                          "order berukuran sama menggerakkan harga lebih jauh",
        })

    jam_funding = pasar.get("funding_persisten_jam")
    if jam_funding and jam_funding >= _AMBANG["funding_persisten_jam"]:
        sisi = "long" if (pasar.get("funding_rate") or 0) > 0 else "short"
        faktor.append({
            "nama": "posisi menumpuk satu sisi",
            "keterangan": f"funding bertahan {int(jam_funding)} jam di sisi {sisi} — "
                          "bahan bakar likuidasi beruntun kalau arah berbalik",
        })

    divergensi = whale.get("divergensi")
    if divergensi is not None and divergensi <= -_AMBANG["divergensi_ritel_pp"]:
        faktor.append({
            "nama": "ritel crowded",
            "keterangan": f"ritel {abs(divergensi):.1f} poin persen lebih long daripada "
                          "pemain besar — sisi yang paling cepat terlikuidasi",
        })

    oi_naik = tek.get("oi_change_pct")
    if oi_naik is not None and oi_naik >= _AMBANG["oi_naik_pct"]:
        faktor.append({
            "nama": "leverage bertambah",
            "keterangan": f"open interest naik {oi_naik:.1f}% — posisi baru masuk, "
                          "eksposur pasar meningkat",
        })

    etf = pasar.get("etf_flow_usd")
    if etf is not None and etf < 0:
        faktor.append({
            "nama": "arus ETF keluar",
            "keterangan": f"arus ETF terakhir negatif (${abs(etf)/1e6:,.1f} juta keluar) — "
                          "permintaan institusi sedang tidak menopang",
        })

    skor = len(faktor)
    tingkat = "tinggi" if skor >= 3 else "sedang" if skor == 2 else "rendah"
    return {"skor": skor, "maks": 5, "tingkat": tingkat, "faktor": faktor}


def risiko_jendela(jendela: Dict[str, Any], rapuh: Dict[str, Any]) -> Dict[str, Any]:
    """Bahaya yang datang dari WAKTU, terpisah dari isi kebijakan.

    Dipisahkan karena keduanya memang dua hal berbeda yang sama pentingnya.
    Sebelumnya keduanya dilebur jadi satu tingkat siaga, dan peleburan itu
    merugikan dua arah: kebijakan besar di hari Rabu tertahan di "sedang"
    hanya karena kalendernya biasa, sementara akhir pekan yang sepi berita
    tetap menyeret perhatian hanya karena bursanya tutup.

    Seluruhnya hitungan kode — fase pasar dan kerapuhan sama-sama tidak
    melibatkan model — jadi tingkat ini tidak bisa dikarang.
    """
    rawan = bool(jendela.get("dalam_jendela_rawan"))
    tingkat_rapuh = (rapuh or {}).get("tingkat")
    if not rawan:
        # Di luar jendela rawan TIDAK ADA risiko jendela — sekalipun pasarnya
        # rapuh. Versi sebelumnya menaikkannya ke "sedang" hanya karena
        # kerapuhan tinggi, sehingga panel menyala saat bursa AS justru sedang
        # BUKA: keadaan ketika ETF dan meja institusi siap menyerap tekanan,
        # yaitu kebalikan dari yang mau diperingatkan.
        #
        # Kerapuhan tetap dilaporkan terpisah lewat `kerapuhan()`. Perannya di
        # sini pengali di DALAM jendela, bukan pemicu yang berdiri sendiri.
        tingkat = "rendah"
    elif tingkat_rapuh == "tinggi":
        tingkat = "tinggi"
    else:
        tingkat = "sedang"
    return {
        "tingkat": tingkat,
        "fase": jendela.get("fase"),
        "dalam_jendela_rawan": rawan,
        "kerapuhan": tingkat_rapuh,
    }


def rangkuman_kode(
    jendela: Dict[str, Any],
    rapuh: Dict[str, Any],
    pernyataan: Optional[list] = None,
    berita: Optional[list] = None,
) -> Dict[str, Any]:
    """Blok `agen_kebijakan` di brief, dirakit TANPA satu pun panggilan model.

    Dulu blok ini keluaran sebuah langkah LLM tersendiri, dan bagian
    prosanya (siaga, ringkasan, pemicu, skenario) tidak pernah dirender di
    mana pun setelah kebijakan AS pindah ke dalam analisa AI. Yang benar-
    benar dipakai halaman dan Telegram cuma empat hal di bawah ini,
    semuanya aritmetika kalender dan data yang sudah dikumpulkan.

    Karena tidak lagi bergantung model, panel jendela risiko kini tetap
    lengkap justru pada hari yang paling membutuhkannya: hari ketika
    OpenRouter bermasalah dan seluruh langkah AI gagal.

    `pernyataan` dan `berita` dipakai sekadar menghitung berapa sinyal KUAT
    yang mendarat saat pasar AS tidak bisa menyerapnya — bukan untuk
    menafsirkan isinya.
    """
    sinyal = [
        {
            "kekuatan": x.get("kekuatan") or 0,
            "mendarat": klasifikasi_sinyal(x.get("waktu_utc")),
        }
        for x in list(pernyataan or []) + list(berita or [])
    ]
    return {
        "jendela": jendela,
        "kerapuhan": rapuh,
        "risiko_jendela": risiko_jendela(jendela, rapuh),
        "pendaratan": ringkas_pendaratan(sinyal),
    }
