"""MetaTrader5 connection and data fetching."""

import logging
from typing import Any, Dict, List, Optional, Tuple

import MetaTrader5 as mt5
import pandas as pd

logger = logging.getLogger(__name__)


def initialize(path: Optional[str] = None) -> bool:
    """Initialize MT5 connection. Returns True on success."""
    if path:
        ok = mt5.initialize(path=path)
    else:
        ok = mt5.initialize()
    if ok:
        logger.info("MT5 initialized successfully")
    else:
        logger.error("MT5 initialize failed: %s", mt5.last_error())
    return bool(ok)


def reconnect(path: Optional[str] = None) -> bool:
    """Shutdown and re-initialize MT5."""
    mt5.shutdown()
    return initialize(path=path)


def get_rates(symbol: str, timeframe: int, count: int = 200) -> Optional[pd.DataFrame]:
    """
    Fetch last `count` bars for symbol/timeframe.
    Returns DataFrame with columns: time, open, high, low, close, tick_volume.
    """
    try:
        # copy_rates_from_pos: 0 = current bar, count = number of bars (newest first in MT5)
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
        if rates is None or len(rates) == 0:
            logger.warning("No rates for %s tf=%s: %s", symbol, timeframe, mt5.last_error())
            return None
        df = pd.DataFrame(rates)
        df = df[["time", "open", "high", "low", "close", "tick_volume"]].copy()
        return df
    except Exception as e:
        logger.exception("get_rates failed for %s: %s", symbol, e)
        return None


def _get_symbol_info(symbol: str):
    """Lấy symbol_info, nếu chưa có trong Market Watch thì thử chọn."""
    info = mt5.symbol_info(symbol)
    if info is None:
        mt5.symbol_select(symbol, True)
        info = mt5.symbol_info(symbol)
    return info


def get_symbol_info(symbol: str):
    """Public: lấy symbol_info (dùng trong scanner cho fixed-risk lot/SL/TP)."""
    return _get_symbol_info(symbol)


def get_pip_value_per_lot(symbol: str, price: float) -> Optional[float]:
    """
    Giá trị 1 pip (USD) cho 1 lot chuẩn. Dùng để tính lot từ max loss.
    XXXUSD: 10 USD/pip; USDJPY: 1000/price; USDXXX: (100000*0.0001)/price; XAU: phụ thuộc contract.
    """
    if not symbol or price <= 0:
        return None
    s = symbol.upper().replace(" ", "")
    contract = 100000  # standard lot
    if s.endswith("USD") and s != "USDUSD":
        return contract * 0.0001
    if s == "USDJPY":
        return (contract * 0.01) / price
    if s.startswith("USD") and len(s) == 6 and "JPY" not in s:
        return (contract * 0.0001) / price
    if "JPY" in s:
        return (contract * 0.01) / price
    return contract * 0.0001


def count_positions_by_magic(magic: int) -> int:
    """Đếm số vị thế đang mở có magic number này."""
    positions = mt5.positions_get()
    if positions is None:
        return 0
    return sum(1 for p in positions if getattr(p, "magic", 0) == magic)


def has_position_for_symbol(symbol: str, is_buy: bool, magic: int) -> bool:
    """True nếu đã có vị thế mở cho symbol này cùng hướng (BUY hoặc SELL)."""
    positions = mt5.positions_get(symbol=symbol)
    if positions is None:
        return False
    for p in positions:
        if getattr(p, "magic", 0) != magic:
            continue
        if is_buy and p.type == mt5.POSITION_TYPE_BUY:
            return True
        if not is_buy and p.type == mt5.POSITION_TYPE_SELL:
            return True
    return False


def get_pip_size(symbol: str) -> Optional[float]:
    """
    Trả về kích thước 1 pip (đơn vị giá) cho symbol.
    Forex 5-digit: point=0.00001 -> pip=0.0001 (10*point).
    Forex 6-digit (nhiều broker): point=0.000001 -> 1 pip vẫn = 0.0001 trong giá = 100*point (nếu chỉ 10*point thì TP sẽ chỉ ~0.1 pip → lãi ~0.02$ thay vì 2$).
    JPY: 1 pip = 0.01; vàng point=0.01 -> pip=0.1.
    """
    info = _get_symbol_info(symbol)
    if info is None:
        return None
    point = getattr(info, "point", None)
    if point is None or point <= 0:
        return None
    if point >= 0.01:
        return 10 * point  # e.g. XAUUSD point=0.01 -> pip=0.1
    if point >= 0.0001:
        return 10 * point  # JPY: point=0.001 -> pip=0.01
    # 5-digit: point=0.00001 -> pip=0.0001; 6-digit: point=0.000001 -> pip=0.0001 (100*point)
    if point >= 0.00001:
        return 10 * point   # 5-digit
    return 100 * point  # 6-digit: 1 pip = 0.0001 trong giá


def round_price(symbol: str, price: float) -> Optional[float]:
    """Làm tròn giá theo digits của symbol."""
    info = _get_symbol_info(symbol)
    if info is None:
        return None
    digits = getattr(info, "digits", 5)
    return round(price, digits)


def place_market_order(
    symbol: str,
    is_buy: bool,
    volume: float,
    sl: float,
    tp: float,
    deviation: int = 20,
    magic: int = 202615,
    comment: str = "M15/M30/H1_squeeze",
) -> Tuple[bool, Optional[dict], str]:
    """
    Đặt lệnh khớp giá thị trường (market). SL/TP đã tính sẵn và làm tròn theo symbol.
    Returns: (success, result_dict, message).
    """
    info = _get_symbol_info(symbol)
    if info is None:
        return False, None, f"Symbol {symbol} not found"
    if not info.visible:
        mt5.symbol_select(symbol, True)
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return False, None, f"No tick for {symbol}"
    price = tick.ask if is_buy else tick.bid
    price = round_price(symbol, price)
    sl_r = round_price(symbol, sl)
    tp_r = round_price(symbol, tp)
    if price is None or sl_r is None or tp_r is None:
        return False, None, "Round price failed"
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL,
        "price": price,
        "sl": sl_r,
        "tp": tp_r,
        "deviation": deviation,
        "magic": magic,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_RETURN,
    }
    # Broker có thể yêu cầu FOK hoặc IOC
    fill_mode = getattr(info, "filling_mode", 0)
    if fill_mode & 1:
        request["type_filling"] = mt5.ORDER_FILLING_FOK
    elif fill_mode & 2:
        request["type_filling"] = mt5.ORDER_FILLING_IOC
    # else giữ ORDER_FILLING_RETURN
    result = mt5.order_send(request)
    if result is None:
        err = mt5.last_error()
        return False, None, str(err) if err else "order_send returned None"
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        return False, {"retcode": result.retcode, "comment": result.comment}, result.comment or str(result.retcode)
    logger.info("Order placed: %s %s vol=%.2f price=%.5f sl=%.5f tp=%.5f", symbol, "BUY" if is_buy else "SELL", volume, price, sl_r, tp_r)
    return True, {"retcode": result.retcode, "order": result.order}, result.comment or "OK"


def close_position(position_ticket: int, symbol: str, volume: float, is_buy: bool) -> Tuple[bool, str]:
    """
    Đóng vị thế (position) bằng lệnh ngược chiều. BUY đóng bằng SELL, SELL đóng bằng BUY.
    Có thể đóng một phần (volume < position.volume).
    Returns: (success, message).
    """
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return False, f"No tick for {symbol}"
    price = tick.bid if is_buy else tick.ask
    price = round_price(symbol, price)
    if price is None:
        return False, "Round price failed"
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
        "position": position_ticket,
        "price": price,
        "deviation": 20,
        "comment": "close_profit",
    }
    result = mt5.order_send(request)
    if result is None:
        err = mt5.last_error()
        return False, str(err) if err else "order_send returned None"
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        return False, result.comment or str(result.retcode)
    logger.info("Position closed: ticket=%s %s vol=%.2f @ %.5f", position_ticket, symbol, volume, price)
    return True, "OK"


def position_modify_sl_tp(position_ticket: int, symbol: str, sl: float, tp: float) -> Tuple[bool, str]:
    """
    Sửa SL và TP của vị thế đang mở. Dùng TRADE_ACTION_SLTP.
    sl/tp sẽ được round theo symbol. Đặt 0 nếu không muốn thay đổi (một số broker yêu cầu cả hai).
    Returns: (success, message).
    """
    sl_r = round_price(symbol, sl) if sl else 0.0
    tp_r = round_price(symbol, tp) if tp else 0.0
    if sl and sl_r is None:
        return False, "Round SL failed"
    if tp and tp_r is None:
        return False, "Round TP failed"
    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": position_ticket,
        "symbol": symbol,
        "sl": sl_r,
        "tp": tp_r,
    }
    result = mt5.order_send(request)
    if result is None:
        err = mt5.last_error()
        return False, str(err) if err else "order_send returned None"
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        return False, result.comment or str(result.retcode)
    logger.info("Position %s SL/TP updated: symbol=%s sl=%.5f tp=%.5f", position_ticket, symbol, sl_r, tp_r)
    return True, "OK"


def get_positions(magic: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    Lấy danh sách vị thế đang mở. Nếu magic không None thì lọc theo magic.
    Mỗi phần tử: ticket, symbol, type, volume, price_open, sl, tp, profit, magic, comment.
    """
    positions = mt5.positions_get()
    if positions is None:
        return []
    out = []
    for p in positions:
        if magic is not None and getattr(p, "magic", 0) != magic:
            continue
        out.append({
            "ticket": p.ticket,
            "symbol": p.symbol,
            "type": "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL",
            "volume": round(p.volume, 2),
            "price_open": p.price_open,
            "sl": p.sl,
            "tp": p.tp,
            "profit": round(p.profit + getattr(p, "swap", 0) + getattr(p, "commission", 0), 2),
            "magic": getattr(p, "magic", 0),
            "comment": getattr(p, "comment", ""),
        })
    return out


def partial_close_and_breakeven(
    position_ticket: int,
    profit_min: float = 5.0,
    close_ratio: float = 0.5,
    magic: Optional[int] = None,
) -> Tuple[bool, str]:
    """
    Edit lệnh: khi profit >= profit_min (USD), đóng một phần (close_ratio, mặc định 50%) để chốt lãi,
    phần còn lại đẩy SL lên sát entry (breakeven) để tránh lỗ.
    Returns: (success, message).
    """
    positions = mt5.positions_get()
    if not positions:
        return False, "Không có vị thế nào"
    pos = None
    for p in positions:
        if p.ticket == position_ticket:
            pos = p
            break
    if pos is None:
        return False, f"Không tìm thấy vị thế ticket={position_ticket}"
    if magic is not None and getattr(pos, "magic", 0) != magic:
        return False, "Vị thế không thuộc magic này"
    profit = pos.profit + getattr(pos, "swap", 0) + getattr(pos, "commission", 0)
    if profit < profit_min:
        return False, f"Lãi hiện tại ${profit:.2f} chưa đạt tối thiểu ${profit_min:.2f}"
    symbol = pos.symbol
    is_buy = pos.type == mt5.POSITION_TYPE_BUY
    info = _get_symbol_info(symbol)
    if info is None:
        return False, f"Symbol {symbol} không tìm thấy"
    volume_step = float(getattr(info, "volume_step", 0.01))
    volume_min = float(getattr(info, "volume_min", 0.01))
    total_vol = pos.volume
    close_vol = round(total_vol * close_ratio / volume_step) * volume_step
    close_vol = max(volume_min, min(close_vol, total_vol - volume_min))
    if close_vol >= total_vol:
        close_vol = round((total_vol - volume_min) / volume_step) * volume_step
        if close_vol < volume_min:
            return False, "Không thể đóng một nửa (lot tối thiểu)"
    entry = pos.price_open
    # Bước 1: Đóng một phần để chốt lãi
    ok_close, msg_close = close_position(position_ticket, symbol, close_vol, is_buy)
    if not ok_close:
        return False, f"Đóng một phần thất bại: {msg_close}"
    # Bước 2: Đẩy SL phần còn lại lên breakeven (sát entry). Position ticket không đổi sau partial close.
    sl_breakeven = round_price(symbol, entry)
    if sl_breakeven is None:
        return True, f"Đã đóng {close_vol} lot. Cập nhật SL breakeven thất bại (round)."
    ok_mod, msg_mod = position_modify_sl_tp(position_ticket, symbol, sl_breakeven, pos.tp)
    if not ok_mod:
        return True, f"Đã đóng {close_vol} lot. Cập nhật SL breakeven lỗ: {msg_mod}"
    return True, f"Đã đóng {close_vol} lot, SL phần còn lại đã đẩy lên {sl_breakeven} (breakeven)."
