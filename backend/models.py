"""Configuration and data models for MT5 Squeeze Scanner."""

# Default symbols (demo/standard account: không hậu tố "m"). Đổi lại thành EURUSDm, GBPUSDm... nếu dùng Standard với suffix m.
DEFAULT_SYMBOLS = [
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD",
    "USDCAD", "USDCHF", "XAUUSD", "XAGUSD", "BTCUSD",
]

# Timeframes: name -> MT5 constant
TIMEFRAMES = {
    "M1": None,
    "M10": None,
    "M15": None,
    "M30": None,
}

# Pattern detection config (độ nhạy cao hơn: nới điều kiện để bắt nhiều mẫu hơn)
LOOKBACK_BARS = 80
# Số nến gần nhất dùng để xác định trend (Mẫu 1/2); 200 nến vẫn lấy cho chart/API
TREND_LOOKBACK_BARS = 15
TRENDLINE_BARS = 6          # (Không dùng) Trendline giờ = đường nối đỉnh/đáy đầu và cuối trong cửa sổ; giữ key để tương thích config/UI.
SQUEEZE_BARS = 5            # Cửa sổ squeeze ngắn hơn (8→5) → dễ thỏa
ATR_SQUEEZE_RATIO = 0.55    # Avg body < 55% ATR (0.4→0.55: cho phép nến to hơn một chút)
SLOPE_MIN_ABS = 0.00005     # Slope tối thiểu (giảm: trendline thoải hơn vẫn chấp nhận)
MA21_NEAR_ATR_RATIO = 0.5   # Nến cuối trong ±0.5*ATR của MA21 (0.3→0.5: nới hơn)
BAND_TOLERANCE_ATR_RATIO = 0.15  # Band hẹp hơn: nến phải thật sự nằm giữa trendline và MA21
MIN_PEAKS = 3               # Số peak/đáy tối thiểu (5→3: ít đỉnh hơn vẫn vẽ được trendline)
MIN_BARS_IN_BAND_RATIO = 1.0    # 100% nến squeeze phải nằm trong band (giữa trendline và MA21), đúng mẫu
BAND_CENTER_MIN_RATIO = 0.15    # Nến phải nằm "giữa" band: close cách mỗi biên ít nhất 15% độ rộng band (không dính sát trendline/MA21)

# Watchlist: only signals within this window (minutes) are "hot"
HOT_WINDOW_MINUTES = 15

# Auto trade (chỉ M15): đặt lệnh market khi có signal, SL = MA21 ± 10 pip, TP = entry ± 30 pip
AUTO_TRADE_ENABLED = False
AUTO_TRADE_LOT = 10.0  # lot tối đa (lot thực tế tính từ lỗ 20$)
AUTO_TRADE_LOT_MULTIPLIER = 1.0  # Hệ số nhân lot: lot cuối = lot tính × hệ số (để chủ động tăng/giảm đánh giá chiến lược)
AUTO_TRADE_SL_PIPS = 10
AUTO_TRADE_TP_PIPS = 30
MAX_OPEN_POSITIONS = 3  # Số cặp tiền / lệnh tối đa đang giữ (auto trade không đặt thêm khi đủ số này)
AUTO_TRADE_COOLDOWN_MINUTES = 1  # Sau khi đặt/đóng lệnh 1 cặp, không đặt lại cặp đó trong N phút (tránh đặt trùng mỗi nến)

# Dự báo SELL (Mẫu 1 Bearish Squeeze): risk management FIXED DOLLAR
FIXED_RISK_USD = 20.0   # Maximum loss per trade (USD)
FIXED_PROFIT_USD = 15.0 # Take Profit target (USD) → đóng lệnh khi đạt hoặc set TP price
SL_MULTIPLIER = 1.5     # SL distance = ATR * SL_MULTIPLIER

# Auto tự chốt lãi: khi lãi >= ngưỡng (USD), tự đóng ~50% lot và đẩy SL phần còn lại lên breakeven
AUTO_TAKE_PROFIT_ENABLED = False
AUTO_TAKE_PROFIT_MIN_USD = 5.0   # Ngưỡng lãi tối thiểu để kích hoạt (USD)
