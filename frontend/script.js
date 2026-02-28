(function () {
  function init() {
  console.log('[MT5 Scanner] init() started, DOM ready');
  const origin = window.location.origin;
  const API_BASE = (origin && origin.startsWith('http'))
    ? origin.replace(/:\d+$/, ':8000')
    : 'http://127.0.0.1:8000';
  const WS_URL = API_BASE.replace(/^http/, 'ws') + '/ws/scanner';

  let signals = [];
  let hotSignals = [];
  let hotSignalIds = new Set();
  let hasReceivedHotBefore = false;
  let scanStatus = { symbols_with_data: 0, total_checked: 0 };
  let autoTradeEnabled = false;
  let chartInstance = null;
  let chartCandleSeries = null;
  let chartMa21Series = null;
  let chartTrendlineSeries = null;
  let currentChartSymbol = null;
  let currentChartTf = 'M15';
  let chartUpdateInterval = null;
  let lastChartBarTime = null;

  const signalsBody = document.getElementById('signals-body');
  const watchlistBody = document.getElementById('watchlist-body');
  const chartModal = document.getElementById('chart-modal');
  const chartContainer = document.getElementById('chart-container');
  const chartNotes = document.getElementById('chart-notes');
  const chartError = document.getElementById('chart-error');
  const chartTitle = document.getElementById('chart-title');
  const closeChartBtn = document.getElementById('close-chart');
  const quickSymbol = document.getElementById('quick-symbol');
  const quickTf = document.getElementById('quick-tf');
  const quickChartBtn = document.getElementById('quick-chart-btn');
  const scanStatusEl = document.getElementById('scan-status');
  const historyBody = document.getElementById('history-body');
  const historyRefreshBtn = document.getElementById('history-refresh');
  const historyFilterSymbol = document.getElementById('history-filter-symbol');
  const historyFilterTf = document.getElementById('history-filter-tf');
  const positionsBody = document.getElementById('positions-body');
  const positionsRefreshBtn = document.getElementById('positions-refresh');
  const positionsMessage = document.getElementById('positions-message');

  if (!signalsBody || !watchlistBody) console.warn('[MT5 Scanner] Missing body elements signals-body or watchlist-body');

  const LOT_SL_PIPS = 20;
  const LOT_MAX_LOSS_USD = 20;

  function getPipValuePerLot(symbol, price) {
    if (!symbol || price == null || price <= 0) return null;
    const s = String(symbol).toUpperCase().replace(/\s/g, '');
    const isMini = /M$/.test(s);
    const contract = isMini ? 10000 : 100000;
    const sym = s.replace(/M$/, '');
    if (sym.endsWith('USD') && sym !== 'USDUSD') return contract * 0.0001;
    if (sym === 'USDJPY') return (contract * 0.01) / price;
    if (sym.startsWith('USD') && sym.length === 6 && !sym.includes('JPY')) return (contract * 0.0001) / price;
    if (sym.includes('JPY')) return (contract * 0.01) / price;
    return contract * 0.0001;
  }

  function getRecommendedLot(symbol, price, slPips, maxLossUSD) {
    if (slPips <= 0 || maxLossUSD <= 0) return null;
    const pipValue = getPipValuePerLot(symbol, price);
    if (pipValue == null) return null;
    const lot = maxLossUSD / (slPips * pipValue);
    return Math.round(lot * 100) / 100;
  }

  function formatLot(signal) {
    if (signal.lot_size != null) return Number(signal.lot_size).toFixed(2);
    const price = signal.last_price != null ? Number(signal.last_price) : (signal.entry_price != null ? Number(signal.entry_price) : null);
    if (price == null || price <= 0) return '—';
    const lot = getRecommendedLot(signal.symbol, price, LOT_SL_PIPS, LOT_MAX_LOSS_USD);
    return lot != null ? lot.toFixed(2) : '—';
  }

  function formatRisk(signal) {
    return signal.risk_usd != null ? signal.risk_usd : '—';
  }
  function formatTp(signal) {
    return signal.expected_profit_if_tp != null ? signal.expected_profit_if_tp : '—';
  }
  function formatSlPrice(signal) {
    return signal.sl_price != null ? signal.sl_price : '—';
  }
  function formatTpPrice(signal) {
    return signal.tp_price != null ? signal.tp_price : '—';
  }

  function formatTime(ts) {
    if (ts == null) return '—';
    const d = new Date(ts * 1000);
    return d.toLocaleString();
  }

  function formatDetectedAt(str) {
    if (!str) return '—';
    const d = new Date(str + 'Z');
    return isNaN(d.getTime()) ? str : d.toLocaleString();
  }

  function renderAllSignals() {
    if (!signalsBody) return;
    const emptyRow = '<tr><td colspan="14" class="empty-message">Chưa phát hiện pattern nào. Dữ liệu realtime từ MT5 mỗi 3 giây. Chỉ hiện khi thỏa điều kiện Bearish/Bullish Squeeze hoặc Cảnh báo sớm. Kiểm tra trạng thái bên dưới.</td></tr>';
    const rec = (s) => (s.recommendation != null ? s.recommendation : '—');
    const entry = (s) => (s.entry_price != null ? s.entry_price : '—');
    const recClass = (s) => (s.recommendation === 'MUA' ? ' rec-buy' : s.recommendation === 'BÁN' ? ' rec-sell' : '');
    signalsBody.innerHTML = signals.map(s => `
      <tr class="signal-row" data-symbol="${escapeHtml(s.symbol)}" data-tf="${escapeHtml(s.tf)}" title="Bấm để mở chart + trendline">
        <td>${escapeHtml(s.symbol)}</td>
        <td>${escapeHtml(s.tf)}</td>
        <td>${escapeHtml(s.pattern)}</td>
        <td class="${recClass(s)}">${escapeHtml(rec(s))}</td>
        <td>${entry(s)}</td>
        <td>${s.score}</td>
        <td>${s.last_price}</td>
        <td>${formatTime(s.timestamp)}</td>
        <td class="lot-cell">${formatLot(s)}</td>
        <td class="lot-cell">${formatRisk(s)}</td>
        <td class="lot-cell">${formatTp(s)}</td>
        <td>${formatSlPrice(s)}</td>
        <td>${formatTpPrice(s)}</td>
        <td><button type="button" class="btn-chart" data-symbol="${escapeHtml(s.symbol)}" data-tf="${escapeHtml(s.tf)}">Chart</button></td>
      </tr>
    `).join('') || emptyRow;
    signalsBody.querySelectorAll('.btn-chart').forEach(btn => {
      btn.addEventListener('click', function (e) { e.stopPropagation(); openChart(btn.dataset.symbol, btn.dataset.tf); });
    });
    signalsBody.querySelectorAll('.signal-row').forEach(row => {
      row.addEventListener('click', function () {
        openChart(row.dataset.symbol, row.dataset.tf);
      });
    });
  }

  function escapeHtml(s) {
    if (s == null) return '';
    const div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
  }

  function bestPerSymbol(list) {
    const bySymbol = {};
    list.forEach(s => {
    const key = s.symbol;
    if (!bySymbol[key] || (s.score > bySymbol[key].score)) bySymbol[key] = s;
    });
    return Object.values(bySymbol);
  }

  function renderWatchlist() {
    if (!watchlistBody) return;
    const best = bestPerSymbol(hotSignals);
    const rec = (s) => (s.recommendation != null ? s.recommendation : '—');
    const entry = (s) => (s.entry_price != null ? s.entry_price : '—');
    const recClass = (s) => (s.recommendation === 'MUA' ? ' rec-buy' : s.recommendation === 'BÁN' ? ' rec-sell' : '');
    watchlistBody.innerHTML = best.map(s => {
      const rowId = `wl-${s.symbol}-${s.tf}`;
      const flash = hotSignalIds.has(symbolTfKey(s.symbol, s.tf)) ? ' flash' : '';
      return `
      <tr class="watchlist-row${flash}" data-row-id="${rowId}">
        <td>${escapeHtml(s.symbol)}</td>
        <td>${escapeHtml(s.tf)}</td>
        <td>${escapeHtml(s.pattern)}</td>
        <td class="${recClass(s)}">${escapeHtml(rec(s))}</td>
        <td>${entry(s)}</td>
        <td>${s.score}</td>
        <td>${s.last_price}</td>
        <td>${formatTime(s.timestamp)}</td>
        <td class="lot-cell">${formatLot(s)}</td>
        <td class="lot-cell">${formatRisk(s)}</td>
        <td class="lot-cell">${formatTp(s)}</td>
        <td>${formatSlPrice(s)}</td>
        <td>${formatTpPrice(s)}</td>
        <td><button type="button" class="btn-chart" data-symbol="${escapeHtml(s.symbol)}" data-tf="${escapeHtml(s.tf)}">Chart</button></td>
      </tr>
    `;
    }).join('') || '<tr><td colspan="14" class="empty-message">Chưa có signal trong 15 phút gần nhất.</td></tr>';
    watchlistBody.querySelectorAll('.btn-chart').forEach(btn => {
      btn.addEventListener('click', () => openChart(btn.dataset.symbol, btn.dataset.tf));
    });
    watchlistBody.querySelectorAll('.watchlist-row').forEach(row => {
      row.addEventListener('animationend', () => row.classList.remove('flash'));
    });
  }

  function symbolTfKey(symbol, tf) {
    return symbol + '|' + tf;
  }

  const PROFIT_MIN_EDIT = 5;

  function loadPositions() {
    if (!positionsBody) return;
    fetch(API_BASE + '/positions')
      .then(function (r) { return r.ok ? r.json() : []; })
      .then(function (list) { renderPositions(list); })
      .catch(function () { renderPositions([]); });
  }

  function renderPositions(list) {
    if (!positionsBody) return;
    var emptyRow = '<tr><td colspan="9" class="empty-message">Chưa có lệnh mở (magic 202615).</td></tr>';
    if (!list || list.length === 0) {
      positionsBody.innerHTML = emptyRow;
      return;
    }
    positionsBody.innerHTML = list.map(function (p) {
      var profit = p.profit != null ? Number(p.profit) : 0;
      var canEdit = profit >= PROFIT_MIN_EDIT;
      var profitClass = profit >= 0 ? 'profit-positive' : 'profit-negative';
      var btn = canEdit
        ? '<button type="button" class="btn-partial-secure" data-ticket="' + p.ticket + '">Chốt ½ + SL breakeven</button>'
        : '<button type="button" class="btn-partial-secure" disabled title="Lãi cần ≥ $5">Chốt ½ + SL breakeven</button>';
      return '<tr>' +
        '<td>' + escapeHtml(String(p.ticket)) + '</td>' +
        '<td>' + escapeHtml(p.symbol) + '</td>' +
        '<td class="' + (p.type === 'BUY' ? 'rec-buy' : 'rec-sell') + '">' + escapeHtml(p.type) + '</td>' +
        '<td>' + (p.volume != null ? p.volume : '—') + '</td>' +
        '<td>' + (p.price_open != null ? p.price_open : '—') + '</td>' +
        '<td>' + (p.sl != null && p.sl !== 0 ? p.sl : '—') + '</td>' +
        '<td>' + (p.tp != null && p.tp !== 0 ? p.tp : '—') + '</td>' +
        '<td class="lot-cell ' + profitClass + '">' + (p.profit != null ? p.profit : '—') + '</td>' +
        '<td>' + btn + '</td></tr>';
    }).join('');
    positionsBody.querySelectorAll('.btn-partial-secure:not([disabled])').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var ticket = parseInt(btn.dataset.ticket, 10);
        if (!ticket) return;
        btn.disabled = true;
        if (positionsMessage) {
          positionsMessage.style.display = 'block';
          positionsMessage.textContent = 'Đang xử lý...';
          positionsMessage.className = 'settings-message';
        }
        fetch(API_BASE + '/positions/' + ticket + '/partial-secure', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ profit_min: PROFIT_MIN_EDIT, close_ratio: 0.5 })
        })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            if (positionsMessage) {
              positionsMessage.style.display = 'block';
              positionsMessage.textContent = data.ok ? data.message : (data.error || 'Lỗi');
              positionsMessage.className = 'settings-message ' + (data.ok ? 'ok' : 'error');
            }
            loadPositions();
          })
          .catch(function (err) {
            if (positionsMessage) {
              positionsMessage.style.display = 'block';
              positionsMessage.textContent = 'Lỗi kết nối: ' + (err && err.message ? err.message : 'Unknown');
              positionsMessage.className = 'settings-message error';
            }
            loadPositions();
          });
      });
    });
  }

  function loadHistory() {
    const symbol = (historyFilterSymbol && historyFilterSymbol.value || '').trim();
    const tf = (historyFilterTf && historyFilterTf.value || '').trim();
    let url = API_BASE + '/history?limit=200';
    if (symbol) url += '&symbol=' + encodeURIComponent(symbol);
    if (tf) url += '&tf=' + encodeURIComponent(tf);
    fetch(url)
      .then(r => r.ok ? r.json() : [])
      .then(rows => renderHistory(rows))
      .catch(() => renderHistory([]));
  }

  function formatLotHistory(r) {
    const price = r.last_price != null ? Number(r.last_price) : null;
    if (price == null || price <= 0) return '—';
    const lot = getRecommendedLot(r.symbol, price, LOT_SL_PIPS, LOT_MAX_LOSS_USD);
    return lot != null ? lot.toFixed(2) : '—';
  }

  function renderHistory(rows) {
    if (!historyBody) return;
    const emptyRow = '<tr><td colspan="12" class="empty-message">Chưa có bản ghi lịch sử.</td></tr>';
    const riskStr = (r) => (r.risk_usd != null ? r.risk_usd : '—');
    const tpStr = (r) => (r.expected_profit_if_tp != null ? r.expected_profit_if_tp : '—');
    const slStr = (r) => (r.sl_price != null ? r.sl_price : '—');
    const tpPriceStr = (r) => (r.tp_price != null ? r.tp_price : '—');
    historyBody.innerHTML = (rows && rows.length)
      ? rows.map(r => `
          <tr>
            <td>${formatDetectedAt(r.detected_at)}</td>
            <td>${escapeHtml(r.symbol)}</td>
            <td>${escapeHtml(r.tf)}</td>
            <td>${escapeHtml(r.pattern)}</td>
            <td>${r.score != null ? r.score : '—'}</td>
            <td>${r.last_price != null ? r.last_price : '—'}</td>
            <td class="lot-cell">${formatLotHistory(r)}</td>
            <td class="lot-cell">${riskStr(r)}</td>
            <td class="lot-cell">${tpStr(r)}</td>
            <td>${slStr(r)}</td>
            <td>${tpPriceStr(r)}</td>
            <td><button type="button" class="btn-chart" data-symbol="${escapeHtml(r.symbol)}" data-tf="${escapeHtml(r.tf)}">Chart</button></td>
          </tr>
        `).join('')
      : emptyRow;
    historyBody.querySelectorAll('.btn-chart').forEach(btn => {
      btn.addEventListener('click', () => openChart(btn.dataset.symbol, btn.dataset.tf));
    });
  }

  function updateScanStatus() {
    const total = scanStatus.total_checked || 0;
    const withData = scanStatus.symbols_with_data || 0;
    if (total === 0) {
      scanStatusEl.textContent = 'Đang chờ dữ liệu scan…';
      scanStatusEl.className = 'scan-status';
      scanStatusEl.innerHTML = scanStatusEl.textContent;
      return;
    }
    if (withData === 0) {
      scanStatusEl.textContent = 'Không nhận được dữ liệu từ MT5. Kiểm tra: MT5 đã mở, đăng nhập tài khoản, các cặp (EURUSD, XAUUSD…) có trong Market Watch.';
      scanStatusEl.className = 'scan-status scan-status-error';
      scanStatusEl.innerHTML = scanStatusEl.textContent;
      return;
    }
    const tradeTimeframes = ['M15', 'M30', 'H1'];
    const hasTradeableSignals = signals.length > 0 && signals.some(s => tradeTimeframes.indexOf(s.tf) >= 0);
    const line1 = 'Realtime: đã nhận dữ liệu ' + withData + '/' + total + ' cặp (symbol×TF). Pattern chỉ hiện khi thỏa điều kiện Squeeze.';
    if (hasTradeableSignals && !autoTradeEnabled) {
      scanStatusEl.innerHTML = line1 + '<br><span class="scan-status-hint">Có tín hiệu MUA/BÁN nhưng <strong>chưa bật đặt lệnh tự động</strong>. Vào tab Settings → bật <strong>AUTO_TRADE_ENABLED</strong> → Lưu.</span>';
    } else {
      scanStatusEl.textContent = line1;
    }
    scanStatusEl.className = 'scan-status scan-status-ok';
  }

  function onWsMessage(data) {
    const prevHotIds = new Set(hotSignals.map(s => symbolTfKey(s.symbol, s.tf)));
    signals = data.signals || [];
    hotSignals = data.hot_signals || [];
    scanStatus = data.scan_status || scanStatus;
    autoTradeEnabled = !!data.auto_trade_enabled;
    const nextHotIds = new Set(hotSignals.map(s => symbolTfKey(s.symbol, s.tf)));
    hotSignalIds = hasReceivedHotBefore
      ? new Set([...nextHotIds].filter(id => !prevHotIds.has(id)))
      : new Set();
    hasReceivedHotBefore = true;
    updateScanStatus();
    renderAllSignals();
    renderWatchlist();
  }

  function openChart(symbol, tf) {
    if (!symbol || !symbol.trim()) return;
    symbol = symbol.trim();
    tf = tf || 'M15';
    var s = (signals && signals.length) ? signals.find(function (x) { return x.symbol === symbol && x.tf === tf; }) : null;
    var q = 'chart.html?symbol=' + encodeURIComponent(symbol) + '&tf=' + encodeURIComponent(tf);
    if (s && s.entry_price != null && s.sl_price != null && s.tp_price != null) {
      q += '&entry=' + encodeURIComponent(String(s.entry_price)) + '&sl=' + encodeURIComponent(String(s.sl_price)) + '&tp=' + encodeURIComponent(String(s.tp_price)) + '&side=' + (s.recommendation === 'BÁN' ? 'sell' : 'buy');
    }
    q += '&api=' + encodeURIComponent(API_BASE);
    window.open(q, '_blank');
  }

  function refreshChart() {
    if (!currentChartSymbol || !chartInstance || !chartCandleSeries) return;
    const baseUrl = API_BASE + '/rates?symbol=' + encodeURIComponent(currentChartSymbol) + '&tf=' + encodeURIComponent(currentChartTf);
    fetch(baseUrl)
      .then(r => r.ok ? r.json() : Promise.reject())
      .then(bars => {
        if (!bars || bars.length === 0) return;
        const ohlc = bars.map(b => ({
          time: Number(b.time),
          open: Number(b.open),
          high: Number(b.high),
          low: Number(b.low),
          close: Number(b.close)
        }));
        const lastBar = ohlc[ohlc.length - 1];
        if (lastChartBarTime === lastBar.time) {
          chartCandleSeries.update(lastBar);
          if (chartMa21Series && ohlc.length >= 21) {
            const closes = ohlc.map(c => c.close);
            const sum = closes.slice(-21).reduce((a, b) => a + b, 0);
            chartMa21Series.update({ time: lastBar.time, value: sum / 21 });
          }
        } else {
          lastChartBarTime = lastBar.time;
          chartCandleSeries.setData(ohlc);
          const closes = ohlc.map(c => c.close);
          const ma21Data = [];
          for (let i = 0; i < ohlc.length; i++) {
            if (i >= 20) {
              const sum = closes.slice(i - 20, i + 1).reduce((a, b) => a + b, 0);
              ma21Data.push({ time: ohlc[i].time, value: sum / 21 });
            }
          }
          if (chartMa21Series && ma21Data.length) chartMa21Series.setData(ma21Data);
        }
        if (chartInstance.timeScale().scrollToRealTime) chartInstance.timeScale().scrollToRealTime();
      })
      .catch(() => {});
  }

  function closeChart() {
    chartModal.setAttribute('aria-hidden', 'true');
    chartModal.classList.remove('open');
    if (chartUpdateInterval) {
      clearInterval(chartUpdateInterval);
      chartUpdateInterval = null;
    }
  }

  if (closeChartBtn) closeChartBtn.addEventListener('click', closeChart);
  if (chartModal) chartModal.addEventListener('click', function (e) { if (e.target === chartModal) closeChart(); });

  document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
      tab.classList.add('active');
      const id = 'panel-' + tab.dataset.tab;
      document.getElementById(id).classList.add('active');
      if (tab.dataset.tab === 'history') loadHistory();
      if (tab.dataset.tab === 'settings') loadSettings();
      if (tab.dataset.tab === 'positions') loadPositions();
    });
  });

  if (positionsRefreshBtn) positionsRefreshBtn.addEventListener('click', loadPositions);

  const SETTING_KEYS = ['LOOKBACK_BARS', 'TRENDLINE_BARS', 'SQUEEZE_BARS', 'ATR_SQUEEZE_RATIO', 'SLOPE_MIN_ABS', 'MA21_NEAR_ATR_RATIO', 'BAND_TOLERANCE_ATR_RATIO', 'MIN_BARS_IN_BAND_RATIO', 'BAND_CENTER_MIN_RATIO', 'HOT_WINDOW_MINUTES', 'AUTO_TRADE_ENABLED', 'AUTO_TRADE_LOT', 'AUTO_TRADE_LOT_MULTIPLIER', 'AUTO_TRADE_SL_PIPS', 'AUTO_TRADE_TP_PIPS', 'MAX_OPEN_POSITIONS', 'AUTO_TRADE_COOLDOWN_MINUTES', 'FIXED_RISK_USD', 'FIXED_PROFIT_USD', 'SL_MULTIPLIER', 'AUTO_TAKE_PROFIT_ENABLED', 'AUTO_TAKE_PROFIT_MIN_USD'];
  const FLOAT_KEYS = ['ATR_SQUEEZE_RATIO', 'SLOPE_MIN_ABS', 'MA21_NEAR_ATR_RATIO', 'BAND_TOLERANCE_ATR_RATIO', 'MIN_BARS_IN_BAND_RATIO', 'BAND_CENTER_MIN_RATIO', 'AUTO_TRADE_LOT', 'AUTO_TRADE_LOT_MULTIPLIER', 'FIXED_RISK_USD', 'FIXED_PROFIT_USD', 'SL_MULTIPLIER', 'AUTO_TAKE_PROFIT_MIN_USD'];
  const DEFAULT_SETTINGS = { LOOKBACK_BARS: 80, TRENDLINE_BARS: 6, SQUEEZE_BARS: 5, ATR_SQUEEZE_RATIO: 0.55, SLOPE_MIN_ABS: 0.00005, MA21_NEAR_ATR_RATIO: 0.5, BAND_TOLERANCE_ATR_RATIO: 0.15, MIN_BARS_IN_BAND_RATIO: 1, BAND_CENTER_MIN_RATIO: 0.15, HOT_WINDOW_MINUTES: 15, AUTO_TRADE_ENABLED: false, AUTO_TRADE_LOT: 10, AUTO_TRADE_LOT_MULTIPLIER: 1, AUTO_TRADE_SL_PIPS: 10, AUTO_TRADE_TP_PIPS: 30, MAX_OPEN_POSITIONS: 3, AUTO_TRADE_COOLDOWN_MINUTES: 60, FIXED_RISK_USD: 20, FIXED_PROFIT_USD: 15, SL_MULTIPLIER: 1.5, AUTO_TAKE_PROFIT_ENABLED: false, AUTO_TAKE_PROFIT_MIN_USD: 5 };
  const settingsMessage = document.getElementById('settings-message');

  function fillSettingsForm(data) {
    SETTING_KEYS.forEach(k => {
      const el = document.getElementById('set-' + k);
      if (!el) return;
      if (el.type === 'checkbox') {
        el.checked = !!data[k];
      } else if (data[k] != null) {
        el.value = data[k];
      }
    });
  }

  function loadSettings() {
    fetch(API_BASE + '/settings')
      .then(r => r.ok ? r.json() : {})
      .then(data => {
        fillSettingsForm(data);
        if (settingsMessage) { settingsMessage.style.display = 'none'; }
      })
      .catch(() => {
        if (settingsMessage) { settingsMessage.textContent = 'Không tải được cấu hình.'; settingsMessage.style.display = 'block'; settingsMessage.className = 'settings-message error'; }
      });
  }

  function saveSettings() {
    console.log('[Settings] saveSettings() called');
    var btn = document.getElementById('settings-save');
    var msgEl = document.getElementById('settings-message');
    if (btn) { btn.disabled = true; btn.textContent = 'Đang lưu...'; }
    if (msgEl) {
      msgEl.style.display = 'block';
      msgEl.textContent = 'Đang gửi cài đặt lên server...';
      msgEl.className = 'settings-message';
      msgEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
    var body = {};
    SETTING_KEYS.forEach(function(k) {
      var el = document.getElementById('set-' + k);
      if (!el) return;
      if (k === 'AUTO_TRADE_ENABLED' || k === 'AUTO_TAKE_PROFIT_ENABLED') {
        body[k] = el.checked;
        return;
      }
      var v = el.value;
      if (v === '' || v == null) return;
      if (FLOAT_KEYS.indexOf(k) >= 0) {
        var num = parseFloat(v);
        if (!isNaN(num)) body[k] = num;
      } else {
        var num = parseInt(v, 10);
        if (!isNaN(num)) body[k] = num;
      }
    });
    if (Object.keys(body).length === 0) {
      var defs = DEFAULT_SETTINGS;
      SETTING_KEYS.forEach(function(k) { body[k] = defs[k]; });
    }
    console.log('[Settings] POST', API_BASE + '/settings', 'body:', body);
    fetch(API_BASE + '/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    })
      .then(function(r) {
        return r.text().then(function(text) {
          var data;
          try { data = text ? JSON.parse(text) : {}; } catch (e) { data = {}; }
          console.log('[Settings] Response ok=', r.ok, 'status=', r.status, 'data=', data);
          return { ok: r.ok, data: data };
        });
      })
      .then(function(result) {
        var ok = result.ok, data = result.data;
        console.log('[Settings] Result ok=', ok, 'data=', data);
        if (btn) { btn.disabled = false; btn.textContent = 'Lưu'; }
        if (msgEl) msgEl.style.display = 'block';
        if (ok) {
          if (msgEl) {
            msgEl.textContent = 'Đã lưu. Lần quét tiếp theo sẽ dùng cấu hình mới.';
            msgEl.className = 'settings-message ok';
            msgEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
          }
          fillSettingsForm(data);
          var riskEl = document.getElementById('config-risk-usd');
          var tpEl = document.getElementById('config-tp-usd');
          if (riskEl && data.FIXED_RISK_USD != null) riskEl.value = data.FIXED_RISK_USD;
          if (tpEl && data.FIXED_PROFIT_USD != null) tpEl.value = data.FIXED_PROFIT_USD;
          updateRiskTpHeaders(data.FIXED_RISK_USD, data.FIXED_PROFIT_USD);
          alert('Đã thay đổi cài đặt thành công.');
        } else {
          if (msgEl) {
            msgEl.textContent = 'Lỗi: ' + (data.error || 'Không lưu được');
            msgEl.className = 'settings-message error';
            msgEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
          }
          alert('Lỗi: ' + (data.error || 'Không lưu được. Kiểm tra backend.'));
        }
      })
      .catch(function(err) {
        console.log('[Settings] Fetch error:', err);
        if (btn) { btn.disabled = false; btn.textContent = 'Lưu'; }
        var errEl = document.getElementById('settings-message');
        if (errEl) {
          errEl.textContent = 'Không kết nối được tới backend. Kiểm tra backend đã chạy chưa (port 8000).';
          errEl.className = 'settings-message error';
          errEl.style.display = 'block';
          errEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
        alert('Không kết nối được tới backend.\nKiểm tra: Backend đã chạy chưa? (http://127.0.0.1:8000)');
      });
  }

  function updateRiskTpHeaders(risk, tp) {
    var r = risk != null ? Number(risk) : 20;
    var t = tp != null ? Number(tp) : 15;
    ['all', 'watchlist'].forEach(function (panel) {
      var thr = document.getElementById('th-risk-' + panel);
      var tht = document.getElementById('th-tp-' + panel);
      if (thr) thr.textContent = 'Risk $' + r;
      if (tht) tht.textContent = 'TP $' + t;
    });
  }

  function loadConfigMoney() {
    fetch(API_BASE + '/settings')
      .then(function (r) { return r.ok ? r.json() : {}; })
      .then(function (data) {
        var riskEl = document.getElementById('config-risk-usd');
        var tpEl = document.getElementById('config-tp-usd');
        var risk = data.FIXED_RISK_USD != null ? data.FIXED_RISK_USD : 20;
        var tp = data.FIXED_PROFIT_USD != null ? data.FIXED_PROFIT_USD : 15;
        if (riskEl) riskEl.value = risk;
        if (tpEl) tpEl.value = tp;
        updateRiskTpHeaders(risk, tp);
      })
      .catch(function () {});
  }

  function saveConfigMoney() {
    var riskEl = document.getElementById('config-risk-usd');
    var tpEl = document.getElementById('config-tp-usd');
    var msgEl = document.getElementById('config-money-msg');
    var risk = riskEl ? parseFloat(riskEl.value) : NaN;
    var tp = tpEl ? parseFloat(tpEl.value) : NaN;
    if (isNaN(risk) || risk < 1) { if (msgEl) { msgEl.textContent = 'Cắt lỗ (Risk) phải ≥ 1.'; msgEl.className = 'config-money-msg error'; } return; }
    if (isNaN(tp) || tp < 0) { if (msgEl) { msgEl.textContent = 'Lãi (TP) phải ≥ 0.'; msgEl.className = 'config-money-msg error'; } return; }
    if (msgEl) { msgEl.textContent = 'Đang lưu...'; msgEl.className = 'config-money-msg'; }
    fetch(API_BASE + '/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ FIXED_RISK_USD: risk, FIXED_PROFIT_USD: tp })
    })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error('Lỗi lưu')); })
      .then(function () {
        updateRiskTpHeaders(risk, tp);
        if (msgEl) { msgEl.textContent = 'Đã cập nhật. Lần quét tiếp theo dùng giá trị mới.'; msgEl.className = 'config-money-msg ok'; }
      })
      .catch(function () {
        if (msgEl) { msgEl.textContent = 'Không lưu được. Kiểm tra backend (port 8000).'; msgEl.className = 'config-money-msg error'; }
      });
  }

  loadConfigMoney();
  var configMoneySave = document.getElementById('config-money-save');
  if (configMoneySave) configMoneySave.addEventListener('click', saveConfigMoney);

  window.saveSettingsClick = saveSettings;
  window.loadSettingsClick = loadSettings;
  document.body.addEventListener('click', function (e) {
    if (!e || !e.target) return;
    var id = e.target.id;
    if (id === 'settings-save' || id === 'settings-load' || id === 'settings-reset-defaults') {
      console.log('[Settings] Click detected, id=', id);
    }
    if (id === 'settings-save') { e.preventDefault(); saveSettings(); return; }
    if (id === 'settings-load') { e.preventDefault(); loadSettings(); return; }
    if (id === 'settings-reset-defaults') { e.preventDefault(); fillSettingsForm(DEFAULT_SETTINGS); if (settingsMessage) { settingsMessage.textContent = 'Đã điền giá trị mặc định. Bấm Lưu để áp dụng.'; settingsMessage.className = 'settings-message ok'; settingsMessage.style.display = 'block'; } }
  });
  console.log('[Settings] Event listener attached on document.body. API_BASE=', API_BASE, 'window.saveSettingsClick=', typeof window.saveSettingsClick);

  if (historyRefreshBtn) historyRefreshBtn.addEventListener('click', loadHistory);
  if (historyFilterSymbol) historyFilterSymbol.addEventListener('change', loadHistory);
  if (historyFilterSymbol) historyFilterSymbol.addEventListener('keyup', function (e) { if (e.key === 'Enter') loadHistory(); });
  if (historyFilterTf) historyFilterTf.addEventListener('change', loadHistory);

  if (quickChartBtn && quickSymbol) {
    quickChartBtn.addEventListener('click', function () {
      var sym = quickSymbol.value;
      if (sym) openChart(sym, quickTf ? quickTf.value : 'M15');
    });
  }

  if (quickSymbol) {
    fetch(API_BASE + '/symbols')
      .then(function (r) { return r.ok ? r.json() : []; })
      .then(function (symbols) {
        if (!quickSymbol) return;
        quickSymbol.innerHTML = '<option value="">-- Chọn cặp --</option>' +
          (symbols.map(function (s) { return '<option value="' + escapeHtml(s) + '">' + escapeHtml(s) + '</option>'; }).join(''));
      })
      .catch(function () {});
  }

  var ws = null;
  function connect() {
    try {
      ws = new WebSocket(WS_URL);
      ws.onmessage = function (e) {
        try {
          var data = JSON.parse(e.data);
          onWsMessage(data);
        } catch (err) {}
      };
      ws.onclose = function () { setTimeout(connect, 3000); };
    } catch (err) { console.warn('[MT5 Scanner] WebSocket connect error', err); setTimeout(connect, 3000); }
  }
  connect();
  console.log('[MT5 Scanner] init() finished');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
  console.log('[MT5 Scanner] script.js loaded, readyState=', document.readyState);
})();
