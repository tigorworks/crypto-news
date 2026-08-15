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
    macro,
    market,
    news,
    whale,
)
from .config import Config, load_config
from .output import builder, telegram
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
) -> Dict[str, Any]:
    """Konteks ringkas untuk sintesis & critic.

    Sengaja tidak mengirim seluruh brief: hemat token, dan critic lebih mudah
    memverifikasi angka kalau daftar angkanya terbatas.
    """
    return {
        "harga": price,
        "teknikal_1d": teknikal.get("1d"),
        "teknikal_4h": teknikal.get("4h"),
        "level_kunci": teknikal.get("key_levels"),
        "sinyal_oi": {
            "sinyal": teknikal.get("oi_price_signal"),
            "interpretasi": teknikal.get("oi_price_interpretasi"),
            "perubahan_oi_pct": teknikal.get("oi_change_pct"),
        },
        "sinyal_palsu_terdeteksi": teknikal.get("sinyal_palsu") or [],
        "posisi_whale": posisi_whale,
        "pasar": pasar,
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
        "sinyal_bertentangan": conflicts,
        "perubahan_vs_sebelumnya": diff.get("ringkasan") if diff else None,
    }


def jalankan(cfg: Config, dry_run: bool = False) -> Dict[str, Any]:
    gagal: List[str] = []
    catatan: List[str] = []

    # -- 1. Harga + klines (FATAL kalau gagal) --------------------------
    log.info("[1/18] Ambil harga dan klines")
    data_harga = binance.fetch_price_and_klines(cfg.symbol, cfg.timeframes, cfg.candle_limit)
    price = data_harga["price"]
    klines = data_harga["klines"]
    if data_harga["source"] != "binance":
        catatan.append("Harga memakai sumber cadangan CoinGecko; candle merupakan hasil resampling.")

    # -- 2. Indikator teknikal ------------------------------------------
    log.info("[2/18] Hitung indikator teknikal")
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
    log.info("[3/18] Ambil data pasar dan posisi whale")
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

    # -- 4. Makro --------------------------------------------------------
    log.info("[4/18] Ambil data makro")
    hasil_makro = macro.collect(cfg.secrets.fred_api_key)
    gagal.extend(hasil_makro["failed"])
    makro = hasil_makro["data"]

    # -- 5. Berita -------------------------------------------------------
    log.info("[5/18] Ambil berita RSS")
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

    # -- 6-8. Rangkaian LLM untuk berita ---------------------------------
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
        log.info("[6/18] LLM filter relevansi")
        artikel = news_analysis.filter_relevansi(
            client,
            cfg.llm_models("filter"),
            artikel,
            min_score=int(cfg.news.get("min_relevance", 40)),
            max_keep=int(cfg.news.get("max_after_filter", 25)),
        )

        log.info("[7/18] LLM klasifikasi berita")
        artikel = news_analysis.klasifikasi(client, cfg.llm_models("classify"), artikel)

        log.info("[8/18] LLM analisa mekanisme")
        artikel = news_analysis.analisa_mekanisme(
            client,
            cfg.llm_models("mechanism"),
            artikel,
            top_n=int(cfg.news.get("max_deep_analysis", 10)),
        )
    else:
        # Tanpa LLM, tetap tampilkan berita paling relevan menurut skor kata kunci.
        artikel = artikel[: int(cfg.news.get("max_after_filter", 25))]
        for a in artikel:
            a["relevansi_btc"] = a.get("skor_prioritas")

    # -- 9. Cross-check berita vs harga ----------------------------------
    log.info("[9/18] Cross-check berita vs pergerakan harga 1H")
    hasil_cross = news_analysis.cross_check(artikel, klines.get("1h", []), funding)
    conflicts = hasil_cross["conflicts"]

    # -- 10. Agregasi sentimen -------------------------------------------
    log.info("[10/18] Agregasi sentimen dan tema")
    agregat = news_analysis.skor_sentimen(artikel, cfg.tier)
    agregat["dominant_themes"] = news_analysis.tema_dominan(artikel)
    agregat["narrative_shift"] = ""

    # -- 11. Baca brief sebelumnya ---------------------------------------
    log.info("[11/18] Baca brief sebelumnya")
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
        "narrative_singkat": "",
        "penyebab_pergerakan": [],
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
            diff_sementara, posisi_whale, agenda,
        )

        # Interpretasi teknikal: angka tetap dari kode, penafsiran dari LLM.
        log.info("[12/18] LLM interpretasi teknikal")
        hasil_teknikal = news_analysis.interpretasi_teknikal(
            client, cfg.llm_models("technical"), teknikal, price
        )

        log.info("[13/18] LLM analisa whale dan sinyal palsu")
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

        log.info("[14/18] LLM sintesis narasi")
        hasil_sintesis = news_analysis.sintesis(
            client, cfg.llm_models("synthesis"), konteks_sintesis
        )

        log.info("[15/18] LLM analisa outlook")
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
            log.info("[16/18] LLM critic")
            hasil_critic = news_analysis.critic(
                client, cfg.llm_models("critic"), teks_diperiksa, konteks
            )
            ai["critic"] = hasil_critic

            if hasil_critic["passed"]:
                if hasil_sintesis:
                    ai["narrative"] = hasil_sintesis["narrative"]
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
                catatan.append("Seluruh analisa AI ditahan karena tidak lolos pemeriksaan critic.")
                log.warning("Analisa AI ditahan — brief dikirim tanpa bagian AI")

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
        news=artikel,
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
    pesan = telegram.render(brief, cfg.site_url)
    if dry_run:
        log.info("[17/18] Dry-run: Telegram tidak dikirim. Pratinjau pesan:\n%s", pesan)
    elif cfg.secrets.telegram_enabled:
        log.info("[17/18] Kirim Telegram")
        terkirim = telegram.kirim(
            cfg.secrets.telegram_token, cfg.secrets.telegram_chat_id, pesan
        )
        if not terkirim:
            log.error("Telegram gagal dikirim, pipeline tetap lanjut menulis file")
    else:
        log.warning("[17/18] TELEGRAM_TOKEN/CHAT_ID kosong, pengiriman dilewati")

    # -- 16. Tulis file ---------------------------------------------------
    if dry_run:
        log.info("[18/18] Dry-run: arsip tidak ditulis, hanya latest.json diperbarui")
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
