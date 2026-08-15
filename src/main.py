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
from typing import Any, Dict, List, Optional

from .analysis import news_analysis, technical
from .analysis.llm import LLMClient
from .collectors import (
    binance,
    calendar as calendar_collector,
    flows,
    macro,
    market,
    news,
    onchain,
    options,
    statements as statements_collector,
    whale,
)
from .config import Config, SUBSCRIBERS_PATH, load_config
from .output import builder, stylist, subscribers, telegram
from .utils.timezone import iso_utc, now_utc

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
        # SELURUH timeframe dikirim. Sebelumnya 1H dihilangkan, padahal
        # langkah interpretasi teknikal menerimanya dan menulis angka 1H di
        # narasinya — critic lalu tidak bisa memverifikasi angka itu dan
        # menandainya sebagai karangan.
        "teknikal_1d": teknikal.get("1d"),
        "teknikal_4h": teknikal.get("4h"),
        "teknikal_1h": teknikal.get("1h"),
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
    gagal: List[str] = []
    catatan: List[str] = []

    # -- 1. Harga + klines (FATAL kalau gagal) --------------------------
    log.info("[1/21] Ambil harga dan klines")
    data_harga = binance.fetch_price_and_klines(cfg.symbol, cfg.timeframes, cfg.candle_limit)
    price = data_harga["price"]
    klines = data_harga["klines"]
    if data_harga["source"] != "binance":
        catatan.append("Harga memakai sumber cadangan CoinGecko; candle merupakan hasil resampling.")

    # -- 2. Indikator teknikal ------------------------------------------
    log.info("[2/21] Hitung indikator teknikal")
    funding = binance.fetch_funding_rate(cfg.symbol)
    open_interest = binance.fetch_open_interest(cfg.symbol)
    oi_history = binance.fetch_open_interest_history(cfg.symbol)
    if funding is None and open_interest is None:
        gagal.append("funding_oi")

    teknikal = technical.analyze(klines, price, oi_history, funding)
    if not teknikal.get("1d"):
        gagal.append("technical")
    if teknikal.get("sinyal_palsu"):
        log.info("Sinyal mencurigakan terdeteksi: %d pola", len(teknikal["sinyal_palsu"]))

    # -- 3. Data pasar + posisi whale ------------------------------------
    log.info("[3/21] Ambil data pasar dan posisi whale")
    hasil_pasar = market.collect(cfg.symbol)
    gagal.extend(hasil_pasar["failed"])
    pasar = {
        "funding_rate": funding,
        "open_interest": open_interest,
        **hasil_pasar["data"],
    }

    hasil_whale = whale.collect(cfg.symbol)
    posisi_whale = hasil_whale["data"]
    if hasil_whale["failed"]:
        gagal.append("whale")
        catatan.append("Data posisi whale tidak tersedia; analisa manipulasi dilewati.")

    # -- 4. Opsi, valuasi on-chain, aliran dana --------------------------
    log.info("[4/21] Ambil data opsi, valuasi on-chain, dan aliran dana")
    hasil_opsi = options.collect()
    opsi = hasil_opsi["data"]
    gagal.extend(hasil_opsi["failed"])

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

    # -- 6. Berita -------------------------------------------------------
    log.info("[6/21] Ambil berita RSS")
    hasil_berita = news.collect(
        cfg.news.get("feeds", []),
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
    hasil_pernyataan = statements_collector.collect(cfg.statements)
    kandidat_pernyataan = hasil_pernyataan["items"]
    gagal.extend(hasil_pernyataan["failed"])
    if hasil_pernyataan["sumber_gagal"] and not hasil_pernyataan["failed"]:
        catatan.append(
            "Sebagian sumber pernyataan tidak terjangkau: "
            + ", ".join(hasil_pernyataan["sumber_gagal"])
        )
    pernyataan: List[Dict[str, Any]] = []

    # -- 7-10. Rangkaian LLM untuk berita dan pernyataan ---------------------------------
    client: Optional[LLMClient] = None
    if cfg.secrets.llm_enabled and artikel:
        client = LLMClient(
            api_key=cfg.secrets.openrouter_api_key,
            base_url=cfg.llm_base_url,
            max_cost_usd=cfg.max_cost_usd,
            referer=cfg.repo_url,
        )
    elif not cfg.secrets.llm_enabled:
        catatan.append("OPENROUTER_API_KEY kosong; seluruh langkah LLM dilewati.")
        log.warning("OPENROUTER_API_KEY kosong, pipeline berjalan tanpa analisa AI")

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

    # -- 9. Cross-check berita vs harga ----------------------------------
    log.info("[12/21] Cross-check berita vs pergerakan harga 1H")
    hasil_cross = news_analysis.cross_check(artikel, klines.get("1h", []), funding)
    conflicts = hasil_cross["conflicts"]

    # -- 10. Agregasi sentimen -------------------------------------------
    log.info("[13/21] Agregasi sentimen dan tema")
    agregat = news_analysis.skor_sentimen(artikel, cfg.tier)
    agregat["dominant_themes"] = news_analysis.tema_dominan(artikel)
    agregat["narrative_shift"] = ""

    # -- 11. Baca brief sebelumnya ---------------------------------------
    log.info("[14/21] Baca brief sebelumnya")
    sebelumnya = builder.brief_sebelumnya()
    diff_sementara = builder.hitung_diff(
        {"price": price, "aggregate": agregat, "market": pasar, "technical": teknikal, "news": artikel},
        sebelumnya,
    )
    diff_sementara["ringkasan"] = builder.ringkas_diff(diff_sementara)

    # -- Kalender (dibutuhkan sebagai konteks outlook) ---------------------
    agenda = calendar_collector.collect(cfg.fomc_dates)

    # -- 12-15. Rangkaian LLM analitis -------------------------------------
    ai: Dict[str, Any] = {
        "narrative": "",
        "bagian": {},
        "narrative_singkat": "",
        "penyebab_pergerakan": [],
        "bagian_ditahan": [],
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
                        log.info("Revisi lolos pemeriksaan critic")
                        catatan.append("Narasi AI melewati satu putaran revisi otomatis.")
                    else:
                        log.warning("Revisi masih belum lolos critic")

            ai["critic"] = hasil_critic

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
                # Hanya bagian yang benar-benar ditandai fatal yang ditahan.
                # Menahan semuanya berarti pembaca kehilangan analisa teknikal
                # dan outlook yang mungkin sama sekali tidak bermasalah.
                bermasalah = {
                    c.get("bagian", "") for c in hasil_critic["corrections"]
                    if c.get("keparahan") == "fatal"
                }
                peta = {
                    "narasi_utama": "narasi",
                    "interpretasi_teknikal": "teknikal",
                    "analisa_whale": "whale",
                    "outlook": "outlook",
                    "outlook_skenario": "outlook",
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
                if "outlook" not in ditahan:
                    ai["outlook"] = hasil_outlook

                ai["bagian_ditahan"] = sorted(ditahan)
                pesan = "Bagian AI yang ditahan critic: " + ", ".join(sorted(ditahan))
                catatan.append(pesan)
                log.warning("%s (sisanya tetap dikirim)", pesan)

            ai["model_used"] = ", ".join(client.models_used) or None
            ai["generated_at"] = iso_utc(now_utc())
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
        gagal, client.total_cost if client else 0.0, catatan
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
        previous=sebelumnya,
    )

    # -- 15. Kirim Telegram (SEBELUM tulis file) --------------------------
    # Perapi menambah emoji dan jeda baris, jadi pesan dasarnya dirender
    # dengan ruang kepala. Tanpa itu hasil rapinya selalu melewati 4096
    # karakter dan selalu ditolak.
    rapikan_aktif = bool(client) and cfg.telegram.get("rapikan_dengan_llm", True)
    pesan = telegram.render(
        brief, cfg.site_url, batas=3400 if rapikan_aktif else None
    )

    # Perapian tata letak lewat LLM murah. Kalau hasilnya tidak lolos
    # verifikasi, pesan asli yang dipakai — jadi langkah ini tidak pernah
    # bisa memperburuk isi, paling banter tidak memperbaiki tampilannya.
    if rapikan_aktif:
        log.info("Rapikan pesan Telegram")
        hasil_rapi = stylist.rapikan(client, cfg.llm_models("format"), pesan, brief)
        pesan = hasil_rapi["pesan"]
        if not hasil_rapi["dirapikan"] and hasil_rapi["alasan"]:
            catatan.append("Perapian pesan dilewati: " + hasil_rapi["alasan"])

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
