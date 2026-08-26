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
from ..utils.timezone import now_utc

#: Di bawah ambang ini, volume candle terakhir belum layak dibandingkan dengan
#: rata-rata harian penuh. Candle harian Binance berganti tepat 00:00 UTC dan
#: volumenya menumpuk sepanjang hari, jadi candle yang baru berjalan separuh
#: otomatis membaca "tipis" berapa pun ramainya pasar.
#:
#: Angkanya dipilih dari jadwal: cron pukul 23.15 UTC memberi candle sekitar
#: 97% terisi, jadi ambang 90% memberi ruang molor eksekusi tanpa pernah
#: menolak run terjadwal. Yang tersaring justru run manual di tengah hari —
#: dan itu memang yang bermasalah.
AMBANG_CANDLE_LENGKAP = 0.9

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


def _kelengkapan_candle(df: pd.DataFrame) -> Optional[float]:
    """Seberapa jauh candle TERAKHIR sudah berjalan, dalam 0..1.

    Durasinya diukur dari jarak antar candle di data itu sendiri, bukan
    ditebak dari nama timeframe — fungsi ini tidak menerima label timeframe,
    dan menebaknya akan salah diam-diam kalau sumber datanya berganti.

    Dibutuhkan karena `volume.terakhir` diambil dari candle yang MASIH
    BERJALAN. Pada brief 17 Agustus pukul 11.28 UTC candle harian baru
    berumur 11,5 jam, dan volumenya terbaca 0,67x rata-rata 20 hari —
    di bawah ambang "volume tipis" — padahal dengan laju yang sama ia akan
    mendarat di sekitar 1,40x, alias di ATAS rata-rata.
    """
    if len(df) < 2 or "waktu" not in df.columns:
        return None
    durasi = (df["waktu"].iloc[-1] - df["waktu"].iloc[-2]).total_seconds()
    if durasi <= 0:
        return None
    lewat = (now_utc() - df["waktu"].iloc[-1].to_pydatetime()).total_seconds()
    return max(0.0, min(1.0, lewat / durasi))


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

    kelengkapan = _kelengkapan_candle(df)
    parsial = bool(kelengkapan is not None and kelengkapan < AMBANG_CANDLE_LENGKAP)
    volume_terakhir = float(df["volume"].iloc[-1])
    volume_rata = float(volume_ma20.iloc[-1]) if pd.notna(volume_ma20.iloc[-1]) else 0.0

    # ARAH OBV DIHITUNG DARI CANDLE YANG SUDAH SELESAI SAJA.
    #
    # OBV menambahkan VOLUME candle (bertanda arah penutupan) ke akumulasi,
    # jadi candle yang baru berjalan separuh menyumbang separuh volumenya.
    # Pada hari dengan arah penutupan berlawanan dari beberapa hari
    # sebelumnya, sumbangan yang terpotong itu bisa membalik tanda kemiringan
    # enam candle terakhir — arahnya berubah bukan karena pasar berubah, tapi
    # karena jam berapa pipeline dijalankan.
    #
    # Solusinya bukan menyembunyikan angkanya (nilai OBV kumulatif tetap
    # ditampilkan apa adanya), melainkan mengukur KEMIRINGANNYA pada jendela
    # yang seluruh candle-nya penuh, lalu menandai bahwa candle berjalan
    # belum ikut dihitung.
    seri_obv_selesai = obv_series.iloc[:-1] if parsial else obv_series
    obv_slope = (
        float(seri_obv_selesai.iloc[-1] - seri_obv_selesai.iloc[-6])
        if len(seri_obv_selesai) > 6 else 0.0
    )

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
            # Rasio di atas hanya sebanding kalau candle-nya sudah hampir
            # penuh. Kedua field ini yang menentukan boleh-tidaknya rasio itu
            # dipakai menyimpulkan sesuatu — bukan rasionya sendiri.
            "kelengkapan": _f(kelengkapan, 3),
            "parsial": parsial,
            "obv": _f(obv_series.iloc[-1], 0),
            "obv_arah": "naik" if obv_slope > 0 else "turun" if obv_slope < 0 else "datar",
            # Arahnya diukur sampai candle terakhir yang SUDAH SELESAI kalau
            # hari berjalan belum penuh — supaya jawabannya tidak berubah
            # hanya karena jam menjalankan pipeline.
            "obv_arah_tanpa_candle_berjalan": parsial,
            "vwap_harian": _f(vwap_harian(df)),
            # VWAP "harian" di atas dihitung dari candle-candle bertanggal
            # hari ini. Pada brief harian yang cuma memakai timeframe 1D,
            # isinya berarti satu candle: harga rata-rata tertimbang HARI
            # BERJALAN, bukan sesi penuh. Selama candle-nya belum penuh,
            # angka itu masih akan bergerak sampai tengah malam UTC — dan
            # tanpa penanda ini ia terbaca seperti level yang sudah pasti.
            "vwap_harian_parsial": parsial,
        },
        "level": {
            "support": levels["support"],
            "resistance": levels["resistance"],
            **fibonacci(df),
            "pivot": pivot_points(df),
        },
    }


def volatilitas_realized_tahunan(klines_1d: List[Dict[str, Any]], window: int = 30) -> Optional[float]:
    """Volatilitas realized tahunan (%) dari log return candle harian.

    Dihitung dari data yang SUDAH ADA (candle harian) tanpa sumber tambahan,
    supaya bisa dibandingkan langsung dengan DVOL (implied vol tahunan
    Deribit): rasio IV/RV menunjukkan opsi mahal (>1, pasar membayar premi
    untuk proteksi/spekulasi) atau murah (<1, jarang dan biasanya sinyal
    kompresi menjelang pergerakan besar) relatif terhadap volatilitas yang
    SUNGGUHAN terjadi.
    """
    df = _to_frame(klines_1d)
    if len(df) < window + 1:
        return None
    closes = df["close"].tail(window + 1).to_numpy()
    if (closes <= 0).any():
        return None
    log_return = np.diff(np.log(closes))
    if len(log_return) < 2:
        return None
    # 365 (bukan 252 seperti saham): kripto berdagang tiap hari, tanpa akhir
    # pekan libur.
    return _f(float(np.std(log_return, ddof=1)) * np.sqrt(365) * 100, 2)


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


# Kelas pergerakan harian, dinyatakan sebagai kelipatan ATR harian. Memakai
# ATR (bukan ambang persen tetap) supaya penilaiannya mengikuti REZIM: -2%
# pada pasar yang biasa bergerak 1,2% sehari adalah hari yang besar, sementara
# -2% pada pasar yang biasa bergerak 4% adalah hari yang biasa saja.
_AMBANG_DATAR_ATR = 0.25
_AMBANG_BESARAN_ATR = ((0.6, "tipis"), (1.2, "wajar"), (2.2, "besar"))

# Dipakai kalau ATR tidak tersedia (candle kurang dari periode ATR).
_AMBANG_DATAR_PCT = 0.5
_AMBANG_BESARAN_PCT = ((1.0, "tipis"), (2.5, "wajar"), (5.0, "besar"))

# Arti tiap kombinasi arah harga x arah open interest. Ini taksonomi baku
# pasar derivatif, dan yang paling menentukan KUALITAS sebuah pergerakan:
# harga naik karena uang baru masuk berbeda sifatnya dari harga naik karena
# posisi jual ditutup paksa, walaupun persentasenya sama.
#
# TIAP JENIS PUNYA DUA BENTUK, dan pembagiannya sengaja:
#
#   label  — 2-3 kata untuk chip. Chip tidak muat kalimat, dan kalimat yang
#            dipaksa masuk ke chip selalu berakhir jadi frasa aneh.
#   arti   — dua kalimat pendek: APA yang terjadi, lalu APA ARTINYA bagi
#            pembaca. Inilah yang tampil di kartu Sorotan dan Telegram.
#
# Tiga percobaan sebelumnya gagal dengan cara yang berbeda, dan ketiganya
# ditulis di sini supaya tidak diulang:
#
#   1. "penutupan posisi jual, bukan permintaan baru" — separuh keduanya
#      benar, tapi berhenti pada KONTRAS. Pembaca tidak pernah diberi tahu
#      apa bedanya dan kenapa itu penting, jadi kalimatnya menggantung.
#   2. "pedagang yang bertaruh harga turun menutup posisinya dengan membeli"
#      — mencoba menjelaskan mekanismenya di dalam label, dan hasilnya
#      panjang, kaku, serta memperkenalkan kata yang tidak dipakai orang
#      ("bertaruh", "taruhan turun"). Lebih buruk lagi: model ikut menyalin
#      kosakata itu ke judul brief 22 Agustus ("ditopang penutupan taruhan
#      turun yang rapuh").
#   3. "ada yang masuk menjual, bukan sekadar pemilik posisi beli yang
#      keluar" — menerjemahkan long/short jadi "posisi beli"/"posisi jual"
#      justru bikin kontrasnya semu: menutup posisi beli JUGA berarti
#      menjual, jadi dua sisi kontras itu kedengarannya sama saja.
#
# Aturannya sekarang: label dan penjelasan memakai "long"/"short" apa
# adanya (istilah pasar yang memang sudah lazim dalam bahasa Inggris,
# menerjemahkannya justru membingungkan — sama seperti "priced in"),
# kata sehari-hari untuk sisanya (masuk, keluar, menutup, membeli lagi),
# dan tidak ada satu pun kalimat yang berhenti pada "bukan X" tanpa
# mengatakan akibatnya.
_ARTI_JENIS = {
    "long_baru": (
        "long baru",
        "Harga naik karena ada posisi long baru yang masuk dan bertahan, "
        "bukan sekadar short yang ditutup. Kenaikan seperti ini punya "
        "penopang yang lebih kuat.",
    ),
    "short_covering": (
        "penutupan short",
        "Harga naik bukan karena posisi long baru berdatangan, tapi karena "
        "short sebelumnya ditutup dengan membeli lagi. Dorongan seperti itu "
        "habis dengan sendirinya, jadi kenaikannya gampang kehilangan "
        "tenaga.",
    ),
    "short_baru": (
        "short baru",
        "Harga turun karena ada posisi short baru yang masuk dan bertahan, "
        "bukan sekadar long yang ditutup. Tekanannya cenderung bertahan "
        "selama short baru itu belum ditutup.",
    ),
    "long_ditutup": (
        "long ditutup",
        "Harga turun karena posisi long ditutup — sebagian terpaksa "
        "dilikuidasi otomatis saat harga jatuh. Tekanan seperti ini "
        "biasanya mereda begitu posisi yang paling rapuh selesai ditutup.",
    ),
}


_JENIS_DARI_SINYAL_OI = {
    "long_buildup": "long_baru",
    "short_covering": "short_covering",
    "short_buildup": "short_baru",
    "long_liquidation": "long_ditutup",
}


def _besaran(perubahan_abs: float, atr_pct: Optional[float]) -> str:
    ambang = _AMBANG_BESARAN_ATR if atr_pct else _AMBANG_BESARAN_PCT
    satuan = atr_pct if atr_pct else 1.0
    for batas, nama in ambang:
        if perubahan_abs < batas * satuan:
            return nama
    return "ekstrem"


def karakter_pergerakan_24j(
    price: Dict[str, Any],
    teknikal: Dict[str, Any],
    berita: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Klasifikasi pergerakan harga 24 jam: ARAH, BESARAN, dan JENISNYA.

    Dihitung KODE, bukan model. Tiga alasan:

      1. Arah dan besaran adalah aritmetika — tidak ada yang perlu ditafsir,
         dan menyerahkannya ke model cuma membuka peluang salah baca.
      2. Jenis pergerakan (uang baru vs posisi ditutup) punya definisi baku
         dari kombinasi arah harga dan arah open interest. Ini yang paling
         sering ditanyakan pembaca — "naiknya ini naik apa?" — dan jawabannya
         tidak boleh berubah-ubah antar run.
      3. Karena hasilnya deterministik, kalimat naratif model bisa DIPERIKSA
         terhadapnya, dan pembaca tetap mendapat jawabannya walaupun bagian
         AI kebetulan gagal atau ditahan.

    Model tetap kebagian tugasnya: MENJELASKAN kenapa, merangkai berita dan
    data posisi jadi rantai sebab-akibat.
    """
    perubahan = price.get("change_24h_pct")
    if perubahan is None:
        return {"arah": None}
    perubahan = float(perubahan)

    harian = teknikal.get("1d") or {}
    atr_pct = (harian.get("volatilitas") or {}).get("atr_pct")
    atr_pct = float(atr_pct) if atr_pct else None

    batas_datar = (atr_pct * _AMBANG_DATAR_ATR) if atr_pct else _AMBANG_DATAR_PCT
    if abs(perubahan) < batas_datar:
        arah = "datar"
    else:
        arah = "naik" if perubahan > 0 else "turun"

    besaran = _besaran(abs(perubahan), atr_pct)

    # Jenis pergerakan hanya bermakna kalau harganya memang bergerak; pada
    # hari datar, arah open interest tidak menceritakan apa-apa.
    jenis = jenis_ringkas = jenis_arti = None
    if arah != "datar":
        jenis = _JENIS_DARI_SINYAL_OI.get(teknikal.get("oi_price_signal"))
        if jenis:
            jenis_ringkas, jenis_arti = _ARTI_JENIS[jenis]

    vol = harian.get("volume") or {}
    volume_rasio = vol.get("rasio_vs_rata")
    volume_rasio = float(volume_rasio) if volume_rasio else None
    # Candle yang belum penuh selalu terbaca tipis, jadi konfirmasi volume
    # DITAHAN — bukan dipaksa jadi "tidak dikonfirmasi", yang justru akan
    # membuat pergerakan sah terbaca meragukan hanya karena jam menjalankan.
    if vol.get("parsial"):
        volume_rasio = None
    if volume_rasio is None:
        volume_konfirmasi = None
    elif volume_rasio >= 1.2:
        volume_konfirmasi = "dikonfirmasi"
    elif volume_rasio >= 0.8:
        volume_konfirmasi = "netral"
    else:
        volume_konfirmasi = "tidak_dikonfirmasi"

    # Berita yang SEARAH dan cukup kuat dianggap kandidat pemicu. Sengaja
    # tidak menyimpulkan sebab-akibat di sini — kode cuma menyodorkan
    # kandidatnya, model yang merangkai penjelasannya.
    searah = "bullish" if arah == "naik" else "bearish" if arah == "turun" else None
    lawan = "bearish" if arah == "naik" else "bullish" if arah == "turun" else None
    kuat = [
        b for b in (berita or [])
        if (b.get("kekuatan") or 0) >= 4 and b.get("status_kepastian") != "rumor"
    ]
    pendukung = [b.get("judul_id") or b.get("judul") for b in kuat if b.get("sentimen") == searah]
    berlawanan = [b.get("judul_id") or b.get("judul") for b in kuat if b.get("sentimen") == lawan]

    if arah == "datar":
        pendorong = "tidak_ada_pergerakan_berarti"
    elif pendukung:
        pendorong = "berita"
    elif jenis:
        pendorong = "posisi_derivatif"
    else:
        pendorong = "tidak_jelas"

    return {
        "arah": arah,
        "perubahan_pct": _f(perubahan),
        "besaran": besaran,
        "atr_harian_pct": _f(atr_pct),
        "jenis": jenis,
        "jenis_ringkas": jenis_ringkas,
        "jenis_arti": jenis_arti,
        "perubahan_oi_pct": teknikal.get("oi_change_pct"),
        "volume_rasio_vs_rata": _f(volume_rasio),
        "volume_konfirmasi": volume_konfirmasi,
        "pendorong": pendorong,
        "berita_pendukung": [j for j in pendukung if j][:3],
        "berita_berlawanan": [j for j in berlawanan if j][:3],
        "ringkas": _kalimat_pergerakan(
            arah, perubahan, besaran, jenis_arti, volume_konfirmasi, pendukung
        ),
    }


def _kalimat_pergerakan(
    arah: str,
    perubahan: float,
    besaran: str,
    jenis_arti: Optional[str],
    volume_konfirmasi: Optional[str],
    pendukung: List[Any],
) -> str:
    """Beberapa kalimat Indonesia yang merangkum klasifikasi di atas.

    Dirakit kode supaya pembaca tetap mendapat jawaban "naik/turun karena apa
    dan kenaikan/penurunan jenis apa" bahkan pada run yang bagian AI-nya gagal
    atau ditahan critic.

    Yang disisipkan di sini PENJELASANNYA, bukan label chip-nya. Bentuk lama
    ("Sifatnya: penutupan posisi jual.") menyodorkan istilah tanpa
    mengatakan apa akibatnya — dan "Sifatnya:" sendiri kata pembuka yang
    abstrak. Label pendeknya tetap dipakai, tapi tempatnya di chip, di
    sebelah angka yang sudah memberinya konteks.
    """
    besar_kata = {
        "tipis": "tipis", "wajar": "dalam kisaran wajar",
        "besar": "cukup besar", "ekstrem": "sangat besar",
    }[besaran]
    angka = f"{abs(perubahan):.2f}".replace(".", ",")

    if arah == "datar":
        return (
            f"Harga praktis datar dalam 24 jam terakhir ({angka}%), bergerak di "
            "dalam kisaran hariannya yang normal."
        )

    kata_arah = "naik" if arah == "naik" else "turun"
    kalimat = f"Harga {kata_arah} {angka}% dalam 24 jam — pergerakan {besar_kata}."

    if jenis_arti:
        kalimat += f" {jenis_arti}"
    if volume_konfirmasi == "dikonfirmasi":
        kalimat += " Volume di atas rata-rata, jadi pergerakannya terkonfirmasi."
    elif volume_konfirmasi == "tidak_dikonfirmasi":
        kalimat += (
            " Volume di bawah rata-rata, jadi pergerakannya belum terkonfirmasi "
            "partisipasi luas."
        )
    if pendukung:
        kalimat += f" Ada {len(pendukung)} berita berdampak kuat yang searah."
    else:
        kalimat += " Tidak ada berita berdampak kuat yang searah dengan pergerakan ini."
    return kalimat


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


# --------------------------------------------------------------------------
# Penjelasan pola untuk pembaca yang tidak membaca kodenya
# --------------------------------------------------------------------------
# Kartu "Sinyal Palsu" dulu cuma memuat satu kalimat berisi angka — "Harga
# menembus swing high 79.500 hingga 80.000 lalu ditutup kembali di 78.993".
# Kalimat itu BENAR dan padat, tapi hanya bisa dibaca oleh orang yang sudah
# tahu apa itu swing high dan kenapa penutupan di bawahnya penting. Pembaca
# yang tidak tahu melihat tiga angka tanpa satu pun kalimat yang memberi tahu
# apa yang harus ia simpulkan darinya.
#
# KENAPA TEKS TETAP, BUKAN LLM. Ini pilihan sadar, bukan penghematan:
#
#   1. Isinya memang tetap. Cara sebuah pola dideteksi ditentukan oleh rumus
#      di file ini, dan artinya di pasar tidak berubah dari hari ke hari.
#      Yang berubah tiap hari cuma angkanya — dan angka itu sudah ada di
#      `keterangan`, dihitung kode, tidak pernah lewat model.
#   2. Angka karangan adalah kegagalan paling mahal di repo ini (lihat bagian
#      critic di README). Meminta model menuliskan ulang penjelasan berangka
#      tiap hari berarti membuka kelas kesalahan itu untuk keuntungan nol.
#   3. Penjelasan ini justru paling dibutuhkan pada hari seluruh langkah LLM
#      gagal — sama seperti `pergerakan_24j`, yang klasifikasinya juga ditulis
#      kode dengan alasan yang persis sama.
#   4. Karena tidak butuh data selain `jenis` dan `kekuatan`, brief yang SUDAH
#      terbit bisa dilengkapi tanpa satu pun panggilan model
#      (`scripts.lengkapi_penjelasan_sinyal`).
#
# Bentuknya tiga bagian tetap, selalu urut: apa yang DIUKUR kode, apa ARTINYA
# di pasar, lalu apa yang MEMBATALKAN pembacaannya. Bagian ketiga yang paling
# sering dilewatkan penjelasan pola candle di tempat lain, dan justru itu yang
# menjaga pembaca tidak memperlakukan petunjuk sebagai kepastian.
_PENJELASAN_POLA: Dict[str, Dict[str, str]] = {
    "sapuan_likuiditas_atas": {
        "cara_ukur": (
            "Kode mengambil 40 candle terakhir, lalu mencari titik tertinggi dari "
            "candle-candle SEBELUM lima hari terakhir — itulah yang disebut swing high "
            "pada kalimat di atas. Sesudah itu lima candle terbaru diperiksa satu per "
            "satu: adakah yang sempat naik melewati level tersebut tapi ditutup kembali "
            "di bawahnya."
        ),
        "arti": (
            "Di atas puncak lama biasanya menumpuk order yang menunggu dipicu: "
            "stop-loss milik yang berposisi jual, dan order beli otomatis milik yang "
            "mengejar breakout. Harga yang menyentuh area itu memicu semuanya sekaligus "
            "— dan kalau setelah itu justru ditarik turun lalu ditutup di bawah level, "
            "artinya tidak ada permintaan lanjutan yang menampung. Kenaikan tadi "
            "memanen likuiditas, bukan memulai tren baru. Itu sebabnya polanya dibaca "
            "condong turun."
        ),
        "pembatal": (
            "Yang membatalkan pembacaan ini: satu penutupan harian kembali di atas level "
            "yang disapu. Kalau itu terjadi, yang tadi terlihat seperti sapuan berubah "
            "jadi breakout yang benar-benar diterima pasar."
        ),
    },
    "sapuan_likuiditas_bawah": {
        "cara_ukur": (
            "Kode mengambil 40 candle terakhir, lalu mencari titik terendah dari "
            "candle-candle SEBELUM lima hari terakhir — itulah yang disebut swing low "
            "pada kalimat di atas. Sesudah itu lima candle terbaru diperiksa satu per "
            "satu: adakah yang sempat turun menembus level tersebut tapi ditutup kembali "
            "di atasnya."
        ),
        "arti": (
            "Di bawah dasar lama menumpuk stop-loss milik yang berposisi beli, dan di "
            "sanalah likuiditas paling mudah dipanen. Harga yang menusuk area itu "
            "memaksa mereka keluar — lalu, kalau harga langsung pulih dan ditutup di "
            "atas level, penurunan tadi tidak menemukan penjual lanjutan. Yang terjadi "
            "adalah stop dipanen, bukan tekanan jual sungguhan. Itu sebabnya polanya "
            "dibaca condong naik."
        ),
        "pembatal": (
            "Yang membatalkan pembacaan ini: satu penutupan harian kembali di bawah level "
            "yang ditembus. Kalau itu terjadi, penurunannya bukan sapuan melainkan "
            "kelanjutan tren turun."
        ),
    },
    "penolakan_atas": {
        "cara_ukur": (
            "Kode membandingkan tiga bagian candle terakhir: badannya (jarak harga buka "
            "ke harga tutup), bayangan atasnya, dan rentang penuh candle. Pola ini "
            "tercatat kalau bayangan atas lebih dari dua kali panjang badan sekaligus "
            "mengisi lebih dari separuh rentangnya."
        ),
        "arti": (
            "Bayangan panjang di atas berarti harga sempat naik jauh ke area itu lalu "
            "didorong balik sebelum candle-nya tutup. Sepanjang periode itu ada yang "
            "bersedia menjual dalam jumlah cukup besar untuk menyerap seluruh "
            "kenaikannya. Areanya jadi layak dicatat sebagai tempat penawaran jual "
            "menumpuk."
        ),
        "pembatal": (
            "Kalau candle berikutnya berhasil ditutup di atas ujung bayangan itu, "
            "penawaran jual tadi berarti sudah habis diserap dan pembacaannya gugur."
        ),
    },
    "penolakan_bawah": {
        "cara_ukur": (
            "Kode membandingkan tiga bagian candle terakhir: badannya (jarak harga buka "
            "ke harga tutup), bayangan bawahnya, dan rentang penuh candle. Pola ini "
            "tercatat kalau bayangan bawah lebih dari dua kali panjang badan sekaligus "
            "mengisi lebih dari separuh rentangnya."
        ),
        "arti": (
            "Bayangan panjang di bawah berarti harga sempat jatuh jauh ke area itu lalu "
            "diangkat kembali sebelum candle-nya tutup. Ada yang bersedia membeli dalam "
            "jumlah cukup besar untuk menyerap seluruh tekanan jualnya. Areanya jadi "
            "layak dicatat sebagai tempat permintaan menumpuk."
        ),
        "pembatal": (
            "Kalau candle berikutnya ditutup di bawah ujung bayangan itu, permintaan tadi "
            "berarti sudah habis dan pembacaannya gugur."
        ),
    },
    "absorpsi_volume": {
        "cara_ukur": (
            "Kode membandingkan volume candle terakhir dengan rata-rata candle "
            "sebelumnya, lalu mengukur berapa persen harga benar-benar berpindah dari "
            "buka ke tutup. Pola tercatat kalau volumenya lebih dari 2,5 kali rata-rata "
            "sementara harganya bergerak kurang dari 0,3 persen."
        ),
        "arti": (
            "Volume sebesar itu normalnya menggerakkan harga. Kalau harganya tetap di "
            "tempat, berarti ada pihak yang terus menampung order lawan dalam ukuran "
            "besar di harga yang sama — pola yang lazim ketika pemain besar membangun "
            "atau melepas posisi tanpa mau menggerakkan harga melawan dirinya sendiri. "
            "Arahnya sendiri belum ketahuan; yang bisa disimpulkan cuma bahwa harga di "
            "area ini sedang ditahan seseorang."
        ),
        "pembatal": (
            "Arahnya baru terbaca ketika harga akhirnya lepas dari area itu — sisi mana "
            "yang ditinggalkan menunjukkan siapa yang tadi menyerap."
        ),
    },
    "breakout_volume_lemah": {
        "cara_ukur": (
            "Kode mencari puncak tertinggi sebelum lima candle terakhir beserta volume "
            "pada candle puncak itu, lalu membandingkannya dengan candle sekarang. Pola "
            "tercatat kalau harga membuat tertinggi baru sementara volumenya kurang dari "
            "70 persen volume di puncak sebelumnya."
        ),
        "arti": (
            "Tertinggi baru semestinya menarik lebih banyak peserta, bukan lebih sedikit. "
            "Kenaikan dengan volume yang justru mengecil berarti yang mendorong tinggal "
            "sedikit: harga naik karena tidak ada yang menghalangi, bukan karena "
            "permintaannya bertambah. Kenaikan seperti ini lebih gampang berbalik begitu "
            "penjual muncul."
        ),
        "pembatal": (
            "Kalau candle berikutnya melanjutkan naik dengan volume yang membesar, "
            "kekurangan partisipasi tadi sudah terjawab dan pembacaannya gugur."
        ),
    },
    "posisi_padat": {
        "cara_ukur": (
            "Ini satu-satunya pola di kartu ini yang tidak dihitung dari candle. Kode "
            "membaca funding rate kontrak perpetual — biaya yang dibayar satu sisi posisi "
            "kepada sisi lawannya setiap 8 jam — bersama perubahan open interest, yaitu "
            "jumlah kontrak yang masih terbuka. Pola tercatat kalau funding-nya ekstrem "
            "(di atas 0,05 persen per 8 jam) sementara open interest bertambah."
        ),
        "arti": (
            "Funding yang mahal berarti satu sisi jauh lebih ramai dari sisi lawannya, "
            "dan open interest yang naik berarti keramaian itu masih bertambah — bukan "
            "sedang bubar. Posisi yang menumpuk di satu sisi sekaligus mahal untuk "
            "dipertahankan adalah bahan bakar likuidasi beruntun: begitu harga bergerak "
            "melawan, sebagian terpaksa keluar, dan penutupan paksa itu mendorong harga "
            "lebih jauh ke arah yang sama."
        ),
        "pembatal": (
            "Tekanannya mereda dengan sendirinya begitu funding kembali normal atau open "
            "interest turun — keduanya tanda bahwa posisi yang menumpuk tadi sudah keluar."
        ),
    },
}

# Hanya sapuan likuiditas yang `kekuatan`-nya benar-benar bervariasi (4 kalau
# volume candle penyapu >1,5x rata-rata, 3 kalau tidak). Pola lain memakai
# nilai tetap, jadi menuliskan "kekuatan 4" untuk mereka tidak menambah satu
# pun informasi. Angka itu sendiri memang tidak pernah ditampilkan ke pembaca
# — yang ditampilkan adalah apa yang membuatnya 4.
_CATATAN_VOLUME_SAPUAN = {
    4: (
        "Volume pada candle yang menyapu lebih dari 1,5 kali rata-rata. Sapuannya "
        "terjadi dengan partisipasi besar, bukan sekadar bayangan tipis di jam sepi — "
        "itu yang membuat polanya lebih layak diperhitungkan."
    ),
    3: (
        "Volume pada candle yang menyapu tidak jauh di atas rata-rata, jadi bobotnya "
        "sedang saja. Pola seperti ini bisa juga muncul dari likuiditas yang kebetulan "
        "tipis, bukan dari perburuan stop."
    ),
}

# Satu kalimat inti per pola, untuk tempat yang benar-benar sempit (Telegram).
# Sengaja bukan hasil pemotongan `arti` di atas: kalimat pertama sebuah
# paragraf penjelas belum tentu kalimat yang paling penting.
_ARTI_SINGKAT_POLA = {
    "sapuan_likuiditas_atas":
        "Order di atas puncak lama terpicu lalu harga tidak bertahan — likuiditas dipanen, bukan tren baru.",
    "sapuan_likuiditas_bawah":
        "Stop di bawah dasar lama terpicu lalu harga pulih — likuiditas dipanen, bukan tekanan jual sungguhan.",
    "penolakan_atas":
        "Kenaikan ke area itu habis diserap penjual sebelum candle tutup.",
    "penolakan_bawah":
        "Tekanan jual ke area itu habis diserap pembeli sebelum candle tutup.",
    "absorpsi_volume":
        "Ada yang menampung order dalam ukuran besar di harga ini; arahnya belum ketahuan.",
    "breakout_volume_lemah":
        "Tertinggi baru dibuat oleh makin sedikit peserta — kenaikan tanpa konfirmasi.",
    "posisi_padat":
        "Posisi menumpuk di satu sisi dan mahal dipertahankan — bahan bakar likuidasi beruntun.",
}


def penjelasan_pola(jenis: str, kekuatan: Optional[int] = None) -> List[str]:
    """Paragraf penjelas sebuah pola: cara diukur, artinya, lalu pembatalnya.

    Memulangkan daftar kosong untuk pola yang tidak dikenal — pola baru yang
    lupa diberi entri lebih baik tampil tanpa penjelasan daripada tampil
    dengan penjelasan pola lain.
    """
    bagian = _PENJELASAN_POLA.get(jenis)
    if not bagian:
        return []

    paragraf = [bagian["cara_ukur"], bagian["arti"]]
    if jenis.startswith("sapuan_likuiditas"):
        catatan = _CATATAN_VOLUME_SAPUAN.get(kekuatan)
        if catatan:
            paragraf.append(catatan)
    paragraf.append(bagian["pembatal"])
    return paragraf


def arti_singkat_pola(jenis: str) -> str:
    """Satu kalimat inti pola, untuk tempat yang sempit. "" kalau tak dikenal."""
    return _ARTI_SINGKAT_POLA.get(jenis, "")


#: Field penjelas yang murni untuk DIBACA MANUSIA di halaman dan Telegram.
#: Isinya tetap dan sudah diketahui model (prompt whale memuat prinsip
#: pembacaan yang sama), jadi mengirimkannya ke LLM cuma menambah token tanpa
#: menambah informasi — sekitar 1.000 token per run pada tiga pola.
_FIELD_TAMPILAN = ("penjelasan", "arti_singkat")


def sinyal_tanpa_penjelasan(sinyal: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Salinan daftar sinyal tanpa field penjelas — bentuk yang dikirim ke LLM."""
    return [
        {k: v for k, v in s.items() if k not in _FIELD_TAMPILAN}
        for s in sinyal
    ]


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

    # Penjelasan dilampirkan di satu tempat, bukan diulang di tujuh titik
    # append di atas: yang membedakan tiap pola cuma `jenis` dan `kekuatan`,
    # dan mengulang pemanggilannya per pola berarti pola yang ditambahkan
    # nanti gampang lupa diberi penjelasan tanpa satu pun tanda.
    for s in sinyal:
        s["penjelasan"] = penjelasan_pola(s["jenis"], s.get("kekuatan"))
        s["arti_singkat"] = arti_singkat_pola(s["jenis"])

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

    # Sinyal palsu dicari pada timeframe terhalus yang tersedia, maksimal dua.
    # Pada brief harian yang tersedia cuma 1D, dan itu memang yang dicari:
    # sapuan likuiditas serta absorpsi volume pada skala harian.
    tf_sinyal = [tf for tf in ("4h", "1h", "1d") if klines_per_tf.get(tf)][:2]
    sinyal_palsu: List[Dict[str, Any]] = []
    for tf in tf_sinyal:
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
