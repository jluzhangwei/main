  const TASK_DETAIL_BOOT = window.NETLOG_TASK_DETAIL_BOOTSTRAP || {};
  const LANG_Q = TASK_DETAIL_BOOT.lang || 'zh';
  const UI_LANG = LANG_Q;
  const TASK_ID = String(TASK_DETAIL_BOOT.taskId || '');
  const T = {
    progress: UI_LANG === 'en' ? 'Progress' : '进度',
    preparing: UI_LANG === 'en' ? 'preparing' : '准备中',
    startFailed: UI_LANG === 'en' ? 'start failed' : '启动失败',
    starting: UI_LANG === 'en' ? 'starting...' : '启动中...',
    running: UI_LANG === 'en' ? 'running' : '运行中',
    queryFailed: UI_LANG === 'en' ? 'query failed' : '查询失败',
    debugPending: UI_LANG === 'en' ? 'filtered.log not generated yet...' : 'filtered.log 尚未生成...',
    debugEmpty: UI_LANG === 'en' ? 'filtered.log is empty' : 'filtered.log 为空',
    selectDevice: UI_LANG === 'en' ? 'Select a device on the left.' : '请先在左侧选择设备。',
    noDevice: UI_LANG === 'en' ? 'No device' : '无设备',
    phaseDone: UI_LANG === 'en' ? 'Phase: completed' : '阶段: 完成',
    phaseFailed: UI_LANG === 'en' ? 'Phase: failed' : '阶段: 失败',
    estimateLoading: UI_LANG === 'en' ? 'Estimate: calculating...' : '分析预估：计算中...',
    estimateFailed: UI_LANG === 'en' ? 'Estimate failed' : '预估失败',
    estimateNotCalculated: UI_LANG === 'en' ? 'Estimate: not calculated' : '分析预估：未计算',
    estimateFailurePrefix: UI_LANG === 'en' ? 'Estimate: failed' : '分析预估：失败',
    noAnalysisToSave: UI_LANG === 'en' ? 'No analysis result to save' : '暂无可保存的分析结果',
    analysisFailed: UI_LANG === 'en' ? 'analysis failed' : '分析失败',
    currentModel: UI_LANG === 'en' ? 'Current model' : '当前模型',
    notStarted: UI_LANG === 'en' ? 'not started' : '未启动',
    noHistory: UI_LANG === 'en' ? 'No history yet.' : '暂无历史记录。',
    loadingHistory: UI_LANG === 'en' ? 'Loading history...' : '加载历史中...',
    latestLoaded: UI_LANG === 'en' ? 'loaded latest analysis' : '已加载最近一次分析',
    noSelectedDevices: UI_LANG === 'en' ? 'Select at least one device for AI analysis' : '请至少勾选一台设备用于 AI 分析',
    previewLoading: UI_LANG === 'en' ? 'Loading preview...' : '预览加载中...',
    previewFailed: UI_LANG === 'en' ? 'Preview failed' : '预览失败',
    previewNoUnits: UI_LANG === 'en' ? 'No preview units' : '没有可预览的单元',
    deviceFilterHint: UI_LANG === 'en' ? 'Press Enter to filter device list' : '输入后回车过滤设备列表',
    deviceFilterEmpty: UI_LANG === 'en' ? 'No matched devices' : '没有匹配的设备',
    timeRangeAll: UI_LANG === 'en' ? 'Showing full time range' : '显示完整时间范围',
    timeRangeApplied: UI_LANG === 'en' ? 'Time filter applied' : '时间过滤已应用',
    timeRangeInvalid: UI_LANG === 'en' ? 'Invalid time format. Use YYYY-MM-DD HH:MM:SS' : '时间格式无效，请使用 YYYY-MM-DD HH:MM:SS',
    timeRangeNoTimestamp: UI_LANG === 'en' ? 'No parsable timestamps in current log' : '当前日志中没有可解析的时间戳',
    timeRangeNoMatch: UI_LANG === 'en' ? 'No log entries in selected time range' : '当前时间范围内没有日志',
    trendLoading: UI_LANG === 'en' ? 'Calculating...' : '统计中...',
    trendWaiting: UI_LANG === 'en' ? 'Waiting for log trend...' : '等待日志趋势数据...',
    trendNoData: UI_LANG === 'en' ? 'No timed log entries in current range' : '当前时间范围内没有可统计的时间戳日志',
    trendTotalLogs: UI_LANG === 'en' ? 'logs' : '条',
    trendVisibleDevices: UI_LANG === 'en' ? 'Visible devices' : '可见设备',
    trendBucketSize: UI_LANG === 'en' ? 'Bucket' : '时间粒度',
    trendRange: UI_LANG === 'en' ? 'Range' : '范围',
    trendLogsUnit: UI_LANG === 'en' ? 'logs' : '条',
    trendModeTotal: UI_LANG === 'en' ? 'Total' : '总量',
    trendModeStacked: UI_LANG === 'en' ? 'Stacked' : '堆叠',
    trendModeSummaryTotal: UI_LANG === 'en' ? 'Mode: total' : '模式: 总量',
    trendModeSummaryStacked: UI_LANG === 'en' ? 'Mode: stacked' : '模式: 堆叠',
    trendRangeModeFull: UI_LANG === 'en' ? 'Full Range' : '完整时间',
    trendRangeModeFocus: UI_LANG === 'en' ? 'Active Focus' : '活动聚焦',
    trendRangeSummaryFull: UI_LANG === 'en' ? 'Display: full range' : '显示: 完整时间',
    trendRangeSummaryFocus: UI_LANG === 'en' ? 'Display: active focus' : '显示: 活动聚焦',
    trendLogCountLabel: UI_LANG === 'en' ? 'Logs' : '日志',
    trendDurationLabel: UI_LANG === 'en' ? 'Duration' : '跨度',
    trendDeviceValueLabel: UI_LANG === 'en' ? 'Device values' : '设备值',
    trendDragHint: UI_LANG === 'en' ? 'Drag on chart to zoom time range' : '可在趋势图上拖拽框选时间范围',
    trendDragZoomIn: UI_LANG === 'en' ? 'Drag left to right to zoom in' : '从左向右拖拽放大',
    trendDragZoomOut: UI_LANG === 'en' ? 'Drag right to left to zoom out' : '从右向左拖拽缩小',
    trendDoubleClickReset: UI_LANG === 'en' ? 'Double-click to reset full time range' : '双击恢复完整时间范围',
    trendReset: UI_LANG === 'en' ? 'Reset to full range' : '恢复完整时间范围',
    trendExpandedRange: UI_LANG === 'en' ? 'Showing full range' : '已恢复完整时间范围',
    trendZoomedInRange: UI_LANG === 'en' ? 'Zoomed into selected range' : '已放大到所选时间范围',
    trendZoomedOutRange: UI_LANG === 'en' ? 'Zoomed out around selected range' : '已围绕所选时间范围缩小',
    fullscreen: UI_LANG === 'en' ? 'Fullscreen' : '全屏',
    exitFullscreen: UI_LANG === 'en' ? 'Exit Fullscreen' : '退出全屏',
    presetLoadFailed: UI_LANG === 'en' ? 'Failed to load highlight presets' : '加载高亮预设失败',
    presetImportFailed: UI_LANG === 'en' ? 'Failed to import preset' : '导入预设失败',
    presetImportOk: UI_LANG === 'en' ? 'Preset imported' : '预设已导入',
    presetExportFailed: UI_LANG === 'en' ? 'Failed to export preset' : '导出预设失败',
  };
  const INITIAL_TASK_DEVICES = Array.isArray(TASK_DETAIL_BOOT.taskDevices) ? TASK_DETAIL_BOOT.taskDevices : [];
  let latestTask = { devices: INITIAL_TASK_DEVICES };
  let currentAnalysisId = null;
  let latestAnalysisText = '';
  let selectedLogDeviceId = String(TASK_DETAIL_BOOT.selectedLogDeviceId || '');
  let analysisPreviewData = null;
  let analysisPreviewIndex = 0;
  let deviceFilterKeyword = '';
  let logSearchKeyword = '';
  let logSearchTimer = null;
  let debugConsolePseudoFullscreen = false;
  let logSearchRequestSeq = 0;
  let logTrendRequestSeq = 0;
  let currentTrendView = null;
  let trendBrushState = null;
  let trendResizeTimer = null;
  let trendResizeObserver = null;
  let trendFlashTimer = null;
  const TREND_COLLAPSE_STORAGE_KEY = TASK_ID ? `netlog.debug.trendCollapsed.v1.${TASK_ID}` : '';
  const TREND_MODE_STORAGE_KEY = TASK_ID ? `netlog.debug.trendMode.v1.${TASK_ID}` : '';
  const TREND_RANGE_MODE_STORAGE_KEY = TASK_ID ? `netlog.debug.trendRangeMode.v1.${TASK_ID}` : '';
  const legendActiveIndex = {};
  const logSearchCounts = {};
  const logFilteredCounts = {};
  const logSearchActiveIndex = {};
  const logTextCache = {};
  const logEntryCache = {};
  const logTimeRangeState = {};
  const DEBUG_TIME_RANGE_STORAGE_KEY = TASK_ID ? `netlog.debug.timeRange.v1.${TASK_ID}` : '';
  const LOG_COLOR_ORDER = ['underline', 'red', 'green', 'yellow', 'blue', 'magenta', 'cyan'];
  const HIGHLIGHT_PRESET_STORAGE_KEY = 'netlog_highlight_preset_v1';
  let highlightPresetItems = [];
  const highlightPresetMap = {};
  let selectedHighlightPresetId = 'auto';
  let currentTrendMode = 'total';
  let currentTrendRangeMode = 'full';
  const initialAiDeviceIds = Array.isArray(TASK_DETAIL_BOOT.initialAiDeviceIds) ? TASK_DETAIL_BOOT.initialAiDeviceIds : [];
  const selectedAiDeviceIds = new Set(initialAiDeviceIds);
  const knownAiDeviceIds = new Set(initialAiDeviceIds);
  function el(id) { return document.getElementById(id); }
  function val(id, fallback) {
    const node = el(id);
    const v = node && node.value != null ? node.value : '';
    return v === '' ? fallback : v;
  }
  function checked(id) {
    const node = el(id);
    return !!(node && node.checked);
  }

  function getSelectedAiDeviceIds() {
    const rows = (latestTask && Array.isArray(latestTask.devices)) ? latestTask.devices : INITIAL_TASK_DEVICES;
    const known = rows.map((d) => d.device_id);
    known.forEach((id) => {
      if (!knownAiDeviceIds.has(id)) {
        knownAiDeviceIds.add(id);
        selectedAiDeviceIds.add(id);
      }
    });
    return known.filter((id) => selectedAiDeviceIds.has(id));
  }

  function syncAiDeviceSelectionUi() {
    document.querySelectorAll('.ai-device-select').forEach((node) => {
      const deviceId = node.dataset.deviceId || '';
      node.checked = selectedAiDeviceIds.has(deviceId);
      node.addEventListener('change', () => {
        if (node.checked) selectedAiDeviceIds.add(deviceId);
        else selectedAiDeviceIds.delete(deviceId);
      });
    });
  }

  function updateAnalysisProgress(visible, percent, text) {
    const box = document.getElementById('analysis-progress-box');
    const txt = document.getElementById('analysis-progress-text');
    const fill = document.getElementById('analysis-progress-fill');
    if (box) box.style.display = visible ? '' : 'none';
    if (txt) txt.textContent = text || `${T.progress}: ${percent || 0}%`;
    if (fill) fill.style.width = `${Math.max(0, Math.min(100, Number(percent || 0)))}%`;
  }

  function getSelectedDevice(devices) {
    const rows = Array.isArray(devices) ? devices : [];
    if (!rows.length) return null;
    const matched = rows.find((d) => d.device_id === selectedLogDeviceId);
    return matched || rows[0];
  }

  function getVisibleLogDevices(devices) {
    const rows = Array.isArray(devices) ? devices : [];
    const keyword = String(deviceFilterKeyword || '').trim().toLowerCase();
    if (!keyword) return rows;
    return rows.filter((d) => String(d.device_name || '').toLowerCase().includes(keyword));
  }

  function renderLogDeviceList(devices) {
    const box = document.getElementById('log-device-list');
    const filterStatusEl = el('device-filter-status');
    if (!box) return;
    const rows = Array.isArray(devices) ? devices : [];
    const keyword = String(deviceFilterKeyword || '').trim().toLowerCase();
    const visibleRows = getVisibleLogDevices(rows);
    const selected = getSelectedDevice(visibleRows);
    if (selected) selectedLogDeviceId = selected.device_id;
    else if (!visibleRows.length) selectedLogDeviceId = '';
    if (filterStatusEl) {
      if (!keyword) filterStatusEl.textContent = T.deviceFilterHint;
      else filterStatusEl.textContent = visibleRows.length
        ? (UI_LANG === 'en' ? `Visible devices: ${visibleRows.length}/${rows.length}` : `显示设备: ${visibleRows.length}/${rows.length}`)
        : T.deviceFilterEmpty;
    }
    box.innerHTML = visibleRows.map((d) => {
      const title = d.device_name || d.device_ip || d.device_id;
      const sub = [d.device_id, d.device_ip, `${getDeviceDisplayedLogCount(d)} ${T.trendLogsUnit}`].filter(Boolean).join(' | ');
      const active = d.device_id === selectedLogDeviceId ? ' active' : '';
      const hitCount = logSearchCounts[d.device_id];
      const hitLine = logSearchKeyword && Number(hitCount) > 0
        ? `<div class="device-log-item-hit">${UI_LANG === 'en' ? 'Hits' : '命中'}: ${hitCount}</div>`
        : '';
      return `<button type="button" class="device-log-item${active}" data-device-id="${d.device_id}">
        <div class="device-log-item-main">${escapeHtml(title)}</div>
        <div class="device-log-item-sub">${escapeHtml(sub)}</div>
        ${hitLine}
      </button>`;
    }).join('');
    box.querySelectorAll('.device-log-item').forEach((node) => {
      node.addEventListener('click', async () => {
        const deviceId = node.dataset.deviceId || '';
        const hasSearch = !!logSearchKeyword.trim();
        const hitCount = Number(logSearchCounts[deviceId] || 0);
        if (hasSearch && deviceId === selectedLogDeviceId && hitCount > 0) {
          logSearchActiveIndex[deviceId] = ((Number(logSearchActiveIndex[deviceId] || 0) + 1) % hitCount);
        } else if (hasSearch && hitCount > 0) {
          logSearchActiveIndex[deviceId] = 0;
        }
        selectedLogDeviceId = deviceId;
        renderLogDeviceList(rows);
        await pollDebug();
      });
    });
  }


  function getDeviceDisplayedLogCount(dev) {
    if (!dev) return 0;
    if (Object.prototype.hasOwnProperty.call(logFilteredCounts, dev.device_id)) {
      return Number(logFilteredCounts[dev.device_id] || 0);
    }
    return Number(dev.hits_count || 0);
  }

  async function refreshDisplayedLogCounts(devices) {
    const rows = Array.isArray(devices) ? devices : [];
    await Promise.all(rows.map(async (dev) => {
      if (!dev || !dev.filtered_log_path) {
        logFilteredCounts[dev && dev.device_id ? dev.device_id : ''] = 0;
        return;
      }
      try {
        const text = await fetchFilteredLogText(dev);
        const filteredPayload = buildFilteredLogText(dev, text);
        logFilteredCounts[dev.device_id] = filteredPayload.invalid ? 0 : Number((filteredPayload.filteredEntries || []).length || 0);
      } catch (e) {
        logFilteredCounts[dev.device_id] = 0;
      }
    }));
  }

  function countKeywordHits(text, keyword) {
    const source = String(text || '').toLowerCase();
    const needle = String(keyword || '').trim().toLowerCase();
    if (!needle) return 0;
    let count = 0;
    let pos = 0;
    while (true) {
      const idx = source.indexOf(needle, pos);
      if (idx === -1) break;
      count += 1;
      pos = idx + needle.length;
    }
    return count;
  }

  async function fetchFilteredLogText(dev) {
    if (!dev || !dev.filtered_log_path) return '';
    if (Object.prototype.hasOwnProperty.call(logTextCache, dev.device_id)) {
      return logTextCache[dev.device_id];
    }
    const filteredUrl = `/api/tasks/${TASK_ID}/devices/${dev.device_id}/log?lang=${LANG_Q}`;
    const res = await fetch(filteredUrl);
    if (!res.ok) {
      logTextCache[dev.device_id] = '';
      return '';
    }
    const text = await res.text();
    logTextCache[dev.device_id] = text || '';
    return logTextCache[dev.device_id];
  }

  function pad2(v) {
    return String(v).padStart(2, '0');
  }

  function formatTimestampMs(ms) {
    if (!Number.isFinite(ms)) return '';
    const d = new Date(ms);
    return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
  }

  function parseUiTimeValue(text) {
    const raw = String(text || '').trim();
    if (!raw) return null;
    const m = raw.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})$/);
    if (!m) return NaN;
    const [, y, mo, d, h, mi, s] = m;
    return new Date(Number(y), Number(mo) - 1, Number(d), Number(h), Number(mi), Number(s)).getTime();
  }

  function parseLogLineTimestamp(line) {
    const text = String(line || '');
    let m = text.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?/);
    if (m) {
      return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]), Number(m[4]), Number(m[5]), Number(m[6])).getTime();
    }
    m = text.match(/^(\d{4})\s+([A-Z][a-z]{2})\s+(\d{1,2})\s+(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?/);
    if (m) {
      const mon = {Jan:0,Feb:1,Mar:2,Apr:3,May:4,Jun:5,Jul:6,Aug:7,Sep:8,Oct:9,Nov:10,Dec:11}[m[2]];
      if (mon != null) return new Date(Number(m[1]), mon, Number(m[3]), Number(m[4]), Number(m[5]), Number(m[6])).getTime();
    }
    m = text.match(/(?:^|[\s>])([A-Z][a-z]{2})\s+(\d{1,2})\s+(\d{4})\s+(\d{2}):(\d{2}):(\d{2})(?:[.+-]\d{2}:\d{2})?/);
    if (m) {
      const mon = {Jan:0,Feb:1,Mar:2,Apr:3,May:4,Jun:5,Jul:6,Aug:7,Sep:8,Oct:9,Nov:10,Dec:11}[m[1]];
      if (mon != null) return new Date(Number(m[3]), mon, Number(m[2]), Number(m[4]), Number(m[5]), Number(m[6])).getTime();
    }
    m = text.match(/^[^:]+:([A-Z][a-z]{2})\s+(\d{1,2})\s+(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?/);
    if (m) {
      const mon = {Jan:0,Feb:1,Mar:2,Apr:3,May:4,Jun:5,Jul:6,Aug:7,Sep:8,Oct:9,Nov:10,Dec:11}[m[1]];
      if (mon != null) return new Date(new Date().getFullYear(), mon, Number(m[2]), Number(m[3]), Number(m[4]), Number(m[5])).getTime();
    }
    return null;
  }

  function parseLogEntriesForDevice(dev, text) {
    const cacheKey = String(dev && dev.device_id || '');
    const cached = logEntryCache[cacheKey];
    const sourceText = String(text || '');
    if (cached && cached.text === sourceText) {
      return cached.payload;
    }
    const lines = sourceText.split('\n');
    const entries = [];
    let current = null;
    for (const line of lines) {
      const ts = parseLogLineTimestamp(line);
      if (ts != null) {
        if (current) entries.push(current);
        current = { timestampMs: ts, lines: [line] };
      } else if (current) {
        current.lines.push(line);
      } else {
        current = { timestampMs: null, lines: [line] };
      }
    }
    if (current) entries.push(current);
    const dated = entries.filter((x) => Number.isFinite(x.timestampMs));
    const minTs = dated.length ? Math.min(...dated.map((x) => x.timestampMs)) : null;
    const maxTs = dated.length ? Math.max(...dated.map((x) => x.timestampMs)) : null;
    const payload = { entries, minTs, maxTs };
    logEntryCache[cacheKey] = { text: sourceText, payload };
    return payload;
  }

  async function ensureGlobalTimeRangeState(force = false) {
    const rows = (latestTask && Array.isArray(latestTask.devices)) ? latestTask.devices : INITIAL_TASK_DEVICES;
    const scopedRows = rows.filter((d) => d && d.filtered_log_path);
    const scopeKey = scopedRows.map((d) => `${d.device_id}:1`).join('|');
    if (!force && logTimeRangeState.initialized && logTimeRangeState.scopeKey === scopeKey) {
      return logTimeRangeState;
    }
    let minTs = null;
    let maxTs = null;
    await Promise.all(scopedRows.map(async (dev) => {
      try {
        const text = await fetchFilteredLogText(dev);
        const parsed = parseLogEntriesForDevice(dev, text);
        if (Number.isFinite(parsed.minTs)) minTs = minTs == null ? parsed.minTs : Math.min(minTs, parsed.minTs);
        if (Number.isFinite(parsed.maxTs)) maxTs = maxTs == null ? parsed.maxTs : Math.max(maxTs, parsed.maxTs);
      } catch (e) {
        // ignore per-device failure when computing global time bounds
      }
    }));
    const defaultStart = minTs != null ? formatTimestampMs(minTs) : '';
    const defaultEnd = maxTs != null ? formatTimestampMs(maxTs) : '';
    const hadCustom = logTimeRangeState.initialized
      && ((logTimeRangeState.start && logTimeRangeState.start !== logTimeRangeState.defaultStart)
        || (logTimeRangeState.end && logTimeRangeState.end !== logTimeRangeState.defaultEnd));
    logTimeRangeState.defaultStart = defaultStart;
    logTimeRangeState.defaultEnd = defaultEnd;
    logTimeRangeState.scopeKey = scopeKey;
    logTimeRangeState.initialized = true;
    if (force || !hadCustom) {
      logTimeRangeState.start = defaultStart;
      logTimeRangeState.end = defaultEnd;
    } else {
      if (!String(logTimeRangeState.start || '').trim()) logTimeRangeState.start = defaultStart;
      if (!String(logTimeRangeState.end || '').trim()) logTimeRangeState.end = defaultEnd;
    }
    if (!hadCustom && DEBUG_TIME_RANGE_STORAGE_KEY) {
      try {
        const raw = window.localStorage.getItem(DEBUG_TIME_RANGE_STORAGE_KEY);
        if (raw) {
          const saved = JSON.parse(raw);
          const savedStart = String(saved && saved.start || '').trim();
          const savedEnd = String(saved && saved.end || '').trim();
          if (savedStart) logTimeRangeState.start = savedStart;
          if (savedEnd) logTimeRangeState.end = savedEnd;
        }
      } catch (e) {
        // ignore storage parse failure
      }
    }
    return logTimeRangeState;
  }

  function persistDebugTimeRangeState() {
    if (!DEBUG_TIME_RANGE_STORAGE_KEY) return;
    try {
      window.localStorage.setItem(DEBUG_TIME_RANGE_STORAGE_KEY, JSON.stringify({
        start: logTimeRangeState.start || '',
        end: logTimeRangeState.end || '',
      }));
    } catch (e) {
      // ignore storage failures
    }
  }

  function updateTrendResetVisibility() {
    const btn = el('log-trend-reset');
    if (!btn) return;
    const start = String(logTimeRangeState.start || logTimeRangeState.defaultStart || '').trim();
    const end = String(logTimeRangeState.end || logTimeRangeState.defaultEnd || '').trim();
    const defaultStart = String(logTimeRangeState.defaultStart || '').trim();
    const defaultEnd = String(logTimeRangeState.defaultEnd || '').trim();
    const isFullRange = !!defaultStart && !!defaultEnd && start === defaultStart && end === defaultEnd;
    btn.hidden = isFullRange;
  }

  function syncTimeRangeInputs() {
    const startEl = el('log-time-start');
    const endEl = el('log-time-end');
    const statusEl = el('log-time-status');
    if (!startEl || !endEl) return;
    startEl.value = logTimeRangeState.start || logTimeRangeState.defaultStart || '';
    endEl.value = logTimeRangeState.end || logTimeRangeState.defaultEnd || '';
    if (statusEl) {
      if (!logTimeRangeState.defaultStart || !logTimeRangeState.defaultEnd) {
        statusEl.textContent = T.timeRangeNoTimestamp;
      } else if ((logTimeRangeState.start || '') === logTimeRangeState.defaultStart && (logTimeRangeState.end || '') === logTimeRangeState.defaultEnd) {
        statusEl.textContent = `${T.timeRangeAll}: ${logTimeRangeState.defaultStart} ~ ${logTimeRangeState.defaultEnd}`;
      } else {
        statusEl.textContent = `${T.timeRangeApplied}: ${logTimeRangeState.start || logTimeRangeState.defaultStart} ~ ${logTimeRangeState.end || logTimeRangeState.defaultEnd} | ${T.timeRangeAll}: ${logTimeRangeState.defaultStart} ~ ${logTimeRangeState.defaultEnd}`;
      }
    }
    updateTrendResetVisibility();
  }

  function buildFilteredLogText(dev, text) {
    const parsed = parseLogEntriesForDevice(dev, text);
    const startValue = logTimeRangeState.start || logTimeRangeState.defaultStart || '';
    const endValue = logTimeRangeState.end || logTimeRangeState.defaultEnd || '';
    const startMs = parseUiTimeValue(startValue);
    const endMs = parseUiTimeValue(endValue);
    if ((startValue && Number.isNaN(startMs)) || (endValue && Number.isNaN(endMs))) {
      return { text: '', parsed, filteredEntries: [], invalid: true, empty: false };
    }
    const entries = parsed.entries || [];
    if ((!startValue && !endValue) || (parsed.minTs == null || parsed.maxTs == null)) {
      return { text: entries.map((x) => x.lines.join('\n')).join('\n'), parsed, filteredEntries: entries, invalid: false, empty: !entries.length };
    }
    const filtered = entries.filter((entry) => {
      if (!Number.isFinite(entry.timestampMs)) return true;
      if (startMs != null && Number.isFinite(startMs) && entry.timestampMs < startMs) return false;
      if (endMs != null && Number.isFinite(endMs) && entry.timestampMs > endMs) return false;
      return true;
    });
    return {
      text: filtered.map((x) => x.lines.join('\n')).join('\n'),
      parsed,
      filteredEntries: filtered,
      invalid: false,
      empty: !filtered.length,
    };
  }

  function setTrendMetric(index, label, value) {
    const items = document.querySelectorAll('#log-trend-metrics .device-log-trend-metric');
    const node = items[index];
    if (!node) return;
    const labelNode = node.querySelector('.metric-label');
    const valueNode = node.querySelector('.metric-value');
    if (labelNode) labelNode.textContent = label;
    if (valueNode) valueNode.textContent = value;
  }

  function formatShortTime(ms) {
    if (!Number.isFinite(ms)) return '-';
    const d = new Date(ms);
    return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
  }

  function formatShortDateTime(ms) {
    if (!Number.isFinite(ms)) return '-';
    const d = new Date(ms);
    return `${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
  }

  function formatBucketDuration(bucketMs) {
    const second = 1000;
    const minute = 60 * 1000;
    const hour = 60 * minute;
    const day = 24 * hour;
    if (bucketMs < minute) return UI_LANG === 'en' ? `${bucketMs / second}s` : `${bucketMs / second}秒`;
    if (bucketMs % day === 0) return UI_LANG === 'en' ? `${bucketMs / day}d` : `${bucketMs / day}天`;
    if (bucketMs % hour === 0) return UI_LANG === 'en' ? `${bucketMs / hour}h` : `${bucketMs / hour}小时`;
    return UI_LANG === 'en' ? `${bucketMs / minute}m` : `${bucketMs / minute}分钟`;
  }

  function formatTrendTooltipRange(startMs, endMs) {
    if (!Number.isFinite(startMs) || !Number.isFinite(endMs)) return '-';
    const start = new Date(startMs);
    const end = new Date(endMs);
    const sameDay = start.getFullYear() === end.getFullYear()
      && start.getMonth() === end.getMonth()
      && start.getDate() === end.getDate();
    if (sameDay) {
      return `${formatShortTime(startMs)} - ${formatShortTime(endMs)}`;
    }
    return `${pad2(start.getMonth() + 1)}-${pad2(start.getDate())} ${formatShortTime(startMs)} - ${pad2(end.getMonth() + 1)}-${pad2(end.getDate())} ${formatShortTime(endMs)}`;
  }

  function chooseTrendBucketMs(spanMs, targetBuckets) {
    const safeSpan = Math.max(spanMs, 60 * 1000);
    const preferredBuckets = Math.max(24, Math.min(180, Number(targetBuckets || 72)));
    const rawSeconds = Math.max(1, (safeSpan / preferredBuckets / 1000));
    const magnitude = 10 ** Math.floor(Math.log10(rawSeconds));
    const normalized = rawSeconds / magnitude;
    let niceNormalized = 10;
    if (normalized <= 1) niceNormalized = 1;
    else if (normalized <= 2) niceNormalized = 2;
    else if (normalized <= 2.5) niceNormalized = 2.5;
    else if (normalized <= 4) niceNormalized = 4;
    else if (normalized <= 5) niceNormalized = 5;
    else if (normalized <= 8) niceNormalized = 8;
    const bucketSeconds = Math.max(1, Math.min(24 * 60 * 60, Math.ceil(niceNormalized * magnitude)));
    return bucketSeconds * 1000;
  }

  function buildTrendCurvePath(points) {
    if (!points.length) return '';
    if (points.length === 1) return `M ${points[0].x} ${points[0].y}`;
    let path = `M ${points[0].x} ${points[0].y}`;
    for (let i = 1; i < points.length - 1; i += 1) {
      const xc = (points[i].x + points[i + 1].x) / 2;
      const yc = (points[i].y + points[i + 1].y) / 2;
      path += ` Q ${points[i].x} ${points[i].y} ${xc} ${yc}`;
    }
    const prev = points[points.length - 2];
    const last = points[points.length - 1];
    path += ` Q ${prev.x} ${prev.y} ${last.x} ${last.y}`;
    return path;
  }

  function buildTrendAreaPath(points, baselineY) {
    if (!points.length) return '';
    const line = buildTrendCurvePath(points);
    const first = points[0];
    const last = points[points.length - 1];
    return `${line} L ${last.x} ${baselineY} L ${first.x} ${baselineY} Z`;
  }

  function ensureTrendTooltip() {
    let tip = document.getElementById('global-log-trend-tip');
    if (!tip) {
      tip = document.createElement('div');
      tip.id = 'global-log-trend-tip';
      tip.className = 'device-log-trend-tip';
      document.body.appendChild(tip);
    }
    return tip;
  }

  function hideTrendTooltip() {
    const tip = document.getElementById('global-log-trend-tip');
    if (tip) tip.style.display = 'none';
  }

  function positionTrendTooltip(tip, event) {
    if (!tip || !event) return;
    const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 0;
    const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
    const margin = 12;
    const gap = 14;
    tip.style.visibility = 'hidden';
    tip.style.display = 'block';
    tip.style.left = '0px';
    tip.style.top = '0px';
    const rect = tip.getBoundingClientRect();
    let left = event.clientX + gap;
    let top = event.clientY - 18;
    if (left + rect.width > viewportWidth - margin) {
      left = event.clientX - rect.width - gap;
    }
    if (left < margin) {
      left = Math.max(margin, Math.min(viewportWidth - rect.width - margin, event.clientX - rect.width / 2));
    }
    if (top + rect.height > viewportHeight - margin) {
      top = event.clientY - rect.height - gap;
    }
    if (top < margin) {
      top = Math.max(margin, Math.min(viewportHeight - rect.height - margin, event.clientY + gap));
    }
    tip.style.left = `${Math.round(left)}px`;
    tip.style.top = `${Math.round(top)}px`;
    tip.style.visibility = 'visible';
  }

  function ensureTrendBrush(container) {
    let brush = container.querySelector('.device-log-trend-brush');
    if (!brush) {
      brush = document.createElement('div');
      brush.className = 'device-log-trend-brush';
      container.appendChild(brush);
    }
    return brush;
  }

  function hideTrendBrush(container) {
    const brush = container.querySelector('.device-log-trend-brush');
    if (brush) brush.style.display = 'none';
  }

  function showTrendFlash(message) {
    const node = el('log-trend-flash');
    if (!node) return;
    if (trendFlashTimer) window.clearTimeout(trendFlashTimer);
    node.textContent = String(message || '').trim();
    node.classList.toggle('is-visible', !!node.textContent);
    if (!node.textContent) return;
    trendFlashTimer = window.setTimeout(() => {
      node.classList.remove('is-visible');
      trendFlashTimer = null;
    }, 1400);
  }

  function isTrendCollapsed() {
    const panel = el('log-trend-panel');
    return !!(panel && panel.classList.contains('is-collapsed'));
  }

  function setTrendCollapsed(collapsed) {
    const panel = el('log-trend-panel');
    const toggle = el('log-trend-toggle');
    if (!panel || !toggle) return;
    panel.classList.toggle('is-collapsed', !!collapsed);
    toggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    toggle.title = collapsed
      ? (UI_LANG === 'en' ? 'Expand trend' : '展开趋势图')
      : (UI_LANG === 'en' ? 'Collapse trend' : '折叠趋势图');
    if (TREND_COLLAPSE_STORAGE_KEY) {
      try {
        window.localStorage.setItem(TREND_COLLAPSE_STORAGE_KEY, collapsed ? '1' : '0');
      } catch (e) {
        // ignore storage failures
      }
    }
  }

  function restoreTrendCollapsed() {
    if (!TREND_COLLAPSE_STORAGE_KEY) return;
    try {
      const raw = window.localStorage.getItem(TREND_COLLAPSE_STORAGE_KEY);
      if (raw === '1') setTrendCollapsed(true);
    } catch (e) {
      // ignore storage failures
    }
  }

  function updateTrendModeUi() {
    const totalBtn = el('log-trend-mode-total');
    const stackedBtn = el('log-trend-mode-stacked');
    const fullBtn = el('log-trend-range-full');
    const focusBtn = el('log-trend-range-focus');
    if (totalBtn) totalBtn.classList.toggle('is-active', currentTrendMode === 'total');
    if (stackedBtn) stackedBtn.classList.toggle('is-active', currentTrendMode === 'stacked');
    if (fullBtn) fullBtn.classList.toggle('is-active', currentTrendRangeMode === 'full');
    if (focusBtn) focusBtn.classList.toggle('is-active', currentTrendRangeMode === 'focus');
  }

  function setTrendMode(mode, persist = true) {
    currentTrendMode = mode === 'stacked' ? 'stacked' : 'total';
    updateTrendModeUi();
    if (persist && TREND_MODE_STORAGE_KEY) {
      try {
        window.localStorage.setItem(TREND_MODE_STORAGE_KEY, currentTrendMode);
      } catch (e) {
        // ignore storage failures
      }
    }
  }

  function restoreTrendMode() {
    if (!TREND_MODE_STORAGE_KEY) {
      updateTrendModeUi();
      return;
    }
    try {
      const raw = window.localStorage.getItem(TREND_MODE_STORAGE_KEY);
      if (raw === 'stacked' || raw === 'total') currentTrendMode = raw;
    } catch (e) {
      // ignore storage failures
    }
    updateTrendModeUi();
  }

  function setTrendRangeMode(mode, persist = true) {
    currentTrendRangeMode = mode === 'focus' ? 'focus' : 'full';
    updateTrendModeUi();
    if (persist && TREND_RANGE_MODE_STORAGE_KEY) {
      try {
        window.localStorage.setItem(TREND_RANGE_MODE_STORAGE_KEY, currentTrendRangeMode);
      } catch (e) {
        // ignore storage failures
      }
    }
  }

  function restoreTrendRangeMode() {
    if (!TREND_RANGE_MODE_STORAGE_KEY) {
      updateTrendModeUi();
      return;
    }
    try {
      const raw = window.localStorage.getItem(TREND_RANGE_MODE_STORAGE_KEY);
      if (raw === 'full' || raw === 'focus') currentTrendRangeMode = raw;
    } catch (e) {
      // ignore storage failures
    }
    updateTrendModeUi();
  }

  function scheduleTrendRerender() {
    if (trendResizeTimer) window.clearTimeout(trendResizeTimer);
    trendResizeTimer = window.setTimeout(() => {
      renderLogTrendPanel();
    }, 120);
  }

  function ensureTrendResizeObserver() {
    const chartEl = el('log-trend-chart');
    if (!chartEl || trendResizeObserver || typeof ResizeObserver === 'undefined') return;
    trendResizeObserver = new ResizeObserver(() => {
      scheduleTrendRerender();
    });
    trendResizeObserver.observe(chartEl);
  }

  function clearTrendPanel(message) {
    const chartEl = el('log-trend-chart');
    const summaryEl = el('log-trend-summary');
    currentTrendView = null;
    if (summaryEl) summaryEl.textContent = message || '';
    if (chartEl) chartEl.innerHTML = `<div class="device-log-trend-empty">${escapeHtml(message || T.trendWaiting)}</div>`;
    setTrendMetric(0, UI_LANG === 'en' ? 'Total Logs' : '总日志', '-');
    setTrendMetric(1, UI_LANG === 'en' ? 'Peak Window' : '峰值时间窗', '-');
    setTrendMetric(2, UI_LANG === 'en' ? 'Avg / Bucket' : '平均每桶', '-');
    setTrendMetric(3, UI_LANG === 'en' ? 'Devices Hit' : '命中设备数', '-');
  }

  function renderTrendSummary(summaryEl, visibleCount, bucketMs, startMs, endMs) {
    if (!summaryEl) return;
    const rangeText = `${formatTimestampMs(startMs)} ~ ${formatTimestampMs(endMs)}`;
    const modeText = currentTrendMode === 'stacked' ? T.trendModeSummaryStacked : T.trendModeSummaryTotal;
    const rangeModeText = currentTrendRangeMode === 'focus' ? T.trendRangeSummaryFocus : T.trendRangeSummaryFull;
    if (!Number.isFinite(bucketMs) || bucketMs <= 0) {
      summaryEl.innerHTML = `<span class="trend-summary-line">${escapeHtml(`${T.trendVisibleDevices}: ${visibleCount} · ${modeText} · ${rangeModeText}`)}</span><span class="trend-summary-line">${escapeHtml(`${T.trendRange}: ${rangeText}`)}</span>`;
      return;
    }
    summaryEl.innerHTML = `<span class="trend-summary-line">${escapeHtml(`${T.trendVisibleDevices}: ${visibleCount} · ${T.trendBucketSize}: ${formatBucketDuration(bucketMs)} · ${modeText} · ${rangeModeText}`)}</span><span class="trend-summary-line">${escapeHtml(`${T.trendRange}: ${rangeText}`)}</span>`;
  }

  function quantile(sortedNumbers, q) {
    const rows = Array.isArray(sortedNumbers) ? sortedNumbers : [];
    if (!rows.length) return 0;
    const clamped = Math.max(0, Math.min(1, Number(q || 0)));
    const pos = (rows.length - 1) * clamped;
    const base = Math.floor(pos);
    const rest = pos - base;
    const left = Number(rows[base] || 0);
    const right = Number(rows[base + 1] || left);
    return left + ((right - left) * rest);
  }

  function getRenderableTrendBuckets(buckets) {
    const rows = Array.isArray(buckets) ? buckets : [];
    if (!rows.length) return rows;
    if (currentTrendRangeMode !== 'focus') return rows;
    let start = 0;
    let end = rows.length - 1;
    while (start < rows.length && Number(rows[start].count || 0) <= 0) start += 1;
    while (end > start && Number(rows[end].count || 0) <= 0) end -= 1;
    if (start >= rows.length) return rows;
    const trimmed = rows.slice(start, end + 1);
    const counts = trimmed.map((row) => Number(row.count || 0));
    const peak = Math.max(...counts, 0);
    if (peak <= 0) return trimmed;
    const threshold = Math.max(1, Math.ceil(peak * 0.05));
    let focusStart = 0;
    let focusEnd = trimmed.length - 1;
    while (focusStart < trimmed.length && Number(trimmed[focusStart].count || 0) < threshold) focusStart += 1;
    while (focusEnd > focusStart && Number(trimmed[focusEnd].count || 0) < threshold) focusEnd -= 1;
    if (focusStart >= trimmed.length) return trimmed;
    if (focusStart > 0) focusStart -= 1;
    if (focusEnd < trimmed.length - 1) focusEnd += 1;
    return trimmed.slice(focusStart, focusEnd + 1);
  }

  function computeTrendBucketPositions(renderBuckets, leftPad, innerWidth) {
    const rows = Array.isArray(renderBuckets) ? renderBuckets : [];
    if (!rows.length) return [];
    if (rows.length === 1) return [leftPad + innerWidth / 2];
    const positions = [];
    const step = innerWidth / Math.max(1, rows.length - 1);
    for (let i = 0; i < rows.length; i += 1) {
      positions.push(leftPad + (step * i));
    }
    positions[0] = leftPad;
    positions[positions.length - 1] = leftPad + innerWidth;
    return positions;
  }

  function getTrendSeriesPalette(index) {
    const palette = [
      { stroke: '#38bdf8', fill: 'rgba(56,189,248,0.18)' },
      { stroke: '#22c55e', fill: 'rgba(34,197,94,0.16)' },
      { stroke: '#f59e0b', fill: 'rgba(245,158,11,0.16)' },
      { stroke: '#a78bfa', fill: 'rgba(167,139,250,0.16)' },
      { stroke: '#f472b6', fill: 'rgba(244,114,182,0.16)' },
      { stroke: '#2dd4bf', fill: 'rgba(45,212,191,0.16)' },
      { stroke: '#fb7185', fill: 'rgba(251,113,133,0.16)' },
      { stroke: '#facc15', fill: 'rgba(250,204,21,0.16)' },
    ];
    return palette[index % palette.length];
  }

  function buildTrendStackedAreaPath(points, lowerPoints, baselineY) {
    if (!points.length) return '';
    const upper = buildTrendCurvePath(points);
    const lower = lowerPoints && lowerPoints.length ? [...lowerPoints].reverse() : [
      { x: points[points.length - 1].x, y: baselineY },
      { x: points[0].x, y: baselineY },
    ];
    let path = upper;
    if (lower.length > 1) {
      path += ` L ${lower[0].x} ${lower[0].y}`;
      for (let i = 1; i < lower.length - 1; i += 1) {
        const xc = (lower[i].x + lower[i + 1].x) / 2;
        const yc = (lower[i].y + lower[i + 1].y) / 2;
        path += ` Q ${lower[i].x} ${lower[i].y} ${xc} ${yc}`;
      }
      const prev = lower[lower.length - 2];
      const last = lower[lower.length - 1];
      path += ` Q ${prev.x} ${prev.y} ${last.x} ${last.y}`;
    } else if (lower.length === 1) {
      path += ` L ${lower[0].x} ${lower[0].y}`;
    }
    return `${path} Z`;
  }

  function syncTrendChartHeight(chartEl) {
    const metricsEl = el('log-trend-metrics');
    if (!chartEl || !metricsEl) return;
    const metricsHeight = Math.round(metricsEl.getBoundingClientRect().height || metricsEl.offsetHeight || 0);
    const fullscreen = !!(document.fullscreenElement && document.fullscreenElement.classList && document.fullscreenElement.classList.contains('device-log-viewer'))
      || document.querySelector('.device-log-viewer.debug-fullscreen-fallback');
    const minHeight = fullscreen ? 200 : 180;
    const maxHeight = fullscreen ? 260 : 240;
    const target = Math.max(minHeight, Math.min(maxHeight, metricsHeight || minHeight));
    chartEl.style.height = `${target}px`;
  }

  function renderTrendChart(chartEl, trend) {
    if (isTrendCollapsed()) return;
    syncTrendChartHeight(chartEl);
    const chartRect = chartEl.getBoundingClientRect();
    const chartStyles = window.getComputedStyle(chartEl);
    const padTopPx = parseFloat(chartStyles.paddingTop || '0') || 0;
    const padBottomPx = parseFloat(chartStyles.paddingBottom || '0') || 0;
    const width = Math.max(760, Math.round(chartEl.clientWidth || chartRect.width || 920));
    const height = Math.max(
      136,
      Math.round((chartEl.clientHeight || chartRect.height || 180) - padTopPx - padBottomPx),
    );
    const renderBuckets = getRenderableTrendBuckets(trend.buckets);
    const visibleEdgePad = 0;
    const leftPad = visibleEdgePad;
    const rightPad = visibleEdgePad;
    const topPad = 14;
    const bottomPad = 30;
    const innerWidth = width - leftPad - rightPad;
    const innerHeight = height - topPad - bottomPad;
    const maxCount = Math.max(1, ...renderBuckets.map((b) => b.count));
    const baselineY = topPad + innerHeight;
    const pointXs = computeTrendBucketPositions(renderBuckets, leftPad, innerWidth);
    const points = renderBuckets.map((bucket, index) => {
      const x = pointXs[index];
      const y = baselineY - (bucket.count / maxCount) * innerHeight;
      return { x, y };
    });
    const stackedSeries = currentTrendMode === 'stacked' && Array.isArray(trend.series) && trend.series.length > 1
      ? trend.series
        .map((series) => ({
          ...series,
          counts: renderBuckets.map((bucket) => Number((bucket.deviceCounts || {})[series.deviceId] || 0)),
        }))
        .filter((series) => series.counts.some((count) => count > 0))
      : null;
    const visualPoints = points.length > 1
      ? [{ x: leftPad, y: points[0].y }, ...points, { x: width - rightPad, y: points[points.length - 1].y }]
      : [{ x: leftPad, y: points[0].y }, { x: width - rightPad, y: points[0].y }];
    const areaPath = buildTrendAreaPath(visualPoints, baselineY);
    const linePath = buildTrendCurvePath(visualPoints);
    const midIndex = Math.floor((renderBuckets.length - 1) / 2);
    const renderStartMs = renderBuckets[0].startMs;
    const renderEndMs = renderBuckets[renderBuckets.length - 1].endMs;
    const filteredStartMs = Number.isFinite(trend.filteredStartMs) ? trend.filteredStartMs : renderStartMs;
    const filteredEndMs = Number.isFinite(trend.filteredEndMs) ? trend.filteredEndMs : renderEndMs;
    const edgeLabelStartMs = currentTrendRangeMode === 'focus' ? renderStartMs : filteredStartMs;
    const edgeLabelEndMs = currentTrendRangeMode === 'focus' ? renderEndMs : filteredEndMs;
    const mapTimeToX = (ms) => {
      if (!Number.isFinite(ms) || renderEndMs <= renderStartMs) return leftPad;
      const ratio = Math.max(0, Math.min(1, (ms - renderStartMs) / (renderEndMs - renderStartMs)));
      return leftPad + (ratio * innerWidth);
    };
    const filteredStartX = Math.max(0, Math.min(width, mapTimeToX(edgeLabelStartMs)));
    const filteredEndX = Math.max(0, Math.min(width, mapTimeToX(edgeLabelEndMs)));
    const labels = [
      { x: Math.max(2, filteredStartX + 2), text: formatTimestampMs(edgeLabelStartMs), anchor: 'start', cls: 'is-edge is-full' },
      { x: points[midIndex].x, text: formatShortDateTime(renderBuckets[midIndex].startMs), anchor: 'middle', cls: '' },
      { x: Math.min(width - 2, filteredEndX - 2), text: formatTimestampMs(edgeLabelEndMs), anchor: 'end', cls: 'is-edge is-full' },
    ];
    currentTrendView = {
      buckets: renderBuckets.map((bucket, index) => {
        const point = points[index];
        const barWidth = Math.max(10, innerWidth / Math.max(renderBuckets.length, 12));
        const rectX = Math.max(leftPad, point.x - barWidth / 2);
        return {
          ...bucket,
          centerX: point.x,
          x: rectX,
          width: barWidth,
          deviceCounts: bucket.deviceCounts || {},
        };
      }),
      leftPad,
      rightPad,
      width,
      height,
      mode: stackedSeries ? 'stacked' : 'total',
      seriesMeta: Array.isArray(trend.series) ? trend.series.map((item, index) => ({
        deviceId: item.deviceId,
        label: item.label,
        color: getTrendSeriesPalette(index),
      })) : [],
    };
    const gridValues = [maxCount, Math.round(maxCount / 2), 0];
    let seriesMarkup = `
        <path class="device-log-trend-area" d="${areaPath}"></path>
        <path class="device-log-trend-line" d="${linePath}"></path>
        ${points.map((point, index) => {
          const bucket = renderBuckets[index];
          const isPeak = bucket.count === trend.peakCount;
          return `<circle class="device-log-trend-point${isPeak ? ' is-peak' : ''}" cx="${point.x}" cy="${point.y}" r="${isPeak ? 3.8 : 2.6}"></circle>`;
        }).join('')}
    `;
    if (stackedSeries) {
      const cumulative = Array.from({ length: renderBuckets.length }, () => 0);
      seriesMarkup = stackedSeries.map((series, seriesIndex) => {
        const palette = getTrendSeriesPalette(seriesIndex);
        const upperPoints = renderBuckets.map((bucket, index) => {
          cumulative[index] += Number(series.counts[index] || 0);
          const x = pointXs[index];
          const y = baselineY - (cumulative[index] / maxCount) * innerHeight;
          return { x, y };
        });
        const lowerValues = renderBuckets.map((bucket, index) => cumulative[index] - Number(series.counts[index] || 0));
        const lowerPoints = renderBuckets.map((bucket, index) => {
          const x = pointXs[index];
          const y = baselineY - (lowerValues[index] / maxCount) * innerHeight;
          return { x, y };
        });
        const visualUpper = upperPoints.length > 1
          ? [{ x: leftPad, y: upperPoints[0].y }, ...upperPoints, { x: width - rightPad, y: upperPoints[upperPoints.length - 1].y }]
          : [{ x: leftPad, y: upperPoints[0].y }, { x: width - rightPad, y: upperPoints[0].y }];
        const visualLower = lowerPoints.length > 1
          ? [{ x: leftPad, y: lowerPoints[0].y }, ...lowerPoints, { x: width - rightPad, y: lowerPoints[lowerPoints.length - 1].y }]
          : [{ x: leftPad, y: lowerPoints[0].y }, { x: width - rightPad, y: lowerPoints[0].y }];
        const fillPath = buildTrendStackedAreaPath(visualUpper, visualLower, baselineY);
        const linePathLocal = buildTrendCurvePath(visualUpper);
        return `
          <path class="device-log-trend-stacked-fill" d="${fillPath}" fill="${palette.fill}"></path>
          <path class="device-log-trend-stacked-layer" d="${linePathLocal}" fill="none" stroke="${palette.stroke}"></path>
        `;
      }).join('');
    }
    chartEl.innerHTML = `
      <svg class="device-log-trend-svg" viewBox="0 0 ${width} ${height}">
        <defs>
          <linearGradient id="logTrendAreaGradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="#38bdf8" stop-opacity="0.42"></stop>
            <stop offset="100%" stop-color="#38bdf8" stop-opacity="0.04"></stop>
          </linearGradient>
        </defs>
        <line class="device-log-trend-axis-line" x1="${leftPad}" y1="${baselineY}" x2="${width - rightPad}" y2="${baselineY}"></line>
        ${seriesMarkup}
        ${renderBuckets.map((bucket, index) => {
          const { x: rectX, width: barWidth } = currentTrendView.buckets[index];
          const isPeak = bucket.count === trend.peakCount;
          return `<rect class="device-log-trend-zone${isPeak ? ' is-peak' : ''}" data-index="${index}" x="${rectX}" y="${topPad}" width="${barWidth}" height="${innerHeight}" rx="2"></rect>`;
        }).join('')}
        ${gridValues.map((value) => {
          const y = baselineY - (Math.max(0, value) / maxCount) * innerHeight;
          return `<line class="device-log-trend-grid-line" x1="${leftPad}" y1="${y}" x2="${width - rightPad}" y2="${y}"></line>`;
        }).join('')}
        ${labels.map((item) => `<text class="device-log-trend-axis-label${item.cls ? ` ${item.cls}` : ''}" x="${item.x}" y="${height - 6}" text-anchor="${item.anchor}">${escapeHtml(item.text)}</text>`).join('')}
      </svg>
    `;
    const tip = ensureTrendTooltip();
    ensureTrendBrush(chartEl);
    chartEl.querySelectorAll('.device-log-trend-zone').forEach((bar) => {
      const showTip = (event) => {
        const index = Number(bar.dataset.index || -1);
        const bucket = currentTrendView && Array.isArray(currentTrendView.buckets) ? currentTrendView.buckets[index] : null;
        if (!bucket) return;
        const deviceDetail = (currentTrendView.mode === 'stacked' && Array.isArray(currentTrendView.seriesMeta))
          ? currentTrendView.seriesMeta
            .map((series) => ({
              label: series.label,
              count: Number((bucket.deviceCounts || {})[series.deviceId] || 0),
            }))
            .filter((item) => item.count > 0)
            .sort((a, b) => b.count - a.count)
          : [];
        tip.innerHTML = `
          <div class="trend-tip-range">${escapeHtml(formatTrendTooltipRange(bucket.startMs, bucket.endMs))}</div>
          <div class="trend-tip-meta">${escapeHtml(`${T.trendLogCountLabel} ${bucket.count} · ${T.trendDurationLabel} ${formatBucketDuration((bucket.endMs - bucket.startMs) + 1)}`)}</div>
          ${deviceDetail.length ? `<div class="trend-tip-device-list">${deviceDetail.map((item) => `<div class="trend-tip-device"><span class="trend-tip-device-name">${escapeHtml(item.label)}</span><span class="trend-tip-device-count">${item.count}</span></div>`).join('')}</div>` : ''}
        `;
        positionTrendTooltip(tip, event);
      };
      bar.addEventListener('mousemove', showTip);
      bar.addEventListener('mouseenter', showTip);
      bar.addEventListener('mouseleave', () => {
        hideTrendTooltip();
      });
    });
    chartEl.removeAttribute('title');
    chartEl.setAttribute('aria-label', `${T.trendDragZoomIn} / ${T.trendDragZoomOut} / ${T.trendDoubleClickReset}`);
    ensureTrendResizeObserver();
  }

  function trendEventLocalX(chartEl, event) {
    const rect = chartEl.getBoundingClientRect();
    const x = event.clientX - rect.left;
    return Math.max(0, Math.min(rect.width, x));
  }

  function trendXToBucketIndex(chartEl, localX) {
    if (!currentTrendView || !Array.isArray(currentTrendView.buckets) || !currentTrendView.buckets.length) return null;
    const scale = chartEl.clientWidth > 0 ? (currentTrendView.width / chartEl.clientWidth) : 1;
    const viewX = localX * scale;
    let bestIndex = 0;
    let bestDistance = Infinity;
    currentTrendView.buckets.forEach((bucket, index) => {
      const center = bucket.centerX;
      const distance = Math.abs(viewX - center);
      if (distance < bestDistance) {
        bestDistance = distance;
        bestIndex = index;
      }
    });
    return bestIndex;
  }

  async function applyTrendBrushSelection(chartEl) {
    if (!trendBrushState || !currentTrendView) return;
    const movingRight = (trendBrushState.currentX ?? trendBrushState.startX) >= trendBrushState.startX;
    const startIndex = trendXToBucketIndex(chartEl, Math.min(trendBrushState.startX, trendBrushState.currentX));
    const endIndex = trendXToBucketIndex(chartEl, Math.max(trendBrushState.startX, trendBrushState.currentX));
    if (startIndex == null || endIndex == null) return;
    const from = currentTrendView.buckets[Math.min(startIndex, endIndex)];
    const to = currentTrendView.buckets[Math.max(startIndex, endIndex)];
    if (!from || !to) return;
    if (movingRight) {
      logTimeRangeState.start = formatTimestampMs(from.startMs);
      logTimeRangeState.end = formatTimestampMs(to.endMs);
      showTrendFlash(T.trendZoomedInRange);
    } else {
      const fullStartMs = parseUiTimeValue(logTimeRangeState.defaultStart || '');
      const fullEndMs = parseUiTimeValue(logTimeRangeState.defaultEnd || '');
      const currentStartMs = parseUiTimeValue(logTimeRangeState.start || logTimeRangeState.defaultStart || '');
      const currentEndMs = parseUiTimeValue(logTimeRangeState.end || logTimeRangeState.defaultEnd || '');
      if (!Number.isFinite(fullStartMs) || !Number.isFinite(fullEndMs) || !Number.isFinite(currentStartMs) || !Number.isFinite(currentEndMs)) return;
      const currentSpan = Math.max(1000, currentEndMs - currentStartMs);
      const selectedSpan = Math.max(1000, to.endMs - from.startMs);
      const selectionRatio = Math.max(0.04, Math.min(1, selectedSpan / currentSpan));
      let targetSpan = Math.round(currentSpan / selectionRatio);
      targetSpan = Math.max(currentSpan, Math.min(fullEndMs - fullStartMs, targetSpan));
      const centerMs = Math.round((from.startMs + to.endMs) / 2);
      let nextStart = centerMs - Math.round(targetSpan / 2);
      let nextEnd = centerMs + Math.round(targetSpan / 2);
      if (nextStart < fullStartMs) {
        nextEnd += (fullStartMs - nextStart);
        nextStart = fullStartMs;
      }
      if (nextEnd > fullEndMs) {
        nextStart -= (nextEnd - fullEndMs);
        nextEnd = fullEndMs;
      }
      nextStart = Math.max(fullStartMs, nextStart);
      nextEnd = Math.min(fullEndMs, nextEnd);
      logTimeRangeState.start = formatTimestampMs(nextStart);
      logTimeRangeState.end = formatTimestampMs(nextEnd);
      showTrendFlash(T.trendZoomedOutRange);
    }
    syncTimeRangeInputs();
    persistDebugTimeRangeState();
    await runLogSearch();
  }

  function bindTrendBrushInteractions() {
    const chartEl = el('log-trend-chart');
    if (!chartEl) return;
    if (chartEl.dataset.brushBound === '1') return;
    chartEl.dataset.brushBound = '1';
    const onMove = (event) => {
      if (!trendBrushState) return;
      trendBrushState.currentX = trendEventLocalX(chartEl, event);
      const brush = ensureTrendBrush(chartEl);
      const left = Math.min(trendBrushState.startX, trendBrushState.currentX);
      const width = Math.abs(trendBrushState.currentX - trendBrushState.startX);
      brush.style.display = width >= 4 ? 'block' : 'none';
      brush.style.left = `${left}px`;
      brush.style.width = `${Math.max(0, width)}px`;
    };
    const clearMove = async (event) => {
      if (!trendBrushState) return;
      const width = Math.abs((trendBrushState.currentX ?? trendBrushState.startX) - trendBrushState.startX);
      const state = trendBrushState;
      trendBrushState = null;
      hideTrendBrush(chartEl);
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', clearMove);
      if (width >= 8 && event) {
        trendBrushState = state;
        await applyTrendBrushSelection(chartEl);
        trendBrushState = null;
      }
    };
    chartEl.addEventListener('mousedown', (event) => {
      if (event.button !== 0) return;
      if (!currentTrendView || !currentTrendView.buckets || !currentTrendView.buckets.length) return;
      if (isTrendCollapsed()) return;
      if (event.target && event.target.closest('.device-log-trend-tip')) return;
      const startX = trendEventLocalX(chartEl, event);
      trendBrushState = { startX, currentX: startX };
      const brush = ensureTrendBrush(chartEl);
      brush.style.display = 'none';
      brush.style.left = `${startX}px`;
      brush.style.width = '0px';
      window.addEventListener('mousemove', onMove);
      window.addEventListener('mouseup', clearMove, { once: false });
      event.preventDefault();
    });
    chartEl.addEventListener('dblclick', async (event) => {
      if (isTrendCollapsed()) return;
      event.preventDefault();
      await ensureGlobalTimeRangeState();
      logTimeRangeState.start = logTimeRangeState.defaultStart || '';
      logTimeRangeState.end = logTimeRangeState.defaultEnd || '';
      syncTimeRangeInputs();
      persistDebugTimeRangeState();
      showTrendFlash(T.trendExpandedRange);
      await renderLogTrendPanel();
      await runLogSearch();
    });
  }

  async function renderLogTrendPanel() {
    const chartEl = el('log-trend-chart');
    const summaryEl = el('log-trend-summary');
    if (!chartEl || !summaryEl) return;
    const rows = (latestTask && Array.isArray(latestTask.devices)) ? latestTask.devices : INITIAL_TASK_DEVICES;
    const visibleRows = getVisibleLogDevices(rows).filter((d) => d && d.filtered_log_path);
    const requestId = ++logTrendRequestSeq;
    if (!visibleRows.length) {
      clearTrendPanel(T.deviceFilterEmpty);
      return;
    }
    summaryEl.textContent = T.trendLoading;
    await ensureGlobalTimeRangeState();
    const startMs = parseUiTimeValue(logTimeRangeState.start || logTimeRangeState.defaultStart || '');
    const endMs = parseUiTimeValue(logTimeRangeState.end || logTimeRangeState.defaultEnd || '');
    if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs < startMs) {
      clearTrendPanel(T.timeRangeInvalid);
      return;
    }
    const allTimedEntries = [];
    await Promise.all(visibleRows.map(async (dev) => {
      try {
        const text = await fetchFilteredLogText(dev);
        const filteredPayload = buildFilteredLogText(dev, text);
        for (const entry of (filteredPayload.filteredEntries || [])) {
          if (!Number.isFinite(entry.timestampMs)) continue;
          allTimedEntries.push({ timestampMs: entry.timestampMs, deviceId: dev.device_id });
        }
      } catch (e) {
        // ignore per-device trend failure
      }
    }));
    if (requestId !== logTrendRequestSeq) return;
    if (!allTimedEntries.length) {
      clearTrendPanel(T.trendNoData);
      return;
    }
    if (isTrendCollapsed()) {
      const totalTimed = allTimedEntries.length;
      const devicesHitCollapsed = new Set(allTimedEntries.map((item) => item.deviceId)).size;
      setTrendMetric(0, UI_LANG === 'en' ? 'Total Logs' : '总日志', `${totalTimed}`);
      setTrendMetric(1, UI_LANG === 'en' ? 'Peak Window' : '峰值时间窗', '-');
      setTrendMetric(2, UI_LANG === 'en' ? 'Avg / Bucket' : '平均每桶', '-');
      setTrendMetric(3, UI_LANG === 'en' ? 'Devices Hit' : '命中设备数', `${devicesHitCollapsed}/${visibleRows.length}`);
      renderTrendSummary(summaryEl, visibleRows.length, 0, startMs, endMs);
      chartEl.innerHTML = '';
      return;
    }
    const chartWidth = Math.max(720, Math.round((chartEl.getBoundingClientRect().width || chartEl.clientWidth || 920)));
    const targetBuckets = Math.max(24, Math.min(180, Math.floor((chartWidth - 56) / 14)));
    const bucketMs = chooseTrendBucketMs(endMs - startMs || 60 * 1000, targetBuckets);
    const bucketCount = Math.max(1, Math.ceil(((endMs - startMs) + 1000) / bucketMs));
    const deviceMeta = visibleRows.map((dev) => ({
      deviceId: dev.device_id,
      label: String(dev.device_name || dev.device_ip || dev.device_id),
    }));
    const buckets = Array.from({ length: bucketCount }, (_, index) => ({
      index,
      startMs: startMs + (index * bucketMs),
      endMs: Math.min(endMs, startMs + ((index + 1) * bucketMs) - 1),
      count: 0,
      devices: new Set(),
      deviceCounts: {},
    }));
    allTimedEntries.forEach((entry) => {
      const idx = Math.min(bucketCount - 1, Math.max(0, Math.floor((entry.timestampMs - startMs) / bucketMs)));
      buckets[idx].count += 1;
      buckets[idx].devices.add(entry.deviceId);
      buckets[idx].deviceCounts[entry.deviceId] = Number(buckets[idx].deviceCounts[entry.deviceId] || 0) + 1;
    });
    const series = deviceMeta
      .map((device) => ({
        ...device,
        counts: buckets.map((bucket) => Number(bucket.deviceCounts[device.deviceId] || 0)),
      }))
      .filter((device) => device.counts.some((count) => count > 0));
    const totalLogs = buckets.reduce((sum, bucket) => sum + bucket.count, 0);
    const peakBucket = buckets.reduce((best, bucket) => (bucket.count > best.count ? bucket : best), buckets[0]);
    const devicesHit = new Set(allTimedEntries.map((item) => item.deviceId)).size;
    const avgPerBucket = totalLogs / buckets.length;
    const filteredStartMs = Math.min(...allTimedEntries.map((item) => item.timestampMs));
    const filteredEndMs = Math.max(...allTimedEntries.map((item) => item.timestampMs));
    setTrendMetric(0, UI_LANG === 'en' ? 'Total Logs' : '总日志', `${totalLogs}`);
    setTrendMetric(1, UI_LANG === 'en' ? 'Peak Window' : '峰值时间窗', `${formatShortTime(peakBucket.startMs)} - ${formatShortTime(peakBucket.endMs)}`);
    setTrendMetric(2, UI_LANG === 'en' ? 'Avg / Bucket' : '平均每桶', avgPerBucket.toFixed(avgPerBucket >= 10 ? 0 : 1));
    setTrendMetric(3, UI_LANG === 'en' ? 'Devices Hit' : '命中设备数', `${devicesHit}/${visibleRows.length}`);
    renderTrendSummary(summaryEl, visibleRows.length, bucketMs, startMs, endMs);
    renderTrendChart(chartEl, {
      buckets,
      peakCount: peakBucket.count,
      series,
      rangeStartMs: startMs,
      rangeEndMs: endMs,
      filteredStartMs,
      filteredEndMs,
    });
    bindTrendBrushInteractions();
  }

  async function applyLogTimeRange(reset = false) {
    await ensureGlobalTimeRangeState();
    if (reset) {
      logTimeRangeState.start = logTimeRangeState.defaultStart || '';
      logTimeRangeState.end = logTimeRangeState.defaultEnd || '';
    } else {
      const startInput = String(el('log-time-start')?.value || '').trim();
      const endInput = String(el('log-time-end')?.value || '').trim();
      logTimeRangeState.start = startInput || logTimeRangeState.defaultStart || '';
      logTimeRangeState.end = endInput || logTimeRangeState.defaultEnd || '';
    }
    syncTimeRangeInputs();
    persistDebugTimeRangeState();
    await renderLogTrendPanel();
    await runLogSearch();
  }

  function getCurrentLogDevice() {
    const devices = (latestTask && latestTask.devices) ? latestTask.devices : INITIAL_TASK_DEVICES;
    return getSelectedDevice(getVisibleLogDevices(devices));
  }

  function updateDebugFullscreenButton() {
    const btn = el('debug-fullscreen-btn');
    const viewer = document.querySelector('.device-log-viewer');
    if (!btn || !viewer) return;
    const active = document.fullscreenElement === viewer || viewer.classList.contains('debug-fullscreen-fallback');
    btn.classList.toggle('is-active', active);
    btn.title = active ? T.exitFullscreen : T.fullscreen;
    btn.setAttribute('aria-label', active ? T.exitFullscreen : T.fullscreen);
    btn.setAttribute('aria-pressed', active ? 'true' : 'false');
  }

  async function toggleDebugFullscreen() {
    const viewer = document.querySelector('.device-log-viewer');
    if (!viewer) return;
    try {
      if (document.fullscreenElement === viewer) {
        await document.exitFullscreen();
      } else if (document.fullscreenEnabled && typeof viewer.requestFullscreen === 'function') {
        await viewer.requestFullscreen();
      } else {
        debugConsolePseudoFullscreen = !viewer.classList.contains('debug-fullscreen-fallback');
        viewer.classList.toggle('debug-fullscreen-fallback', debugConsolePseudoFullscreen);
        document.body.classList.toggle('debug-fullscreen-body-lock', debugConsolePseudoFullscreen);
      }
    } catch (e) {
      debugConsolePseudoFullscreen = !viewer.classList.contains('debug-fullscreen-fallback');
      viewer.classList.toggle('debug-fullscreen-fallback', debugConsolePseudoFullscreen);
      document.body.classList.toggle('debug-fullscreen-body-lock', debugConsolePseudoFullscreen);
    }
    updateDebugFullscreenButton();
  }

  function getAutoHighlightPresetId(device) {
    const raw = String((device && [device.vendor, device.os_family, device.log_source].filter(Boolean).join(' ')) || '').toLowerCase();
    if (raw.includes('huawei')) return 'huawei_alarm';
    if (raw.includes('nxos')) return 'nxos_syslog';
    if (raw.includes('iosxr') || raw.includes('xr')) return 'iosxr_routing';
    return 'default';
  }

  function getEffectiveHighlightPresetId(device) {
    const chosen = String(selectedHighlightPresetId || 'auto');
    return chosen === 'auto' ? getAutoHighlightPresetId(device) : chosen;
  }

  function renderHighlightPresetOptions() {
    const select = el('log-highlight-preset');
    if (!select) return;
    const current = selectedHighlightPresetId || 'auto';
    const items = [`<option value="auto">${UI_LANG === 'en' ? 'Auto by vendor' : '按厂商自动匹配'}</option>`];
    highlightPresetItems.forEach((item) => {
      const sourceLabel = item.source === 'custom'
        ? (UI_LANG === 'en' ? 'custom' : '自定义')
        : (UI_LANG === 'en' ? 'builtin' : '内置');
      items.push(`<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}${item.source === 'custom' ? ` (${sourceLabel})` : ''}</option>`);
    });
    select.innerHTML = items.join('');
    select.value = current;
  }

  function updateHighlightPresetMeta(device) {
    const meta = el('log-highlight-preset-meta');
    if (!meta) return;
    const effectiveId = getEffectiveHighlightPresetId(device);
    const item = highlightPresetMap[effectiveId] || null;
    if (!item) {
      meta.textContent = '';
      return;
    }
    const modeLabel = String(selectedHighlightPresetId || 'auto') === 'auto'
      ? (UI_LANG === 'en' ? `Auto => ${item.name}` : `自动 => ${item.name}`)
      : item.name;
    const desc = item.description || '';
    meta.textContent = desc ? `${modeLabel} | ${desc}` : modeLabel;
  }

  async function ensureHighlightPresetDetail(presetId) {
    const normalized = String(presetId || '').trim();
    if (!normalized || normalized === 'auto') return null;
    const existing = highlightPresetMap[normalized];
    if (existing && existing.colors) return existing;
    const res = await fetch(`/api/log-highlight/presets/${encodeURIComponent(normalized)}`);
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    if (!data || !data.ok || !data.preset) throw new Error('preset load failed');
    highlightPresetMap[normalized] = data.preset;
    return highlightPresetMap[normalized];
  }

  async function loadHighlightPresets() {
    const saved = window.localStorage.getItem(HIGHLIGHT_PRESET_STORAGE_KEY);
    if (saved) selectedHighlightPresetId = saved;
    const res = await fetch('/api/log-highlight/presets');
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    highlightPresetItems = Array.isArray(data.items) ? data.items : [];
    highlightPresetItems.forEach((item) => {
      highlightPresetMap[item.id] = { ...(highlightPresetMap[item.id] || {}), ...item };
    });
    if (selectedHighlightPresetId !== 'auto' && !highlightPresetMap[selectedHighlightPresetId]) {
      selectedHighlightPresetId = 'auto';
    }
    renderHighlightPresetOptions();
    const currentDevice = getCurrentLogDevice();
    await ensureHighlightPresetDetail(getEffectiveHighlightPresetId(currentDevice));
    updateHighlightPresetMeta(currentDevice);
  }

  function getSyntaxRulesForDevice(device) {
    const presetId = getEffectiveHighlightPresetId(device);
    const preset = highlightPresetMap[presetId];
    if (!preset || !preset.colors) return [];
    const merged = [];
    for (const color of LOG_COLOR_ORDER) {
      const rules = Array.isArray(preset.colors[color]) ? preset.colors[color] : [];
      rules.forEach((pattern) => merged.push({ cls: `log-color-${color}`, pattern }));
    }
    return merged;
  }

  function buildHighlightedLogHtml(text, keyword, activeIndex, device) {
    const source = String(text || '');
    const needle = String(keyword || '').trim();
    if (!needle) return { html: applySyntaxHighlighting(source, device), count: 0 };

    const lowerSource = source.toLowerCase();
    const lowerNeedle = needle.toLowerCase();
    const hits = [];
    let pos = 0;
    while (true) {
      const idx = lowerSource.indexOf(lowerNeedle, pos);
      if (idx === -1) break;
      hits.push({ start: idx, end: idx + needle.length });
      pos = idx + needle.length;
    }
    if (!hits.length) return { html: applySyntaxHighlighting(source, device), count: 0 };

    const active = Math.max(0, Math.min(Number(activeIndex || 0), hits.length - 1));
    let cursor = 0;
    const chunks = [];
    hits.forEach((hit, index) => {
      chunks.push(applySyntaxHighlighting(source.slice(cursor, hit.start), device));
      const cls = index === active ? 'log-hit log-hit-active' : 'log-hit';
      chunks.push(`<span class="${cls}" data-hit-index="${index}">${applySyntaxHighlighting(source.slice(hit.start, hit.end), device)}</span>`);
      cursor = hit.end;
    });
    chunks.push(applySyntaxHighlighting(source.slice(cursor), device));
    return { html: chunks.join(''), count: hits.length };
  }

  function rangesOverlap(startA, endA, startB, endB) {
    return startA < endB && startB < endA;
  }

  function collectSyntaxRanges(line, device) {
    const ranges = [];
    for (const rule of getSyntaxRulesForDevice(device)) {
      const regex = new RegExp(rule.pattern, 'gi');
      let match;
      while ((match = regex.exec(line)) !== null) {
        const raw = match[0] || '';
        if (!raw) {
          regex.lastIndex += 1;
          continue;
        }
        const start = match.index;
        const end = start + raw.length;
        const overlapped = ranges.some((item) => rangesOverlap(start, end, item.start, item.end));
        if (!overlapped) {
          ranges.push({ start, end, cls: rule.cls });
        }
      }
    }
    ranges.sort((a, b) => a.start - b.start || a.end - b.end);
    return ranges;
  }

  function highlightSyntaxLine(line, device) {
    const source = String(line || '');
    const ranges = collectSyntaxRanges(source, device);
    if (!ranges.length) return escapeHtml(source);
    let cursor = 0;
    const chunks = [];
    for (const item of ranges) {
      chunks.push(escapeHtml(source.slice(cursor, item.start)));
      chunks.push(`<span class="${item.cls}">${escapeHtml(source.slice(item.start, item.end))}</span>`);
      cursor = item.end;
    }
    chunks.push(escapeHtml(source.slice(cursor)));
    return chunks.join('');
  }

  function applySyntaxHighlighting(text, device) {
    return String(text || '')
      .split('\n')
      .map((line) => highlightSyntaxLine(line, device))
      .join('\n');
  }

  function updateLegendCounts(consoleEl) {
    const root = consoleEl || el('debug-console');
    document.querySelectorAll('.legend-chip[data-legend-kind]').forEach((node) => {
      const kind = String(node.dataset.legendKind || '').trim();
      const countNode = node.querySelector('.legend-count');
      if (!countNode) return;
      if (!root || !kind) {
        countNode.textContent = '0';
        return;
      }
      countNode.textContent = String(root.querySelectorAll(`.log-color-${kind}`).length || 0);
    });
  }

  function renderConsoleText(consoleEl, text, keyword, activeIndex, device) {
    if (!consoleEl) return 0;
    const result = buildHighlightedLogHtml(text || T.debugEmpty, keyword || '', activeIndex, device);
    consoleEl.innerHTML = result.html || escapeHtml(text || '');
    updateLegendCounts(consoleEl);
    const activeNode = consoleEl.querySelector('.log-hit-active');
    if (activeNode) {
      activeNode.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
    return result.count;
  }

  function navigateLegendKind(kind) {
    const consoleEl = el('debug-console');
    if (!consoleEl) return;
    const nodes = Array.from(consoleEl.querySelectorAll(`.log-color-${String(kind || '').trim()}`));
    if (!nodes.length) return;
    const nextIndex = Number.isFinite(Number(legendActiveIndex[kind])) ? ((Number(legendActiveIndex[kind]) + 1) % nodes.length) : 0;
    legendActiveIndex[kind] = nextIndex;
    consoleEl.querySelectorAll('.legend-hit-active').forEach((node) => node.classList.remove('legend-hit-active'));
    const node = nodes[nextIndex];
    node.classList.add('legend-hit-active');
    node.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }

  async function runLogSearch() {
    const statusEl = el('log-search-status');
    const rows = (latestTask && Array.isArray(latestTask.devices)) ? latestTask.devices : [];
    const visibleRows = getVisibleLogDevices(rows);
    const keyword = logSearchKeyword.trim();
    const requestId = ++logSearchRequestSeq;

    Object.keys(logSearchCounts).forEach((key) => delete logSearchCounts[key]);
    Object.keys(logSearchActiveIndex).forEach((key) => delete logSearchActiveIndex[key]);
    await ensureGlobalTimeRangeState();
    await refreshDisplayedLogCounts(visibleRows);
    if (!keyword) {
      if (statusEl) statusEl.textContent = '';
      renderLogDeviceList(rows);
      await renderLogTrendPanel();
      await pollDebug();
      return;
    }

    if (statusEl) {
      statusEl.textContent = UI_LANG === 'en' ? 'Searching...' : '搜索中...';
    }

    await Promise.all(visibleRows.map(async (dev) => {
      try {
        const text = await fetchFilteredLogText(dev);
        const filteredPayload = buildFilteredLogText(dev, text);
        const hits = countKeywordHits(filteredPayload.text, keyword);
        if (hits > 0) logSearchCounts[dev.device_id] = hits;
      } catch (e) {
        // ignore individual device fetch failure
      }
    }));

    if (requestId !== logSearchRequestSeq) return;

    const matchedDevices = Object.keys(logSearchCounts).length;
    const totalHits = Object.values(logSearchCounts).reduce((sum, value) => sum + Number(value || 0), 0);
    if (statusEl) {
      statusEl.textContent = matchedDevices > 0
        ? (UI_LANG === 'en'
            ? `Matched devices: ${matchedDevices} | Total hits: ${totalHits}`
            : `命中设备: ${matchedDevices} | 总命中: ${totalHits}`)
        : (UI_LANG === 'en' ? 'No matches' : '无命中');
    }
    renderLogDeviceList(rows);
    await renderLogTrendPanel();
    await pollDebug();
  }

  function scheduleLogSearch() {
    if (logSearchTimer) window.clearTimeout(logSearchTimer);
    logSearchTimer = window.setTimeout(runLogSearch, 250);
  }

  async function poll() {
    const res = await fetch(`/api/tasks/${TASK_ID}`);
    if (!res.ok) return;
    const task = await res.json();
    latestTask = task;
    document.getElementById('task-status').innerText = task.status;
    document.getElementById('task-progress').innerText = `${task.progress_done}/${task.progress_total}`;

    const tbody = document.querySelector('#dev-table tbody');
    tbody.innerHTML = '';
    for (const d of task.devices) {
      const tr = document.createElement('tr');
      const sqlLinks = d.filtered_log_path && d.log_source === 'sql_log_server'
        ? ` | <a href="/api/tasks/${TASK_ID}/devices/${d.device_id}/device-filtered?lang=${LANG_Q}">filtered_device.log</a> | <a href="/api/tasks/${TASK_ID}/devices/${d.device_id}/sql/raw?lang=${LANG_Q}">raw_sql.log</a> | <a href="/api/tasks/${TASK_ID}/devices/${d.device_id}/sql/filtered?lang=${LANG_Q}">filtered_sql.log</a>`
        : '';
      const semanticLinks = d.semantic_compact_exists
        ? `${d.filtered_log_path ? ' | ' : ''}<a href="/api/tasks/${TASK_ID}/devices/${d.device_id}/semantic/compact?lang=${LANG_Q}">semantic_compact.md</a>${d.semantic_index_exists ? ` | <a href="/api/tasks/${TASK_ID}/devices/${d.device_id}/semantic/index?lang=${LANG_Q}">semantic_index.json</a>` : ''}`
        : (d.semantic_index_exists ? `${d.filtered_log_path ? ' | ' : ''}<a href="/api/tasks/${TASK_ID}/devices/${d.device_id}/semantic/index?lang=${LANG_Q}">semantic_index.json</a>` : '');
      const baseFiltered = d.filtered_log_path ? `<a href="/api/tasks/${TASK_ID}/devices/${d.device_id}/log?lang=${LANG_Q}">filtered.log</a>` : '';
      const dl = `${baseFiltered}${semanticLinks}${sqlLinks}`;
      const dbg = d.debug_log_path ? `<a href="/api/tasks/${TASK_ID}/devices/${d.device_id}/debug?lang=${LANG_Q}">debug.log</a>` : '';
      tr.innerHTML = `
        <td>${d.device_id}</td>
        <td>${d.device_ip}</td>
        <td>${d.device_name || ''}</td>
        <td>${d.status}</td>
        <td>${d.vendor || ''}</td>
        <td>${d.log_source || ''}</td>
        <td>${d.offset_seconds == null ? '' : d.offset_seconds}</td>
        <td>${d.hits_count == null ? 0 : d.hits_count}</td>
        <td>${d.reason || ''}</td>
        <td>${dl}</td>
        <td>${dbg}</td>`;
      tbody.appendChild(tr);
    }
    if (DEBUG_ENABLED) {
      renderLogDeviceList(task.devices);
      renderLogTrendPanel();
      pollDebug();
    }

    if (task.status === 'running' || task.status === 'pending') {
      setTimeout(poll, 2000);
    }
  }

  const DEBUG_ENABLED = !!TASK_DETAIL_BOOT.debugMode;

  async function pollDebug() {
    const consoleEl = document.getElementById('debug-console');
    const titleEl = document.getElementById('selected-log-device');
    const metaEl = document.getElementById('selected-log-meta');
    const filteredLinkEl = document.getElementById('filtered-download-link');
    const linkEl = document.getElementById('debug-download-link');
    if (!consoleEl || !linkEl || !filteredLinkEl) return;
    const devices = (latestTask && latestTask.devices) ? latestTask.devices : INITIAL_TASK_DEVICES;
    const visibleDevices = getVisibleLogDevices(devices);
    const dev = getSelectedDevice(visibleDevices);
    if (!dev) {
      selectedLogDeviceId = '';
      if (titleEl) titleEl.textContent = T.noDevice;
      if (metaEl) metaEl.textContent = '';
      filteredLinkEl.removeAttribute('href');
      linkEl.removeAttribute('href');
      consoleEl.textContent = T.selectDevice;
      return;
    }
    selectedLogDeviceId = dev.device_id;
    await ensureHighlightPresetDetail(getEffectiveHighlightPresetId(dev));
    updateHighlightPresetMeta(dev);
    if (titleEl) titleEl.textContent = dev.device_name || dev.device_ip || dev.device_id;
    if (metaEl) metaEl.textContent = [dev.device_id, dev.device_ip, `${getDeviceDisplayedLogCount(dev)} ${T.trendLogsUnit}`].filter(Boolean).join(' | ');
    const filteredUrl = `/api/tasks/${TASK_ID}/devices/${dev.device_id}/log?lang=${LANG_Q}`;
    filteredLinkEl.href = filteredUrl;
    if (dev.debug_log_path) {
      linkEl.href = `/api/tasks/${TASK_ID}/devices/${dev.device_id}/debug?lang=${LANG_Q}`;
    } else {
      linkEl.removeAttribute('href');
    }
    if (!dev.filtered_log_path) {
      consoleEl.textContent = dev.reason || T.debugPending;
      return;
    }
    try {
      await ensureGlobalTimeRangeState();
      await renderLogTrendPanel();
      const text = await fetchFilteredLogText(dev);
      if (text) {
        const filteredPayload = buildFilteredLogText(dev, text);
        logFilteredCounts[dev.device_id] = filteredPayload.invalid ? 0 : Number((filteredPayload.filteredEntries || []).length || 0);
        syncTimeRangeInputs();
        if (filteredPayload.invalid) {
          consoleEl.textContent = T.timeRangeInvalid;
          return;
        }
        if (!filteredPayload.text.trim()) {
          consoleEl.textContent = T.timeRangeNoMatch;
          return;
        }
        const hitCount = renderConsoleText(
          consoleEl,
          filteredPayload.text || T.debugEmpty,
          logSearchKeyword,
          logSearchActiveIndex[dev.device_id] || 0,
          dev,
        );
        if (logSearchKeyword.trim() && hitCount > 0 && Number(logSearchCounts[dev.device_id] || 0) !== hitCount) {
          logSearchCounts[dev.device_id] = hitCount;
          renderLogDeviceList(devices);
        }
      } else {
        consoleEl.textContent = dev.reason || T.debugPending;
      }
    } catch (e) {
      consoleEl.textContent = dev.reason || T.debugPending;
    }
  }
  if (DEBUG_ENABLED) restoreTrendMode();
  if (DEBUG_ENABLED) restoreTrendRangeMode();
  if (DEBUG_ENABLED) ensureGlobalTimeRangeState().then(() => { syncTimeRangeInputs(); renderLogTrendPanel(); }).catch(() => {});
  if (DEBUG_ENABLED) renderLogDeviceList(INITIAL_TASK_DEVICES);
  const trendModeTotalBtn = el('log-trend-mode-total');
  if (trendModeTotalBtn) {
    trendModeTotalBtn.addEventListener('click', async () => {
      if (currentTrendMode === 'total') return;
      setTrendMode('total');
      await renderLogTrendPanel();
    });
  }
  const trendModeStackedBtn = el('log-trend-mode-stacked');
  if (trendModeStackedBtn) {
    trendModeStackedBtn.addEventListener('click', async () => {
      if (currentTrendMode === 'stacked') return;
      setTrendMode('stacked');
      await renderLogTrendPanel();
    });
  }
  const trendRangeFullBtn = el('log-trend-range-full');
  if (trendRangeFullBtn) {
    trendRangeFullBtn.addEventListener('click', async () => {
      if (currentTrendRangeMode === 'full') return;
      setTrendRangeMode('full');
      await renderLogTrendPanel();
    });
  }
  const trendRangeFocusBtn = el('log-trend-range-focus');
  if (trendRangeFocusBtn) {
    trendRangeFocusBtn.addEventListener('click', async () => {
      if (currentTrendRangeMode === 'focus') return;
      setTrendRangeMode('focus');
      await renderLogTrendPanel();
    });
  }
  const deviceFilterInput = el('device-filter-keyword');
  if (deviceFilterInput) {
    const applyDeviceFilter = async () => {
      deviceFilterKeyword = deviceFilterInput.value || '';
      renderLogDeviceList((latestTask && latestTask.devices) ? latestTask.devices : INITIAL_TASK_DEVICES);
      await runLogSearch();
    };
    deviceFilterInput.addEventListener('keydown', async (event) => {
      if (event.key !== 'Enter') return;
      event.preventDefault();
      await applyDeviceFilter();
    });
  }
  const logSearchInput = el('log-search-keyword');
  if (logSearchInput) {
    logSearchInput.addEventListener('input', () => {
      logSearchKeyword = logSearchInput.value || '';
      scheduleLogSearch();
    });
  }
  const logTimeStartInput = el('log-time-start');
  const logTimeEndInput = el('log-time-end');
  if (logTimeStartInput) {
    logTimeStartInput.addEventListener('keydown', async (event) => {
      if (event.key !== 'Enter') return;
      event.preventDefault();
      await applyLogTimeRange(false);
    });
    logTimeStartInput.addEventListener('blur', async () => {
      if (String(logTimeStartInput.value || '').trim()) return;
      await ensureGlobalTimeRangeState();
      logTimeRangeState.start = logTimeRangeState.defaultStart || '';
      logTimeStartInput.value = logTimeRangeState.start;
      syncTimeRangeInputs();
    });
  }
  if (logTimeEndInput) {
    logTimeEndInput.addEventListener('keydown', async (event) => {
      if (event.key !== 'Enter') return;
      event.preventDefault();
      await applyLogTimeRange(false);
    });
    logTimeEndInput.addEventListener('blur', async () => {
      if (String(logTimeEndInput.value || '').trim()) return;
      await ensureGlobalTimeRangeState();
      logTimeRangeState.end = logTimeRangeState.defaultEnd || '';
      logTimeEndInput.value = logTimeRangeState.end;
      syncTimeRangeInputs();
    });
  }
  const logTimeApplyBtn = el('log-time-apply-btn');
  if (logTimeApplyBtn) {
    logTimeApplyBtn.addEventListener('click', async () => {
      await applyLogTimeRange(false);
    });
  }
  const logTimeResetBtn = el('log-time-reset-btn');
  if (logTimeResetBtn) {
    logTimeResetBtn.addEventListener('click', async () => {
      await applyLogTimeRange(true);
    });
  }
  const presetSelect = el('log-highlight-preset');
  if (presetSelect) {
    presetSelect.addEventListener('change', async () => {
      selectedHighlightPresetId = presetSelect.value || 'auto';
      window.localStorage.setItem(HIGHLIGHT_PRESET_STORAGE_KEY, selectedHighlightPresetId);
      try {
        const currentDevice = getCurrentLogDevice();
        await ensureHighlightPresetDetail(getEffectiveHighlightPresetId(currentDevice));
        updateHighlightPresetMeta(currentDevice);
        await pollDebug();
      } catch (e) {
        alert(`${T.presetLoadFailed}: ${e}`);
      }
    });
  }
  const presetImportBtn = el('log-highlight-import-btn');
  const presetImportFile = el('log-highlight-import-file');
  if (presetImportBtn && presetImportFile) {
    presetImportBtn.addEventListener('click', () => presetImportFile.click());
    presetImportFile.addEventListener('change', async () => {
      const file = presetImportFile.files && presetImportFile.files[0];
      if (!file) return;
      const form = new FormData();
      form.append('file', file);
      try {
        const res = await fetch('/api/log-highlight/presets/import', { method: 'POST', body: form });
        const data = await res.json();
        if (!res.ok || !data.ok || !data.preset) {
          throw new Error(data.detail || data.error || T.presetImportFailed);
        }
        highlightPresetMap[data.preset.id] = { ...(highlightPresetMap[data.preset.id] || {}), ...data.preset };
        await loadHighlightPresets();
        selectedHighlightPresetId = data.preset.id;
        window.localStorage.setItem(HIGHLIGHT_PRESET_STORAGE_KEY, selectedHighlightPresetId);
        renderHighlightPresetOptions();
        await ensureHighlightPresetDetail(selectedHighlightPresetId);
        updateHighlightPresetMeta(getCurrentLogDevice());
        await pollDebug();
      } catch (e) {
        alert(`${T.presetImportFailed}: ${e}`);
      } finally {
        presetImportFile.value = '';
      }
    });
  }
  const presetExportBtn = el('log-highlight-export-btn');
  const presetExportFormat = el('log-highlight-export-format');
  if (presetExportBtn && presetExportFormat) {
    presetExportBtn.addEventListener('click', async () => {
      try {
        const currentDevice = getCurrentLogDevice();
        const effectivePresetId = getEffectiveHighlightPresetId(currentDevice);
        await ensureHighlightPresetDetail(effectivePresetId);
        const fmt = presetExportFormat.value || 'json';
        window.open(`/api/log-highlight/presets/${encodeURIComponent(effectivePresetId)}/export?format=${encodeURIComponent(fmt)}`, '_blank');
      } catch (e) {
        alert(`${T.presetExportFailed}: ${e}`);
      }
    });
  }
  const debugFullscreenBtn = el('debug-fullscreen-btn');
  if (debugFullscreenBtn) {
    debugFullscreenBtn.addEventListener('click', toggleDebugFullscreen);
  }
  const trendToggleBtn = el('log-trend-toggle');
  if (trendToggleBtn) {
    trendToggleBtn.addEventListener('click', async () => {
      setTrendCollapsed(!isTrendCollapsed());
      await renderLogTrendPanel();
    });
  }
  const trendResetBtn = el('log-trend-reset');
  if (trendResetBtn) {
    trendResetBtn.addEventListener('click', async () => {
      await ensureGlobalTimeRangeState();
      logTimeRangeState.start = logTimeRangeState.defaultStart || '';
      logTimeRangeState.end = logTimeRangeState.defaultEnd || '';
      syncTimeRangeInputs();
      persistDebugTimeRangeState();
      showTrendFlash(T.trendExpandedRange);
      await renderLogTrendPanel();
      await runLogSearch();
    });
  }
  document.querySelectorAll('.legend-chip[data-legend-kind]').forEach((node) => {
    node.addEventListener('click', () => navigateLegendKind(node.dataset.legendKind || ''));
  });
  document.addEventListener('fullscreenchange', updateDebugFullscreenButton);
  document.addEventListener('fullscreenchange', scheduleTrendRerender);
  window.addEventListener('resize', scheduleTrendRerender);
  updateDebugFullscreenButton();
  restoreTrendCollapsed();

  async function startAnalysis() {
    const btn = document.getElementById('ai-start-btn');
    const statusEl = document.getElementById('ai-status');
    const outEl = document.getElementById('ai-output');
    if (!btn || !statusEl || !outEl) return;
    btn.disabled = true;
    statusEl.textContent = T.starting;
    outEl.innerHTML = '';
    updateAnalysisProgress(true, 0, `${T.progress}: 0% (${T.preparing})`);
    const selectedDeviceIds = getSelectedAiDeviceIds();
    if (!selectedDeviceIds.length) {
      btn.disabled = false;
      statusEl.textContent = T.noSelectedDevices;
      return;
    }
    const payload = {
      selected_device_ids: selectedDeviceIds,
      selected_system_prompt: val('selected_system_prompt', ''),
      selected_task_prompt: val('selected_task_prompt', ''),
      batched_analysis: checked('batched_analysis'),
      fragmented_analysis: checked('fragmented_analysis'),
      text_compression_strategy: val('text_compression_strategy', 'template_vars'),
      sql_log_inclusion_mode: val('sql_log_inclusion_mode', 'final_only'),
      analysis_parallelism: parseInt(val('analysis_parallelism', '2'), 10),
      chunk_parallelism: parseInt(val('chunk_parallelism', '1'), 10),
      max_tokens_per_chunk: parseInt(val('max_tokens_per_chunk', '4500'), 10),
      max_chunks_per_device: parseInt(val('max_chunks_per_device', '12'), 10),
      chunk_strategy: val('chunk_strategy', 'hybrid'),
      analysis_retries: parseInt(val('analysis_retries', '1'), 10),
      llm_call_timeout_sec: parseInt(val('llm_call_timeout_sec', '240'), 10),
      analysis_time_start: logTimeRangeState.start || logTimeRangeState.defaultStart || '',
      analysis_time_end: logTimeRangeState.end || logTimeRangeState.defaultEnd || '',
    };
    const res = await fetch(`/api/tasks/${TASK_ID}/analysis/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      btn.disabled = false;
      statusEl.textContent = T.startFailed;
      return;
    }
    const data = await res.json();
    currentAnalysisId = data.analysis_id;
    statusEl.textContent = `${T.running} (${currentAnalysisId})`;
    setTimeout(pollAnalysis, 1000);
  }

  async function pollAnalysis() {
    const statusEl = document.getElementById('ai-status');
    const outEl = document.getElementById('ai-output');
    const btn = document.getElementById('ai-start-btn');
    if (!currentAnalysisId || !statusEl || !outEl || !btn) return;
    const res = await fetch(`/api/analysis/${currentAnalysisId}`);
    if (!res.ok) {
      statusEl.textContent = T.queryFailed;
      btn.disabled = false;
      return;
    }
    const data = await res.json();
    statusEl.textContent = `${data.status} | provider=${data.provider_used || '-'} | model=${data.model_used || '-'}`;
    const p = Number(data.progress_percent || 0);
    const stageText = data.progress_text || '';
    const progressLine = stageText ? `${T.progress}: ${p}% | ${stageText}` : `${T.progress}: ${p}%`;
    updateAnalysisProgress(
      true,
      p,
      progressLine
    );
    if (data.status === 'success') {
      latestAnalysisText = data.result || '';
      outEl.innerHTML = renderMarkdown(latestAnalysisText);
      updateAnalysisProgress(true, 100, `${T.progress}: 100% | ${data.progress_text || T.phaseDone}`);
      loadAnalysisHistory();
      btn.disabled = false;
      return;
    }
    if (data.status === 'failed') {
      outEl.innerHTML = renderMarkdown(data.error || T.analysisFailed);
      updateAnalysisProgress(true, p || 1, `${T.progress}: ${p || 1}% | ${data.progress_text || T.phaseFailed}`);
      loadAnalysisHistory();
      btn.disabled = false;
      return;
    }
    setTimeout(pollAnalysis, 2000);
  }

  function renderHistoryList(items) {
    const box = document.getElementById('analysis-history-list');
    if (!box) return;
    if (!items || !items.length) {
      box.textContent = T.noHistory;
      return;
    }
    const rows = items.map((x) => {
      const aid = x.analysis_id || '-';
      const status = x.status || '-';
      const created = x.created_at || '-';
      const provider = x.provider_used || '-';
      const model = x.model_used || '-';
      const md = x.markdown_file
        ? `<a href="/api/tasks/${TASK_ID}/analysis/history/${encodeURIComponent(x.markdown_file)}?lang=${LANG_Q}" target="_blank">.md</a>`
        : '';
      const js = x.json_file
        ? `<a href="/api/tasks/${TASK_ID}/analysis/history/${encodeURIComponent(x.json_file)}?lang=${LANG_Q}" target="_blank">.json</a>`
        : '';
      return `<div class="history-row"><code>${aid}</code> | ${created} | ${status} | ${provider} | ${model} ${md} ${js}</div>`;
    }).join('');
    box.innerHTML = rows;
  }

  async function loadAnalysisHistory() {
    const box = document.getElementById('analysis-history-list');
    if (box) box.textContent = T.loadingHistory;
    try {
      const res = await fetch(`/api/tasks/${TASK_ID}/analysis/history?limit=12`);
      if (!res.ok) {
        renderHistoryList([]);
        return;
      }
      const data = await res.json();
      renderHistoryList((data && data.items) ? data.items : []);
    } catch (e) {
      renderHistoryList([]);
    }
  }

  async function restoreAnalysisState() {
    const statusEl = document.getElementById('ai-status');
    const outEl = document.getElementById('ai-output');
    const btn = document.getElementById('ai-start-btn');
    try {
      const activeRes = await fetch(`/api/tasks/${TASK_ID}/analysis/active`);
      if (activeRes.ok) {
        const activeData = await activeRes.json();
        if (activeData && activeData.active && activeData.analysis_id) {
          currentAnalysisId = String(activeData.analysis_id);
          if (statusEl) statusEl.textContent = `${T.running} (${currentAnalysisId})`;
          if (btn) btn.disabled = true;
          setTimeout(pollAnalysis, 600);
          return;
        }
      }
    } catch (e) {
      // continue fallback
    }
    try {
      const latestRes = await fetch(`/api/tasks/${TASK_ID}/analysis/latest`);
      if (!latestRes.ok) return;
      const latestData = await latestRes.json();
      if (!latestData || !latestData.found || !latestData.status) return;
      const data = latestData.status;
      const p = Number(data.progress_percent || 0);
      const stageText = data.progress_text || '';
      if (statusEl) statusEl.textContent = `${data.status || '-'} | provider=${data.provider_used || '-'} | model=${data.model_used || '-'}`;
      if (outEl && (data.result || data.error)) {
        latestAnalysisText = data.result || '';
        outEl.innerHTML = renderMarkdown(data.result || data.error || '');
      }
      if (stageText || p > 0) {
        const progressLine = stageText ? `${T.progress}: ${p}% | ${stageText}` : `${T.progress}: ${p}%`;
        updateAnalysisProgress(true, p, progressLine);
      }
      if (statusEl && data.status !== 'running') {
        statusEl.textContent += ` | ${T.latestLoaded}`;
      }
    } catch (e) {
      // ignore
    }
  }

  async function runPrecheck() {
    const box = document.getElementById('analysis-precheck-box');
    if (box) box.textContent = T.estimateLoading;
    const selectedDeviceIds = getSelectedAiDeviceIds();
    if (!selectedDeviceIds.length) {
      if (box) box.textContent = T.noSelectedDevices;
      return;
    }
    const payload = {
      selected_device_ids: selectedDeviceIds,
      selected_system_prompt: val('selected_system_prompt', ''),
      selected_task_prompt: val('selected_task_prompt', ''),
      batched_analysis: checked('batched_analysis'),
      fragmented_analysis: checked('fragmented_analysis'),
      text_compression_strategy: val('text_compression_strategy', 'template_vars'),
      sql_log_inclusion_mode: val('sql_log_inclusion_mode', 'final_only'),
      analysis_parallelism: parseInt(val('analysis_parallelism', '2'), 10),
      chunk_parallelism: parseInt(val('chunk_parallelism', '1'), 10),
      max_tokens_per_chunk: parseInt(val('max_tokens_per_chunk', '4500'), 10),
      max_chunks_per_device: parseInt(val('max_chunks_per_device', '12'), 10),
      chunk_strategy: val('chunk_strategy', 'hybrid'),
      analysis_retries: parseInt(val('analysis_retries', '1'), 10),
      llm_call_timeout_sec: parseInt(val('llm_call_timeout_sec', '240'), 10),
      analysis_time_start: logTimeRangeState.start || logTimeRangeState.defaultStart || '',
      analysis_time_end: logTimeRangeState.end || logTimeRangeState.defaultEnd || '',
    };
    try {
      const res = await fetch(`/api/tasks/${TASK_ID}/analysis/precheck`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        throw new Error(data.detail || data.error || T.estimateFailed);
      }
      if (box) box.textContent = data.line || T.estimateNotCalculated;
    } catch (e) {
      if (box) box.textContent = `${T.estimateFailurePrefix} (${e})`;
    }
  }

  function renderPreviewUnit() {
    const data = analysisPreviewData || {};
    const units = Array.isArray(data.units) ? data.units : [];
    const unit = units[analysisPreviewIndex] || null;
    const metaEl = el('analysis-preview-meta');
    const sysEl = el('analysis-preview-system');
    const taskEl = el('analysis-preview-task');
    const contentEl = el('analysis-preview-content');
    if (!unit) {
      if (metaEl) metaEl.textContent = T.previewNoUnits;
      if (sysEl) sysEl.value = data.system_prompt_text || '';
      if (taskEl) taskEl.value = '';
      if (contentEl) contentEl.value = '';
      return;
    }
    if (metaEl) {
      const sqlParts = Array.isArray(unit.attached_sql_sections) && unit.attached_sql_sections.length
        ? unit.attached_sql_sections.join(',')
        : 'none';
      metaEl.textContent = [
        `${data.provider || '-'} | ${data.model_used || '-'}`,
        `strategy=${data.compression_strategy || 'template_vars'}`,
        `sql=${data.sql_log_inclusion_mode || 'final_only'}`,
        `time=${data.analysis_time_start || '-'}~${data.analysis_time_end || '-'}`,
        `sql_sections=${sqlParts}`,
        `scope=${unit.scope || '-'}`,
        `unit=${analysisPreviewIndex + 1}/${units.length}`,
        `tokens≈${unit.estimated_tokens || 0}`,
      ].join(' | ');
    }
    if (sysEl) sysEl.value = data.system_prompt_text || '';
    if (taskEl) taskEl.value = unit.task_prompt_text || '';
    if (contentEl) contentEl.value = unit.report_text || '';
  }

  function openPreviewModal() {
    const modal = el('analysis-preview-modal');
    if (modal) {
      modal.style.display = 'flex';
      document.body.style.overflow = 'hidden';
    }
  }

  function closePreviewModal() {
    const modal = el('analysis-preview-modal');
    if (modal) modal.style.display = 'none';
    document.body.style.overflow = '';
  }

  async function runAnalysisPreview() {
    const btn = el('analysis-preview-btn');
    const selectedDeviceIds = getSelectedAiDeviceIds();
    if (!selectedDeviceIds.length) {
      alert(T.noSelectedDevices);
      return;
    }
    if (btn) btn.disabled = true;
    const payload = {
      selected_device_ids: selectedDeviceIds,
      preview_device_id: selectedLogDeviceId || selectedDeviceIds[0],
      selected_system_prompt: val('selected_system_prompt', ''),
      selected_task_prompt: val('selected_task_prompt', ''),
      batched_analysis: checked('batched_analysis'),
      fragmented_analysis: checked('fragmented_analysis'),
      text_compression_strategy: val('text_compression_strategy', 'template_vars'),
      sql_log_inclusion_mode: val('sql_log_inclusion_mode', 'final_only'),
      analysis_parallelism: parseInt(val('analysis_parallelism', '2'), 10),
      chunk_parallelism: parseInt(val('chunk_parallelism', '1'), 10),
      max_tokens_per_chunk: parseInt(val('max_tokens_per_chunk', '4500'), 10),
      max_chunks_per_device: parseInt(val('max_chunks_per_device', '12'), 10),
      chunk_strategy: val('chunk_strategy', 'hybrid'),
      analysis_retries: parseInt(val('analysis_retries', '1'), 10),
      llm_call_timeout_sec: parseInt(val('llm_call_timeout_sec', '240'), 10),
      analysis_time_start: logTimeRangeState.start || logTimeRangeState.defaultStart || '',
      analysis_time_end: logTimeRangeState.end || logTimeRangeState.defaultEnd || '',
    };
    try {
      const metaEl = el('analysis-preview-meta');
      if (metaEl) metaEl.textContent = T.previewLoading;
      openPreviewModal();
      const res = await fetch(`/api/tasks/${TASK_ID}/analysis/preview`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        throw new Error(data.detail || data.error || T.previewFailed);
      }
      analysisPreviewData = data;
      analysisPreviewIndex = 0;
      renderPreviewUnit();
    } catch (e) {
      const metaEl = el('analysis-preview-meta');
      if (metaEl) metaEl.textContent = `${T.previewFailed}: ${e}`;
      if (el('analysis-preview-system')) el('analysis-preview-system').value = '';
      if (el('analysis-preview-task')) el('analysis-preview-task').value = '';
      if (el('analysis-preview-content')) el('analysis-preview-content').value = '';
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function saveAnalysis() {
    const outNode = document.getElementById('ai-output');
    const text = latestAnalysisText || ((outNode && outNode.innerText) ? outNode.innerText : '');
    if (!text.trim()) {
      alert(T.noAnalysisToSave);
      return;
    }
    const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `analysis_${TASK_ID}.txt`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(a.href);
  }

  function escapeHtml(text) {
    return String(text || '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }

  function renderInline(text) {
    let s = escapeHtml(text);
    s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
    s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/\*([^*]+)\*/g, '<em>$1</em>');
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    return s;
  }

  function renderMarkdown(mdText) {
    const text = String(mdText || '').replace(/\r\n/g, '\n');
    const lines = text.split('\n');
    const out = [];
    let inCode = false;
    let inUl = false;
    let inOl = false;

    const closeLists = () => {
      if (inUl) { out.push('</ul>'); inUl = false; }
      if (inOl) { out.push('</ol>'); inOl = false; }
    };

    for (const raw of lines) {
      const line = raw || '';
      if (line.trim().startsWith('```')) {
        closeLists();
        if (!inCode) {
          inCode = true;
          out.push('<pre><code>');
        } else {
          inCode = false;
          out.push('</code></pre>');
        }
        continue;
      }
      if (inCode) {
        out.push(escapeHtml(line) + '\n');
        continue;
      }

      if (!line.trim()) {
        closeLists();
        out.push('<div class="md-gap"></div>');
        continue;
      }

      const h = line.match(/^(#{1,6})\s+(.*)$/);
      if (h) {
        closeLists();
        const lv = h[1].length;
        out.push(`<h${lv}>${renderInline(h[2])}</h${lv}>`);
        continue;
      }

      const ul = line.match(/^\s*[-*+]\s+(.*)$/);
      if (ul) {
        if (!inUl) {
          if (inOl) { out.push('</ol>'); inOl = false; }
          out.push('<ul>');
          inUl = true;
        }
        out.push(`<li>${renderInline(ul[1])}</li>`);
        continue;
      }

      const ol = line.match(/^\s*\d+\.\s+(.*)$/);
      if (ol) {
        if (!inOl) {
          if (inUl) { out.push('</ul>'); inUl = false; }
          out.push('<ol>');
          inOl = true;
        }
        out.push(`<li>${renderInline(ol[1])}</li>`);
        continue;
      }

      const quote = line.match(/^\s*>\s?(.*)$/);
      if (quote) {
        closeLists();
        out.push(`<blockquote>${renderInline(quote[1])}</blockquote>`);
        continue;
      }

      closeLists();
      out.push(`<p>${renderInline(line)}</p>`);
    }
    closeLists();
    if (inCode) out.push('</code></pre>');
    return out.join('');
  }

  async function loadAiDefaults() {
    try {
      const res = await fetch('/api/ai/settings');
      if (!res.ok) return;
      const data = await res.json();
      const cfg = data.cfg || {};
      if (document.getElementById('batched_analysis')) document.getElementById('batched_analysis').checked = !!cfg.batched_analysis;
      if (document.getElementById('fragmented_analysis')) document.getElementById('fragmented_analysis').checked = !!cfg.fragmented_analysis;
      if (document.getElementById('text_compression_strategy')) {
        document.getElementById('text_compression_strategy').value = cfg.text_compression_strategy || (cfg.text_compression_enabled ? 'group_repeats' : 'template_vars');
      }
      if (document.getElementById('sql_log_inclusion_mode')) {
        document.getElementById('sql_log_inclusion_mode').value = cfg.sql_log_inclusion_mode || 'final_only';
      }
      if (document.getElementById('analysis_parallelism')) document.getElementById('analysis_parallelism').value = cfg.analysis_parallelism == null ? 2 : cfg.analysis_parallelism;
      if (document.getElementById('chunk_parallelism')) document.getElementById('chunk_parallelism').value = cfg.chunk_parallelism == null ? 1 : cfg.chunk_parallelism;
      if (document.getElementById('max_tokens_per_chunk')) document.getElementById('max_tokens_per_chunk').value = cfg.max_tokens_per_chunk == null ? 4500 : cfg.max_tokens_per_chunk;
      if (document.getElementById('max_chunks_per_device')) {
        document.getElementById('max_chunks_per_device').value = cfg.max_chunks_per_device == null ? ((cfg.large_report_chunk_items == null) ? 12 : cfg.large_report_chunk_items) : cfg.max_chunks_per_device;
      }
      if (document.getElementById('chunk_strategy')) document.getElementById('chunk_strategy').value = cfg.chunk_strategy || 'hybrid';
      if (document.getElementById('analysis_retries')) document.getElementById('analysis_retries').value = cfg.analysis_retries == null ? 1 : cfg.analysis_retries;
      if (document.getElementById('llm_call_timeout_sec')) document.getElementById('llm_call_timeout_sec').value = cfg.llm_call_timeout_sec == null ? 240 : cfg.llm_call_timeout_sec;
      if (document.getElementById('selected_system_prompt') && cfg.selected_system_prompt) {
        document.getElementById('selected_system_prompt').value = cfg.selected_system_prompt;
      }
      if (document.getElementById('selected_task_prompt') && cfg.selected_task_prompt) {
        document.getElementById('selected_task_prompt').value = cfg.selected_task_prompt;
      }
      const model = cfg.provider ? `${cfg.provider} | ${cfg[cfg.provider + '_model'] || '-'}` : '-';
      const aiStatus = document.getElementById('ai-status');
      if (aiStatus) aiStatus.textContent = `${T.currentModel}: ${model} | ${T.notStarted}`;
    } catch (e) {
      // ignore
    }
  }

  loadHighlightPresets().catch((e) => {
    const meta = el('log-highlight-preset-meta');
    if (meta) meta.textContent = `${T.presetLoadFailed}: ${e}`;
  }).finally(() => {
    if (typeof pollDebug === 'function') pollDebug();
  });
  setTimeout(poll, 1000);
