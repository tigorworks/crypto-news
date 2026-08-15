"""Baca konfigurasi dari config.yaml + environment variable."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
DOCS_DIR = ROOT / "docs"
DATA_DIR = DOCS_DIR / "data"
ARCHIVE_DIR = DATA_DIR / "archive"


class ConfigError(Exception):
    pass


@dataclass
class Secrets:
    openrouter_api_key: Optional[str] = None
    telegram_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    fred_api_key: Optional[str] = None

    @property
    def llm_enabled(self) -> bool:
        return bool(self.openrouter_api_key)

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_token and self.telegram_chat_id)


@dataclass
class Config:
    symbol: str = "BTCUSDT"
    timeframes: List[str] = field(default_factory=lambda: ["1d", "4h", "1h"])
    candle_limit: int = 250
    llm: Dict[str, Any] = field(default_factory=dict)
    news: Dict[str, Any] = field(default_factory=dict)
    statements: Dict[str, Any] = field(default_factory=dict)
    source_tiers: Dict[str, int] = field(default_factory=dict)
    fomc_dates: List[str] = field(default_factory=list)
    archive_retention_days: int = 90
    site_url: str = ""
    repo_url: str = ""
    secrets: Secrets = field(default_factory=Secrets)

    # --- akses turunan -------------------------------------------------
    def llm_models(self, step: str) -> List[str]:
        """Daftar model untuk satu step, sudah dibuang placeholder yang belum diisi."""
        raw = self.llm.get(step) or []
        if isinstance(raw, str):
            raw = [raw]
        return [m for m in raw if m and not str(m).upper().startswith("ISI-")]

    @property
    def llm_base_url(self) -> str:
        return self.llm.get("base_url", "https://openrouter.ai/api/v1")

    @property
    def max_cost_usd(self) -> float:
        return float(self.llm.get("max_cost_usd_per_run", 0.15))

    def tier(self, domain: str) -> int:
        """Tier kredibilitas sumber; default 3 kalau tidak terdaftar."""
        domain = (domain or "").lower().removeprefix("www.")
        for known, tier in self.source_tiers.items():
            if domain.endswith(known.lower()):
                return int(tier)
        return 3


def load_config(path: Path = CONFIG_PATH) -> Config:
    if not path.exists():
        raise ConfigError(f"config.yaml tidak ditemukan di {path}")

    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    secrets = Secrets(
        openrouter_api_key=os.environ.get("OPENROUTER_API_KEY") or None,
        telegram_token=os.environ.get("TELEGRAM_TOKEN") or None,
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID") or None,
        fred_api_key=os.environ.get("FRED_API_KEY") or None,
    )

    cfg = Config(
        symbol=raw.get("symbol", "BTCUSDT"),
        timeframes=raw.get("timeframes", ["1d", "4h", "1h"]),
        candle_limit=int(raw.get("candle_limit", 250)),
        llm=raw.get("llm", {}) or {},
        news=raw.get("news", {}) or {},
        statements=raw.get("statements", {}) or {},
        source_tiers=raw.get("source_tiers", {}) or {},
        fomc_dates=[str(d) for d in (raw.get("fomc_dates") or [])],
        archive_retention_days=int(raw.get("archive_retention_days", 90)),
        site_url=raw.get("site_url", ""),
        repo_url=raw.get("repo_url", ""),
        secrets=secrets,
    )
    return cfg
