# So sánh logic phát hiện mẫu hình: mt5-squeeze-scanner vs forex (mt5-service + frontend)

## Tổng quan

| Tiêu chí | **mt5-squeeze-scanner (backend)** | **forex (mt5-service + frontend)** |
|----------|-----------------------------------|-------------------------------------|
| **Nơi chạy logic** | Backend Python (scanner.py) | **Frontend** JavaScript (patternDetection.js) — mt5-service chỉ gửi dữ liệu, không detect pattern |
| **Số loại mẫu** | 2: Mẫu 1 Bearish, Mẫu 2 Bullish | 2 hàm: mẫu bullish đủ 6 bước + **cảnh báo sớm** (chỉ cảnh báo sớm được dùng cho badge trong UI) |
| **Dữ liệu vào** | OHLC từ MT5, 200 nến, 4 TF (M1, M10, M15, M30) | Candles M15 hoặc M30 từ websocket (mt5-service → server → client) |
| **Phạm vi** | Mỗi (symbol × TF) → tối đa 1 signal (best score) | Mỗi symbol có candles → chạy detect early; nhiều symbol có thể cùng match → nhiều badge |

---

## 1. Backend mt5-squeeze-scanner (`backend/scanner.py`)

### Mẫu 1 – Bearish Squeeze (cuối downtrend)

- **Trendline**: Đỉnh (peaks) từ `scipy.signal.find_peaks(high, distance=5)`. Cần **≥ 5 peaks**. Hồi quy tuyến tính trên `TRENDLINE_BARS` peaks cuối → slope **âm** (≤ -SLOPE_MIN_ABS).
- **Squeeze**: Cửa sổ **SQUEEZE_BARS** nến cuối. Trung bình |body| < **ATR_SQUEEZE_RATIO × ATR(14)**.
- **Ràng buộc chặt**: **Mọi nến** trong squeeze phải:
  - high ≤ trendline + 0.1×ATR
  - low ≥ MA21 - 0.1×ATR
- Nến cuối: |close - MA21| ≤ **MA21_NEAR_ATR_RATIO × ATR**.

Tham số mặc định: LOOKBACK_BARS=80, TRENDLINE_BARS=10, SQUEEZE_BARS=8, ATR_SQUEEZE_RATIO=0.4, SLOPE_MIN_ABS=0.0001, MA21_NEAR_ATR_RATIO=0.3.

### Mẫu 2 – Bullish Squeeze (cuối uptrend)

- **Trendline**: Đáy từ `find_peaks(-low, distance=5)`. Cần ≥ 5 đáy. Slope **dương** (≥ SLOPE_MIN_ABS).
- **Squeeze**: Cùng công thức body nhỏ so với ATR.
- **Ràng buộc**: Mọi nến trong squeeze: low ≥ trendline - tol, high ≤ MA21 + tol; nến cuối gần MA21.

### Đặc điểm

- **Rất chặt**: Nhiều điều kiện cùng lúc (số peaks, slope, cả cửa sổ squeeze nằm trong band trendline–MA21, ATR, MA21 near).
- **Mỗi (symbol, TF)** chỉ trả về **một** pattern (bear hoặc bull, score cao hơn).
- Quét **symbol × 4 TF**; chỉ cặp nào thỏa mới vào danh sách signal.

---

## 2. Forex – logic trong frontend (`frontend/src/utils/patternDetection.js`)

**mt5-service** chỉ thu thập tick, MA21, candles (M15 + M30) và gửi lên websocket-server. **Không có** code detect pattern. Toàn bộ “tìm mẫu hình” nằm ở **frontend**.

### 2.1. Cảnh báo sớm (early breakout) – đang dùng cho badge trong UI

- **Trendline resistance giảm dần**: Swing highs (cửa sổ 5), 2 đỉnh vẽ line, **hai đỉnh không quá xa** (MAX_TREND_BAR_DISTANCE = 30 nến).
- **MA21**: Phải nằm **dưới** trendline “gần như hoàn toàn” (≥ 90% số nến từ start trendline đến đoạn kiểm tra).
- **Điểm tiếp giáp**: MA21 và trendline “gần nhau” (< 0.2% giá). Trong vùng quanh điểm tiếp giáp (TIGHT_RANGE_WINDOW = 5 nến), range (high–low) < 0.4% giá → “siết nền”.
- **Tín hiệu**: Trong **2 nến gần nhất** có **ít nhất 1 nến xanh mạnh** (body ≥ 50% range nến) đóng trên **cả** trendline và MA21; nến kế tiếp không được là nến đỏ đóng dưới trendline (rejection).

Hàm: `detectEarlyBreakoutSignal(candles)`. UI (CandlestickChart.jsx) duyệt **mọi symbol** có đủ candles, gọi hàm này; symbol nào `matched: true` thì đưa vào list “Cảnh báo sớm” → **nhiều symbol = nhiều badge**.

### 2.2. Mẫu bullish đủ 6 bước (không dùng cho badge hiện tại)

- Bước 1: Trendline resistance giảm (swing highs).
- Bước 2: Có nến breakout (close trên trendline, nến xanh mạnh).
- Bước 3: Sau breakout có retest (chạm trendline hoặc MA21).
- Bước 4: Có vùng consolidation (range < 0.2% giá, 3–30 nến).
- Bước 5: MA21 nằm dưới/sát đáy consolidation, giá không thủng MA.
- Bước 6: Sau consolidation có nến phá lên mạnh; mẫu chỉ hợp lệ nếu nến phá lên nằm trong **RECENT_PATTERN_BARS** (15 nến) gần nhất.

Hàm: `detectBullishPattern(candles)`. Trong code hiện tại **không** dùng để tạo list badge “có mẫu hình”.

---

## 3. Tại sao phía forex (mt5-service + frontend) “tìm được nhiều mẫu hình” hơn?

1. **Định nghĩa “mẫu” đang dùng**
   - **Forex**: Mỗi **symbol** match “cảnh báo sớm” = 1 mẫu hiển thị. Có 20 cặp match → 20 badge.
   - **mt5-squeeze-scanner**: Mỗi **(symbol, TF)** tối đa 1 signal; điều kiện squeeze rất chặt nên ít (symbol, TF) thỏa → ít dòng trong bảng.

2. **Độ nhạy**
   - **Cảnh báo sớm**: Chỉ cần trendline giảm, MA21 dưới trendline, vùng siết quanh junction, **1 nến xanh mạnh trong 2 nến gần nhất** → dễ trigger.
   - **Squeeze backend**: Cần **cả cửa sổ 8 nến** thỏa body nhỏ và **nằm hoàn toàn** trong band trendline–MA21, cộng slope/peaks/ATR/MA21 near → khó thỏa hơn nhiều.

3. **Cách đếm**
   - **Forex**: Đếm theo **số symbol** có early signal (không phân biệt M15/M30 trong badge, chỉ 1 list symbol).
   - **mt5-squeeze-scanner**: Đếm **số (symbol, TF)** có squeeze; mỗi symbol có thể xuất hiện 1–4 lần (M1, M10, M15, M30).

4. **Logic trendline**
   - **Forex**: Swing high với khoảng cách tối đa 30 nến giữa 2 đỉnh; không dùng ATR cho band.
   - **Backend**: Peaks với distance=5, fit line trên 10 peaks; band = trendline ± 0.1×ATR, nến phải nằm **trong** band → chặt hơn.

5. **Thời điểm**
   - **Cảnh báo sớm**: Báo ngay khi có nến xanh mạnh trong **2 nến gần nhất** (rất sát realtime).
   - **Squeeze**: Cần 8 nến cuối thỏa squeeze + toàn bộ trong band → thường trễ hơn và ít lần thỏa hơn.

---

## 4. Gợi ý nếu muốn hai bên “cân bằng” hơn

- **Muốn mt5-squeeze-scanner tìm được nhiều hơn (giống cảnh báo sớm)**:
  - Thêm một “early” mode: chỉ cần trendline + MA21 gần nhau + vài nến siết + nến gần nhất đóng trên trendline/MA21, không bắt buộc cả 8 nến trong band.
  - Nới điều kiện: giảm TRENDLINE_BARS / SQUEEZE_BARS, tăng ATR_SQUEEZE_RATIO hoặc MA21_NEAR_ATR_RATIO (trong giới hạn config cho phép).

- **Muốn forex ít false positive hơn (gần backend hơn)**:
  - Thêm điều kiện ATR/body nhỏ trong vùng squeeze (tương tự backend).
  - Hoặc dùng thêm `detectBullishPattern` (mẫu đủ 6 bước) để chỉ highlight symbol vừa early vừa có mẫu hoàn chỉnh gần đây.

- **Thống nhất logic**:
  - Có thể port “cảnh báo sớm” từ JS sang Python trong mt5-squeeze-scanner để vừa giữ 2 mẫu squeeze, vừa thêm 1 loại signal early (và tùy chọn bật/tắt trong config).

---

## 5. Tóm tắt nhanh

| | mt5-squeeze-scanner backend | forex (pattern ở frontend) |
|--|-----------------------------|----------------------------|
| **Số “mẫu” hiển thị** | Ít (điều kiện chặt, từng symbol×TF) | Nhiều (early signal dễ match, đếm theo symbol) |
| **Loại** | 2: Bearish/Bullish Squeeze | 1 loại đang dùng: Cảnh báo sớm (MA21 + trendline + nến đu lên) |
| **Vị trí** | Python server | JavaScript trình duyệt |
| **Dữ liệu** | 200 nến, 4 TF | Candles M15/M30 từ mt5-service |

“mt5-service tìm được nhiều mẫu hình hơn” thực chất là **frontend forex** (dùng dữ liệu do mt5-service cung cấp) đang chạy **cảnh báo sớm** – điều kiện **dễ thỏa** và đếm theo **từng symbol** – nên số badge nhiều hơn so với số dòng signal **squeeze chặt** của backend mt5-squeeze-scanner.
