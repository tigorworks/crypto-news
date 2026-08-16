"""Pernyataan tokoh berpengaruh yang bisa menggerakkan pasar kripto.

Kenapa tidak langsung dari Twitter/X: API gratis X sudah tidak mengizinkan
pembacaan timeline, dan instance Nitter praktis mati semua. Jadi pendekatannya
tiga lapis, dari yang paling primer:

  1. Truth Social  — platform utama Trump, API publik (sering diblokir CDN,
                     jadi best-effort dan boleh gagal)
  2. Feed resmi    — pernyataan kepresidenan dari whitehouse.gov
  3. Google News   — laporan media atas pernyataannya di platform mana pun,
                     termasuk X. Ini yang paling andal dan paling luas.

Konsekuensinya: sebagian item adalah LAPORAN tentang pernyataan, bukan
kutipan mentah. Perbedaan itu penting untuk pasar, jadi langkah LLM
berikutnya wajib menandai `status` (verbatim / dilaporkan media / rumor)
dan tidak boleh menyamakan keduanya.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus, urlparse

import feedparser

from ..utils.http import HttpError, get_json
from ..utils.timezone import iso_utc, now_utc
from . import x_grok

log = logging.getLogger(__name__)

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
TRUTH_LOOKUP = "https://truthsocial.com/api/v1/accounts/lookup"
TRUTH_STATUSES = "https://truthsocial.com/api/v1/accounts/{id}/statuses"

SIMILARITY_THRESHOLD = 0.82


def _bersih(teks: str) -> str:
    teks = re.sub(r"<[^>]+>", " ", teks or "")
    teks = re.sub(r"&[a-z]+;", " ", teks)
    return re.sub(r"\s+", " ", teks).strip()


def _id_pernyataan(teks: str, url: str) -> str:
    basis = re.sub(r"[^a-z0-9 ]", "", (teks or url).lower())[:200]
    return "st" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:10]


def _waktu_entry(entry: Any) -> Optional[datetime]:
    for field in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, field, None) or entry.get(field)
        if parsed:
            try:
                return datetime(*parsed[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
    return None


# --------------------------------------------------------------------------
# Truth Social (sumber primer, best-effort)
# --------------------------------------------------------------------------
def _truth_social(akun: str, batas: int = 20) -> List[Dict[str, Any]]:
    """Ambil postingan terbaru satu akun Truth Social.

    Endpoint ini publik tapi sering dijegal Cloudflare dari IP data center.
    Kegagalan di sini normal dan tidak dianggap masalah.
    """
    profil = get_json(TRUTH_LOOKUP, params={"acct": akun}, timeout=20, retries=1)
    akun_id = profil.get("id")
    if not akun_id:
        raise ValueError(f"akun Truth Social '{akun}' tidak ditemukan")

    posts = get_json(
        TRUTH_STATUSES.format(id=akun_id),
        params={"limit": batas, "exclude_replies": "true"},
        timeout=20,
        retries=1,
    )

    hasil = []
    for post in posts if isinstance(posts, list) else []:
        teks = _bersih(post.get("content", ""))
        if not teks:
            continue
        waktu = None
        if post.get("created_at"):
            try:
                waktu = datetime.fromisoformat(post["created_at"].replace("Z", "+00:00"))
            except ValueError:
                waktu = None
        hasil.append({
            "id": _id_pernyataan(teks, post.get("url", "")),
            "tokoh": akun,
            "teks": teks[:1200],
            "url": post.get("url") or f"https://truthsocial.com/@{akun}",
            "sumber": "Truth Social",
            "domain": "truthsocial.com",
            "jenis_sumber": "primer",
            "waktu_utc": iso_utc(waktu) if waktu else None,
            "_waktu": waktu,
        })
    return hasil


# --------------------------------------------------------------------------
# Feed resmi
# --------------------------------------------------------------------------
def _feed_resmi(url: str) -> List[Dict[str, Any]]:
    parsed = feedparser.parse(url)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"feed resmi tidak terbaca: {parsed.get('bozo_exception')}")

    hasil = []
    for entry in parsed.entries[:20]:
        judul = _bersih(entry.get("title", ""))
        if not judul:
            continue
        ringkasan = _bersih(entry.get("summary", ""))[:600]
        waktu = _waktu_entry(entry)
        hasil.append({
            "id": _id_pernyataan(judul, entry.get("link", "")),
            "tokoh": "Gedung Putih",
            "teks": f"{judul}. {ringkasan}".strip(),
            "url": entry.get("link", ""),
            "sumber": "Gedung Putih",
            "domain": urlparse(url).netloc.lower().removeprefix("www."),
            "jenis_sumber": "resmi",
            "waktu_utc": iso_utc(waktu) if waktu else None,
            "_waktu": waktu,
        })
    return hasil


# --------------------------------------------------------------------------
# Google News (laporan media)
# --------------------------------------------------------------------------
def _google_news(query: str, batas: int = 20) -> List[Dict[str, Any]]:
    parsed = feedparser.parse(GOOGLE_NEWS_RSS.format(q=quote_plus(query)))
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"Google News '{query}' tidak terbaca")

    hasil = []
    for entry in parsed.entries[:batas]:
        judul_penuh = _bersih(entry.get("title", ""))
        if not judul_penuh:
            continue
        # Google News memformat judul sebagai "Judul - Nama Outlet".
        if " - " in judul_penuh:
            judul, outlet = judul_penuh.rsplit(" - ", 1)
        else:
            judul, outlet = judul_penuh, "Google News"
        ringkasan = _bersih(entry.get("summary", ""))[:500]
        waktu = _waktu_entry(entry)
        hasil.append({
            "id": _id_pernyataan(judul, entry.get("link", "")),
            "tokoh": None,  # ditentukan langkah LLM dari isi artikel
            "teks": f"{judul}. {ringkasan}".strip()[:1200],
            "url": entry.get("link", ""),
            "sumber": outlet,
            "domain": "news.google.com",
            "jenis_sumber": "media",
            "waktu_utc": iso_utc(waktu) if waktu else None,
            "_waktu": waktu,
            "_query": query,
        })
    return hasil


# --------------------------------------------------------------------------
# Perakitan
# --------------------------------------------------------------------------
def _dedup(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Buang item yang isinya nyaris sama; sumber primer selalu menang."""
    prioritas = {"primer": 0, "resmi": 1, "media": 2}
    items = sorted(items, key=lambda i: prioritas.get(i["jenis_sumber"], 3))

    disimpan: List[Dict[str, Any]] = []
    for item in items:
        norm = re.sub(r"[^a-z0-9 ]", "", item["teks"].lower())[:300]
        duplikat = False
        for ada in disimpan:
            ada_norm = re.sub(r"[^a-z0-9 ]", "", ada["teks"].lower())[:300]
            if SequenceMatcher(None, norm, ada_norm).ratio() >= SIMILARITY_THRESHOLD:
                duplikat = True
                break
        if not duplikat:
            disimpan.append(item)
    return disimpan


def collect(
    cfg_statements: Dict[str, Any],
    client: Any = None,
    models_x: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Kumpulkan kandidat pernyataan dari semua sumber.

    Penyaringan apakah sebuah item benar-benar memuat pernyataan yang relevan
    dilakukan langkah LLM berikutnya, bukan di sini.

    `client` + `models_x` mengaktifkan pengambilan postingan X lewat Grok.
    Hasilnya masuk lewat pintu yang sama dengan sumber lain — penyaringan
    umur, dedup, lalu analisa LLM — jadi tidak ada jalur pintas untuknya.
    """
    max_age = int(cfg_statements.get("max_age_hours", 48))
    max_items = int(cfg_statements.get("max_items", 25))

    kandidat: List[Dict[str, Any]] = []
    gagal: List[str] = []

    for akun in cfg_statements.get("truth_social_accounts", []) or []:
        try:
            item = _truth_social(akun)
            log.info("Truth Social @%s: %d postingan", akun, len(item))
            kandidat.extend(item)
        except (HttpError, ValueError, KeyError, TypeError) as exc:
            log.warning("Truth Social @%s gagal (wajar diblokir CDN): %s", akun, exc)
            gagal.append(f"truth_social:{akun}")

    for url in cfg_statements.get("official_feeds", []) or []:
        try:
            item = _feed_resmi(url)
            log.info("Feed resmi %s: %d item", urlparse(url).netloc, len(item))
            kandidat.extend(item)
        except Exception as exc:
            log.warning("Feed resmi %s gagal: %s", url, exc)
            gagal.append("feed_resmi")

    for query in cfg_statements.get("google_news_queries", []) or []:
        try:
            item = _google_news(query)
            log.info("Google News '%s': %d artikel", query, len(item))
            kandidat.extend(item)
        except Exception as exc:
            log.warning("Google News '%s' gagal: %s", query, exc)
            gagal.append("google_news")

    # X lewat Grok: opsional, dan sengaja ditaruh PALING AKHIR supaya
    # `tandai_konfirmasi_media` bisa membandingkannya dengan kandidat dari
    # sumber lain yang sudah terkumpul.
    cfg_x = cfg_statements.get("x_grok") or {}
    if client is not None and models_x and cfg_x.get("aktif"):
        for akun in cfg_x.get("akun", []) or []:
            try:
                item = x_grok.ambil_postingan(client, models_x, akun, max_age)
                # id dibuat di sini, bukan di x_grok, supaya seluruh kandidat
                # memakai satu skema id yang sama dan dedup lintas-sumber
                # tetap bekerja.
                for i in item:
                    i["id"] = _id_pernyataan(i["teks"], i["url"])
                x_grok.tandai_konfirmasi_media(item, kandidat)
                kandidat.extend(item)
            except Exception as exc:
                log.warning("Pengambilan X @%s gagal: %s", akun, exc)
                gagal.append(f"x_grok:{akun}")

    batas_waktu = now_utc() - timedelta(hours=max_age)
    segar = [i for i in kandidat if i["_waktu"] is not None and i["_waktu"] >= batas_waktu]
    log.info("Kandidat pernyataan segar (< %sj): %d dari %d", max_age, len(segar), len(kandidat))

    segar.sort(key=lambda i: i["_waktu"], reverse=True)
    unik = _dedup(segar)[:max_items]
    for i in unik:
        i.pop("_waktu", None)
        i.pop("_query", None)

    # Kalau semua sumber gagal, ini kegagalan sumber. Kalau sumber jalan tapi
    # memang tidak ada pernyataan baru, itu hasil yang sah — bukan kegagalan.
    total_sumber = (
        len(cfg_statements.get("truth_social_accounts", []) or [])
        + len(cfg_statements.get("official_feeds", []) or [])
        + len(cfg_statements.get("google_news_queries", []) or [])
        + (len(cfg_x.get("akun", []) or []) if cfg_x.get("aktif") else 0)
    )
    # `>= 0` akan selalu benar kalau tidak ada sumber terkonfigurasi sama
    # sekali, dan itu bukan kegagalan sumber — itu konfigurasi kosong.
    semua_gagal = total_sumber > 0 and len(gagal) >= total_sumber

    return {
        "items": unik,
        "failed": ["statements"] if semua_gagal else [],
        "sumber_gagal": gagal,
    }
