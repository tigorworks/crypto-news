"""Orkestrator pipeline Bitcoin Market Brief.

Jalankan: python -m src.main [--dry-run]

Urutan langkah mengikuti BUILD.md. Hanya langkah 1 (harga) yang fatal;
sisanya boleh gagal sebagian dan dicatat di data_quality.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from typing import Any, Dict, List, Optional

from .analysis import news_analysis, technical
from .analysis.llm import LLMClient
from .collectors import binance, calendar as calendar_collector, macro, market, news
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
        "pasar": pasar,
        "makro": makro,
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
    log.info("[1/16] Ambil harga dan klines")
    data_harga = binance.fetch_price_and_klines(cfg.symbol, cfg.timeframes, cfg.candle_limit)
    price = data_harga["price"]
    klines = data_harga["klines"]
    if data_harga["source"] != "binance":
        catatan.append("Harga memakai sumber cadangan CoinGecko; candle merupakan hasil resampling.")

    # -- 2. Indikator teknikal ------------------------------------------
    log.info("[2/16] Hitung indikator teknikal")
    funding = binance.fetch_funding_rate(cfg.symbol)
    open_interest = binance.fetch_open_interest(cfg.symbol)
    oi_history = binance.fetch_open_interest_history(cfg.symbol)
    if funding is None and open_interest is None:
        gagal.append("funding_oi")

    teknikal = technical.analyze(klines, price, oi_history)
    if not teknikal.get("1d"):
        gagal.append("technical")

    # -- 3. Data pasar ---------------------------------------------------
    log.info("[3/16] Ambil data pasar")
    hasil_pasar = market.collect(cfg.symbol)
    gagal.extend(hasil_pasar["failed"])
    pasar = {
        "funding_rate": funding,
        "open_interest": open_interest,
        **hasil_pasar["data"],
    }

    # -- 4. Makro --------------------------------------------------------
    log.info("[4/16] Ambil data makro")
    hasil_makro = macro.collect(cfg.secrets.fred_api_key)
    gagal.extend(hasil_makro["failed"])
    makro = hasil_makro["data"]

    # -- 5. Berita -------------------------------------------------------
    log.info("[5/16] Ambil berita RSS")
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
        log.info("[6/16] LLM filter relevansi")
        artikel = news_analysis.filter_relevansi(
            client,
            cfg.llm_models("filter"),
            artikel,
            min_score=int(cfg.news.get("min_relevance", 40)),
            max_keep=int(cfg.news.get("max_after_filter", 25)),
        )

        log.info("[7/16] LLM klasifikasi berita")
        artikel = news_analysis.klasifikasi(client, cfg.llm_models("classify"), artikel)

        log.info("[8/16] LLM analisa mekanisme")
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
    log.info("[9/16] Cross-check berita vs pergerakan harga 1H")
    hasil_cross = news_analysis.cross_check(artikel, klines.get("1h", []), funding)
    conflicts = hasil_cross["conflicts"]

    # -- 10. Agregasi sentimen -------------------------------------------
    log.info("[10/16] Agregasi sentimen dan tema")
    agregat = news_analysis.skor_sentimen(artikel, cfg.tier)
    agregat["dominant_themes"] = news_analysis.tema_dominan(artikel)
    agregat["narrative_shift"] = ""

    # -- 11. Baca brief sebelumnya ---------------------------------------
    log.info("[11/16] Baca brief sebelumnya")
    sebelumnya = builder.brief_sebelumnya()
    diff_sementara = builder.hitung_diff(
        {"price": price, "aggregate": agregat, "market": pasar, "technical": teknikal, "news": artikel},
        sebelumnya,
    )
    diff_sementara["ringkasan"] = builder.ringkas_diff(diff_sementara)

    # -- 12-13. Sintesis + critic -----------------------------------------
    ai: Dict[str, Any] = {
        "narrative": "",
        "narrative_singkat": "",
        "model_used": None,
        "generated_at": None,
        "critic": {"passed": True, "corrections": [], "dijalankan": False},
    }

    if client:
        konteks = _konteks_llm(price, teknikal, pasar, makro, artikel, agregat, conflicts, diff_sementara)

        log.info("[12/16] LLM sintesis narasi")
        hasil_sintesis = news_analysis.sintesis(client, cfg.llm_models("synthesis"), konteks)

        if hasil_sintesis:
            log.info("[13/16] LLM critic")
            hasil_critic = news_analysis.critic(
                client, cfg.llm_models("critic"), hasil_sintesis["narrative"], konteks
            )
            ai["critic"] = hasil_critic

            if hasil_critic["passed"]:
                ai["narrative"] = hasil_sintesis["narrative"]
                ai["narrative_singkat"] = _ringkas_narasi(hasil_sintesis["narrative"])
                if hasil_sintesis["dominant_themes"]:
                    agregat["dominant_themes"] = hasil_sintesis["dominant_themes"]
                agregat["narrative_shift"] = hasil_sintesis["narrative_shift"]
                conflicts = conflicts + [
                    {"tipe": "catatan_ai", "keterangan": c} for c in hasil_sintesis["conflicts"]
                ]
            else:
                catatan.append("Narasi AI ditahan karena tidak lolos pemeriksaan critic.")
                log.warning("Narasi AI ditahan — brief dikirim tanpa bagian AI")

            ai["model_used"] = ", ".join(client.models_used) or None
            ai["generated_at"] = iso_utc(now_utc())
        else:
            catatan.append("Sintesis narasi AI tidak tersedia pada run ini.")

        if client.budget_habis:
            catatan.append(f"Budget LLM ${cfg.max_cost_usd:.2f} per run tercapai; sebagian langkah AI dihentikan.")

        ringkasan_llm = client.ringkasan()
        log.info(
            "Total LLM: %d panggilan, $%.5f",
            ringkasan_llm["jumlah_panggilan"],
            ringkasan_llm["total_cost_usd"],
        )

    # -- Kalender ---------------------------------------------------------
    agenda = calendar_collector.collect(cfg.fomc_dates)

    # -- 14. Susun brief --------------------------------------------------
    log.info("[14/16] Susun brief")
    kualitas = builder.hitung_kualitas(
        gagal, client.total_cost if client else 0.0, catatan
    )
    brief = builder.build_brief(
        price=price,
        technical=teknikal,
        market=pasar,
        macro=makro,
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
        log.info("[15/16] Dry-run: Telegram tidak dikirim. Pratinjau pesan:\n%s", pesan)
    elif cfg.secrets.telegram_enabled:
        log.info("[15/16] Kirim Telegram")
        terkirim = telegram.kirim(
            cfg.secrets.telegram_token, cfg.secrets.telegram_chat_id, pesan
        )
        if not terkirim:
            log.error("Telegram gagal dikirim, pipeline tetap lanjut menulis file")
    else:
        log.warning("[15/16] TELEGRAM_TOKEN/CHAT_ID kosong, pengiriman dilewati")

    # -- 16. Tulis file ---------------------------------------------------
    if dry_run:
        log.info("[16/16] Dry-run: arsip tidak ditulis, hanya latest.json diperbarui")
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
