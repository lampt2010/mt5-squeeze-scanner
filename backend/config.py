"""Runtime config for pattern detection. Load/save from JSON file."""

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from backend import models

logger = logging.getLogger(__name__)

CONFIG_FILE = Path(__file__).resolve().parent.parent / "scanner_config.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    "LOOKBACK_BARS": models.LOOKBACK_BARS,
    "TREND_LOOKBACK_BARS": models.TREND_LOOKBACK_BARS,
    "TRENDLINE_BARS": models.TRENDLINE_BARS,
    "SQUEEZE_BARS": models.SQUEEZE_BARS,
    "ATR_SQUEEZE_RATIO": models.ATR_SQUEEZE_RATIO,
    "SLOPE_MIN_ABS": models.SLOPE_MIN_ABS,
    "MA21_NEAR_ATR_RATIO": models.MA21_NEAR_ATR_RATIO,
    "BAND_TOLERANCE_ATR_RATIO": models.BAND_TOLERANCE_ATR_RATIO,
    "MIN_PEAKS": models.MIN_PEAKS,
    "MIN_BARS_IN_BAND_RATIO": models.MIN_BARS_IN_BAND_RATIO,
    "BAND_CENTER_MIN_RATIO": models.BAND_CENTER_MIN_RATIO,
    "HOT_WINDOW_MINUTES": models.HOT_WINDOW_MINUTES,
    "AUTO_TRADE_ENABLED": models.AUTO_TRADE_ENABLED,
    "AUTO_TRADE_LOT": models.AUTO_TRADE_LOT,
    "AUTO_TRADE_LOT_MULTIPLIER": models.AUTO_TRADE_LOT_MULTIPLIER,
    "AUTO_TRADE_SL_PIPS": models.AUTO_TRADE_SL_PIPS,
    "AUTO_TRADE_TP_PIPS": models.AUTO_TRADE_TP_PIPS,
    "MAX_OPEN_POSITIONS": models.MAX_OPEN_POSITIONS,
    "AUTO_TRADE_COOLDOWN_MINUTES": models.AUTO_TRADE_COOLDOWN_MINUTES,
    "FIXED_RISK_USD": models.FIXED_RISK_USD,
    "FIXED_PROFIT_USD": models.FIXED_PROFIT_USD,
    "SL_MULTIPLIER": models.SL_MULTIPLIER,
    "AUTO_TAKE_PROFIT_ENABLED": models.AUTO_TAKE_PROFIT_ENABLED,
    "AUTO_TAKE_PROFIT_MIN_USD": models.AUTO_TAKE_PROFIT_MIN_USD,
}

_config: Optional[Dict[str, Any]] = None


def _validate(key: str, value: Any) -> bool:
    if key == "LOOKBACK_BARS":
        return isinstance(value, (int, float)) and 50 <= value <= 300
    if key == "TREND_LOOKBACK_BARS":
        return isinstance(value, (int, float)) and 5 <= value <= 100
    if key == "TRENDLINE_BARS":
        return isinstance(value, (int, float)) and 2 <= value <= 25
    if key == "SQUEEZE_BARS":
        return isinstance(value, (int, float)) and 4 <= value <= 20
    if key == "ATR_SQUEEZE_RATIO":
        return isinstance(value, (int, float)) and 0.1 <= value <= 0.9
    if key == "SLOPE_MIN_ABS":
        return isinstance(value, (int, float)) and 1e-6 <= value <= 0.01
    if key == "MA21_NEAR_ATR_RATIO":
        return isinstance(value, (int, float)) and 0.1 <= value <= 1.0
    if key == "BAND_TOLERANCE_ATR_RATIO":
        return isinstance(value, (int, float)) and 0.05 <= value <= 0.35
    if key == "MIN_PEAKS":
        return isinstance(value, (int, float)) and 2 <= value <= 10
    if key == "MIN_BARS_IN_BAND_RATIO":
        return isinstance(value, (int, float)) and 0.3 <= value <= 1.0
    if key == "BAND_CENTER_MIN_RATIO":
        return isinstance(value, (int, float)) and 0.05 <= value <= 0.4
    if key == "HOT_WINDOW_MINUTES":
        return isinstance(value, (int, float)) and 5 <= value <= 120
    if key == "AUTO_TRADE_ENABLED":
        if isinstance(value, bool):
            return True
        if isinstance(value, str):
            return value.lower() in ("1", "true", "yes", "on")
        return False
    if key == "AUTO_TRADE_LOT":
        return isinstance(value, (int, float)) and 0.01 <= value <= 100
    if key == "AUTO_TRADE_LOT_MULTIPLIER":
        return isinstance(value, (int, float)) and 0.1 <= value <= 10.0
    if key == "AUTO_TRADE_SL_PIPS":
        return isinstance(value, (int, float)) and 1 <= value <= 500
    if key == "AUTO_TRADE_TP_PIPS":
        return isinstance(value, (int, float)) and 1 <= value <= 1000
    if key == "MAX_OPEN_POSITIONS":
        return isinstance(value, (int, float)) and 1 <= value <= 20
    if key == "AUTO_TRADE_COOLDOWN_MINUTES":
        return isinstance(value, (int, float)) and 0 <= value <= 1440
    if key == "FIXED_RISK_USD":
        return isinstance(value, (int, float)) and 1 <= value <= 500
    if key == "FIXED_PROFIT_USD":
        return isinstance(value, (int, float)) and 0.1 <= value <= 500
    if key == "SL_MULTIPLIER":
        return isinstance(value, (int, float)) and 0.5 <= value <= 5.0
    if key == "AUTO_TAKE_PROFIT_ENABLED":
        if isinstance(value, bool):
            return True
        if isinstance(value, str):
            return value.lower() in ("1", "true", "yes", "on")
        return False
    if key == "AUTO_TAKE_PROFIT_MIN_USD":
        return isinstance(value, (int, float)) and 1 <= value <= 100
    return False


def load_config() -> Dict[str, Any]:
    """Load config from file or return defaults."""
    global _config
    if _config is not None:
        return _config
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            _config = {**DEFAULT_CONFIG}
            for k in DEFAULT_CONFIG:
                if k in data and _validate(k, data[k]):
                    v = data[k]
                    if k == "LOOKBACK_BARS":
                        _config[k] = int(v)
                    elif k in ("TREND_LOOKBACK_BARS", "TRENDLINE_BARS", "SQUEEZE_BARS", "MIN_PEAKS", "AUTO_TRADE_SL_PIPS", "AUTO_TRADE_TP_PIPS", "MAX_OPEN_POSITIONS", "AUTO_TRADE_COOLDOWN_MINUTES"):
                        _config[k] = int(float(v))
                    elif k in ("AUTO_TRADE_ENABLED", "AUTO_TAKE_PROFIT_ENABLED"):
                        _config[k] = bool(v) if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes")
                    elif k in ("AUTO_TRADE_LOT", "AUTO_TRADE_LOT_MULTIPLIER", "FIXED_RISK_USD", "FIXED_PROFIT_USD", "SL_MULTIPLIER", "AUTO_TAKE_PROFIT_MIN_USD"):
                        _config[k] = float(v)
                    else:
                        _config[k] = v
            return _config
        except Exception as e:
            logger.warning("Load config failed: %s, using defaults", e)
    _config = dict(DEFAULT_CONFIG)
    return _config


def get_config() -> Dict[str, Any]:
    """Return current config (load from file if needed)."""
    if _config is None:
        load_config()
    return _config


def save_config(data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate, merge with defaults, save to file, update in-memory. Returns saved config."""
    global _config
    out = dict(DEFAULT_CONFIG)
    for k in DEFAULT_CONFIG:
        if k in data:
            val = data[k]
            if k in ("LOOKBACK_BARS", "TREND_LOOKBACK_BARS", "TRENDLINE_BARS", "SQUEEZE_BARS", "HOT_WINDOW_MINUTES", "MIN_PEAKS", "AUTO_TRADE_SL_PIPS", "AUTO_TRADE_TP_PIPS", "MAX_OPEN_POSITIONS", "AUTO_TRADE_COOLDOWN_MINUTES"):
                val = int(float(val))
            elif k in ("AUTO_TRADE_ENABLED", "AUTO_TAKE_PROFIT_ENABLED"):
                val = bool(val) if isinstance(val, bool) else str(val).lower() in ("1", "true", "yes")
            elif k in ("AUTO_TRADE_LOT", "AUTO_TRADE_LOT_MULTIPLIER", "FIXED_RISK_USD", "FIXED_PROFIT_USD", "SL_MULTIPLIER", "AUTO_TAKE_PROFIT_MIN_USD"):
                val = float(val)
            else:
                val = float(val) if isinstance(val, (int, float)) else val
            if _validate(k, val):
                out[k] = val
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)
        _config = out
        logger.info("Config saved to %s: %s", CONFIG_FILE, out)
        return out
    except Exception as e:
        logger.exception("Save config failed: %s", e)
        raise
