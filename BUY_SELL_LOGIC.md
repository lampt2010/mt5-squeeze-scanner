# Logic dự đoán BUY / SELL — mt5-squeeze-scanner

Tài liệu mô tả cách backend **tự động** quét từng cặp tiền theo từng khung thời gian (M15, M30, H1) và đưa vào khuyến nghị **MUA (BUY)** hoặc **BÁN (SELL)** dựa trên dữ liệu nến từ MT5.

**Code tham chiếu:** `backend/scanner.py`, cấu hình: `backend/models.py`, `backend/config.py`, `scanner_config.json`.

---

## 1. Tổng quan

| Khuyến nghị | Nguồn tín hiệu | Điều kiện vào danh sách |
|-------------|----------------|--------------------------|
| **BUY (MUA)** | Mẫu 2 Bullish Squeeze | `_check_bullish_squeeze(df)` thỏa **và** nến cuối xanh (`close > open`). |
| **BUY (MUA)** | Cảnh báo sớm | `_check_early_breakout(df)` thỏa (đã bao gồm điều kiện nến cuối xanh). |
| **SELL (BÁN)** | Mẫu 1 Bearish Squeeze | `_check_bearish_squeeze(df)` thỏa **và** nến cuối đỏ (`close < open`). |
| **SELL (BÁN)** | Mẫu 3 Phá vỡ support | `_check_bearish_breakdown_support(df)` thỏa: trendline vẽ **bằng đáy nến** (support dốc lên), giá **phá xuống dưới** → BÁN. |

- Mỗi **(symbol, timeframe)** được quét độc lập; một cặp có thể xuất hiện nhiều lần (M15, M30, H1).
- Khung thời gian dùng: **M15, M30, H1** (không dùng M1, M5, M10 cho khuyến nghị).
- **200 nến** chỉ dùng cho API/chart (vẽ UI); **xác định trend** chỉ dùng **TREND_LOOKBACK_BARS** (mặc định **15**) nến gần nhất. **Chỉ dùng nến đã đóng:** nến cuối (đang hình thành) từ MT5 được bỏ qua khi kiểm tra pattern, tránh cảnh báo khi giá đã phá band.
- Với SELL: **Mẫu 1** chỉ gắn lot/SL/TP khi score ≥ 0.75; **Mẫu 3** (phá vỡ support) luôn tính lot/SL/TP nếu có. Nếu không tính được lot hoặc lot < min thì không đưa vào.

---

## 2. Định nghĩa dùng chung

### 2.1 ATR(14)

- Average True Range 14 nến: `TR = max(high-low, |high-prev_close|, |low-prev_close|)`, sau đó lấy trung bình 14 kỳ.

### 2.2 MA21

- Trung bình 21 nến: MA21 tại nến `i` = trung bình cộng `close` của 21 nến `[i-20, i]`. Cần ít nhất 21 nến.

### 2.3 Swing / Peak (scipy)

- **Đỉnh (peak):** `scipy.signal.find_peaks(high, distance=5)` — đỉnh cục bộ, hai đỉnh liền kề cách ít nhất 5 nến.
- **Đáy (trough):** `scipy.signal.find_peaks(-low, distance=5)` — đáy cục bộ, hai đáy cách ít nhất 5 nến.

### 2.4 Nến mạnh (cảnh báo sớm)

- **Nến xanh mạnh (bullish):** `close > open` và **body ≥ 50%** range nến (`body = close - open`, `range = high - low`).
- **Nến đỏ mạnh (bearish):** `close < open` và **body ≥ 50%** range nến (`body = open - close`).

### 2.5 Tham số cấu hình (mặc định từ `models.py`)

| Tham số | Mặc định | Ý nghĩa |
|---------|----------|--------|
| `LOOKBACK_BARS` | 80 | (Legacy / dự phòng) Số nến tối đa lưu. |
| **`TREND_LOOKBACK_BARS`** | **15** | **Số nến gần nhất dùng để xác định trend (Mẫu 1 Bearish, Mẫu 2 Bullish).** Chỉ cửa sổ này dùng cho peak/trendline/squeeze; API/chart vẫn lấy **200 nến** để vẽ. |
| `TRENDLINE_BARS` | 6 | (Không dùng trong logic) Trendline = **đường nối đỉnh đầu và đỉnh cuối** (bearish) hoặc **đáy đầu và đáy cuối** (bullish) trong cửa sổ — hai điểm có thể xa nhau, không bắt buộc các bar gần nhau. |
| `SQUEEZE_BARS` | 5 | Cửa sổ nến “squeeze” (nến nhỏ, ít biến động). |
| `ATR_SQUEEZE_RATIO` | 0.55 | Trung bình \|body\| nến squeeze < ATR × tỷ lệ này. |
| `SLOPE_MIN_ABS` | 0.00005 | Slope tối thiểu (trendline không quá ngang). |
| `MA21_NEAR_ATR_RATIO` | 0.5 | Nến cuối: \|close - MA21\| ≤ ATR × tỷ lệ. |
| `BAND_TOLERANCE_ATR_RATIO` | 0.15 | Band hẹp: nến phải thật sự nằm giữa trendline và MA21. |
| `MIN_PEAKS` | 3 | Số peak (đỉnh/đáy) tối thiểu để vẽ trendline. |
| `MIN_BARS_IN_BAND_RATIO` | 1.0 | **100%** nến squeeze phải nằm trong band (đúng mẫu). |
| `BAND_CENTER_MIN_RATIO` | 0.15 | Nến phải nằm **giữa** band: close cách mỗi biên (trendline, MA21) ít nhất 15% độ rộng band — không dính sát biên. |

Có thể ghi đè bằng `scanner_config.json` (xem `backend/config.py`).

---

## 3. SELL (BÁN) — Mẫu 1: Bearish Squeeze

**Hàm:** `_check_bearish_squeeze(df)`  
**Ý tưởng:** Xu hướng giảm (đỉnh sau thấp hơn đỉnh trước), giá bị “siết” trong dải hẹp giữa trendline kháng cự và MA21, nến cuối gần MA21 → khuyến nghị BÁN.

### 3.1 Điều kiện (theo thứ tự)

1. **Đủ dữ liệu:** `len(df) ≥ max(21 + SQUEEZE_BARS, TREND_LOOKBACK_BARS) + 1`. **Chỉ dùng nến đã đóng:** nến cuối từ MT5 (nến đang hình thành) bị bỏ qua; cửa sổ trend và squeeze được tính trên các nến đã đóng để tránh cảnh báo khi giá đã phá band.

2. **Trendline resistance giảm dần:**
   - Tìm **đỉnh** bằng `find_peaks(high, distance=5)`.
   - Cần ít nhất `MIN_PEAKS` đỉnh. Trendline = **đường thẳng nối đỉnh đầu tiên và đỉnh cuối cùng** trong cửa sổ (hai điểm có thể xa nhau, giống kéo trend tay).
   - Slope phải **âm**: `slope ≤ -SLOPE_MIN_ABS`.

3. **Squeeze (nến co cụm):**
   - Cửa sổ **SQUEEZE_BARS** nến cuối.
   - Trung bình \|body\| của các nến trong cửa sổ < **ATR_SQUEEZE_RATIO × ATR(14)**.

4. **Thân nến (body) nằm trong band trendline–MA21 (và nằm giữa band):**
   - Với mỗi nến trong cửa sổ squeeze: **thân nến** (body = từ min(open,close) đến max(open,close)) phải nằm trong band: `body_high ≤ trendline + tol` và `body_low ≥ MA21 - tol`. Không dùng râu (high/low) — nếu chỉ râu chạm band mà thân nằm ngoài thì không đạt.
   - **100%** nến squeeze phải thỏa (**MIN_BARS_IN_BAND_RATIO = 1.0**).
   - **Nến phải nằm giữa band:** với mỗi nến, `close` phải nằm trong khoảng [MA21 + 15%×độ_rộng_band, trendline − 15%×độ_rộng_band] (**BAND_CENTER_MIN_RATIO**), tức không được dính sát trendline hay MA21 — đúng mẫu “các nến con nằm gần như giữa không gian giữa trendline và M21”.

5. **Nến cuối gần MA21:** \|close - MA21\| ≤ **MA21_NEAR_ATR_RATIO × ATR**.

Khi tất cả thỏa → trả về dict (pattern `MẪU_1_BEARISH`, score, last_price, ma21, trendline, slope, …). Trong `run_scan`, chỉ đưa vào danh sách SELL khi **thêm** điều kiện **nến đóng gần nhất đỏ** (`close < open` trên nến đã đóng, không dùng nến đang hình thành). Nếu **score ≥ 0.75** thì tính lot/SL/TP (fixed risk) và gắn vào signal; nếu không tính được hoặc lot < min thì bỏ qua signal đó.

---

## 4. BUY (MUA) — Mẫu 2: Bullish Squeeze

**Hàm:** `_check_bullish_squeeze(df)`  
**Ý tưởng:** Xu hướng tăng (đáy sau cao hơn đáy trước), giá bị “siết” trong dải hẹp giữa trendline hỗ trợ và MA21, nến cuối gần MA21 → khuyến nghị MUA.

### 4.1 Điều kiện (theo thứ tự)

1. **Đủ dữ liệu:** `len(df) ≥ max(21 + SQUEEZE_BARS, TREND_LOOKBACK_BARS)`. Cửa sổ trend = **TREND_LOOKBACK_BARS** (mặc định 15) nến cuối.

2. **Trendline support tăng dần:**
   - Tìm **đáy** bằng `find_peaks(-low, distance=5)`.
   - Cần ít nhất `MIN_PEAKS` đáy. Trendline = **đường thẳng nối đáy đầu tiên và đáy cuối cùng** trong cửa sổ (hai điểm có thể xa nhau).
   - Slope phải **dương**: `slope ≥ SLOPE_MIN_ABS`.

3. **Squeeze:** Cửa sổ **SQUEEZE_BARS** nến cuối, trung bình \|body\| < **ATR_SQUEEZE_RATIO × ATR(14)**.

4. **Thân nến (body) trong band và nằm giữa band:** Mỗi nến squeeze: **thân nến** (body) phải trong band: `body_low ≥ trendline - tol` và `body_high ≤ MA21 + tol`. **100%** nến thỏa. Thêm điều kiện **nến nằm giữa**: `close` trong [trendline + 15%×độ_rộng_band, MA21 − 15%×độ_rộng_band] (**BAND_CENTER_MIN_RATIO**).

5. **Nến cuối gần MA21:** \|close - MA21\| ≤ **MA21_NEAR_ATR_RATIO × ATR**.

Khi tất cả thỏa → trả về dict (pattern `MẪU_2_BULLISH`, …). Trong `run_scan`, chỉ đưa vào danh sách BUY khi **nến cuối xanh** (`close > open`).

---

## 4.5. SELL (BÁN) — Mẫu 3: Phá vỡ support (trendline vẽ bằng đáy nến)

**Hàm:** `_check_bearish_breakdown_support(df)`  
**Ý tưởng:** Trendline vẽ **bằng đáy nến** (support dốc lên). Khi giá **phá xuống dưới** đường support này → khuyến nghị **BÁN** (không nhầm với Mẫu 2 BUY: Mẫu 2 là giá còn nằm *giữa* support và MA21; Mẫu 3 là giá đã *phá vỡ* xuống dưới support).

### Điều kiện (theo thứ tự)

1. **Đủ dữ liệu**, chỉ dùng nến đã đóng (bỏ nến đang hình thành).
2. **Trendline support (đáy nến):** Tìm đáy bằng `find_peaks(-low, distance=5)`, nối đáy đầu và đáy cuối trong cửa sổ; slope **dương** (support dốc lên).
3. **Phá vỡ xuống:** Nến đóng gần nhất có `close < trendline` tại vị trí đó (giá đã nằm dưới support).
4. **Từng có giá trên support:** Trong vài nến trước đó có ít nhất một nến có giá trên/near trendline (đúng là breakdown, không phải giá luôn nằm dưới).

Khi thỏa → trả về pattern `MẪU_3_PHÁ_VỠ_SUPPORT`; trong `run_scan` đưa vào danh sách **BÁN**, tính lot/SL/TP (fixed risk) khi có.

---

## 5. BUY (MUA) — Cảnh báo sớm (Early Breakout)

**Hàm:** `_check_early_breakout(df)`  
**Ý tưởng:** Trendline **kháng cự giảm dần** (swing high), MA21 nằm dưới trendline, có vùng “siết nền” quanh điểm MA gần trendline, xuất hiện nến xanh mạnh đóng **trên** cả trendline và MA21 trong 2 nến gần nhất, và **nến mới nhất là nến xanh** → báo MUA sớm.

### 5.1 Định nghĩa phụ (trong code)

| Hằng số | Giá trị | Ý nghĩa |
|---------|---------|--------|
| `EARLY_MAX_TREND_BAR_DISTANCE` | 30 | Hai đỉnh swing vẽ trendline không cách quá 30 nến. |
| `EARLY_MA_NEAR_TRENDLINE_PCT` | 0.002 | MA21 và trendline “gần” nếu chênh < 0,2% (so với mid). |
| `EARLY_MA_BELOW_TRENDLINE_RATIO` | 0.9 | MA21 nằm **dưới** trendline ≥ 90% số nến (đoạn từ start trendline đến bar đang xét). |
| `EARLY_TIGHT_RANGE_PCT` | 0.004 | Vùng siết nền: range (high–low) của cửa sổ quanh điểm tiếp giáp < 0,4% giá. |
| `EARLY_TIGHT_RANGE_WINDOW_HALF` | 2 | Cửa sổ ±2 nến quanh điểm tiếp giáp để kiểm tra siết nền. |
| `EARLY_LOOKBACK_BARS` | 12 | Số nến gần cuối để quét tìm điểm MA gần trendline. |
| `EARLY_STRONG_CANDLE_WITHIN` | 2 | Nến xanh mạnh phải nằm trong **2 nến gần nhất**. |
| `BODY_RATIO_MIN` | 0.5 | Nến mạnh: body ≥ 50% range nến. |

### 5.2 Điều kiện (theo thứ tự)

1. **Trendline resistance giảm dần:** Vẽ từ 2 **swing high** (đỉnh cục bộ trong cửa sổ 5 nến mỗi bên), đỉnh mới **≤** đỉnh cũ, hai đỉnh cách nhau ≤ 30 nến. Slope < 0.

2. **MA21 gần trendline:** Trong **EARLY_LOOKBACK_BARS** nến gần cuối, tồn tại ít nhất một nến mà \|MA21 - trendline\| / mid < **EARLY_MA_NEAR_TRENDLINE_PCT**.

3. **MA21 nằm dưới trendline:** Từ bar bắt đầu trendline đến bar đang xét: tỷ lệ số nến có MA21 < trendline ≥ **EARLY_MA_BELOW_TRENDLINE_RATIO** (90%).

4. **Siết nền quanh điểm tiếp giáp:** Chọn “điểm tiếp giáp” là nến có \|MA21 − trendline\| nhỏ nhất trong đoạn từ start trendline đến bar đang xét. Trong cửa sổ ±2 nến quanh điểm đó: (high_max - low_min) / mid < **EARLY_TIGHT_RANGE_PCT** (0,4%).

5. **Nến xanh mạnh đóng trên trendline và MA21:** Có ít nhất một **nến xanh mạnh** trong **2 nến gần nhất**, với `close > trendline` và `close > MA21` tại thời điểm nến đó. Nến **ngay sau** không được là nến đỏ mạnh đóng **dưới** trendline (nếu có thì bỏ qua).

6. **Nến mới nhất xanh:** `close.iloc[-1] > open.iloc[-1]` (chỉ khuyến nghị BUY khi nến đóng cửa mới nhất là nến xanh).

Khi tất cả thỏa → trả về dict (pattern `CẢNH_BÁO_SỚM`, recommendation `MUA`, entry_price, …). Trong `run_scan` đây là signal riêng (có thể cùng symbol+TF với Mẫu 2 nếu cả hai thỏa).

---

## 6. Quy trình quét (`run_scan`)

1. Với mỗi **symbol** trong danh sách mặc định (hoặc danh sách truyền vào).
2. Với mỗi **timeframe** trong **M15, M30, H1**:
   - Lấy 200 nến từ MT5 (cho chart/UI). Nếu ít hơn `max(21+SQUEEZE_BARS, TREND_LOOKBACK_BARS)` → bỏ qua. Xác định trend chỉ dùng **TREND_LOOKBACK_BARS** (15) nến gần nhất.
   - Gọi `_check_bearish_squeeze(df)`, `_check_bullish_squeeze(df)`, `_check_early_breakout(df)`.
   - **Nếu có Bearish:** và nến cuối đỏ → thêm signal **SELL** (BÁN); nếu score ≥ 0.75 thì tính lot/SL/TP và gắn vào signal (nếu lot hợp lệ).
   - **Nếu có Bullish:** và nến cuối xanh → thêm signal **BUY** (MUA).
   - **Nếu có Cảnh báo sớm:** thêm signal **BUY** (MUA) (đã kiểm tra nến cuối xanh trong `_check_early_breakout`).
3. Signal “hot”: những signal có `timestamp` trong vòng **HOT_WINDOW_MINUTES** phút (mặc định 15).

---

## 7. Tính lot / SL / TP (fixed risk)

- **SELL:** `_compute_fixed_risk_sell(symbol, df, entry_price, cfg)`. SL = entry + ATR×SL_MULTIPLIER, TP tính theo FIXED_PROFIT_USD. Lot tính từ FIXED_RISK_USD. Nếu lot < volume_min thì không recommend (bỏ signal).
- **BUY:** `_compute_fixed_risk_buy(symbol, df, entry_price, cfg)`. SL = entry - ATR×SL_MULTIPLIER, TP theo FIXED_PROFIT_USD. Lot từ FIXED_RISK_USD.

Tham số: `FIXED_RISK_USD`, `FIXED_PROFIT_USD`, `SL_MULTIPLIER` (mặc định trong `models.py`, có thể chỉnh trong `scanner_config.json`).

---

## 8. File code liên quan

| File | Nội dung |
|------|----------|
| `backend/scanner.py` | Toàn bộ logic: `_check_bearish_squeeze`, `_check_bullish_squeeze`, `_check_early_breakout`, `_get_descending_resistance_trendline`, `_find_swing_highs`, `run_scan`, tính lot/SL/TP. |
| `backend/models.py` | Hằng số mặc định: LOOKBACK_BARS, TRENDLINE_BARS, SQUEEZE_BARS, ATR_SQUEEZE_RATIO, MIN_PEAKS, FIXED_RISK_USD, … |
| `backend/config.py` | Đọc/ghi `scanner_config.json`, `get_config()`. |
| `scanner_config.json` | Cấu hình runtime (ghi đè mặc định). |

---

*Tài liệu mô tả logic dự đoán BUY/SELL của dự án **mt5-squeeze-scanner**; tham số có thể chỉnh trong `scanner_config.json` hoặc `backend/models.py`.*
