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
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from ..utils.timezone import now_utc

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

    if sedang_buka:
        jam_sampai_buka = 0.0
        panjang_jeda = 0.0
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
        jam_sampai_buka = (berikut - ny).total_seconds() / 3600
        panjang_jeda = (berikut - sebelum).total_seconds() / 3600
        fase = "jeda_akhir_pekan" if panjang_jeda > _JEDA_NORMAL_JAM else "tutup_harian"

    return {
        "fase": fase,
        "waktu_ny": ny.strftime("%Y-%m-%d %H:%M %Z"),
        "bursa_as_buka": sedang_buka,
        "jam_sampai_buka": round(jam_sampai_buka, 1),
        # Panjang total jeda yang sedang berjalan — inilah ukuran sebenarnya
        # dari "berapa lama pasar kripto menanggung sendirian".
        "panjang_jeda_jam": round(panjang_jeda, 1),
        "dalam_jendela_rawan": fase in ("jeda_akhir_pekan", "jelang_tutup_pekan"),
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

    rasio_vol = (harian.get("volume") or {}).get("rasio_vs_rata")
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
