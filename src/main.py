"""Orkestrator pipeline Bitcoin Market Brief.

Jalankan: python -m src.main [--dry-run]

Urutan langkah mengikuti BUILD.md. Hanya langkah 1 (harga) yang fatal;
sisanya boleh gagal sebagian dan dicatat di data_quality.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .analysis import news_analysis, riset, technical
from .analysis.llm import LLMClient
from .collectors import (
    binance,
    calendar as calendar_collector,
    ff_calendar,
    flows,
    investing,
    macro,
    market,
    news,
    okx,
    onchain,
    options,
    statements as statements_collector,
    whale,
)
from .config import Config, SUBSCRIBERS_PATH, load_config
from .output import builder, stylist, subscribers, telegram
from .utils import istilah
from .utils.timezone import format_wib, iso_utc, now_utc

log = logging.getLogger("brief")


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)-24s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    # yfinance dan urllib3 sangat berisik di level INFO.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("yfinance").setLevel(logging.ERROR)
    logging.getLogger("peewee").setLevel(logging.ERROR)


def _ringkas_narasi(narasi: str, maks_kalimat: int = 3, maks_karakter: int = 600) -> str:
    """Ambil beberapa kalimat pertama untuk versi Telegram.

    Dipotong di kode, bukan lewat panggilan LLM tambahan — lebih murah dan
    hasilnya pasti konsisten dengan narasi di web.
    """
    if not narasi:
        return ""
    paragraf_pertama = narasi.split("\n\n")[0].strip()
    kalimat = re.split(r"(?<=[.!?])\s+", paragraf_pertama)
    hasil = " ".join(kalimat[:maks_kalimat]).strip()
    if len(hasil) > maks_karakter:
        hasil = hasil[:maks_karakter].rsplit(" ", 1)[0] + "…"
    return hasil


#: Batas umur angka arus ETF yang masih boleh dipakai ulang. Angka ini
#: terbit harian; memakai ulang selama sumbernya gagal masih jujur karena
#: tanggalnya ikut ditampilkan, tapi TANPA batas ia bisa bertahan berminggu-
#: minggu dan terbaca sebagai kondisi hari ini.
_ETF_MAKS_HARI = 7


def _etf_terlalu_tua(tanggal: Optional[str]) -> bool:
    """True kalau tanggal arus ETF sudah melewati batas pakai ulang.

    Format tanggal mengikuti apa yang discrape market._etf_flow(): "15 Aug
    2026" atau "2026-08-15". Tanggal yang tidak bisa diparsing TIDAK dianggap
    tua — lebih baik memakai angka yang mungkin agak lama daripada membuang
    data karena format sumbernya berubah.
    """
    if not tanggal:
        return False
    for pola in ("%d %b %Y", "%Y-%m-%d"):
        try:
            terbit = datetime.strptime(str(tanggal).strip(), pola).replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue
        umur = (now_utc() - terbit).days
        if umur > _ETF_MAKS_HARI:
            log.warning(
                "Arus ETF terakhir (%s) sudah %d hari, tidak dipakai ulang lagi",
                tanggal, umur,
            )
            return True
        return False
    return False


def _konteks_llm(
    price: Dict[str, Any],
    teknikal: Dict[str, Any],
    pasar: Dict[str, Any],
    makro: Dict[str, Any],
    berita: List[Dict[str, Any]],
    agregat: Dict[str, Any],
    conflicts: List[Dict[str, Any]],
    diff: Dict[str, Any],
    posisi_whale: Dict[str, Any],
    agenda: List[Dict[str, Any]],
    pernyataan: List[Dict[str, Any]],
    opsi: Dict[str, Any],
    onchain_data: Dict[str, Any],
    aliran: Dict[str, Any],
) -> Dict[str, Any]:
    """Konteks ringkas untuk sintesis & critic.

    Sengaja tidak mengirim seluruh brief: hemat token, dan critic lebih mudah
    memverifikasi angka kalau daftar angkanya terbatas.
    """
    return {
        "harga": price,
        # Klasifikasi pergerakan 24 jam yang SUDAH dihitung kode. Dikirim ke
        # model bukan supaya ia menghitung ulang, tapi supaya penjelasannya
        # bertumpu pada arah dan jenis yang sama dengan yang dilihat pembaca
        # di halaman — dan supaya critic punya patokan untuk memeriksanya.
        "pergerakan_24j": teknikal.get("pergerakan_24j"),
        # Hanya candle harian. Sebelumnya 4H dan 1H ikut dikirim, dan itu
        # justru menimbulkan masalah: model menulis EMA 1H lalu critic
        # mencocokkannya dengan EMA 4H dan memvonisnya karangan. Untuk laporan
        # harian, satu timeframe menghapus seluruh kelas kesalahan itu.
        "teknikal_1d": teknikal.get("1d"),
        "level_kunci": teknikal.get("key_levels"),
        "sinyal_oi": {
            "sinyal": teknikal.get("oi_price_signal"),
            "interpretasi": teknikal.get("oi_price_interpretasi"),
            "perubahan_oi_pct": teknikal.get("oi_change_pct"),
        },
        "sinyal_palsu_terdeteksi": teknikal.get("sinyal_palsu") or [],
        "posisi_whale": posisi_whale,
        "pasar": pasar,
        # Data posisi institusional dan valuasi on-chain. Ini yang membedakan
        # analisa dari sekadar membaca grafik harga.
        "opsi_deribit": opsi,
        "valuasi_onchain": onchain_data,
        "aliran_dana": aliran,
        "makro": makro,
        "agenda_mendatang": agenda,
        "sentimen_agregat": agregat,
        "berita": [
            {
                "judul": b.get("judul"),
                # Sebelumnya hanya judul yang dikirim. Model lalu mengarang
                # detail (persentase, nilai dolar, nama entitas) yang
                # kedengarannya masuk akal untuk judul itu tapi tidak pernah
                # ada di data — critic menangkapnya sebagai pengetahuan_luar.
                # Ringkasan aslinya sudah ada sejak langkah klasifikasi;
                # sekadar tidak pernah diteruskan ke sini.
                "ringkasan": b.get("ringkasan_id") or (b.get("ringkasan") or "")[:280],
                "sumber": b.get("sumber"),
                "kategori": b.get("kategori"),
                "sentimen": b.get("sentimen"),
                "kekuatan": b.get("kekuatan"),
                "status_kepastian": b.get("status_kepastian"),
                "mekanisme": b.get("mekanisme"),
                "reaksi_harga_1j": b.get("reaksi_harga_1j"),
            }
            for b in berita[:12]
        ],
        "pernyataan_tokoh": [
            {
                "tokoh": p.get("tokoh"),
                "ringkasan": p.get("ringkasan"),
                "kutipan": p.get("kutipan"),
                "topik": p.get("topik"),
                "dampak_btc": p.get("dampak_btc"),
                "kekuatan": p.get("kekuatan"),
                "status": p.get("status"),
                "mekanisme": p.get("mekanisme"),
                "waktu_utc": p.get("waktu_utc"),
            }
            for p in pernyataan[:8]
        ],
        "sinyal_bertentangan": conflicts,
        "perubahan_vs_sebelumnya": diff.get("ringkasan") if diff else None,
    }


def _kumpulkan_penerima(cfg: Config, dry_run: bool, catatan: List[str]):
    """Gabungkan penerima tetap dan pelanggan bot.

    TELEGRAM_CHAT_ID boleh berisi beberapa ID dipisah koma. Pelanggan dari
    perintah /start ditambahkan kalau fiturnya aktif dan kuncinya tersedia.
    """
    penerima: List[str] = []
    if cfg.secrets.telegram_chat_id:
        penerima.extend(
            bagian.strip()
            for bagian in cfg.secrets.telegram_chat_id.split(",")
            if bagian.strip()
        )

    state = None
    if not cfg.telegram.get("aktifkan_pelanggan", True):
        pass
    elif not cfg.secrets.telegram_subscriber_key:
        pesan = (
            "Fitur pelanggan dimatikan: TELEGRAM_SUBSCRIBER_KEY belum diisi. "
            "Tanpa kunci itu daftar chat ID akan tersimpan sebagai teks biasa "
            "di repo, jadi fiturnya sengaja tidak diaktifkan diam-diam."
        )
        log.warning(pesan)
        catatan.append(pesan)
    elif not cfg.secrets.telegram_token:
        log.warning("TELEGRAM_TOKEN kosong; daftar pelanggan tidak bisa disinkronkan")
    else:
        state = subscribers.muat(SUBSCRIBERS_PATH, cfg.secrets.telegram_subscriber_key)
        state, baru, keluar = subscribers.sinkronkan(cfg.secrets.telegram_token, state)

        # Sapaan dikirim saat itu juga supaya pendaftar tahu statusnya
        # sekarang, bukan menunggu brief berikutnya.
        if not dry_run:
            for chat_id in baru:
                telegram.kirim(
                    cfg.secrets.telegram_token, chat_id, subscribers.PESAN_SELAMAT_DATANG
                )
            for chat_id in keluar:
                telegram.kirim(
                    cfg.secrets.telegram_token, chat_id, subscribers.PESAN_BERHENTI
                )
            subscribers.simpan(
                SUBSCRIBERS_PATH, cfg.secrets.telegram_subscriber_key, state
            )

        penerima.extend(subscribers.daftar_chat(state))

    # Satu chat bisa muncul sebagai penerima tetap sekaligus pelanggan.
    unik = list(dict.fromkeys(penerima))
    if unik:
        log.info("Penerima Telegram: %d chat", len(unik))
    return unik, state


def jalankan(cfg: Config, dry_run: bool = False) -> Dict[str, Any]:
    # monotonic(), bukan time(): kebal terhadap jam sistem yang digeser NTP
    # di tengah run — durasi tidak boleh bisa jadi negatif.
    mulai_run = time.monotonic()
    gagal: List[str] = []
    catatan: List[str] = []

    # Brief sebelumnya dibaca sekali di awal: dipakai untuk menurunkan
    # perubahan OI, menambal arus ETF yang gagal di-scrape, dan menghitung diff.
    sebelumnya = builder.brief_sebelumnya()

    # -- 1. Harga + klines (FATAL kalau gagal) --------------------------
    log.info("[1/21] Ambil harga dan klines")
    # Candle reaksi diambil terpisah dari timeframe analisa: dipakai hanya untuk
    # mengukur gerak harga satu jam setelah berita terbit, tidak pernah
    # ditampilkan maupun dikirim ke LLM sebagai timeframe.
    tf_diambil = list(dict.fromkeys([*cfg.timeframes, cfg.timeframe_reaksi]))
    data_harga = binance.fetch_price_and_klines(cfg.symbol, tf_diambil, cfg.candle_limit)
    price = data_harga["price"]
    klines = data_harga["klines"]
    # Hanya CoinGecko yang perlu dicatat: candle-nya di-RESAMPLE dari deret
    # harga, jadi high/low/volume cuma perkiraan dan itu memengaruhi seluruh
    # indikator teknikal. OKX menyediakan OHLCV sungguhan — setara Binance
    # untuk keperluan analisa — jadi memakainya bukan penurunan mutu dan
    # tidak perlu diperingatkan.
    if data_harga["source"] == "coingecko":
        catatan.append(
            "Harga memakai sumber cadangan CoinGecko; candle merupakan hasil resampling."
        )

    # -- 2. Indikator teknikal ------------------------------------------
    log.info("[2/21] Hitung indikator teknikal")
    funding = binance.fetch_funding_rate(cfg.symbol)
    open_interest = binance.fetch_open_interest(cfg.symbol)
    oi_history = binance.fetch_open_interest_history(cfg.symbol)
    if funding is None and open_interest is None:
        gagal.append("funding_oi")

    # Tidak ada bursa yang menyediakan riwayat OI dari IP runner ini, jadi
    # perubahannya diturunkan dari brief sebelumnya. Satu titik pembanding
    # sehari cukup untuk sinyal OI-vs-harga, dan jauh lebih baik daripada
    # kehilangan sinyalnya sama sekali.
    if not oi_history and open_interest is not None:
        oi_lama = ((sebelumnya or {}).get("market") or {}).get("open_interest")
        if oi_lama:
            oi_history = [
                {"timestamp": 0, "open_interest": float(oi_lama)},
                {"timestamp": 1, "open_interest": float(open_interest)},
            ]
            log.info(
                "Riwayat OI tidak tersedia; perubahan dihitung dari brief sebelumnya "
                "(%.0f -> %.0f)", float(oi_lama), float(open_interest),
            )

    klines_analisa = {tf: klines.get(tf, []) for tf in cfg.timeframes}
    teknikal = technical.analyze(klines_analisa, price, oi_history, funding)
    if not teknikal.get("1d"):
        gagal.append("technical")
    if teknikal.get("sinyal_palsu"):
        log.info("Sinyal mencurigakan terdeteksi: %d pola", len(teknikal["sinyal_palsu"]))

    # -- 3. Data pasar + posisi whale ------------------------------------
    log.info("[3/21] Ambil data pasar dan posisi whale")
    hasil_pasar = market.collect(cfg.symbol, cfg.secrets.soso_api_key)
    gagal.extend(hasil_pasar["failed"])
    # Funding rate SAAT INI bisa positif/negatif tanpa berarti apa-apa; yang
    # membedakan sinyal kuat dari derau adalah SUDAH BERAPA LAMA bertahan di
    # sisi yang sama. best-effort: gagal di sini tidak menggagalkan apa pun,
    # cuma funding_persisten_jam/funding_rata_7h_pct jadi None.
    funding_tren = okx.tren_funding(okx.fetch_funding_rate_history())
    pasar = {
        "funding_rate": funding,
        "open_interest": open_interest,
        **funding_tren,
        **hasil_pasar["data"],
    }

    # Arus ETF hanya bisa di-scrape dari halaman HTML pihak ketiga, jadi
    # sesekali gagal. Angkanya sendiri terbit sekali sehari dan SELALU membawa
    # tanggalnya sendiri, jadi memakai ulang nilai terakhir yang diketahui
    # tetap jujur — pembaca melihat tanggal aslinya. Yang tidak boleh adalah
    # menampilkannya seolah baru, karena itu ada penanda kedaluwarsa.
    if pasar.get("etf_flow_usd") is None:
        pasar_lama = (sebelumnya or {}).get("market") or {}
        if pasar_lama.get("etf_flow_usd") is not None and not _etf_terlalu_tua(
            pasar_lama.get("etf_flow_date")
        ):
            pasar["etf_flow_usd"] = pasar_lama["etf_flow_usd"]
            pasar["etf_flow_date"] = pasar_lama.get("etf_flow_date")
            pasar["etf_flow_kedaluwarsa"] = True
            if "etf_flow" in gagal:
                gagal.remove("etf_flow")
            catatan.append(
                "Arus ETF gagal diambil; dipakai angka terakhir yang diketahui "
                f"(tanggal {pasar.get('etf_flow_date') or 'tidak diketahui'})."
            )
            log.info("Arus ETF memakai nilai terakhir yang diketahui")

    hasil_whale = whale.collect(cfg.symbol)
    posisi_whale = hasil_whale["data"]
    # Sumber ini punya tiga jalur cadangan; yang menentukan gagal atau tidak
    # adalah DATA yang akhirnya didapat, bukan berapa jalur yang sempat error.
    # Sebelumnya satu percobaan gagal sudah cukup menandai sumbernya gagal,
    # padahal cadangannya berhasil — dan itu menyeret skor kualitas tiap run.
    ada_posisi = (
        posisi_whale.get("whale_long_pct") is not None
        or posisi_whale.get("ritel_long_pct") is not None
    )
    if not ada_posisi:
        gagal.append("whale")
        catatan.append("Data posisi whale tidak tersedia; analisa manipulasi dilewati.")
    elif posisi_whale.get("whale_long_pct") is None:
        catatan.append(
            "Rasio posisi pemain besar tidak tersedia; hanya sisi ritel yang terbaca."
        )

    # -- 4. Opsi, valuasi on-chain, aliran dana --------------------------
    log.info("[4/21] Ambil data opsi, valuasi on-chain, dan aliran dana")
    hasil_opsi = options.collect()
    opsi = hasil_opsi["data"]
    gagal.extend(hasil_opsi["failed"])

    # Volatilitas realized dari candle harian yang SUDAH ADA (tanpa sumber
    # tambahan), dibandingkan dengan DVOL (implied) — rasio IV/RV menandakan
    # opsi mahal/murah relatif terhadap volatilitas yang sungguhan terjadi.
    # Nama field pakai "30hari" secara eksplisit, BUKAN "30h" — singkatan itu
    # pernah terbaca "30 hours" oleh LLM dan membuat narasi salah tulis
    # jangka waktu (lihat PR pembetulan _perubahan_30hari_pct).
    opsi["realized_vol_30hari_pct"] = technical.volatilitas_realized_tahunan(
        klines_analisa.get("1d", [])
    )
    if opsi.get("dvol") is not None and opsi.get("realized_vol_30hari_pct"):
        opsi["iv_rv_ratio"] = round(opsi["dvol"] / opsi["realized_vol_30hari_pct"], 2)

    hasil_onchain = onchain.collect()
    onchain_data = hasil_onchain["data"]
    gagal.extend(hasil_onchain["failed"])

    hasil_aliran = flows.collect(price.get("last"))
    aliran = hasil_aliran["data"]
    gagal.extend(hasil_aliran["failed"])

    # -- 5. Makro --------------------------------------------------------
    log.info("[5/21] Ambil data makro")
    hasil_makro = macro.collect(cfg.secrets.fred_api_key)
    gagal.extend(hasil_makro["failed"])
    makro = hasil_makro["data"]

    # Klien LLM dibuat SEBELUM berita diambil supaya langkah riset di bawah
    # bisa ikut menentukan sumber apa yang dicari hari ini.
    client: Optional[LLMClient] = None
    if cfg.secrets.llm_enabled:
        client = LLMClient(
            api_key=cfg.secrets.openrouter_api_key,
            base_url=cfg.llm_base_url,
            max_cost_usd=cfg.max_cost_usd,
            referer=cfg.repo_url,
        )
    else:
        catatan.append("OPENROUTER_API_KEY kosong; seluruh langkah LLM dilewati.")
        log.warning("OPENROUTER_API_KEY kosong, pipeline berjalan tanpa analisa AI")

    # -- 6. Berita -------------------------------------------------------
    # Feed tetap dari config, ditambah feed hasil riset: model murah
    # mengusulkan apa yang layak dicari hari ini, lalu KODE yang mengambil
    # artikelnya lewat Google News RSS. Model tidak pernah menghasilkan
    # berita, judul, atau URL — semuanya tetap dari feed sungguhan dan
    # melewati penyaringan yang sama persis dengan feed tetap.
    query_riset: List[str] = []
    if client and cfg.news.get("riset_dinamis", True):
        log.info("[6a/21] LLM riset arah pencarian berita")
        query_riset = riset.usulkan_query(
            client,
            cfg.llm_models("riset"),
            {
                "harga_btc": price.get("last"),
                "perubahan_24j_pct": price.get("change_24h_pct"),
                "tema_laporan_sebelumnya": (
                    (sebelumnya or {}).get("aggregate", {}).get("dominant_themes") or []
                ),
                "pergeseran_narasi_sebelumnya": (
                    (sebelumnya or {}).get("aggregate", {}).get("narrative_shift") or ""
                ),
                "tanggal_wib": format_wib(now_utc()),
            },
        )
        if query_riset:
            catatan.append("Riset berita tambahan: " + "; ".join(query_riset))

    log.info("[6/21] Ambil berita RSS")
    hasil_berita = news.collect(
        list(cfg.news.get("feeds", [])) + riset.feed_dari_query(query_riset),
        max_fetch=int(cfg.news.get("max_fetch", 120)),
        max_age_hours=int(cfg.news.get("max_age_hours", 36)),
    )
    artikel = hasil_berita["articles"]
    if hasil_berita["failed"]:
        catatan.append("Feed gagal: " + ", ".join(hasil_berita["failed"]))
    if not artikel:
        gagal.append("news")

    for a in artikel:
        a["kredibilitas_sumber"] = cfg.tier(a.get("domain", ""))

    # -- 7. Pernyataan tokoh berpengaruh ----------------------------------
    log.info("[7/21] Ambil pernyataan tokoh berpengaruh")
    # Client diteruskan supaya postingan X bisa diambil lewat Grok. Hasilnya
    # tetap masuk lewat pintu yang sama (saringan umur, dedup, analisa LLM,
    # critic) — tidak ada jalur pintas untuk sumber ini.
    hasil_pernyataan = statements_collector.collect(
        cfg.statements, client=client, models_x=cfg.llm_models("x_posts")
    )
    kandidat_pernyataan = hasil_pernyataan["items"]
    gagal.extend(hasil_pernyataan["failed"])
    # Truth Social SELALU diblokir Cloudflare dari IP pusat data — itu kondisi
    # permanen, bukan gangguan, dan pernyataan Trump tetap tertangkap lewat
    # Google News, feed Gedung Putih, dan X. Mencatatnya tiap run cuma
    # menghasilkan peringatan abadi yang tidak bisa ditindaklanjuti siapa pun.
    sumber_gagal_layak_dicatat = [
        s for s in hasil_pernyataan["sumber_gagal"] if not s.startswith("truth_social:")
    ]
    if sumber_gagal_layak_dicatat and not hasil_pernyataan["failed"]:
        catatan.append(
            "Sebagian sumber pernyataan tidak terjangkau: "
            + ", ".join(sumber_gagal_layak_dicatat)
        )
    pernyataan: List[Dict[str, Any]] = []

    # Corong berita: dari jaring yang ditebar sampai yang benar-benar dipakai.
    # Dilaporkan di footer supaya terlihat berapa banyak yang disaring — angka
    # ini juga yang menunjukkan apakah penambahan feed benar-benar menambah
    # bahan atau cuma menambah derau.
    corong_berita = dict(hasil_berita.get("jumlah") or {})

    # -- 7-10. Rangkaian LLM untuk berita dan pernyataan ---------------------------------
    if client:
        log.info("[8/21] LLM filter relevansi")
        artikel = news_analysis.filter_relevansi(
            client,
            cfg.llm_models("filter"),
            artikel,
            min_score=int(cfg.news.get("min_relevance", 40)),
            max_keep=int(cfg.news.get("max_after_filter", 25)),
        )

        log.info("[9/21] LLM klasifikasi berita")
        artikel = news_analysis.klasifikasi(client, cfg.llm_models("classify"), artikel)

        log.info("[10/21] LLM analisa mekanisme")
        artikel = news_analysis.analisa_mekanisme(
            client,
            cfg.llm_models("mechanism"),
            artikel,
            top_n=int(cfg.news.get("max_deep_analysis", 10)),
        )

        log.info("[11/21] LLM analisa pernyataan tokoh")
        pernyataan = news_analysis.analisa_pernyataan(
            client,
            cfg.llm_models("statements"),
            kandidat_pernyataan,
            min_relevansi=int(cfg.statements.get("min_relevance", 35)),
            maks_hasil=int(cfg.statements.get("max_analyzed", 12)),
        )
    else:
        # Tanpa LLM, tetap tampilkan berita paling relevan menurut skor kata kunci.
        artikel = artikel[: int(cfg.news.get("max_after_filter", 25))]
        for a in artikel:
            a["relevansi_btc"] = a.get("skor_prioritas")

    # Dicatat SETELAH seluruh penyaringan, jadi apa adanya baik jalur LLM
    # maupun jalur cadangan skor kata kunci.
    corong_berita["dipakai"] = len(artikel)

    # -- 9. Cross-check berita vs harga ----------------------------------
    log.info("[12/21] Cross-check berita vs pergerakan harga 1H")
    hasil_cross = news_analysis.cross_check(
        artikel, klines.get(cfg.timeframe_reaksi, []), funding
    )
    conflicts = hasil_cross["conflicts"]

    # -- 10. Agregasi sentimen -------------------------------------------
    log.info("[13/21] Agregasi sentimen dan tema")
    agregat = news_analysis.skor_sentimen(artikel, cfg.tier)
    agregat["dominant_themes"] = news_analysis.tema_dominan(artikel)
    agregat["narrative_shift"] = ""

    # Karakter pergerakan 24 jam: arah, besaran, dan JENISNYA (uang baru vs
    # posisi ditutup). Dihitung di sini karena butuh berita yang SUDAH
    # diklasifikasi — sentimen dan kekuatannya dipakai untuk mencari kandidat
    # pemicu yang searah. Hasilnya masuk ke `technical` supaya ikut tersimpan
    # di brief, dan diteruskan ke konteks LLM sebagai bahan sintesis.
    teknikal["pergerakan_24j"] = technical.karakter_pergerakan_24j(
        price, teknikal, artikel
    )
    log.info("Pergerakan 24 jam: %s", teknikal["pergerakan_24j"].get("ringkas") or "tidak diketahui")

    # -- 11. Bandingkan dengan brief sebelumnya --------------------------
    log.info("[14/21] Bandingkan dengan brief sebelumnya")
    diff_sementara = builder.hitung_diff(
        {"price": price, "aggregate": agregat, "market": pasar, "technical": teknikal, "news": artikel},
        sebelumnya,
    )
    diff_sementara["ringkasan"] = builder.ringkas_diff(diff_sementara)

    # -- Kalender (dibutuhkan sebagai konteks outlook) ---------------------
    # Kalender bawaan menghitung CPI/NFP/PCE dari pola bulanan (dugaan,
    # ditandai `perkiraan: true`) karena tidak membaca sumber luar sama
    # sekali. Dua sumber luar dicoba untuk menggantinya dengan tanggal
    # sungguhan, berurutan dari yang paling bisa diandalkan:
    #
    #   1. Feed JSON ForexFactory — terstruktur, tanpa API key, tanpa LLM.
    #      Parsingnya deterministik, jadi tidak ada yang bisa dikarang.
    #   2. Scrape investing.com lewat LLM murah — cadangan kalau feed di
    #      atas mati. Halamannya kerap diblokir dari IP pusat data, jadi
    #      wajar kalau sering kembali kosong.
    #
    # Kalau dua-duanya gagal, kalender bawaan tetap menghasilkan agenda
    # (dengan tanggal dugaan) dan pipeline lanjut tanpa keluhan.
    konfirmasi_agenda = ff_calendar.collect()
    if not konfirmasi_agenda and client:
        konfirmasi_agenda = investing.collect(
            client, cfg.llm_models("agenda"), format_wib(now_utc())
        )
    agenda = calendar_collector.collect(cfg.fomc_dates, konfirmasi=konfirmasi_agenda)

    # Kalender cuma menghasilkan daftar mentah; "acara ekonomi" tidak berarti
    # "berdampak ke BTC". Langkah ini menilai relevansi tiap acara terhadap
    # kripto secara spesifik dan menjelaskan lewat jalur apa dampaknya sampai
    # ke harga. Model hanya memberi anotasi — pencocokannya lewat indeks yang
    # dikirim kode, jadi acara tidak bisa ditambah maupun dibuang.
    if client and agenda:
        log.info("[14b/21] LLM analisa dampak agenda ke kripto")
        agenda = news_analysis.analisa_agenda(
            client,
            cfg.llm_models("agenda_dampak"),
            agenda,
            {
                "harga_btc": price.get("last"),
                "perubahan_24j_pct": price.get("change_24h_pct"),
                "funding_rate": pasar.get("funding_rate"),
                "max_pain_opsi": opsi.get("max_pain_expiry_terdekat"),
            },
        )

    # -- 12-15. Rangkaian LLM analitis -------------------------------------
    ai: Dict[str, Any] = {
        "narrative": "",
        "bagian": {},
        "narrative_singkat": "",
        "penyebab_pergerakan": [],
        "bagian_ditahan": [],
        # Catatan editorial: kalimat yang menyerempet anjuran atau sebab-akibat
        # yang terlalu percaya diri. Ditampilkan sebagai keterangan, TIDAK
        # menahan analisanya.
        "tanda_editorial": [],
        "teknikal": None,
        "whale": None,
        "outlook": None,
        "model_used": None,
        "generated_at": None,
        "critic": {"passed": True, "corrections": [], "dijalankan": False},
    }

    if client:
        konteks = _konteks_llm(
            price, teknikal, pasar, makro, artikel, agregat, conflicts,
            diff_sementara, posisi_whale, agenda, pernyataan,
            opsi, onchain_data, aliran,
        )

        # Interpretasi teknikal: angka tetap dari kode, penafsiran dari LLM.
        log.info("[15/21] LLM interpretasi teknikal")
        hasil_teknikal = news_analysis.interpretasi_teknikal(
            client, cfg.llm_models("technical"), teknikal, price
        )

        log.info("[16/21] LLM analisa whale dan sinyal palsu")
        hasil_whale_ai = news_analysis.analisa_whale(
            client, cfg.llm_models("whale"), posisi_whale,
            teknikal.get("sinyal_palsu") or [], teknikal, price,
        )

        # Sintesis melihat hasil dua langkah di atas supaya narasinya nyambung.
        konteks_sintesis = {
            **konteks,
            "interpretasi_teknikal": hasil_teknikal,
            "analisa_whale": hasil_whale_ai,
        }

        log.info("[17/21] LLM sintesis narasi")
        hasil_sintesis = news_analysis.sintesis(
            client, cfg.llm_models("synthesis"), konteks_sintesis
        )

        log.info("[18/21] LLM analisa outlook")
        hasil_outlook = news_analysis.outlook(
            client, cfg.llm_models("outlook"), konteks_sintesis
        )

        # Langkah AI yang GAGAL (balasan tidak bisa diparse, budget habis,
        # model error) sebelumnya hilang tanpa jejak bagi pembaca: catatan
        # "Analisa AI tidak tersedia" hanya muncul kalau SELURUH blok ai
        # kosong, sedangkan sintesis gagal + outlook sukses menghasilkan
        # brief yang kehilangan narasi utamanya tanpa satu pun keterangan.
        # Bagian yang ditahan critic sudah punya catatannya sendiri; ini
        # untuk yang tidak pernah sempat dihasilkan sama sekali.
        for hasil_langkah, nama_langkah in (
            (hasil_sintesis, "narasi utama"),
            (hasil_outlook, "pandangan ke depan"),
            (hasil_teknikal, "pembacaan teknikal"),
            (hasil_whale_ai, "analisa whale"),
        ):
            if not hasil_langkah:
                pesan = f"Bagian AI '{nama_langkah}' gagal dihasilkan pada run ini."
                catatan.append(pesan)
                log.warning(pesan)

        # Critic memeriksa SEMUA bagian naratif sekaligus.
        teks_diperiksa = {
            "narasi_utama": (hasil_sintesis or {}).get("narrative", ""),
            "interpretasi_teknikal": (hasil_teknikal or {}).get("ringkasan", ""),
            "analisa_whale": (hasil_whale_ai or {}).get("ringkasan", ""),
            "outlook": (hasil_outlook or {}).get("ringkasan", ""),
        }
        if hasil_outlook:
            teks_diperiksa["outlook_skenario"] = json.dumps(
                {
                    "naik": hasil_outlook["skenario_naik"],
                    "turun": hasil_outlook["skenario_turun"],
                },
                ensure_ascii=False,
            )

        if any(teks_diperiksa.values()):
            log.info("[19/21] LLM critic")
            # Critic memeriksa dengan data yang PERSIS SAMA dengan yang
            # dipakai synthesis. Kalau critic melihat lebih sedikit, angka
            # yang sah akan divonis karangan.
            #
            # Modelnya juga disaring agar tidak sekeluarga dengan model yang
            # BENAR-BENAR melayani synthesis — config boleh mendaftarkan
            # cadangan yang bertumpang tindih, yang penting hasil akhirnya
            # tetap diperiksa pihak yang berbeda.
            model_critic = news_analysis.pilih_model_critic(
                cfg.llm_models("critic"), client.model_terpakai("synthesis")
            )
            hasil_critic = news_analysis.critic(
                client, model_critic, teks_diperiksa, konteks_sintesis
            )

            # Satu putaran perbaikan sebelum menyerah. Menahan seluruh analisa
            # karena beberapa kalimat bermasalah membuang juga bagian yang
            # benar — dan tokennya sudah telanjur dibayar.
            if not hasil_critic["passed"] and hasil_sintesis:
                log.info("[19b/21] Critic menolak; mencoba satu putaran revisi")
                narasi_revisi = news_analysis.revisi_narasi(
                    client,
                    cfg.llm_models("synthesis"),
                    hasil_sintesis["narrative"],
                    hasil_critic["corrections"],
                    konteks_sintesis,
                )
                if narasi_revisi:
                    hasil_sintesis["narrative"] = narasi_revisi
                    teks_diperiksa["narasi_utama"] = narasi_revisi
                    hasil_critic = news_analysis.critic(
                        client, model_critic, teks_diperiksa, konteks_sintesis
                    )
                    if hasil_critic["passed"]:
                        # "passed" juga true saat critic gagal dijalankan (fail-open,
                        # lihat news_analysis.critic) — dibedakan di log supaya "lolos"
                        # tidak diam-diam berarti "tidak sempat diperiksa ulang".
                        if hasil_critic.get("dijalankan", True):
                            log.info("Revisi lolos pemeriksaan critic")
                        else:
                            log.warning(
                                "Revisi TIDAK sempat diverifikasi ulang (critic gagal "
                                "dijalankan pada putaran kedua), dipakai apa adanya"
                            )
                        catatan.append("Narasi AI melewati satu putaran revisi otomatis.")
                    else:
                        log.warning("Revisi masih belum lolos critic")
                        # Sudah diberi satu kesempatan perbaikan. Yang masih
                        # menahan mulai titik ini hanya kesalahan angka;
                        # sisanya turun jadi tanda editorial supaya analisanya
                        # tidak hilang seluruhnya (lihat longgarkan_setelah_revisi).
                        hasil_critic = news_analysis.longgarkan_setelah_revisi(hasil_critic)
                        if hasil_critic["passed"]:
                            catatan.append(
                                "Narasi AI direvisi otomatis; sebagian kalimat tafsir "
                                "diberi tanda editorial."
                            )
                else:
                    # Langkah revisi TIDAK menghasilkan teks (error LLM, anggaran
                    # token habis, atau struktur balasan di luar dugaan). Dulu
                    # cabang ini tidak melakukan apa-apa, sehingga temuan fatal
                    # bertahan utuh dan SELURUH bagian AI ditahan — padahal
                    # kegagalan ada di pihak kita, bukan pada isi analisanya.
                    # Perlakuannya kini sama dengan revisi yang sudah dicoba
                    # tapi belum lolos: hanya kesalahan angka yang menahan.
                    log.warning(
                        "Revisi narasi gagal dihasilkan; temuan non-angka diturunkan "
                        "jadi tanda agar analisa tidak hilang seluruhnya"
                    )
                    hasil_critic = news_analysis.longgarkan_setelah_revisi(hasil_critic)
                    if hasil_critic["passed"]:
                        catatan.append(
                            "Revisi otomatis tidak berjalan; sebagian kalimat tafsir "
                            "diberi tanda editorial."
                        )

            ai["critic"] = hasil_critic
            ai["tanda_editorial"] = hasil_critic.get("tanda") or []
            if ai["tanda_editorial"]:
                log.info(
                    "%d kalimat diberi tanda editorial (analisa tetap dikirim)",
                    len(ai["tanda_editorial"]),
                )

            # Critic yang gagal dijalankan diperlakukan sebagai lolos supaya
            # analisa tidak hilang, tapi statusnya TIDAK boleh diam-diam
            # tampak seperti sudah diverifikasi.
            if not hasil_critic.get("dijalankan", True):
                catatan.append(
                    "Analisa AI terkirim tanpa sempat diverifikasi critic "
                    "(pemeriksaan gagal dijalankan)."
                )

            if hasil_critic["passed"]:
                if hasil_sintesis:
                    ai["narrative"] = hasil_sintesis["narrative"]
                    ai["bagian"] = hasil_sintesis.get("bagian") or {}
                    ai["narrative_singkat"] = _ringkas_narasi(hasil_sintesis["narrative"])
                    ai["penyebab_pergerakan"] = hasil_sintesis["penyebab_pergerakan"]
                    if hasil_sintesis["dominant_themes"]:
                        agregat["dominant_themes"] = hasil_sintesis["dominant_themes"]
                    agregat["narrative_shift"] = hasil_sintesis["narrative_shift"]
                    conflicts = conflicts + [
                        {"tipe": "catatan_ai", "keterangan": c}
                        for c in hasil_sintesis["conflicts"]
                    ]
                ai["teknikal"] = hasil_teknikal
                ai["whale"] = hasil_whale_ai
                ai["outlook"] = hasil_outlook
            else:
                # Sampai di sini berarti ada kesalahan FAKTA (angka karangan
                # atau peristiwa yang tidak ada di data) — satu-satunya alasan
                # yang boleh menahan. Yang ditahan pun hanya bagian yang
                # ditandai; sisanya tetap dikirim.
                bermasalah = {
                    c.get("bagian", "") for c in hasil_critic["corrections"]
                    if c.get("keparahan") == "fatal"
                }
                # `outlook_skenario` (skenario naik/turun) dipisah dari
                # `outlook` (prosa pandangan ke depan + geopolitik + agenda).
                # Sebelumnya keduanya dipetakan ke "outlook", jadi satu
                # kesalahan angka di skenario ikut menghapus seluruh
                # pembahasan geopolitik — bagian yang justru paling penting
                # dan sering tidak ada hubungannya dengan angka yang salah.
                peta = {
                    "narasi_utama": "narasi",
                    "interpretasi_teknikal": "teknikal",
                    "analisa_whale": "whale",
                    "outlook": "outlook",
                    "outlook_skenario": "outlook_skenario",
                }
                ditahan = {peta.get(b) for b in bermasalah if peta.get(b)}
                # Temuan tanpa nama bagian tidak bisa dilokalisasi; amannya
                # menahan narasi utama saja.
                if not ditahan:
                    ditahan = {"narasi"}

                if "narasi" not in ditahan and hasil_sintesis:
                    ai["narrative"] = hasil_sintesis["narrative"]
                    ai["bagian"] = hasil_sintesis.get("bagian") or {}
                    ai["narrative_singkat"] = _ringkas_narasi(hasil_sintesis["narrative"])
                    ai["penyebab_pergerakan"] = hasil_sintesis["penyebab_pergerakan"]
                if "teknikal" not in ditahan:
                    ai["teknikal"] = hasil_teknikal
                if "whale" not in ditahan:
                    ai["whale"] = hasil_whale_ai
                # Skenario ditahan sendiri tanpa ikut membuang prosa outlook,
                # geopolitik, dan agenda di sekitarnya.
                if "outlook" not in ditahan and hasil_outlook:
                    ai["outlook"] = dict(hasil_outlook)
                    if "outlook_skenario" in ditahan:
                        ai["outlook"]["skenario_naik"] = {"pemicu": [], "kondisi": ""}
                        ai["outlook"]["skenario_turun"] = {"pemicu": [], "kondisi": ""}

                ai["bagian_ditahan"] = sorted(ditahan)
                pesan = "Bagian AI yang ditahan critic: " + ", ".join(sorted(ditahan))
                catatan.append(pesan)
                log.warning("%s (sisanya tetap dikirim)", pesan)

            ai["model_used"] = ", ".join(client.models_used) or None
            ai["generated_at"] = iso_utc(now_utc())

            # Konteks yang dilihat LLM berbentuk JSON, jadi model kerap
            # menyalin nama field dan nilai enum apa adanya ke dalam narasi
            # ("pola short_covering", "invalidasi_turun di $64.314"). Prompt
            # saja tidak cukup untuk mencegahnya, jadi penggantian dilakukan
            # kode di sini — deterministik dan tidak menyentuh angka.
            ai = istilah.manusiakan_dalam(ai)
        else:
            catatan.append("Analisa AI tidak tersedia pada run ini.")

        if client.budget_habis:
            catatan.append(f"Budget LLM ${cfg.max_cost_usd:.2f} per run tercapai; sebagian langkah AI dihentikan.")

        ringkasan_llm = client.ringkasan()
        log.info(
            "Total LLM: %d panggilan, $%.5f",
            ringkasan_llm["jumlah_panggilan"],
            ringkasan_llm["total_cost_usd"],
        )

    # -- Susun brief ------------------------------------------------------
    log.info("Susun brief")
    kualitas = builder.hitung_kualitas(
        gagal, client.total_cost if client else 0.0, catatan,
        token_masuk=client.total_token_masuk if client else 0,
        token_keluar=client.total_token_keluar if client else 0,
        durasi_detik=round(time.monotonic() - mulai_run, 1),
        corong_berita=corong_berita,
    )
    brief = builder.build_brief(
        price=price,
        technical=teknikal,
        market=pasar,
        macro=makro,
        whale=posisi_whale,
        options=opsi,
        onchain=onchain_data,
        flows=aliran,
        news=artikel,
        statements=pernyataan,
        aggregate=agregat,
        calendar=agenda,
        conflicts=conflicts,
        ai=ai,
        data_quality=kualitas,
        price_series=[
            {"t": k["open_time"], "c": round(k["close"], 2)}
            for k in klines.get("1d", [])[-60:]
        ],
        tautan_luar=cfg.tautan_luar,
        bot_telegram=str(cfg.telegram.get("bot_username") or "").lstrip("@"),
        previous=sebelumnya,
    )

    # -- 15. Kirim Telegram (SEBELUM tulis file) --------------------------
    # Perapi menambah emoji dan jeda baris, jadi pesan dasarnya dirender
    # dengan ruang kepala. Tanpa itu hasil rapinya selalu melewati 4096
    # karakter dan selalu ditolak.
    rapikan_aktif = bool(client) and cfg.telegram.get("rapikan_dengan_llm", True)
    kepala_pesan, badan_pesan = telegram.render_terpisah(
        brief, cfg.site_url, batas=3400 if rapikan_aktif else None
    )

    # Perapian tata letak lewat LLM murah — HANYA badan pesan yang dikirim.
    # Judul "Ringkasan Pasar Bitcoin" dan timestamp tidak pernah dilihat LLM
    # sama sekali, jadi tidak mungkin hilang atau ditulis ulang biar pun
    # modelnya lupa instruksi "pertahankan judul" (yang pernah terjadi).
    # Kalau hasilnya tidak lolos verifikasi, badan asli yang dipakai — jadi
    # langkah ini tidak pernah bisa memperburuk isi.
    if rapikan_aktif:
        log.info("Rapikan pesan Telegram")
        hasil_rapi = stylist.rapikan(client, cfg.llm_models("format"), badan_pesan, brief)
        pesan = kepala_pesan + hasil_rapi["pesan"]
        if not hasil_rapi["dirapikan"] and hasil_rapi["alasan"]:
            catatan.append("Perapian pesan dilewati: " + hasil_rapi["alasan"])
    else:
        pesan = kepala_pesan + badan_pesan

    penerima, state_pelanggan = _kumpulkan_penerima(cfg, dry_run, catatan)

    if dry_run:
        log.info(
            "[20/21] Dry-run: Telegram tidak dikirim ke %d penerima. Pratinjau pesan:\n%s",
            len(penerima), pesan,
        )
    elif not cfg.secrets.telegram_token:
        log.warning("[20/21] TELEGRAM_TOKEN kosong, pengiriman dilewati")
    elif not penerima:
        log.warning("[20/21] Tidak ada penerima; isi TELEGRAM_CHAT_ID atau aktifkan pelanggan")
    else:
        log.info("[20/21] Kirim Telegram ke %d penerima", len(penerima))
        hasil = telegram.broadcast(
            cfg.secrets.telegram_token,
            penerima,
            pesan,
            jeda=float(cfg.telegram.get("jeda_kirim_detik", 0.06)),
        )
        # Penerima yang memblokir bot dikeluarkan supaya daftar tidak menumpuk
        # chat mati dan tiap run tidak membuang waktu mengirim ke sana.
        if state_pelanggan is not None:
            for chat_id in hasil["gugur"]:
                subscribers.buang(state_pelanggan, chat_id)
            subscribers.simpan(
                SUBSCRIBERS_PATH, cfg.secrets.telegram_subscriber_key, state_pelanggan
            )
        if not hasil["berhasil"]:
            log.error("Telegram gagal ke semua penerima, pipeline tetap lanjut menulis file")

    # -- 16. Tulis file ---------------------------------------------------
    if dry_run:
        log.info("[21/21] Dry-run: arsip tidak ditulis, hanya latest.json diperbarui")
    ditulis = builder.tulis_output(
        brief,
        retention_days=cfg.archive_retention_days,
        tulis_arsip=not dry_run,
    )
    for nama, path in ditulis.items():
        log.info("Ditulis %s -> %s", nama, path)

    # Stempel sidik jari app.js ke index.html. Tanpa ini, browser bisa terus
    # memakai app.js versi lama walaupun datanya sudah baru — halaman lalu
    # menampilkan elemen yang sudah dihapus dari kode.
    builder.segarkan_versi_aset()

    log.info(
        "Selesai. Harga $%s | sentimen %s | kualitas %s (%d/%d sumber)",
        f"{price['last']:,.0f}",
        agregat["sentiment_score"],
        kualitas["confidence"],
        kualitas["sources_ok"],
        kualitas["sources_total"],
    )
    return brief


def main() -> int:
    parser = argparse.ArgumentParser(description="Bitcoin Market Brief")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Jalankan pipeline tanpa mengirim Telegram dan tanpa menulis arsip",
    )
    args = parser.parse_args()

    setup_logging()
    cfg = load_config()

    try:
        jalankan(cfg, dry_run=args.dry_run)
    except binance.PriceDataError as exc:
        log.error("BERHENTI: data harga tidak tersedia dari sumber mana pun. %s", exc)
        return 1
    except Exception as exc:
        log.exception("BERHENTI: kegagalan tak terduga: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
