---
name: mt5-scanner
description: Hỗ trợ phân tích và code cho MT5 Squeeze Scanner. Bao gồm logic BUY/SELL dựa trên Bullish/Bearish Squeeze, Early Breakout, và tính volume lot theo fixed risk. Sử dụng khi cần generate code cho scanner.py hoặc tính lot cho lệnh giao dịch Forex/Gold/BTC.
---

# MT5 Scanner Skill

## Mô tả
Skill này giúp AI agent trong Cursor:
- Phân tích mẫu hình BUY (Bullish Squeeze hoặc Early Breakout) từ biểu đồ MT5.
- Phân tích mẫu hình SELL (Bearish Squeeze).
- Tính volume lot cho lệnh BUY/SELL dựa trên fixed risk (từ PDF "Tính vol giao dịch.pdf").
- Generate code Python để implement logic trong scanner.py.

## Cách sử dụng
- Gõ `/mt5-scanner` trong chat để kích hoạt.
- Ví dụ prompt: "Phân tích mẫu hình BUY từ dữ liệu dataframe df, dựa trên _check_bullish_squeeze."
- Hoặc: "Tính lot cho lệnh BUY XAUUSD với entry=5082, SL=5073, risk=200 USD."

## Logic BUY/SELL (từ BUY_SELL_LOGIC.md)
- **BUY (Bullish Squeeze)**: Kiểm tra _check_bullish_squeeze(df) và nến cuối xanh (close > open). Điều kiện: Ascending support trendline (ít nhất 3 swing lows), giá nén trong band MA21, ATR squeeze < 0.7, score >=0.5.
- **BUY (Early Breakout)**: _check_early_breakout(df), nến mạnh breakout lên trên MA21.
- **SELL (Bearish Squeeze)**: _check_bearish_squeeze(df), nến cuối đỏ, score >=0.75 để tính lot/SL/TP.

## Công thức tính lot (từ Tính vol giao dịch.pdf)
Lot = Risk / (SL_points * Value_1_point)
- Value_1_point = Contract Size * Point Size.
- Ví dụ XAUUSD: Contract Size=100, Point Size=0.01 → Value_1_point=1 USD.
- Script tham chiếu: ./calculate_lot.py (chạy để tính lot tự động).

## Đối chiếu ảnh mẫu (assets) với logic
- Thư mục `assets/` chứa ảnh mẫu BUY/SELL (Screenshot_9 … Screenshot_23).
- File **ASSETS_MAPPING.md** (cùng thư mục skill) map từng ảnh → pattern và hàm scanner (`_check_bullish_squeeze`, `_check_bearish_squeeze`, `_check_early_breakout`, `_check_bearish_breakdown_support`).
- Cấu hình khuyến nghị để cảnh báo giống mẫu hình nhất: xem **scanner_config.json** (gốc dự án) với `MIN_BARS_IN_BAND_RATIO=1.0`, `SQUEEZE_BARS=5`, `BAND_TOLERANCE_ATR_RATIO=0.32`.

## Script hỗ trợ
Sử dụng script Python bên dưới để tính lot. Copy vào code nếu cần.

```python
def calculate_lot(risk_usd, sl_points, contract_size, point_size):
    value_1_point = contract_size * point_size
    lot = risk_usd / (sl_points * value_1_point)
    return round(lot, 2)

# Ví dụ cho XAUUSD BUY
risk = 200  # 2% tài khoản 10k USD
sl_points = 9  # Entry - SL
contract_size = 100
point_size = 0.01
print(calculate_lot(risk, sl_points, contract_size, point_size))  # Output: 22.22