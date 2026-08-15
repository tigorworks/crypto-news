"""Indikator teknikal — murni perhitungan pandas/numpy.

Tidak ada LLM di file ini, dan tidak boleh ada. Semua angka yang muncul di
output berasal dari rumus di bawah supaya bisa diverifikasi ulang.
Sengaja tanpa pandas-ta / TA-Lib: keduanya rapuh saat build di CI.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ..utils.format import angka_id, persen_id

log = logging.getLogger(__name__)

EMA_PERIODS = (20, 50, 100, 200)


# --------------------------------------------------------------------------
# Rumus dasar
# --------------------------------------------------------------------------
def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    # Wilder smoothing = EMA dengan alpha 1/period
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    # avg_loss == 0: tidak ada penurunan sama sekali.
    #   - masih ada kenaikan -> RSI 100
    #   - sama sekali datar   -> RSI 50 (netral), bukan 100
    flat = (avg_gain == 0) & (avg_loss == 0)
    out = out.mask(flat, 50.0)
    return out.fillna(100.0)


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Dict[str, pd.Series]:
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal)
    return {
        "macd": macd_line,
        "signal": signal_line,
        "histogram": macd_line - signal_line,
    }


def stoch_rsi(series: pd.Series, period: int = 14, smooth_k: int = 3, smooth_d: int = 3) -> Dict[str, pd.Series]:
    r = rsi(series, period)
    lowest = r.rolling(period).min()
    highest = r.rolling(period).max()
    span = (highest - lowest).replace(0, np.nan)
    raw = ((r - lowest) / span * 100).fillna(50.0)
    k = raw.rolling(smooth_k).mean()
    return {"k": k, "d": k.rolling(smooth_d).mean()}


def bollinger(series: pd.Series, period: int = 20, std_mult: float = 2.0) -> Dict[str, pd.Series]:
    middle = series.rolling(period).mean()
    std = series.rolling(period).std(ddof=0)
    upper = middle + std_mult * std
    lower = middle - std_mult * std
    bandwidth = (upper - lower) / middle.replace(0, np.nan) * 100
    return {"upper": upper, "middle": middle, "lower": lower, "bandwidth": bandwidth}


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False).mean()


def obv(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df["close"].diff().fillna(0.0))
    return (direction * df["volume"]).cumsum()


def vwap_harian(df: pd.DataFrame) -> Optional[float]:
    """VWAP sejak awal hari UTC berjalan (butuh kolom `open_time` bertipe datetime)."""
    if "waktu" not in df.columns or df.empty:
        return None
    hari_ini = df["waktu"].dt.date.iloc[-1]
    sesi = df[df["waktu"].dt.date == hari_ini]
    if sesi.empty or sesi["volume"].sum() == 0:
        return None
    typical = (sesi["high"] + sesi["low"] + sesi["close"]) / 3
    return float((typical * sesi["volume"]).sum() / sesi["volume"].sum())


def pivot_points(df: pd.DataFrame) -> Dict[str, float]:
    """Pivot klasik berdasarkan candle terakhir yang sudah selesai."""
    if len(df) < 2:
        return {}
    prev = df.iloc[-2]
    pivot = (prev["high"] + prev["low"] + prev["close"]) / 3
    return {
        "pivot": round(float(pivot), 2),
        "r1": round(float(2 * pivot - prev["low"]), 2),
        "r2": round(float(pivot + (prev["high"] - prev["low"])), 2),
        "s1": round(float(2 * pivot - prev["high"]), 2),
        "s2": round(float(pivot - (prev["high"] - prev["low"])), 2),
    }


# --------------------------------------------------------------------------
# Struktur pasar
# --------------------------------------------------------------------------
def _swing_points(df: pd.DataFrame, window: int = 5) -> Dict[str, List[float]]:
    """Swing high/low: titik yang lebih ekstrem dari `window` candle di kiri-kanannya."""
    highs, lows = [], []
    high, low = df["high"].values, df["low"].values
    for i in range(window, len(df) - window):
        segment_h = high[i - window : i + window + 1]
        segment_l = low[i - window : i + window + 1]
        if high[i] == segment_h.max():
            highs.append(float(high[i]))
        if low[i] == segment_l.min():
            lows.append(float(low[i]))
    return {"highs": highs, "lows": lows}


def _cluster(levels: List[float], tolerance_pct: float = 0.6) -> List[float]:
    """Gabungkan level yang berdekatan jadi satu supaya daftarnya tidak berisik."""
    if not levels:
        return []
    levels = sorted(levels)
    clusters: List[List[float]] = [[levels[0]]]
    for level in levels[1:]:
        if abs(level - clusters[-1][-1]) / clusters[-1][-1] * 100 <= tolerance_pct:
            clusters[-1].append(level)
        else:
            clusters.append([level])
    return [round(float(np.mean(c)), 2) for c in clusters]


def support_resistance(df: pd.DataFrame, price: float) -> Dict[str, List[float]]:
    swings = _swing_points(df)
    all_levels = _cluster(swings["highs"] + swings["lows"])
    support = sorted([lv for lv in all_levels if lv < price], reverse=True)[:3]
    resistance = sorted([lv for lv in all_levels if lv > price])[:3]
    return {"support": support, "resistance": resistance}


def fibonacci(df: pd.DataFrame, lookback: int = 120) -> Dict[str, float]:
    window = df.tail(lookback)
    if window.empty:
        return {}
    high = float(window["high"].max())
    low = float(window["low"].min())
    span = high - low
    if span <= 0:
        return {}
    return {
        "high": round(high, 2),
        "low": round(low, 2),
        "fib_236": round(high - span * 0.236, 2),
        "fib_382": round(high - span * 0.382, 2),
        "fib_500": round(high - span * 0.5, 2),
        "fib_618": round(high - span * 0.618, 2),
        "fib_786": round(high - span * 0.786, 2),
    }


def rsi_divergence(df: pd.DataFrame, rsi_series: pd.Series, lookback: int = 60, window: int = 5) -> Optional[str]:
    """Deteksi divergensi antara dua swing terakhir.

    bearish: harga higher-high tapi RSI lower-high
    bullish: harga lower-low tapi RSI higher-low
    """
    tail = df.tail(lookback).reset_index(drop=True)
    rsi_tail = rsi_series.tail(lookback).reset_index(drop=True)
    if len(tail) < window * 3:
        return None

    high_idx, low_idx = [], []
    high, low = tail["high"].values, tail["low"].values
    for i in range(window, len(tail) - window):
        if high[i] == high[i - window : i + window + 1].max():
            high_idx.append(i)
        if low[i] == low[i - window : i + window + 1].min():
            low_idx.append(i)

    if len(high_idx) >= 2:
        a, b = high_idx[-2], high_idx[-1]
        if high[b] > high[a] and rsi_tail[b] < rsi_tail[a]:
            return "bearish"
    if len(low_idx) >= 2:
        a, b = low_idx[-2], low_idx[-1]
        if low[b] < low[a] and rsi_tail[b] > rsi_tail[a]:
            return "bullish"
    return None


def bollinger_squeeze(bandwidth: pd.Series, lookback: int = 100) -> bool:
    """True kalau bandwidth berada di persentil terendah 20% dari 100 periode terakhir."""
    window = bandwidth.tail(lookback).dropna()
    if len(window) < 20:
        return False
    return bool(window.iloc[-1] <= np.percentile(window.values, 20))


def _cross_signal(fast: pd.Series, slow: pd.Series, lookback: int = 5) -> Optional[str]:
    """Golden/death cross yang terjadi dalam `lookback` candle terakhir."""
    if len(fast.dropna()) < lookback + 1 or len(slow.dropna()) < lookback + 1:
        return None
    diff = (fast - slow).dropna()
    if len(diff) < lookback + 1:
        return None
    recent = diff.tail(lookback + 1)
    sebelum, sesudah = recent.iloc[0], recent.iloc[-1]
    if sebelum <= 0 < sesudah:
        return "golden_cross"
    if sebelum >= 0 > sesudah:
        return "death_cross"
    return None


# --------------------------------------------------------------------------
# Perakitan per timeframe
# --------------------------------------------------------------------------
def _to_frame(klines: List[Dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(klines)
    if df.empty:
        return df
    df["waktu"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df[["waktu", "open", "high", "low", "close", "volume"]].astype(
        {"open": float, "high": float, "low": float, "close": float, "volume": float}
    )


def _f(value: Any, digits: int = 2) -> Optional[float]:
    """Bulatkan dengan aman; NaN/inf jadi None supaya JSON tetap valid."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(v):
        return None
    return round(v, digits)


def analyze_timeframe(klines: List[Dict[str, Any]]) -> Dict[str, Any]:
    df = _to_frame(klines)
    if df.empty or len(df) < 30:
        log.warning("Data candle terlalu sedikit (%d), timeframe dilewati", len(df))
        return {}

    close = df["close"]
    price = float(close.iloc[-1])

    emas = {p: ema(close, p) for p in EMA_PERIODS}
    rsi_series = rsi(close)
    macd_data = macd(close)
    stoch = stoch_rsi(close)
    bb = bollinger(close)
    atr_series = atr(df)
    obv_series = obv(df)
    volume_ma20 = df["volume"].rolling(20).mean()

    ema_values = {f"ema{p}": _f(emas[p].iloc[-1]) for p in EMA_PERIODS}
    posisi_ema = {
        f"ema{p}": ("di_atas" if price > emas[p].iloc[-1] else "di_bawah")
        for p in EMA_PERIODS
        if pd.notna(emas[p].iloc[-1])
    }

    volume_terakhir = float(df["volume"].iloc[-1])
    volume_rata = float(volume_ma20.iloc[-1]) if pd.notna(volume_ma20.iloc[-1]) else 0.0
    obv_slope = float(obv_series.iloc[-1] - obv_series.iloc[-6]) if len(obv_series) > 6 else 0.0

    levels = support_resistance(df, price)
    macd_hist = macd_data["histogram"]

    return {
        "harga": _f(price),
        "tren": {
            **ema_values,
            "posisi": posisi_ema,
            "cross_50_200": _cross_signal(emas[50], emas[200], lookback=5),
            "cross_20_50": _cross_signal(emas[20], emas[50], lookback=3),
            "struktur": (
                "naik" if price > emas[50].iloc[-1] > emas[200].iloc[-1]
                else "turun" if price < emas[50].iloc[-1] < emas[200].iloc[-1]
                else "campuran"
            ),
        },
        "momentum": {
            "rsi": _f(rsi_series.iloc[-1], 1),
            "rsi_zona": (
                "jenuh_beli" if rsi_series.iloc[-1] >= 70
                else "jenuh_jual" if rsi_series.iloc[-1] <= 30
                else "netral"
            ),
            "macd": _f(macd_data["macd"].iloc[-1]),
            "macd_signal": _f(macd_data["signal"].iloc[-1]),
            "macd_histogram": _f(macd_hist.iloc[-1]),
            "macd_arah": (
                "menguat" if len(macd_hist) > 1 and macd_hist.iloc[-1] > macd_hist.iloc[-2]
                else "melemah"
            ),
            "stoch_rsi_k": _f(stoch["k"].iloc[-1], 1),
            "stoch_rsi_d": _f(stoch["d"].iloc[-1], 1),
            "divergensi_rsi": rsi_divergence(df, rsi_series),
        },
        "volatilitas": {
            "bb_atas": _f(bb["upper"].iloc[-1]),
            "bb_tengah": _f(bb["middle"].iloc[-1]),
            "bb_bawah": _f(bb["lower"].iloc[-1]),
            "bb_bandwidth": _f(bb["bandwidth"].iloc[-1]),
            "bb_squeeze": bollinger_squeeze(bb["bandwidth"]),
            "posisi_dalam_band": (
                "atas" if price > bb["upper"].iloc[-1]
                else "bawah" if price < bb["lower"].iloc[-1]
                else "dalam"
            ),
            "atr": _f(atr_series.iloc[-1]),
            "atr_pct": _f(atr_series.iloc[-1] / price * 100 if price else None),
        },
        "volume": {
            "terakhir": _f(volume_terakhir, 0),
            "rata_20": _f(volume_rata, 0),
            "rasio_vs_rata": _f(volume_terakhir / volume_rata if volume_rata else None),
            "obv": _f(obv_series.iloc[-1], 0),
            "obv_arah": "naik" if obv_slope > 0 else "turun" if obv_slope < 0 else "datar",
            "vwap_harian": _f(vwap_harian(df)),
        },
        "level": {
            "support": levels["support"],
            "resistance": levels["resistance"],
            **fibonacci(df),
            "pivot": pivot_points(df),
        },
    }


def oi_price_signal(
    price_change_pct: Optional[float], oi_history: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Interpretasi arah open interest terhadap arah harga."""
    if price_change_pct is None or len(oi_history) < 2:
        return {"sinyal": None, "oi_change_pct": None, "interpretasi": None}

    oi_now = oi_history[-1]["open_interest"]
    oi_prev = oi_history[-2]["open_interest"]
    if not oi_prev:
        return {"sinyal": None, "oi_change_pct": None, "interpretasi": None}

    oi_change = (oi_now - oi_prev) / oi_prev * 100
    naik_harga = price_change_pct > 0
    naik_oi = oi_change > 0

    if naik_harga and naik_oi:
        sinyal = "long_buildup"
        teks = "Harga naik dengan open interest bertambah: posisi long baru masuk."
    elif naik_harga and not naik_oi:
        sinyal = "short_covering"
        teks = "Harga naik tapi open interest turun: kenaikan didorong penutupan short."
    elif not naik_harga and naik_oi:
        sinyal = "short_buildup"
        teks = "Harga turun dengan open interest bertambah: posisi short baru masuk."
    else:
        sinyal = "long_liquidation"
        teks = "Harga turun dengan open interest turun: posisi long ditutup atau dilikuidasi."

    return {
        "sinyal": sinyal,
        "oi_change_pct": _f(oi_change),
        "interpretasi": teks,
    }


def key_levels(per_tf: Dict[str, Dict[str, Any]], price: float, atr_1d: Optional[float]) -> Dict[str, Any]:
    """Gabungkan level dari semua timeframe + hitung level invalidasi.

    Invalidasi naik  = di bawah level ini skenario naik batal (support terdekat - buffer ATR)
    Invalidasi turun = di atas level ini skenario turun batal (resistance terdekat + buffer ATR)
    """
    support: List[float] = []
    resistance: List[float] = []
    for tf_data in per_tf.values():
        level = (tf_data or {}).get("level", {})
        support.extend(level.get("support", []))
        resistance.extend(level.get("resistance", []))

    # Filter ulang terhadap harga sekarang. Level dari tiap timeframe dihitung
    # relatif terhadap harga penutupan timeframe itu sendiri, jadi tanpa
    # penyaringan ini sebuah "resistance" bisa mendarat di bawah harga.
    support = sorted([lv for lv in _cluster(support) if lv < price], reverse=True)[:4]
    resistance = sorted([lv for lv in _cluster(resistance) if lv > price])[:4]

    buffer = (atr_1d or price * 0.01) * 0.5
    invalidasi_naik = round(support[0] - buffer, 2) if support else round(price * 0.97, 2)
    invalidasi_turun = round(resistance[0] + buffer, 2) if resistance else round(price * 1.03, 2)

    return {
        "support": support,
        "resistance": resistance,
        "invalidasi_naik": invalidasi_naik,
        "invalidasi_turun": invalidasi_turun,
    }


def deteksi_sinyal_palsu(
    klines: List[Dict[str, Any]],
    funding_rate: Optional[float] = None,
    oi_change_pct: Optional[float] = None,
    window: int = 5,
    lookback: int = 40,
) -> List[Dict[str, Any]]:
    """Deteksi pola yang sering menandai pergerakan tidak tulus.

    Semua deteksi di sini murni geometri candle dan volume — tidak ada
    penilaian LLM. Yang dikembalikan adalah fakta terukur; penafsirannya
    diserahkan ke langkah berikutnya.

    Perlu ditegaskan: pola-pola ini adalah petunjuk, bukan bukti. Wick panjang
    bisa muncul dari likuiditas tipis biasa, bukan hanya dari perburuan stop.
    """
    df = _to_frame(klines)
    if df.empty or len(df) < lookback:
        return []

    sinyal: List[Dict[str, Any]] = []
    tail = df.tail(lookback).reset_index(drop=True)
    high, low, close, open_, vol = (
        tail["high"].values, tail["low"].values, tail["close"].values,
        tail["open"].values, tail["volume"].values,
    )
    vol_rata = float(np.mean(vol[:-1])) if len(vol) > 1 else 0.0

    # -- 1. Sapuan likuiditas (stop hunt) --------------------------------
    # Candle menembus swing sebelumnya lalu ditutup kembali di dalam rentang:
    # level dipicu, tapi harga tidak mau bertahan di sana.
    swing_high = float(np.max(high[:-window])) if len(high) > window else None
    swing_low = float(np.min(low[:-window])) if len(low) > window else None

    for i in range(len(tail) - window, len(tail)):
        if swing_high and high[i] > swing_high and close[i] < swing_high:
            sinyal.append({
                "jenis": "sapuan_likuiditas_atas",
                "arah": "bearish",
                "keterangan": (
                    f"Harga menembus swing high {angka_id(swing_high)} hingga {angka_id(high[i])} "
                    f"lalu ditutup kembali di {angka_id(close[i])} — level dipicu tanpa diikuti."
                ),
                "kekuatan": 4 if vol[i] > vol_rata * 1.5 else 3,
            })
            break

    for i in range(len(tail) - window, len(tail)):
        if swing_low and low[i] < swing_low and close[i] > swing_low:
            sinyal.append({
                "jenis": "sapuan_likuiditas_bawah",
                "arah": "bullish",
                "keterangan": (
                    f"Harga menembus swing low {angka_id(swing_low)} hingga {angka_id(low[i])} "
                    f"lalu ditutup kembali di {angka_id(close[i])} — stop dipanen lalu harga pulih."
                ),
                "kekuatan": 4 if vol[i] > vol_rata * 1.5 else 3,
            })
            break

    # -- 2. Wick dominan pada candle terakhir ----------------------------
    body = abs(close[-1] - open_[-1])
    wick_atas = high[-1] - max(close[-1], open_[-1])
    wick_bawah = min(close[-1], open_[-1]) - low[-1]
    rentang = high[-1] - low[-1]
    if rentang > 0 and body > 0:
        if wick_atas > body * 2 and wick_atas / rentang > 0.5:
            sinyal.append({
                "jenis": "penolakan_atas",
                "arah": "bearish",
                "keterangan": (
                    f"Wick atas {angka_id(wick_atas)} berbanding badan candle {angka_id(body)} — "
                    "penjual menyerap kenaikan di area itu."
                ),
                "kekuatan": 3,
            })
        if wick_bawah > body * 2 and wick_bawah / rentang > 0.5:
            sinyal.append({
                "jenis": "penolakan_bawah",
                "arah": "bullish",
                "keterangan": (
                    f"Wick bawah {angka_id(wick_bawah)} berbanding badan candle {angka_id(body)} — "
                    "pembeli menyerap tekanan jual di area itu."
                ),
                "kekuatan": 3,
            })

    # -- 3. Volume besar tanpa perpindahan harga (absorpsi) --------------
    if vol_rata > 0 and vol[-1] > vol_rata * 2.5:
        gerak_pct = abs(close[-1] - open_[-1]) / open_[-1] * 100 if open_[-1] else 0
        if gerak_pct < 0.3:
            sinyal.append({
                "jenis": "absorpsi_volume",
                "arah": "netral",
                "keterangan": (
                    f"Volume {angka_id(vol[-1] / vol_rata, 1)}× rata-rata tapi harga hanya bergerak "
                    f"{persen_id(gerak_pct)} — ada pihak besar menyerap order di harga ini."
                ),
                "kekuatan": 4,
            })

    # -- 4. Breakout dengan volume menurun -------------------------------
    # Harga membuat tertinggi baru, tapi volumenya lebih kecil dari puncak
    # sebelumnya: partisipasi tidak mengonfirmasi pergerakan.
    idx_tertinggi_lama = int(np.argmax(high[:-window])) if len(high) > window else None
    if idx_tertinggi_lama is not None and high[-1] > high[idx_tertinggi_lama]:
        if vol[-1] < vol[idx_tertinggi_lama] * 0.7:
            sinyal.append({
                "jenis": "breakout_volume_lemah",
                "arah": "bearish",
                "keterangan": (
                    f"Tertinggi baru {angka_id(high[-1])} terbentuk dengan volume hanya "
                    f"{persen_id(vol[-1] / vol[idx_tertinggi_lama] * 100, 0)} dari volume di puncak sebelumnya."
                ),
                "kekuatan": 3,
            })

    # -- 5. Funding ekstrem + OI naik ------------------------------------
    if funding_rate is not None and abs(funding_rate) > 0.0005:
        arah_posisi = "long" if funding_rate > 0 else "short"
        if oi_change_pct is not None and oi_change_pct > 0:
            sinyal.append({
                "jenis": "posisi_padat",
                "arah": "bearish" if funding_rate > 0 else "bullish",
                "keterangan": (
                    f"Funding {persen_id(funding_rate * 100, 3)} per 8 jam dengan open interest naik "
                    f"{persen_id(oi_change_pct, 1)} — posisi {arah_posisi} menumpuk dan mahal untuk "
                    "dipertahankan."
                ),
                "kekuatan": 4,
            })

    return sinyal


def analyze(
    klines_per_tf: Dict[str, List[Dict[str, Any]]],
    price: Dict[str, Any],
    oi_history: Optional[List[Dict[str, Any]]] = None,
    funding_rate: Optional[float] = None,
) -> Dict[str, Any]:
    """Analisa lengkap semua timeframe + level kunci + sinyal OI."""
    result: Dict[str, Any] = {}
    for tf, klines in klines_per_tf.items():
        try:
            result[tf] = analyze_timeframe(klines)
        except Exception as exc:
            log.warning("Analisa teknikal %s gagal: %s", tf, exc)
            result[tf] = {}

    last_price = float(price.get("last") or 0)
    atr_1d = (result.get("1d", {}).get("volatilitas", {}) or {}).get("atr")
    result["key_levels"] = key_levels(result, last_price, atr_1d)

    oi_signal = oi_price_signal(price.get("change_24h_pct"), oi_history or [])
    result["oi_price_signal"] = oi_signal["sinyal"]
    result["oi_price_interpretasi"] = oi_signal["interpretasi"]
    result["oi_change_pct"] = oi_signal["oi_change_pct"]

    # Sinyal palsu dicari di 4H (cukup halus untuk menangkap sapuan likuiditas,
    # cukup kasar untuk tidak kebanjiran derau seperti di 1H).
    sinyal_palsu: List[Dict[str, Any]] = []
    for tf in ("4h", "1h"):
        for s in deteksi_sinyal_palsu(
            klines_per_tf.get(tf, []), funding_rate, oi_signal["oi_change_pct"]
        ):
            s["timeframe"] = tf
            sinyal_palsu.append(s)
    # Funding/OI tidak bergantung timeframe — cukup laporkan sekali.
    terlihat = set()
    unik = []
    for s in sinyal_palsu:
        kunci = (s["jenis"], s["keterangan"])
        if kunci in terlihat:
            continue
        terlihat.add(kunci)
        unik.append(s)
    result["sinyal_palsu"] = unik

    return result
