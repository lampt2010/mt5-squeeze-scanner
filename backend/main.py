"""FastAPI app: symbols, WebSocket scanner broadcast, background scan task."""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from fastapi import Body, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend import models
from backend.config import get_config, get_symbol_lists, save_config
from backend.db import get_history, init_db, save_detections
from backend.mt5_connector import (
    close_position,
    count_positions_by_magic,
    get_pip_size,
    get_pip_value_per_lot,
    get_positions,
    get_rates,
    get_symbol_info,
    has_position_for_symbol,
    initialize,
    partial_close_and_breakeven,
    place_market_order,
    reconnect,
    round_price,
)
from backend.scanner import get_trendline_series, run_scan

import MetaTrader5 as mt5
TF_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M10": mt5.TIMEFRAME_M10,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# WebSocket clients and state
ws_clients: Set[WebSocket] = set()


def _is_weekend() -> bool:
    """True nếu là cuối tuần (UTC): thị trường forex đóng, chỉ quét crypto."""
    return datetime.now(timezone.utc).weekday() >= 5  # 5=Saturday, 6=Sunday


def get_symbols_to_scan() -> List[str]:
    """Danh sách symbol cần quét: cuối tuần = chỉ crypto (nhẹ), ngày thường = full list."""
    weekday_symbols, weekend_symbols = get_symbol_lists()
    return weekend_symbols if _is_weekend() else weekday_symbols


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if not initialize():
        logger.warning("MT5 not available; scanner will retry on each run.")
    app.state.recent_signals: List[dict] = []
    app.state.hot_signals: List[dict] = []
    app.state.current_signals: List[dict] = []
    app.state.scan_status: dict = {"symbols_with_data": 0, "total_checked": 0}
    app.state.traded_bar_keys: Set[str] = set()  # "symbol|tf|bar_time" đã gửi lệnh, tránh trùng
    app.state.last_trade_time: Dict[str, float] = {}  # symbol -> timestamp, dùng cho cooldown
    task = asyncio.create_task(background_scanner(app))
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def background_scanner(app: FastAPI):
    """Every 3s: run scan, update state, broadcast to WebSocket clients."""
    cycle = 0
    while True:
        try:
            import time
            symbols = get_symbols_to_scan()
            if cycle == 1 and symbols:
                logger.info(
                    "[MT5 scan] %s mode: %s pairs (%s)",
                    "weekend (crypto only)" if _is_weekend() else "weekday",
                    len(symbols),
                    ", ".join(symbols[:8]) + ("..." if len(symbols) > 8 else ""),
                )
            all_signals, _, scan_status = run_scan(symbols=symbols)
            cycle += 1
            n_ok = scan_status.get("symbols_with_data", 0)
            n_total = scan_status.get("total_checked", 0)
            n_signals = len(all_signals)
            n_ws = len(ws_clients)
            logger.info(
                "[MT5 scan #%s] data: %s/%s pairs | signals: %s | WS clients: %s",
                cycle, n_ok, n_total, n_signals, n_ws
            )
            now_ts = time.time()
            app.state.scan_status = scan_status
            # Accumulate and prune to last HOT_WINDOW_MINUTES
            app.state.recent_signals = list(app.state.recent_signals) + all_signals
            window_sec = get_config()["HOT_WINDOW_MINUTES"] * 60
            app.state.recent_signals = [
                s for s in app.state.recent_signals
                if (now_ts - s.get("timestamp", 0)) <= window_sec
            ]
            app.state.hot_signals = list(app.state.recent_signals)
            app.state.current_signals = all_signals
            if all_signals:
                save_detections(all_signals)

            # Auto trade: dùng FIXED_RISK_USD / FIXED_PROFIT_USD từ cấu hình (trùng với header "Cấu hình đặt tiền")
            cfg = get_config()
            AUTO_TRADE_MAGIC = 202615
            MAX_LOSS_USD = float(cfg.get("FIXED_RISK_USD", models.FIXED_RISK_USD))
            TARGET_PROFIT_USD = float(cfg.get("FIXED_PROFIT_USD", models.FIXED_PROFIT_USD))
            TARGET_PROFIT_USD = max(1.0, TARGET_PROFIT_USD)  # Không đặt TP/đóng lệnh ở lãi < 1$ (tránh 0.1$–0.2$)
            MAX_OPEN_POSITIONS = int(cfg.get("MAX_OPEN_POSITIONS", 3))  # Số cặp/lệnh tối đa đang giữ
            AUTO_CLOSE_PROFIT_USD = TARGET_PROFIT_USD  # Tự đóng lệnh khi lãi >= TP $
            SL_PIPS_FROM_MA21 = int(cfg.get("AUTO_TRADE_SL_PIPS", 10))

            # Auto trade: M15, M30, H1 (không dùng M5)
            AUTO_TRADE_TIMEFRAMES = ("M15", "M30", "H1")
            auto_enabled = cfg.get("AUTO_TRADE_ENABLED")
            if isinstance(auto_enabled, str):
                auto_enabled = auto_enabled.lower() in ("1", "true", "yes")
            positions_count = count_positions_by_magic(AUTO_TRADE_MAGIC)
            trade_signals = [s for s in all_signals if s.get("tf") in AUTO_TRADE_TIMEFRAMES]
            if auto_enabled:
                logger.info(
                    "[Auto trade] BẬT | positions=%s/%s | signals(M15+M30+H1)=%s | total_signals=%s",
                    positions_count, MAX_OPEN_POSITIONS, len(trade_signals), len(all_signals),
                )
            else:
                if len(trade_signals) > 0:
                    logger.info(
                        "[Auto trade] TẮT — có %s tín hiệu M15/M30/H1 nhưng không đặt lệnh. "
                        "Bật trong Settings → AUTO_TRADE_ENABLED ✓ → Lưu.",
                        len(trade_signals),
                    )
                elif len(all_signals) > 0:
                    logger.debug("[Auto trade] TẮT (AUTO_TRADE_ENABLED=%s). Bật trong Settings và bấm Lưu.", cfg.get("AUTO_TRADE_ENABLED"))
            if auto_enabled:
                # Tự đóng lệnh khi lãi >= AUTO_CLOSE_PROFIT_USD (FIXED_PROFIT_USD, tối thiểu 1$)
                import MetaTrader5 as mt5
                positions = mt5.positions_get()
                if positions:
                    for p in positions:
                        if getattr(p, "magic", 0) != AUTO_TRADE_MAGIC:
                            continue
                        profit = getattr(p, "profit", 0) or 0
                        if profit >= AUTO_CLOSE_PROFIT_USD:
                            ok = close_position(
                                p.ticket, p.symbol, p.volume,
                                p.type == mt5.POSITION_TYPE_BUY,
                            )
                            if ok:
                                logger.info("[Auto trade] Đã đóng lệnh lãi >= %.0f$: %s ticket=%s profit=%.2f", AUTO_CLOSE_PROFIT_USD, p.symbol, p.ticket, profit)
                            else:
                                logger.warning("[Auto trade] Đóng lệnh thất bại: %s ticket=%s", p.symbol, p.ticket)
            # Auto tự chốt lãi: khi lãi >= ngưỡng, đóng ~50% và đẩy SL phần còn lại lên breakeven (chạy riêng, không phụ thuộc AUTO_TRADE_ENABLED)
            auto_tp_enabled = cfg.get("AUTO_TAKE_PROFIT_ENABLED")
            if isinstance(auto_tp_enabled, str):
                auto_tp_enabled = auto_tp_enabled.lower() in ("1", "true", "yes")
            if auto_tp_enabled:
                positions_tp = mt5.positions_get()
                if positions_tp:
                    min_profit = float(cfg.get("AUTO_TAKE_PROFIT_MIN_USD", 5.0))
                    min_profit = max(1.0, min_profit)  # Không partial-close khi lãi < 1$
                    for p in positions_tp:
                        if getattr(p, "magic", 0) != AUTO_TRADE_MAGIC:
                            continue
                        profit = getattr(p, "profit", 0) or 0
                        if profit < min_profit:
                            continue
                        # Bỏ qua nếu SL đã sát entry (đã áp dụng breakeven rồi)
                        info = get_symbol_info(p.symbol)
                        point = getattr(info, "point", 0.00001) if info else 0.00001
                        epsilon = max(10 * point, 1e-8)
                        if p.sl and abs(float(p.sl) - float(p.price_open)) <= epsilon:
                            continue
                        ok_tp, msg_tp = partial_close_and_breakeven(
                            p.ticket, profit_min=min_profit, close_ratio=0.5, magic=AUTO_TRADE_MAGIC
                        )
                        if ok_tp:
                            logger.info("[Auto take profit] %s ticket=%s: %s", p.symbol, p.ticket, msg_tp)
                        else:
                            logger.debug("[Auto take profit] %s ticket=%s: %s", p.symbol, p.ticket, msg_tp)
            if auto_enabled:
                positions_count = count_positions_by_magic(AUTO_TRADE_MAGIC)
                if positions_count >= MAX_OPEN_POSITIONS:
                    logger.info("[Auto trade] Skip: đã đủ %s lệnh, không đặt thêm.", MAX_OPEN_POSITIONS)
                else:
                    for sig in all_signals:
                        if count_positions_by_magic(AUTO_TRADE_MAGIC) >= MAX_OPEN_POSITIONS:
                            break
                        if sig.get("tf") not in AUTO_TRADE_TIMEFRAMES:
                            continue
                        bar_time = sig.get("bar_time")
                        symbol = sig.get("symbol", "")
                        tf = sig.get("tf", "")
                        rec_raw = (sig.get("recommendation") or "").strip().upper()
                        if rec_raw in ("MUA", "BUY"):
                            is_buy = True
                        elif rec_raw in ("BÁN", "BAN", "SELL"):
                            is_buy = False
                        else:
                            logger.info("[Auto trade] Skip %s %s: recommendation=\"%s\" không hợp lệ (cần MUA/BÁN hoặc BUY/SELL).", symbol, tf, rec_raw or "(rỗng)")
                            continue
                        if has_position_for_symbol(symbol, is_buy, AUTO_TRADE_MAGIC):
                            logger.info("[Auto trade] Skip %s: đã có lệnh %s cùng cặp.", symbol, "BUY" if is_buy else "SELL")
                            continue
                        cooldown_min = int(cfg.get("AUTO_TRADE_COOLDOWN_MINUTES", getattr(models, "AUTO_TRADE_COOLDOWN_MINUTES", 60)))
                        if cooldown_min > 0:
                            last_ts = getattr(app.state, "last_trade_time", {}).get(symbol, 0)
                            if (now_ts - last_ts) < cooldown_min * 60:
                                logger.info(
                                    "[Auto trade] Skip %s: cooldown %s phút (còn %.0f s).",
                                    symbol, cooldown_min, cooldown_min * 60 - (now_ts - last_ts),
                                )
                                continue
                        key = f"{symbol}|{tf}|{bar_time}"
                        if key in app.state.traded_bar_keys:
                            # Nếu lệnh đã đóng (không còn position cho symbol này) thì cho phép đặt lại
                            has_any = has_position_for_symbol(symbol, True, AUTO_TRADE_MAGIC) or has_position_for_symbol(symbol, False, AUTO_TRADE_MAGIC)
                            if has_any:
                                logger.info("[Auto trade] Skip %s %s: đã đặt lệnh cho bar này (key=%s).", symbol, tf, key)
                                continue
                            # Lệnh cũ đã đóng — xóa key để được đặt lại
                            to_drop = {k for k in app.state.traded_bar_keys if k.startswith(f"{symbol}|")}
                            app.state.traded_bar_keys -= to_drop
                            logger.info("[Auto trade] Đã xóa key %s (lệnh đã đóng), cho phép đặt lại %s.", list(to_drop), symbol)
                        entry = float(sig.get("entry_price") or sig.get("last_price") or 0)
                        ma21 = float(sig.get("ma21") or 0)
                        if entry <= 0 or ma21 <= 0:
                            logger.info("[Auto trade] Skip %s %s: entry=%s ma21=%s không hợp lệ.", symbol, tf, entry, ma21)
                            continue
                        pip_size = get_pip_size(symbol)
                        if pip_size is None or pip_size <= 0:
                            logger.warning("Auto trade skip %s: no pip size", symbol)
                            continue
                        # is_buy đã gán ở trên (trước khi check has_position_for_symbol)
                        # SL cách MA21 đúng 10 pip (không quá 10 giá)
                        if is_buy:
                            sl_price = ma21 - SL_PIPS_FROM_MA21 * pip_size
                        else:
                            sl_price = ma21 + SL_PIPS_FROM_MA21 * pip_size
                        sl_distance_price = abs(entry - sl_price)
                        sl_distance_pips = sl_distance_price / pip_size if pip_size else 0
                        if sl_distance_pips <= 0:
                            logger.warning("Auto trade skip %s: SL too close to entry", symbol)
                            continue
                        pip_value = get_pip_value_per_lot(symbol, entry)
                        if pip_value is None or pip_value <= 0:
                            logger.warning("Auto trade skip %s: no pip value", symbol)
                            continue
                        # Lot sao cho lỗ = FIXED_RISK_USD khi chạm SL; giới hạn bởi cấu hình (min/max lot)
                        lot = MAX_LOSS_USD / (sl_distance_pips * pip_value)
                        lot = round(lot, 2)
                        max_lot = float(cfg.get("AUTO_TRADE_LOT", 10.0))
                        min_lot = float(cfg.get("AUTO_TRADE_MIN_LOT", getattr(models, "AUTO_TRADE_MIN_LOT", 0.01)))
                        lot = max(min_lot, min(lot, max_lot))
                        # TP theo FIXED_PROFIT_USD: profit = lot * tp_distance_pips * pip_value
                        if TARGET_PROFIT_USD > 0 and pip_value and lot:
                            tp_distance_pips = TARGET_PROFIT_USD / (lot * pip_value)
                            if is_buy:
                                tp_price = entry + tp_distance_pips * pip_size
                            else:
                                tp_price = entry - tp_distance_pips * pip_size
                        else:
                            # Fallback: RR 1:2
                            tp_distance_pips = 2.0 * sl_distance_pips
                            if is_buy:
                                tp_price = entry + tp_distance_pips * pip_size
                            else:
                                tp_price = entry - tp_distance_pips * pip_size
                        # MT5 requires: BUY -> SL < entry, TP > entry; SELL -> SL > entry, TP < entry (retcode 10016 = Invalid stops)
                        # If MA21 is on wrong side of entry, place SL same distance from entry on correct side
                        if is_buy:
                            if sl_price >= entry:
                                sl_price = entry - SL_PIPS_FROM_MA21 * pip_size
                            if tp_price <= entry:
                                tp_price = entry + tp_distance_pips * pip_size
                        else:
                            if sl_price <= entry:
                                sl_price = entry + SL_PIPS_FROM_MA21 * pip_size
                            if tp_price >= entry:
                                tp_price = entry - tp_distance_pips * pip_size
                        sl_distance_price = abs(entry - sl_price)
                        sl_distance_pips = sl_distance_price / pip_size if pip_size else 0
                        if sl_distance_pips <= 0:
                            logger.warning("Auto trade skip %s: SL too close to entry after clamp", symbol)
                            continue
                        # Recompute lot if SL was clamped (risk stays MAX_LOSS_USD)
                        lot = MAX_LOSS_USD / (sl_distance_pips * pip_value)
                        lot = round(lot, 2)
                        lot = max(min_lot, min(lot, max_lot))
                        # Hệ số nhân lot (chủ động tăng/giảm để đánh giá chiến lược)
                        lot_mult = float(cfg.get("AUTO_TRADE_LOT_MULTIPLIER", 1.0))
                        if lot_mult != 1.0:
                            lot = round(lot * lot_mult, 2)
                            lot = max(min_lot, min(lot, max_lot))
                        # TP phải tính từ giá khớp thực tế (tick), không phải entry từ tín hiệu — nếu không khi lệnh khớp lệch vài pip thì chạm TP chỉ lãi 0.1–0.2$
                        fill_price = None
                        tick = mt5.symbol_info_tick(symbol)
                        if tick is not None:
                            fill_price = round_price(symbol, float(tick.ask if is_buy else tick.bid))
                            if fill_price is not None:
                                if is_buy:
                                    tp_price = fill_price + tp_distance_pips * pip_size
                                else:
                                    tp_price = fill_price - tp_distance_pips * pip_size
                                # Đảm bảo TP đúng phía so với fill
                                if is_buy and tp_price <= fill_price:
                                    tp_price = fill_price + tp_distance_pips * pip_size
                                if not is_buy and tp_price >= fill_price:
                                    tp_price = fill_price - tp_distance_pips * pip_size
                        base_price = fill_price if fill_price is not None else entry
                        # Ép TP tối thiểu 1 pip để tránh làm tròn sát entry
                        min_tp_pips = 1.0
                        if pip_size and abs(tp_price - base_price) / pip_size < min_tp_pips:
                            if is_buy:
                                tp_price = base_price + min_tp_pips * pip_size
                            else:
                                tp_price = base_price - min_tp_pips * pip_size
                        expected_profit_usd = (lot * pip_value) * tp_distance_pips
                        if pip_size and (fill_price is not None or entry):
                            base = fill_price if fill_price is not None else entry
                            actual_tp_pips = abs(tp_price - base) / pip_size
                            expected_profit_usd = lot * pip_value * actual_tp_pips
                        if expected_profit_usd < TARGET_PROFIT_USD * 0.2:
                            logger.warning(
                                "[Auto trade] TP %s: kỳ vọng lãi ~%.2f$ (thiết lập %.0f$). pip_size=%.6f pip_value=%.2f — kiểm tra broker 5/6 digit.",
                                symbol, expected_profit_usd, TARGET_PROFIT_USD, pip_size or 0, pip_value or 0,
                            )
                        logger.info(
                            "[Auto trade] Đặt lệnh: %s %s lot=%s entry=%.5f sl=%.5f tp=%.5f (tp_dist≈%.1f pip, kỳ vọng lãi ~%.2f$)",
                            symbol, "BUY" if is_buy else "SELL", lot, entry, sl_price, tp_price,
                            tp_distance_pips, expected_profit_usd,
                        )
                        ok, res, msg = place_market_order(
                            symbol, is_buy, lot, sl_price, tp_price,
                            magic=AUTO_TRADE_MAGIC, comment="M15/M30/H1_squeeze"
                        )
                        if ok:
                            app.state.traded_bar_keys.add(key)
                            if not hasattr(app.state, "last_trade_time"):
                                app.state.last_trade_time = {}
                            app.state.last_trade_time[symbol] = now_ts
                            logger.info("[Auto trade] Đã đặt lệnh thành công: %s (cooldown %s phút).", symbol, cooldown_min)
                        else:
                            logger.warning("[Auto trade] %s thất bại: %s (retcode=%s)", symbol, msg, res)

            payload = {
                "signals": app.state.current_signals,
                "hot_signals": app.state.hot_signals,
                "scan_status": app.state.scan_status,
                "auto_trade_enabled": bool(auto_enabled),
            }
            text = json.dumps(payload)
            dead = set()
            for ws in ws_clients:
                try:
                    await ws.send_text(text)
                except Exception:
                    dead.add(ws)
            for ws in dead:
                ws_clients.discard(ws)
        except Exception as e:
            logger.exception("Scanner error: %s", e)
            reconnect()
        await asyncio.sleep(3)


app = FastAPI(title="MT5 Squeeze Scanner", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/symbols")
def get_symbols():
    """Return list of symbols đang được quét (ngày thường = full, cuối tuần = chỉ crypto)."""
    return JSONResponse(content=get_symbols_to_scan())


@app.get("/settings")
def get_settings():
    """Return current scanner logic settings (pattern detection params)."""
    return JSONResponse(content=get_config())


@app.post("/settings")
def post_settings(body: dict = Body(...)):
    """Save scanner settings. Validates and persists to scanner_config.json."""
    try:
        logger.info("Settings POST: AUTO_TRADE_ENABLED=%s, AUTO_TRADE_LOT=%s", body.get("AUTO_TRADE_ENABLED"), body.get("AUTO_TRADE_LOT"))
        saved = save_config(body)
        logger.info("Config saved. AUTO_TRADE_ENABLED=%s (lần quét sau sẽ dùng).", saved.get("AUTO_TRADE_ENABLED"))
        return JSONResponse(content=saved)
    except Exception as e:
        logger.warning("Config save failed: %s", e)
        return JSONResponse(content={"error": str(e)}, status_code=400)


@app.get("/hot-pairs")
def get_hot_pairs():
    """Return hot_signals (signals within HOT_WINDOW_MINUTES)."""
    return JSONResponse(content=getattr(app.state, "hot_signals", []))


@app.get("/history")
def get_detection_history(limit: int = 100, symbol: str = "", tf: str = ""):
    """Return detection history from SQLite. Optional: symbol, tf, limit."""
    sym = symbol.strip() or None
    t = tf.strip() or None
    rows = get_history(limit=min(limit, 500), symbol=sym, tf=t)
    return JSONResponse(content=rows)


@app.get("/rates")
def get_rates_endpoint(symbol: str, tf: str = "M15", count: int = 200):
    """Return OHLC bars for chart. tf: M1, M10, M15, M30."""
    tf_key = (tf or "M15").strip().upper()
    if tf_key not in TF_MAP:
        return JSONResponse(content={"error": "Invalid tf"}, status_code=400)
    df = get_rates(symbol, TF_MAP[tf_key], count)
    if df is None:
        return JSONResponse(content={"error": "No data"}, status_code=404)
    # Return list of { time, open, high, low, close } for Lightweight Charts (time as unix)
    rows = df[["time", "open", "high", "low", "close"]].to_dict("records")
    return JSONResponse(content=rows)


@app.get("/chart-indicators")
def get_chart_indicators(symbol: str, tf: str = "M15", bar_time: Optional[int] = None):
    """Return trendline + metadata for chart. bar_time: xem trendline đúng thời điểm (vd từ History)."""
    tf_key = (tf or "M15").strip().upper()
    if tf_key not in TF_MAP:
        return JSONResponse(content={"trendline": None})
    result = get_trendline_series(symbol, tf_key, as_of_bar_time=bar_time)
    if result is None:
        return JSONResponse(content={"trendline": None})
    return JSONResponse(content=result)


# Magic của lệnh từ scanner/auto trade
POSITIONS_MAGIC = 202615


@app.get("/positions")
def list_positions(magic: int = POSITIONS_MAGIC):
    """Danh sách vị thế đang mở (lọc theo magic, mặc định 202615)."""
    positions = get_positions(magic=magic if magic else None)
    return JSONResponse(content=positions)


@app.post("/positions/{ticket:int}/partial-secure")
def post_partial_secure(
    ticket: int,
    body: dict = Body(default=None),
):
    """
    Edit lệnh: khi profit >= 5$, đóng ~50% lot để chốt lãi, phần còn lại đẩy SL lên breakeven.
    Body: { "profit_min": 5, "close_ratio": 0.5 } (tùy chọn).
    """
    profit_min = float(body.get("profit_min", 5.0)) if body else 5.0
    close_ratio = float(body.get("close_ratio", 0.5)) if body else 0.5
    ok, msg = partial_close_and_breakeven(
        position_ticket=ticket,
        profit_min=profit_min,
        close_ratio=close_ratio,
        magic=POSITIONS_MAGIC,
    )
    if not ok:
        return JSONResponse(content={"ok": False, "error": msg}, status_code=400)
    return JSONResponse(content={"ok": True, "message": msg})


@app.websocket("/ws/scanner")
async def ws_scanner(websocket: WebSocket):
    await websocket.accept()
    ws_clients.add(websocket)
    try:
        # Send current state immediately
        cfg_ws = get_config()
        at = cfg_ws.get("AUTO_TRADE_ENABLED")
        at_bool = at if isinstance(at, bool) else str(at).lower() in ("1", "true", "yes")
        payload = {
            "signals": getattr(app.state, "current_signals", []),
            "hot_signals": getattr(app.state, "hot_signals", []),
            "scan_status": getattr(app.state, "scan_status", {"symbols_with_data": 0, "total_checked": 0}),
            "auto_trade_enabled": bool(at_bool),
        }
        await websocket.send_text(json.dumps(payload))
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        ws_clients.discard(websocket)
