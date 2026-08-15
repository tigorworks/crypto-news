"""Ambil berita RSS, buang duplikat, dan beri skor prioritas awal."""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import feedparser

from ..utils.timezone import iso_utc, now_utc

log = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = 0.85

# Kata kunci yang menaikkan skor prioritas sebelum LLM menyentuh apa pun.
# Bobot ditentukan seberapa besar dampak historis tema tersebut ke harga BTC.
KEYWORD_WEIGHTS = {
    r"\bhack(ed|ing)?\b|\bexploit\b|\bbreach\b|\bstolen\b": 30,
    r"\bsec\b|\bregulat|\blawsuit\b|\bban\b|\bcrackdown\b": 25,
    r"\betf\b|\binflow|\boutflow": 25,
    r"\bfed\b|\bfomc\b|\brate cut\b|\brate hike\b|\bpowell\b": 25,
    r"\bcpi\b|\binflation\b|\bnfp\b|\bjobs report\b|\bpce\b": 20,
    r"\bhalving\b": 20,
    r"\bliquidat": 15,
    r"\bmicrostrategy\b|\btreasury\b|\bcorporate buy": 15,
    r"\bblackrock\b|\bfidelity\b|\bgrayscale\b": 15,
    r"\bbitcoin\b|\bbtc\b": 10,
    r"\bwhale\b|\bmempool\b|\bhashrate\b|\bminer": 10,
}


def _clean_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (title or "").lower()).strip()


def _entry_time(entry: Any) -> Optional[datetime]:
    for field in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, field, None) or entry.get(field)
        if parsed:
            try:
                return datetime(*parsed[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
    return None


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except ValueError:
        return ""


def _source_name(entry: Any, feed_title: str, url: str) -> str:
    for candidate in (getattr(entry, "source", None), feed_title):
        if isinstance(candidate, dict):
            candidate = candidate.get("title")
        if candidate:
            return str(candidate)
    return _domain(url) or "Tidak diketahui"


def _priority_score(title: str, summary: str) -> int:
    text = f"{title} {summary}".lower()
    score = 0
    for pattern, weight in KEYWORD_WEIGHTS.items():
        if re.search(pattern, text):
            score += weight
    return min(score, 100)


def _article_id(title: str, url: str) -> str:
    basis = _normalize_title(title) or url
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12]


def _fetch_feed(url: str) -> List[Dict[str, Any]]:
    parsed = feedparser.parse(url)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"feed tidak bisa diparsing: {parsed.get('bozo_exception')}")

    feed_title = _clean_text(getattr(parsed.feed, "title", "") or "")
    items: List[Dict[str, Any]] = []
    for entry in parsed.entries:
        link = entry.get("link") or ""
        title = _clean_text(entry.get("title", ""))
        if not title or not link:
            continue
        summary = _clean_text(entry.get("summary", "") or entry.get("description", ""))[:600]
        published = _entry_time(entry)
        items.append(
            {
                "id": _article_id(title, link),
                "judul": title,
                "ringkasan": summary,
                "url": link,
                "sumber": _source_name(entry, feed_title, link),
                "domain": _domain(link),
                "waktu_utc": iso_utc(published) if published else None,
                "_published": published,
            }
        )
    return items


def _dedup(articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Gabungkan artikel dengan judul mirip; hitung berapa outlet melaporkannya."""
    kept: List[Dict[str, Any]] = []
    seen_ids: set = set()

    for article in articles:
        if article["id"] in seen_ids:
            continue
        norm = _normalize_title(article["judul"])
        duplicate_of = None
        for existing in kept:
            if SequenceMatcher(None, norm, _normalize_title(existing["judul"])).ratio() >= SIMILARITY_THRESHOLD:
                duplicate_of = existing
                break

        if duplicate_of is None:
            article["jumlah_konfirmasi"] = 1
            article["outlet_lain"] = []
            kept.append(article)
            seen_ids.add(article["id"])
            continue

        # Cerita yang sama dari outlet lain = konfirmasi tambahan, bukan sampah.
        if article["domain"] and article["domain"] not in duplicate_of["outlet_lain"]:
            if article["domain"] != duplicate_of["domain"]:
                duplicate_of["outlet_lain"].append(article["domain"])
                duplicate_of["jumlah_konfirmasi"] += 1
        # Simpan versi paling awal terbit sebagai acuan waktu.
        if article["_published"] and duplicate_of["_published"]:
            if article["_published"] < duplicate_of["_published"]:
                duplicate_of["waktu_utc"] = article["waktu_utc"]
                duplicate_of["_published"] = article["_published"]

    return kept


def collect(feeds: List[str], max_fetch: int = 120, max_age_hours: int = 36) -> Dict[str, Any]:
    """Ambil semua feed, buang yang basi, dedup, lalu urutkan berdasar prioritas."""
    raw: List[Dict[str, Any]] = []
    failed: List[str] = []

    for feed_url in feeds:
        try:
            items = _fetch_feed(feed_url)
            log.info("Feed %s: %d artikel", _domain(feed_url), len(items))
            raw.extend(items)
        except Exception as exc:  # feedparser bisa melempar apa saja
            log.warning("Feed %s gagal: %s", feed_url, exc)
            failed.append(_domain(feed_url) or feed_url)

    cutoff = now_utc() - timedelta(hours=max_age_hours)
    fresh = [
        a for a in raw
        if a["_published"] is not None and a["_published"] >= cutoff
    ]
    log.info("Artikel segar (< %sj): %d dari %d", max_age_hours, len(fresh), len(raw))

    fresh.sort(key=lambda a: a["_published"], reverse=True)
    deduped = _dedup(fresh[:max_fetch])

    for article in deduped:
        base = _priority_score(article["judul"], article["ringkasan"])
        # Cerita yang dilaporkan banyak outlet biasanya lebih penting.
        article["skor_prioritas"] = min(100, base + (article["jumlah_konfirmasi"] - 1) * 5)
        article.pop("_published", None)
        article.pop("outlet_lain", None)

    deduped.sort(key=lambda a: a["skor_prioritas"], reverse=True)
    log.info("Artikel unik setelah dedup: %d", len(deduped))

    return {"articles": deduped, "failed": failed}
