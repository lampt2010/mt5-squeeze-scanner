# Đối chiếu ảnh mẫu (assets) với logic BUY/SELL

Tài liệu này map từng ảnh trong `assets/` với pattern và hàm scanner tương ứng để cấu hình cảnh báo tín hiệu giống mẫu hình nhất.

## Tổng quan pattern ↔ ảnh

| Ảnh | Pattern | Hàm scanner | Khuyến nghị |
|-----|---------|-------------|-------------|
| Screenshot_9.png | Bullish breakout từ resistance giảm dần, volume tăng | `_check_early_breakout` | **BUY** (Early Breakout) |
| Screenshot_10.png | Bearish: giá dưới MA + trendline, squeeze/consolidation | `_check_bearish_squeeze` | **SELL** (Mẫu 1) |
| Screenshot_11.png | Squeeze trong band (red box), giá trên MA | `_check_bullish_squeeze` | **BUY** (Mẫu 2) |
| Screenshot_12.png | GBPUSD H4: W pattern, descending trendline, breakout lên | `_check_early_breakout` | **BUY** (Early Breakout) |
| Screenshot_13.png | Consolidation → nến xanh mạnh phá lên trendline (dấu +) | `_check_early_breakout` | **BUY** (Early Breakout) |
| Screenshot_14.png | Downtrend resistance, consolidation, chuẩn bị breakout | `_check_early_breakout` | **BUY** (Early Breakout) |
| Screenshot_15.png | Bullish breakout trên trendline xanh + MA | `_check_early_breakout` | **BUY** (Early Breakout) |
| Screenshot_16.png | Squeeze giữa trendline và MA → nến xanh breakout | `_check_early_breakout` + `_check_bullish_squeeze` | **BUY** |
| Screenshot_17.png | Support dốc lên bị phá xuống (dấu +) | `_check_bearish_breakdown_support` | **SELL** (Mẫu 3) |

## Cấu hình khuyến nghị (giống mẫu hình nhất)

Đã áp dụng trong `scanner_config.json` (thư mục gốc dự án):

- **MIN_BARS_IN_BAND_RATIO = 1.0** — 100% nến squeeze phải nằm trong band (đúng mẫu Squeeze trong ảnh).
- **SQUEEZE_BARS = 5** — Cửa sổ squeeze ngắn, khớp vùng “nén” trong ảnh.
- **ATR_SQUEEZE_RATIO = 0.55** — Cho phép nến squeeze to vừa phải (body < 55% ATR).
- **BAND_TOLERANCE_ATR_RATIO = 0.32** — Band không quá rộng, giá thật sự nằm giữa trendline và MA21.
- **BAND_CENTER_MIN_RATIO = 0.15** — Nến nằm giữa band, không dính sát trendline/MA21.
- **TREND_LOOKBACK_BARS = 15** — Xác định trend trên 15 nến gần nhất (M15/M30/H1).
- **MIN_PEAKS = 3** — Ít nhất 3 đỉnh/đáy để vẽ trendline.
- **SLOPE_MIN_ABS = 0.0001** — Độ dốc tối thiểu (trendline không quá ngang); đối chiếu ảnh mẫu (resistance/support rõ dốc) → dùng **0.0001** làm mặc định.

## Chạy dự án cảnh báo tín hiệu

1. Mở MT5, đăng nhập tài khoản (demo/real).
2. Chạy backend: `python -m backend.main` hoặc `run-both.bat`.
3. Scanner quét **M15, M30, H1** cho các symbol trong `models.DEFAULT_SYMBOLS`.
4. Tín hiệu khớp mẫu:
   - **BUY**: Bullish Squeeze (Mẫu 2) hoặc Early Breakout (cảnh báo sớm).
   - **SELL**: Bearish Squeeze (Mẫu 1) hoặc Phá vỡ support (Mẫu 3).

Frontend hiển thị danh sách signal; “hot” = signal trong vòng `HOT_WINDOW_MINUTES` (mặc định 15 phút).
