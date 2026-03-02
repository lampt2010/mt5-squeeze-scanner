"""Squeeze pattern scanner: Bearish (Mẫu 1) and Bullish (Mẫu 2)."""

import logging
import math
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import scipy.signal
import scipy.stats

from backend import models
from backend.config import get_config
from backend.mt5_connector import get_rates, get_symbol_info

logger = logging.getLogger(__name__)

# Resolve timeframe names to MT5 constants at runtime
import MetaTrader5 as mt5
TF_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M10": mt5.TIMEFRAME_M10,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
}
# Khuyến nghị chỉ lấy M15, M30, H1 (không dùng M5)
RECOMMENDATION_TIMEFRAMES = ("M15", "M30", "H1")


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR(period) using high, low, close."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def _tick_value_fallback(symbol: str, point: float, tick_size: float, contract_size: float, lot_size: float) -> float:
    """Fallback $ per tick per lot khi trade_tick_value_profit = 0 hoặc không đáng tin."""
    s = (symbol or "").upper().replace(" ", "")
    if point <= 0:
        return 0.0
    # Forex XXXUSD: ~$10/pip = $10/(10*point) = 1/point per lot? No: 1 pip = 10 point, $10/lot -> $1 per point per lot for 5-digit.
    if s in ("EURUSD", "GBPUSD", "AUDUSD", "NZDUSD") or (len(s) == 6 and s.endswith("USD") and not s.startswith("USD")):
        pip_value = 10.0  # $10 per pip per lot
        pip_size = 10 * point
        if pip_size > 0:
            return pip_value * (tick_size / pip_size)
    if s == "USDJPY" or (len(s) == 6 and "JPY" in s):
        # ~1000/price per pip per lot
        return (1000.0 / 100.0) * (tick_size / (10 * point)) if point < 0.01 else 0.01
    if s == "XAUUSD":
        # 1 point = 0.01 move ≈ $1 per lot (điều chỉnh theo point)
        return 1.0 * (tick_size / 0.01) if tick_size > 0 else 1.0
    if s == "BTCUSD":
        return float(contract_size) * point * (tick_size / point) if point > 0 else 0.0
    return 10.0 * (tick_size / (10 * point)) if (10 * point) > 0 else 0.0


def _compute_fixed_risk_sell(
    symbol: str, df: pd.DataFrame, entry_price: float, cfg: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """
    Tính lot, SL, TP cho SELL (Mẫu 1 Bearish) với FIXED_RISK_USD và FIXED_PROFIT_USD.
    Returns dict với lot_size, sl_price, tp_price, risk_usd, ... hoặc None nếu không recommend (lot < min).
    """
    risk_usd = float(cfg.get("FIXED_RISK_USD", models.FIXED_RISK_USD))
    profit_usd = float(cfg.get("FIXED_PROFIT_USD", models.FIXED_PROFIT_USD))
    sl_mult = float(cfg.get("SL_MULTIPLIER", models.SL_MULTIPLIER))

    info = get_symbol_info(symbol)
    if info is None:
        logger.warning("No symbol_info for %s, skip fixed-risk SELL", symbol)
        return None

    point = getattr(info, "point", None) or 0.00001
    tick_value = getattr(info, "trade_tick_value_profit", None) or 0.0
    tick_size = getattr(info, "trade_tick_size", None) or point
    contract_size = getattr(info, "trade_contract_size", 100000)
    digits = int(getattr(info, "digits", 5))
    volume_min = float(getattr(info, "volume_min", 0.01))
    volume_step = float(getattr(info, "volume_step", 0.01))
    volume_max = float(getattr(info, "volume_max", 100.0))

    atr_ser = _atr(df, 14)
    if len(atr_ser) < 1:
        return None
    atr_val = float(atr_ser.iloc[-1])
    if atr_val != atr_val or atr_val <= 0:
        return None

    sl_distance_price = atr_val * sl_mult
    sl_distance_points = sl_distance_price / point if point > 0 else 0
    sl_price = entry_price + sl_distance_price  # SELL: SL above entry

    if tick_value is None or tick_value <= 0:
        tick_value = _tick_value_fallback(symbol, point, tick_size, contract_size, 1.0)
    if tick_value <= 0 or tick_size <= 0:
        logger.warning("Cannot compute tick value for %s", symbol)
        return None

    # Lot: risk_usd = lot * (sl_distance_price / tick_size) * tick_value
    loss_per_lot = (sl_distance_price / tick_size) * tick_value
    if loss_per_lot <= 0:
        return None
    lot_size = risk_usd / loss_per_lot

    lot_size = max(volume_min, math.floor(lot_size / volume_step) * volume_step)
    lot_size = min(lot_size, volume_max)

    if lot_size < volume_min:
        logger.info("SELL %s: Lot quá nhỏ, risk <20$ (lot=%.4f < min %.4f)", symbol, lot_size, volume_min)
        return None

    # TP cho fixed profit 15$
    profit_per_lot_per_tick = tick_value
    ticks_for_profit = profit_usd / (lot_size * profit_per_lot_per_tick) if (lot_size * profit_per_lot_per_tick) > 0 else 0
    tp_distance_price = ticks_for_profit * tick_size
    tp_price = entry_price - tp_distance_price  # SELL: TP below entry

    tp_distance_points = tp_distance_price / point if point > 0 else 0
    max_loss_actual = lot_size * (sl_distance_price / tick_size) * tick_value

    lot_round = 3 if digits >= 3 else 2
    return {
        "recommend": "SELL",
        "lot_size": round(lot_size, lot_round),
        "risk_usd": round(risk_usd, 2),
        "max_loss_if_sl": round(max_loss_actual, 2),
        "entry_price": entry_price,
        "sl_price": round(sl_price, digits),
        "tp_price": round(tp_price, digits),
        "expected_profit_if_tp": round(profit_usd, 2),
        "rr_ratio": round(profit_usd / risk_usd, 2),
        "sl_distance_points": round(sl_distance_points, 2),
        "tp_distance_points": round(tp_distance_points, 2),
    }


def _compute_fixed_risk_buy(
    symbol: str, df: pd.DataFrame, entry_price: float, cfg: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """
    Tính lot, SL, TP cho MUA (Mẫu 2 Bullish / CẢNH_BÁO_SỚM) với FIXED_RISK_USD và FIXED_PROFIT_USD.
    BUY: SL dưới entry, TP trên entry.
    """
    risk_usd = float(cfg.get("FIXED_RISK_USD", models.FIXED_RISK_USD))
    profit_usd = float(cfg.get("FIXED_PROFIT_USD", models.FIXED_PROFIT_USD))
    sl_mult = float(cfg.get("SL_MULTIPLIER", models.SL_MULTIPLIER))

    info = get_symbol_info(symbol)
    if info is None:
        return None

    point = getattr(info, "point", None) or 0.00001
    tick_value = getattr(info, "trade_tick_value_profit", None) or 0.0
    tick_size = getattr(info, "trade_tick_size", None) or point
    contract_size = getattr(info, "trade_contract_size", 100000)
    digits = int(getattr(info, "digits", 5))
    volume_min = float(getattr(info, "volume_min", 0.01))
    volume_step = float(getattr(info, "volume_step", 0.01))
    volume_max = float(getattr(info, "volume_max", 100.0))

    atr_ser = _atr(df, 14)
    if len(atr_ser) < 1:
        return None
    atr_val = float(atr_ser.iloc[-1])
    if atr_val != atr_val or atr_val <= 0:
        return None

    sl_distance_price = atr_val * sl_mult
    sl_distance_points = sl_distance_price / point if point > 0 else 0
    sl_price = entry_price - sl_distance_price  # BUY: SL below entry

    if tick_value is None or tick_value <= 0:
        tick_value = _tick_value_fallback(symbol, point, tick_size, contract_size, 1.0)
    if tick_value <= 0 or tick_size <= 0:
        return None

    loss_per_lot = (sl_distance_price / tick_size) * tick_value
    if loss_per_lot <= 0:
        return None
    lot_size = risk_usd / loss_per_lot

    lot_size = max(volume_min, math.floor(lot_size / volume_step) * volume_step)
    lot_size = min(lot_size, volume_max)

    if lot_size < volume_min:
        return None

    ticks_for_profit = profit_usd / (lot_size * tick_value) if (lot_size * tick_value) > 0 else 0
    tp_distance_price = ticks_for_profit * tick_size
    tp_price = entry_price + tp_distance_price  # BUY: TP above entry

    tp_distance_points = tp_distance_price / point if point > 0 else 0
    max_loss_actual = lot_size * (sl_distance_price / tick_size) * tick_value

    lot_round = 3 if digits >= 3 else 2
    return {
        "recommend": "BUY",
        "lot_size": round(lot_size, lot_round),
        "risk_usd": round(risk_usd, 2),
        "max_loss_if_sl": round(max_loss_actual, 2),
        "entry_price": entry_price,
        "sl_price": round(sl_price, digits),
        "tp_price": round(tp_price, digits),
        "expected_profit_if_tp": round(profit_usd, 2),
        "rr_ratio": round(profit_usd / risk_usd, 2),
        "sl_distance_points": round(sl_distance_points, 2),
        "tp_distance_points": round(tp_distance_points, 2),
    }


def _check_bearish_squeeze(df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    """Mẫu 1: Bearish Squeeze at end of downtrend. Xác định trend chỉ dùng TREND_LOOKBACK_BARS nến gần nhất."""
    cfg = get_config()
    trend_lookback = int(cfg.get("TREND_LOOKBACK_BARS", 15))
    squeeze_bars = int(cfg["SQUEEZE_BARS"])
    atr_ratio = cfg["ATR_SQUEEZE_RATIO"]
    slope_min = cfg["SLOPE_MIN_ABS"]
    ma21_near = cfg["MA21_NEAR_ATR_RATIO"]
    band_tol_ratio = cfg.get("BAND_TOLERANCE_ATR_RATIO", 0.15)
    min_peaks = int(cfg.get("MIN_PEAKS", 3))
    min_in_band_ratio = cfg.get("MIN_BARS_IN_BAND_RATIO", 1.0)
    band_center_ratio = cfg.get("BAND_CENTER_MIN_RATIO", 0.15)

    n = len(df)
    # Cần đủ dữ liệu: MA21 cần 21 nến, ATR 14; cửa sổ trend = trend_lookback
    min_bars = max(21 + squeeze_bars, trend_lookback)
    # Chỉ dùng nến đã đóng: nến cuối trong MT5 là nến đang hình thành (giá đổi từng tick) → bỏ qua để tránh cảnh báo khi nến đã phá band
    if n < min_bars + 1:
        return None
    df = df.iloc[:-1].copy()
    n = len(df)
    win = df.tail(trend_lookback).reset_index(drop=True)
    close = win["close"]
    high = win["high"].values
    low = win["low"].values
    open_ = win["open"].values

    # MA21 và ATR tính trên full df (đã bỏ nến đang hình thành) để có đủ lịch sử
    ma21_full = df["close"].rolling(21).mean()
    if ma21_full.iloc[-1] != ma21_full.iloc[-1]:  # NaN
        return None
    atr_ser = _atr(df, 14)
    atr_val = atr_ser.iloc[-1]
    if atr_val != atr_val or atr_val <= 0:
        return None

    peaks, _ = scipy.signal.find_peaks(high, distance=5)
    if len(peaks) < min_peaks:
        return None
    # Trendline = đường nối đỉnh đầu và đỉnh cuối (2 điểm xa nhau), không bắt buộc các bar gần nhau
    p1, p2 = int(peaks[0]), int(peaks[-1])
    if p2 <= p1:
        return None
    peak_indices = np.array([p1, p2])
    slope = (high[p2] - high[p1]) / (p2 - p1)  # slope theo chỉ số bar trong cửa sổ
    intercept = float(high[p1]) - slope * p1
    if slope >= -slope_min:
        return None

    trendline_last = slope * (len(win) - 1) + intercept
    # Vùng xiết = các nến nằm GIỮA hai nến dùng để vẽ trendline (từ p1 đến p2, bao gồm cả hai đầu)
    squeeze_win = win.iloc[p1 : p2 + 1]
    squeeze_bars_actual = len(squeeze_win)
    if squeeze_bars_actual < 2 or squeeze_bars_actual < squeeze_bars:
        return None

    body = (squeeze_win["close"] - squeeze_win["open"]).abs()
    if body.mean() >= atr_ratio * atr_val:
        return None

    n_df = len(df)
    start_df = n_df - trend_lookback + p1
    end_df = n_df - trend_lookback + p2 + 1
    if start_df < 20 or end_df > n_df:
        return None
    ma21_vals = ma21_full.iloc[start_df:end_df].values
    tol = band_tol_ratio * atr_val
    in_band = 0
    in_center = 0
    for i in range(squeeze_bars_actual):
        bar_open = float(squeeze_win["open"].iloc[i])
        bar_close = float(squeeze_win["close"].iloc[i])
        body_low = min(bar_open, bar_close)
        body_high = max(bar_open, bar_close)
        ti = p1 + i
        tl_val = slope * ti + intercept
        ma = ma21_vals[i]
        band_width = tl_val - ma
        # Band phải hợp lệ: trendline (resistance) phải ở trên MA21; nếu không thì nến không thể "nằm giữa"
        if band_width <= tol:
            continue
        # Thân nến không được nằm hoàn toàn ngoài: phải overlap vùng (ma, tl_val)
        if body_high < ma or body_low > tl_val:
            continue
        # Thân nến (body) phải nằm trong band: dưới trendline, trên MA21 (không chỉ râu)
        if body_high <= tl_val + tol and body_low >= ma - tol:
            in_band += 1
        # Nến phải nằm "giữa" band: close không sát trendline hay MA21 (đúng mẫu hình 2)
        low_bound = ma + band_width * band_center_ratio
        high_bound = tl_val - band_width * band_center_ratio
        if low_bound <= bar_close <= high_bound:
            in_center += 1
    if in_band < max(1, int(squeeze_bars_actual * min_in_band_ratio)):
        return None
    if in_center < max(1, int(squeeze_bars_actual * min_in_band_ratio)):
        return None

    last_ma = float(ma21_full.iloc[-1])
    last_close = float(close.iloc[-1])
    if abs(last_close - last_ma) > ma21_near * atr_val:
        return None
    # Nến cuối (đã đóng) phải vẫn nằm TRONG band: dưới trendline, trên MA21 — không cảnh báo khi đã phá lên
    if last_close > trendline_last + tol:
        return None
    if last_close < last_ma - tol:
        return None

    score = max(0, min(1, 1 - body.mean() / (atr_ratio * atr_val)))
    return {
        "pattern": "MẪU_1_BEARISH",
        "score": round(score, 2),
        "last_price": float(close.iloc[-1]),
        "ma21": float(last_ma),
        "trendline": float(trendline_last),
        "slope": slope,
        "intercept": intercept,
        "peak_indices": [int(p1), int(p2)],
    }


def _check_bullish_squeeze(df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    """Mẫu 2: Bullish Squeeze at end of uptrend. Xác định trend chỉ dùng TREND_LOOKBACK_BARS nến gần nhất."""
    cfg = get_config()
    trend_lookback = int(cfg.get("TREND_LOOKBACK_BARS", 15))
    squeeze_bars = int(cfg["SQUEEZE_BARS"])
    atr_ratio = cfg["ATR_SQUEEZE_RATIO"]
    slope_min = cfg["SLOPE_MIN_ABS"]
    ma21_near = cfg["MA21_NEAR_ATR_RATIO"]
    band_tol_ratio = cfg.get("BAND_TOLERANCE_ATR_RATIO", 0.15)
    min_peaks = int(cfg.get("MIN_PEAKS", 3))
    min_in_band_ratio = cfg.get("MIN_BARS_IN_BAND_RATIO", 1.0)
    band_center_ratio = cfg.get("BAND_CENTER_MIN_RATIO", 0.15)

    n = len(df)
    min_bars = max(21 + squeeze_bars, trend_lookback)
    # Chỉ dùng nến đã đóng (bỏ nến đang hình thành) để tránh cảnh báo khi giá đã phá band
    if n < min_bars + 1:
        return None
    df = df.iloc[:-1].copy()
    n = len(df)
    win = df.tail(trend_lookback).reset_index(drop=True)
    close = win["close"]
    high = win["high"].values
    low = win["low"].values

    ma21_full = df["close"].rolling(21).mean()
    if ma21_full.iloc[-1] != ma21_full.iloc[-1]:
        return None
    atr_ser = _atr(df, 14)
    atr_val = atr_ser.iloc[-1]
    if atr_val != atr_val or atr_val <= 0:
        return None

    peaks, _ = scipy.signal.find_peaks(-low, distance=5)
    if len(peaks) < min_peaks:
        return None
    # Trendline = đường nối đáy đầu và đáy cuối (2 điểm xa nhau)
    p1, p2 = int(peaks[0]), int(peaks[-1])
    if p2 <= p1:
        return None
    peak_indices = np.array([p1, p2])
    slope = (low[p2] - low[p1]) / (p2 - p1)
    intercept = float(low[p1]) - slope * p1
    if slope <= slope_min:
        return None

    # Vùng xiết = các nến nằm GIỮA hai nến dùng để vẽ trendline (từ p1 đến p2)
    squeeze_win = win.iloc[p1 : p2 + 1]
    squeeze_bars_actual = len(squeeze_win)
    if squeeze_bars_actual < 2 or squeeze_bars_actual < squeeze_bars:
        return None

    body = (squeeze_win["close"] - squeeze_win["open"]).abs()
    if body.mean() >= atr_ratio * atr_val:
        return None

    n_df = len(df)
    start_df = n_df - trend_lookback + p1
    end_df = n_df - trend_lookback + p2 + 1
    if start_df < 20 or end_df > n_df:
        return None
    ma21_vals = ma21_full.iloc[start_df:end_df].values
    tol = band_tol_ratio * atr_val
    in_band = 0
    in_center = 0
    for i in range(squeeze_bars_actual):
        bar_open = float(squeeze_win["open"].iloc[i])
        bar_close = float(squeeze_win["close"].iloc[i])
        body_low = min(bar_open, bar_close)
        body_high = max(bar_open, bar_close)
        ti = p1 + i
        tl_val = slope * ti + intercept
        ma = ma21_vals[i]
        band_width = ma - tl_val
        # Band phải hợp lệ: MA21 phải ở trên trendline (support); nếu không thì nến không thể "nằm giữa"
        if band_width <= tol:
            continue
        # Thân nến không được nằm hoàn toàn ngoài: phải overlap vùng (tl_val, ma)
        if body_low > ma or body_high < tl_val:
            continue
        # Thân nến (body) phải nằm trong band: trên trendline (support), dưới MA21
        if body_low >= tl_val - tol and body_high <= ma + tol:
            in_band += 1
        # Nến phải nằm "giữa" band: close không sát trendline hay MA21 (đúng mẫu)
        low_bound = tl_val + band_width * band_center_ratio
        high_bound = ma - band_width * band_center_ratio
        if low_bound <= bar_close <= high_bound:
            in_center += 1
    if in_band < max(1, int(squeeze_bars_actual * min_in_band_ratio)):
        return None
    if in_center < max(1, int(squeeze_bars_actual * min_in_band_ratio)):
        return None

    last_ma = float(ma21_full.iloc[-1])
    last_close = float(close.iloc[-1])
    if abs(last_close - last_ma) > ma21_near * atr_val:
        return None
    trendline_last = slope * (len(win) - 1) + intercept
    # Nến cuối (đã đóng) phải vẫn nằm TRONG band: trên trendline (support), dưới MA21 — không cảnh báo khi đã ra ngoài
    if last_close < trendline_last - tol:
        return None
    if last_close > last_ma + tol:
        return None

    score = max(0, min(1, 1 - body.mean() / (atr_ratio * atr_val)))
    return {
        "pattern": "MẪU_2_BULLISH",
        "score": round(score, 2),
        "last_price": float(last_close),
        "ma21": float(last_ma),
        "trendline": float(trendline_last),
        "slope": slope,
        "intercept": intercept,
        "peak_indices": [int(p1), int(p2)],
    }


def _check_bearish_breakdown_support(df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    """
    Mẫu 3: SELL khi giá phá vỡ xuống dưới trendline support (vẽ bằng đáy nến).
    Trendline = đường nối đáy đầu và đáy cuối (support dốc lên); khi nến đóng dưới đường này → khuyến nghị BÁN.
    """
    cfg = get_config()
    trend_lookback = int(cfg.get("TREND_LOOKBACK_BARS", 15))
    slope_min = cfg["SLOPE_MIN_ABS"]
    min_peaks = int(cfg.get("MIN_PEAKS", 3))

    n = len(df)
    min_bars = max(21, trend_lookback)
    if n < min_bars + 1:
        return None
    df = df.iloc[:-1].copy()
    n = len(df)
    win = df.tail(trend_lookback).reset_index(drop=True)
    close = win["close"].values
    low = win["low"].values

    peaks, _ = scipy.signal.find_peaks(-low, distance=5)
    if len(peaks) < min_peaks:
        return None
    p1, p2 = int(peaks[0]), int(peaks[-1])
    if p2 <= p1:
        return None
    slope = (low[p2] - low[p1]) / (p2 - p1)
    intercept = float(low[p1]) - slope * p1
    if slope <= slope_min:
        return None

    # Giá tại nến cuối (đã đóng) phải nằm DƯỚI trendline support = phá vỡ xuống
    ti_last = len(win) - 1
    tl_val_last = slope * ti_last + intercept
    last_close = float(close[ti_last])
    if last_close >= tl_val_last:
        return None

    # Trước đó từng có giá trên/near support (đúng là breakdown, không phải giá luôn dưới)
    tol = 0.1 * (tl_val_last - low.min()) if (tl_val_last - low.min()) > 0 else 1e-8
    had_above = any(close[i] >= (slope * i + intercept) - tol for i in range(max(0, ti_last - 5), ti_last))
    if not had_above:
        return None

    ma21_full = df["close"].rolling(21).mean()
    last_ma = float(ma21_full.iloc[-1]) if ma21_full.iloc[-1] == ma21_full.iloc[-1] else last_close
    trendline_last = slope * ti_last + intercept
    return {
        "pattern": "MẪU_3_PHÁ_VỠ_SUPPORT",
        "score": 0.8,
        "last_price": last_close,
        "ma21": last_ma,
        "trendline": float(trendline_last),
        "slope": slope,
        "intercept": intercept,
        "peak_indices": [int(p1), int(p2)],
    }


# --- Cảnh báo sớm (early breakout) - logic tương tự project forex ---
SWING_WINDOW = 5
EARLY_MAX_TREND_BAR_DISTANCE = 30
EARLY_MA_BELOW_TRENDLINE_RATIO = 0.9
EARLY_MA_NEAR_TRENDLINE_PCT = 0.002
EARLY_TIGHT_RANGE_PCT = 0.004
EARLY_TIGHT_RANGE_WINDOW_HALF = 2
EARLY_LOOKBACK_BARS = 12
EARLY_STRONG_CANDLE_WITHIN = 2
BODY_RATIO_MIN = 0.5


def _find_swing_highs(high: np.ndarray, window: int = SWING_WINDOW) -> List[int]:
    """Chỉ số các đỉnh swing (high lớn nhất trong cửa sổ 2*window+1)."""
    n = len(high)
    out = []
    for i in range(window, n - window):
        if high[i] >= high[i - window : i + window + 1].max():
            out.append(i)
    return out


def _get_descending_resistance_trendline(
    df: pd.DataFrame, max_bar_distance: int = EARLY_MAX_TREND_BAR_DISTANCE
) -> Optional[Dict[str, Any]]:
    """
    Trendline resistance giảm dần: 2 đỉnh swing, p2 gần nhất, p1 trước đó trong max_bar_distance, p1.value >= p2.value.
    Trả về dict: slope, value_at(t), start_index, last_swing_high_index.
    """
    high = df["high"].values
    time_arr = df["time"].values
    swing_highs = _find_swing_highs(high)
    if len(swing_highs) < 2:
        return None
    p2_idx = swing_highs[-1]
    p2_val = high[p2_idx]
    p2_time = float(time_arr[p2_idx])
    p1_idx = None
    for j in range(len(swing_highs) - 2, -1, -1):
        idx = swing_highs[j]
        if high[idx] >= p2_val and (p2_idx - idx) <= max_bar_distance:
            p1_idx = idx
            break
    if p1_idx is None:
        return None
    p1_time = float(time_arr[p1_idx])
    p1_val = high[p1_idx]
    if p1_time == p2_time:
        return None
    slope = (p2_val - p1_val) / (p2_time - p1_time)
    if slope >= 0:
        return None

    def value_at(t: float) -> float:
        return p2_val + slope * (t - p2_time)

    return {
        "slope": slope,
        "value_at": value_at,
        "start_index": p1_idx,
        "last_swing_high_index": p2_idx,
    }


def _is_strong_bullish(open_: float, high: float, low: float, close: float) -> bool:
    if close <= open_:
        return False
    r = high - low
    if r <= 0:
        return False
    return (close - open_) >= BODY_RATIO_MIN * r


def _is_strong_bearish(open_: float, high: float, low: float, close: float) -> bool:
    if close >= open_:
        return False
    r = high - low
    if r <= 0:
        return False
    return (open_ - close) >= BODY_RATIO_MIN * r


def _check_early_breakout(df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    """
    Cảnh báo sớm: trendline giảm, MA21 dưới trendline gần hết, siết nền quanh junction,
    nến xanh mạnh trong 2 nến gần nhất đóng trên trendline + MA21, không bị nến sau rejection.
    Chỉ dùng nến đã đóng (bỏ nến đang hình thành).
    """
    n = len(df)
    if n < 26:
        return None
    df = df.iloc[:-1].copy()
    n = len(df)
    win = df.reset_index(drop=True)
    close = win["close"]
    high = win["high"].values
    low = win["low"].values
    open_ = win["open"].values
    time_arr = win["time"].values

    trendline = _get_descending_resistance_trendline(win)
    if trendline is None:
        return None

    ma21 = close.rolling(21).mean()
    value_at = trendline["value_at"]
    start_idx = trendline["start_index"]
    from_idx = max(20, n - EARLY_LOOKBACK_BARS)
    min_strong_candle_index = n - EARLY_STRONG_CANDLE_WITHIN

    for i in range(from_idx, n):
        ma_val = ma21.iloc[i]
        if ma_val != ma_val:
            continue
        line_val = value_at(float(time_arr[i]))
        mid = (close.iloc[i] + line_val) / 2.0 or close.iloc[i]
        if mid <= 0:
            continue
        dist_pct = abs(ma_val - line_val) / mid
        if dist_pct > EARLY_MA_NEAR_TRENDLINE_PCT:
            continue

        seg_start = max(20, start_idx)
        total, below = 0, 0
        for k in range(seg_start, i + 1):
            ma_k = ma21.iloc[k]
            if ma_k != ma_k:
                continue
            total += 1
            lv = value_at(float(time_arr[k]))
            if ma_k < lv:
                below += 1
        if total == 0 or below / total < EARLY_MA_BELOW_TRENDLINE_RATIO:
            continue

        junction_bar = i
        min_dist = dist_pct
        for k in range(seg_start, i + 1):
            ma_k = ma21.iloc[k]
            if ma_k != ma_k:
                continue
            lv = value_at(float(time_arr[k]))
            m = (close.iloc[k] + lv) / 2.0 or close.iloc[k]
            if m <= 0:
                continue
            d = abs(ma_k - lv) / m
            if d < min_dist:
                min_dist = d
                junction_bar = k
        left = max(0, junction_bar - EARLY_TIGHT_RANGE_WINDOW_HALF)
        right = min(n - 1, junction_bar + EARLY_TIGHT_RANGE_WINDOW_HALF)
        slice_high = high[left : right + 1].max()
        slice_low = low[left : right + 1].min()
        mid_slice = (slice_high + slice_low) / 2.0 or close.iloc[right]
        if mid_slice <= 0:
            continue
        if (slice_high - slice_low) / mid_slice >= EARLY_TIGHT_RANGE_PCT:
            continue

        strong_at_i = _is_strong_bullish(open_[i], high[i], low[i], close.iloc[i])
        strong_at_next = (
            i + 1 < n and _is_strong_bullish(open_[i + 1], high[i + 1], low[i + 1], close.iloc[i + 1])
        )
        strong_candle_index = i if strong_at_i else (i + 1 if strong_at_next else -1)
        if strong_candle_index < 0 or strong_candle_index < min_strong_candle_index:
            continue

        break_close = close.iloc[strong_candle_index]
        break_line_val = value_at(float(time_arr[strong_candle_index]))
        break_ma = ma21.iloc[strong_candle_index]
        if break_close <= break_line_val or break_ma != break_ma or break_close <= break_ma:
            continue

        if strong_candle_index + 1 < n:
            next_close = close.iloc[strong_candle_index + 1]
            next_line = value_at(float(time_arr[strong_candle_index + 1]))
            if _is_strong_bearish(
                open_[strong_candle_index + 1],
                high[strong_candle_index + 1],
                low[strong_candle_index + 1],
                next_close,
            ) and next_close < next_line:
                continue

        # Chỉ khuyến nghị BUY khi nến mới nhất (đóng cửa) là nến xanh
        if close.iloc[-1] <= open_[-1]:
            continue

        last_ma = ma21.iloc[-1]
        last_close = float(close.iloc[-1])
        trendline_last = value_at(float(time_arr[-1]))
        return {
            "pattern": "CẢNH_BÁO_SỚM",
            "score": 0.85,
            "last_price": last_close,
            "ma21": float(last_ma),
            "trendline": float(trendline_last),
            "slope": trendline["slope"],
            "intercept": None,
            "recommendation": "MUA",
            "entry_price": round(last_close, 5),
        }
    return None


def get_trendline_series(symbol: str, tf_name: str, as_of_bar_time: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """
    Trả về trendline + metadata để FE vẽ đúng và hiển thị cách nhìn của hệ thống.
    - trendline: list [{time, value}] để vẽ đường.
    - pattern: MẪU_1_BEARISH | MẪU_2_BULLISH | MẪU_3_PHÁ_VỠ_SUPPORT | CẢNH_BÁO_SỚM | null.
    - trend_lookback: số nến cửa sổ (vd 15).
    - peak_bar_times: [time1, time2] hai nến dùng để vẽ trendline (đỉnh hoặc đáy).
    - squeeze_zone: { start_time, end_time } vùng xiết = các nến giữa hai peak.
    - peak_type: "high" (resistance) | "low" (support).
    - as_of_bar_time: nếu có, chỉ dùng nến có time <= giá trị này (xem chart từ History đúng thời điểm).
    """
    tf_val = TF_MAP.get(tf_name)
    if tf_val is None:
        return None
    df = get_rates(symbol, tf_val, 200)
    if df is not None and as_of_bar_time is not None:
        df = df[df["time"] <= as_of_bar_time].copy()
    cfg = get_config()
    trend_lookback = int(cfg.get("TREND_LOOKBACK_BARS", 15))
    squeeze_bars = int(cfg["SQUEEZE_BARS"])
    min_bars = max(21 + squeeze_bars, trend_lookback)
    if df is None or len(df) < min_bars:
        return None
    bear = _check_bearish_squeeze(df)
    bull = _check_bullish_squeeze(df)
    breakdown_support = _check_bearish_breakdown_support(df)
    cands = []
    if bear:
        cands.append((bear["score"], bear))
    if bull:
        cands.append((bull["score"], bull))
    if breakdown_support:
        cands.append((breakdown_support["score"], breakdown_support))
    if cands:
        # Ưu tiên Mẫu 3 (phá vỡ support) khi có để chart vẽ đúng trendline SELL
        data = breakdown_support if breakdown_support else max(cands, key=lambda c: c[0])[1]
        slope = data["slope"]
        intercept = data["intercept"]
        peak_indices = data.get("peak_indices")
        if peak_indices and len(peak_indices) >= 2:
            win = df.tail(trend_lookback).reset_index(drop=True)
            times = win["time"]
            p1, p2 = int(peak_indices[0]), int(peak_indices[-1])
            out = []
            for i in range(p1, p2 + 1):
                if i < 0 or i >= len(times):
                    continue
                val = slope * i + intercept
                out.append({"time": int(times.iloc[i]), "value": round(float(val), 5)})
            if out:
                t1 = int(times.iloc[p1])
                t2 = int(times.iloc[p2])
                pattern = data.get("pattern", "")
                peak_type = "high" if pattern == "MẪU_1_BEARISH" else "low"
                return {
                    "trendline": out,
                    "pattern": pattern,
                    "trend_lookback": trend_lookback,
                    "peak_bar_times": [t1, t2],
                    "squeeze_zone": {"start_time": t1, "end_time": t2},
                    "peak_type": peak_type,
                }
    # Cảnh báo sớm (BUY): trendline resistance giảm dần
    if len(df) >= 25:
        early = _check_early_breakout(df)
        if early is not None:
            tl = _get_descending_resistance_trendline(df)
            if tl is not None:
                time_arr = df["time"].values
                start_idx = tl["start_index"]
                value_at = tl["value_at"]
                out = []
                for i in range(start_idx, len(df)):
                    t = float(time_arr[i])
                    val = value_at(t)
                    out.append({"time": int(t), "value": round(float(val), 5)})
                if out:
                    return {
                        "trendline": out,
                        "pattern": "CẢNH_BÁO_SỚM",
                        "trend_lookback": trend_lookback,
                        "peak_bar_times": None,
                        "squeeze_zone": None,
                        "peak_type": "high",
                    }
    # Fallback: trendline đơn giản đỉnh đầu–đỉnh cuối; thử nhiều cửa sổ để chart (vd từ History) vẫn có trendline
    for lb in [trend_lookback, 25, 40, 60]:
        if lb > len(df):
            continue
        simple = _get_simple_trendline_series(df, lb)
        if simple:
            return {
                "trendline": simple,
                "pattern": None,
                "trend_lookback": lb,
                "peak_bar_times": None,
                "squeeze_zone": None,
                "peak_type": "high",
            }
    return None


def _get_simple_trendline_series(df: pd.DataFrame, trend_lookback: int) -> Optional[List[Dict[str, Any]]]:
    """Trendline đơn giản = đường nối đỉnh đầu và đỉnh cuối trong cửa sổ. Dùng khi không còn khớp pattern/early (vd data mới)."""
    if len(df) < trend_lookback:
        return None
    win = df.tail(trend_lookback).reset_index(drop=True)
    high = win["high"].values
    swing_highs = _find_swing_highs(high)
    if len(swing_highs) < 2:
        return None
    p1, p2 = int(swing_highs[0]), int(swing_highs[-1])
    if p2 <= p1:
        return None
    slope = (high[p2] - high[p1]) / (p2 - p1)
    intercept = float(high[p1]) - slope * p1
    times = win["time"]
    out = []
    for i in range(p1, p2 + 1):
        if i < 0 or i >= len(times):
            continue
        val = slope * i + intercept
        out.append({"time": int(times.iloc[i]), "value": round(float(val), 5)})
    return out if out else None


def run_scan(symbols: Optional[List[str]] = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """
    Scan all symbol x TF; returns (all_signals, hot_signals, scan_status).
    scan_status: { symbols_with_data, total_checked } for UI.
    """
    symbols = symbols or models.DEFAULT_SYMBOLS
    all_signals: List[Dict[str, Any]] = []
    now_ts = time.time()
    total_checked = 0
    symbols_with_data = 0

    for symbol in symbols:
        for tf_name in RECOMMENDATION_TIMEFRAMES:
            tf_val = TF_MAP[tf_name]
            total_checked += 1
            df = get_rates(symbol, tf_val, 200)
            cfg = get_config()
            trend_lookback = int(cfg.get("TREND_LOOKBACK_BARS", 15))
            min_bars = max(21 + int(cfg["SQUEEZE_BARS"]), trend_lookback)
            # Cần thêm 1 nến vì logic pattern chỉ dùng nến đã đóng (bỏ nến đang hình thành)
            if df is None or len(df) < min_bars + 1:
                continue
            symbols_with_data += 1
            bear = _check_bearish_squeeze(df)
            bull = _check_bullish_squeeze(df)
            breakdown_support = _check_bearish_breakdown_support(df)
            early = _check_early_breakout(df)
            cands = []
            if bear:
                cands.append(("MẪU_1_BEARISH", bear))
            if bull:
                cands.append(("MẪU_2_BULLISH", bull))
            # Phá vỡ support (trendline đáy nến) → SELL ưu tiên hơn BUY (Mẫu 2) khi cùng thỏa
            if breakdown_support:
                pattern_name, data = "MẪU_3_PHÁ_VỠ_SUPPORT", breakdown_support
            elif cands:
                best = max(cands, key=lambda c: c[1]["score"])
                pattern_name, data = best
            else:
                pattern_name, data = None, None
            if pattern_name and data:
                rec = "BÁN" if pattern_name in ("MẪU_1_BEARISH", "MẪU_3_PHÁ_VỠ_SUPPORT") else "MUA"
                # Dùng nến đã đóng (trước nến đang hình thành) để xác định màu nến
                last_close = float(df["close"].iloc[-2])
                last_open = float(df["open"].iloc[-2])
                # BUY chỉ khi nến đóng gần nhất xanh; SELL chỉ khi nến đóng gần nhất đỏ
                if rec == "MUA" and last_close <= last_open:
                    pass  # bỏ qua, không đưa khuyến nghị
                elif rec == "BÁN" and last_close >= last_open:
                    pass  # bỏ qua
                else:
                    bar_time = int(df["time"].iloc[-2])
                    entry = round(float(data["last_price"]), 5)
                    sig = {
                        "symbol": symbol,
                        "tf": tf_name,
                        "pattern": pattern_name,
                        "score": data["score"],
                        "last_price": data["last_price"],
                        "ma21": data["ma21"],
                        "trendline": data["trendline"],
                        "timestamp": now_ts,
                        "bar_time": bar_time,
                        "recommendation": rec,
                        "entry_price": entry,
                    }
                    fixed = None
                    if rec == "BÁN" and (data["score"] >= 0.75 or pattern_name == "MẪU_3_PHÁ_VỠ_SUPPORT"):
                        fixed = _compute_fixed_risk_sell(symbol, df, entry, cfg)
                        if fixed is None:
                            continue  # Lot quá nhỏ hoặc không tính được → không recommend
                    if rec == "BÁN" and fixed is not None:
                        sig["lot_size"] = fixed["lot_size"]
                        sig["risk_usd"] = fixed["risk_usd"]
                        sig["max_loss_if_sl"] = fixed["max_loss_if_sl"]
                        sig["sl_price"] = fixed["sl_price"]
                        sig["tp_price"] = fixed["tp_price"]
                        sig["expected_profit_if_tp"] = fixed["expected_profit_if_tp"]
                        sig["rr_ratio"] = fixed["rr_ratio"]
                        logger.info(
                            "SELL Recommend %s %s: Lot %s, Risk ~$%s, SL %s points, TP %s points",
                            symbol, tf_name, fixed["lot_size"], fixed["risk_usd"],
                            fixed["sl_distance_points"], fixed["tp_distance_points"],
                        )
                    elif rec == "MUA":
                        fixed = _compute_fixed_risk_buy(symbol, df, entry, cfg)
                        if fixed is not None:
                            sig["lot_size"] = fixed["lot_size"]
                            sig["risk_usd"] = fixed["risk_usd"]
                            sig["max_loss_if_sl"] = fixed["max_loss_if_sl"]
                            sig["sl_price"] = fixed["sl_price"]
                            sig["tp_price"] = fixed["tp_price"]
                            sig["expected_profit_if_tp"] = fixed["expected_profit_if_tp"]
                            sig["rr_ratio"] = fixed["rr_ratio"]
                    all_signals.append(sig)
                    logger.info("Detected %s on %s %s at %s", pattern_name, symbol, tf_name, now_ts)
            if early:
                bar_time = int(df["time"].iloc[-2])  # nến đã đóng (trước nến đang hình thành)
                entry_early = early.get("entry_price") or round(float(early["last_price"]), 5)
                sig_early = {
                    "symbol": symbol,
                    "tf": tf_name,
                    "pattern": early["pattern"],
                    "score": early["score"],
                    "last_price": early["last_price"],
                    "ma21": early["ma21"],
                    "trendline": early["trendline"],
                    "timestamp": now_ts,
                    "bar_time": bar_time,
                    "recommendation": early.get("recommendation", "MUA"),
                    "entry_price": entry_early,
                }
                fixed_buy = _compute_fixed_risk_buy(symbol, df, entry_early, cfg)
                if fixed_buy is not None:
                    sig_early["lot_size"] = fixed_buy["lot_size"]
                    sig_early["risk_usd"] = fixed_buy["risk_usd"]
                    sig_early["max_loss_if_sl"] = fixed_buy["max_loss_if_sl"]
                    sig_early["sl_price"] = fixed_buy["sl_price"]
                    sig_early["tp_price"] = fixed_buy["tp_price"]
                    sig_early["expected_profit_if_tp"] = fixed_buy["expected_profit_if_tp"]
                    sig_early["rr_ratio"] = fixed_buy["rr_ratio"]
                all_signals.append(sig_early)
                logger.info("Detected %s on %s %s at %s", early["pattern"], symbol, tf_name, now_ts)

    window_sec = get_config()["HOT_WINDOW_MINUTES"] * 60
    hot_signals = [s for s in all_signals if (now_ts - s["timestamp"]) <= window_sec]
    scan_status = {"symbols_with_data": symbols_with_data, "total_checked": total_checked}
    return all_signals, hot_signals, scan_status
