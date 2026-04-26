  function id(v) { return document.getElementById(v); }
  function on(v, ev, fn) {
    const el = id(v);
    if (el) el.addEventListener(ev, fn);
  }
  function parseJsonFromScript(idName) {
    try {
      const el = id(idName);
      const raw = el ? (el.textContent || '{}') : '{}';
      return JSON.parse(raw);
    } catch (e) {
      return {};
    }
  }
  const AI_SETTINGS_BOOT = window.AI_SETTINGS_BOOTSTRAP || {};
  const UI_LANG = AI_SETTINGS_BOOT.lang || 'zh';
  let savedKeyState = { ...(AI_SETTINGS_BOOT.hasKeys || {}) };
  let apiKeys = {
    chatgpt: '',
    deepseek: '',
    qwen: '',
    gemini: '',
    nvidia: ''
  };
  let systemPromptMap = parseJsonFromScript('system_prompt_map_json');
  let taskPromptMap = parseJsonFromScript('task_prompt_map_json');
  let systemPromptLabels = parseJsonFromScript('system_prompt_labels_json');
  let taskPromptLabels = parseJsonFromScript('task_prompt_labels_json');
  const aiDeviceInfoRows = parseJsonFromScript('ai_device_info_json');
  const aiDeviceInfoMap = Object.fromEntries((Array.isArray(aiDeviceInfoRows) ? aiDeviceInfoRows : []).map((item) => {
    const deviceId = String((item || {}).device_id || '').trim();
    return [deviceId, {
      device_id: deviceId,
      device_name: String((item || {}).device_name || '').trim(),
      device_ip: String((item || {}).device_ip || (item || {}).ip || '').trim(),
      vendor: String((item || {}).vendor || '').trim(),
      status: String((item || {}).status || '').trim(),
      log_source: String((item || {}).log_source || '').trim(),
      hits: String((item || {}).hits_count || (item || {}).hits || '').trim(),
    }];
  }));
  const PROMPT_USAGE = {
    system: {
      "网络日志诊断专家-平衡模式": UI_LANG === 'en'
        ? 'Balanced default. Suitable for most troubleshooting tasks: evidence-first, but not overly restrictive.'
        : '平衡默认模板。适合大多数排障场景：强调证据，但不过度限制分析空间。',
      "网络日志诊断专家-问题发现": UI_LANG === 'en'
        ? 'Best for broad issue discovery and batch screening. Encourages finding meaningful patterns beyond preset categories.'
        : '适合批量筛查和主动发现问题。鼓励识别预设类别之外的重要异常模式。',
      "网络日志诊断专家-严格模式": UI_LANG === 'en'
        ? 'Use when you need stricter evidence boundaries and standardized output structure.'
        : '适合需要更严格证据边界、固定输出结构的场景。',
      "网络日志诊断专家-变更评审": UI_LANG === 'en'
        ? 'Use when the main question is whether current anomalies are related to recent changes.'
        : '适合判断当前异常是否与近期变更相关。'
    },
    task: {
      "网络问题发现-通用分析": UI_LANG === 'en'
        ? 'General-purpose discovery template. Good first choice for most mixed network logs.'
        : '通用问题发现模板。适合作为大多数混合网络日志的首选分析模板。',
      "控制平面与路由异常": UI_LANG === 'en'
        ? 'Focus on routing, adjacency, FIB, convergence, and control-plane instability.'
        : '聚焦路由、邻接、FIB、收敛与控制平面不稳定问题。',
      "链路与物理层稳定性": UI_LANG === 'en'
        ? 'Focus on interface flaps, local/remote fault, optics, bundle states, and physical stability.'
        : '聚焦接口抖动、局端/远端 fault、光模块、聚合口状态和物理层稳定性。',
      "资源与容量风险分析": UI_LANG === 'en'
        ? 'Focus on CPU, memory, FIB, prefix scale, queues, and sustained resource pressure.'
        : '聚焦 CPU、内存、FIB、前缀规模、队列和持续性资源压力。',
      "日志异常诊断-标准版": UI_LANG === 'en'
        ? 'Legacy standard template. Broad but more report-oriented than discovery-oriented.'
        : '旧版标准模板。范围较广，但更偏汇总报告，而不是主动发现问题。',
      "BGP会话波动专项": UI_LANG === 'en'
        ? 'Use for BGP flap, holdtime expiry, max-prefix, and session instability analysis.'
        : '适合分析 BGP flap、holdtime expired、max-prefix 和会话波动。',
      "链路抖动与接口告警专项": UI_LANG === 'en'
        ? 'Use for link up/down, CRC/error, and interface alarm investigation.'
        : '适合分析链路 up/down、CRC/error 和接口告警问题。'
    }
  };

  function selectedModel(selectEl, customEl) {
    if (!selectEl) return '';
    const val = (selectEl.value || '').trim();
    if (val === '__custom__') return ((customEl && customEl.value) || '').trim();
    return val;
  }

  function refreshPromptSelect(kind, prompts, selectedName) {
    const selectEl = kind === 'system' ? id('selected_system_prompt') : id('selected_task_prompt');
    const labels = kind === 'system' ? systemPromptLabels : taskPromptLabels;
    if (!selectEl) return;
    while (selectEl.firstChild) selectEl.removeChild(selectEl.firstChild);
    Object.keys(prompts || {}).forEach((k) => {
      const opt = document.createElement('option');
      opt.value = k;
      opt.textContent = labels && labels[k] ? labels[k] : k;
      selectEl.appendChild(opt);
    });
    if (selectedName && (prompts || {})[selectedName]) {
      selectEl.value = selectedName;
    } else if (selectEl.options.length > 0) {
      selectEl.selectedIndex = 0;
    }
  }

  function updatePromptUsageHints() {
    const systemName = id('selected_system_prompt')?.value || '';
    const taskName = id('selected_task_prompt')?.value || '';
    const systemHint = id('system_prompt_usage');
    const taskHint = id('task_prompt_usage');
    if (systemHint) systemHint.textContent = (PROMPT_USAGE.system && PROMPT_USAGE.system[systemName]) || '';
    if (taskHint) taskHint.textContent = (PROMPT_USAGE.task && PROMPT_USAGE.task[taskName]) || '';
  }

  function syncCustomModelInput(selectId, wrapId, inputId) {
    const sel = id(selectId);
    const wrap = id(wrapId);
    if (!sel || !wrap) return;
    wrap.style.display = sel.value === '__custom__' ? '' : 'none';
    if (sel.value !== '__custom__') {
      const input = id(inputId);
      if (input) input.value = '';
    }
  }

  function getConfigFromUI() {
    const chatgptModel = selectedModel(id('chatgpt_model_select'), id('chatgpt_model_custom'));
    const codexModel = selectedModel(id('codex_model_select'), id('codex_model_custom'));
    const deepseekModel = selectedModel(id('deepseek_model_select'), id('deepseek_model_custom'));
    const qwenModel = selectedModel(id('qwen_model_select'), id('qwen_model_custom'));
    const geminiModel = selectedModel(id('gemini_model_select'), id('gemini_model_custom'));
    const nvidiaModel = selectedModel(id('nvidia_model_select'), id('nvidia_model_custom'));
    const localModel = selectedModel(id('local_model_select'), id('local_model_custom'));
    return {
      provider: id('provider_select').value,
      analysis_language: UI_LANG,
      chatgpt_api_key: apiKeys.chatgpt || '',
      deepseek_api_key: apiKeys.deepseek || '',
      qwen_api_key: apiKeys.qwen || '',
      gemini_api_key: apiKeys.gemini || '',
      nvidia_api_key: apiKeys.nvidia || '',
      chatgpt_model: chatgptModel,
      codex_model: codexModel,
      codex_cli_path: id('codex_cli_path').value,
      local_base_url: id('local_base_url').value,
      local_model: localModel,
      deepseek_model: deepseekModel,
      qwen_model: qwenModel,
      gemini_model: geminiModel,
      nvidia_model: nvidiaModel,
      selected_system_prompt: id('selected_system_prompt').value,
      selected_task_prompt: id('selected_task_prompt').value,
      system_prompt_extra: id('system_prompt_extra').value,
      task_prompt_extra: id('task_prompt_extra').value,
    };
  }

  function updateProviderSection() {
    const p = id('provider_select').value;
    id('chatgpt_settings').style.display = p === 'chatgpt' ? '' : 'none';
    id('codex_local_settings').style.display = p === 'codex_local' ? '' : 'none';
    id('deepseek_settings').style.display = p === 'deepseek' ? '' : 'none';
    id('qwen_settings').style.display = p === 'qwen' ? '' : 'none';
    id('gemini_settings').style.display = p === 'gemini' ? '' : 'none';
    id('nvidia_settings').style.display = p === 'nvidia' ? '' : 'none';
    id('local_settings').style.display = p === 'local' ? '' : 'none';
    syncCustomModelInput('chatgpt_model_select', 'chatgpt_custom_wrap', 'chatgpt_model_custom');
    syncCustomModelInput('codex_model_select', 'codex_custom_wrap', 'codex_model_custom');
    syncCustomModelInput('deepseek_model_select', 'deepseek_custom_wrap', 'deepseek_model_custom');
    syncCustomModelInput('qwen_model_select', 'qwen_custom_wrap', 'qwen_model_custom');
    syncCustomModelInput('gemini_model_select', 'gemini_custom_wrap', 'gemini_model_custom');
    syncCustomModelInput('nvidia_model_select', 'nvidia_custom_wrap', 'nvidia_model_custom');
    syncCustomModelInput('local_model_select', 'local_custom_wrap', 'local_model_custom');
    updateProviderIcon();
  }

  function updateProviderIcon() {
    const providerEl = id('provider_select');
    const providerBrandInlineEl = id('provider_brand_inline');
    if (!providerEl || !providerBrandInlineEl) return;
    const provider = (providerEl.value || 'chatgpt').trim();
    const svgDataUri = (bg, txt) => {
      const color = bg || '#334155';
      const svg =
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">' +
        '<rect width="24" height="24" rx="6" fill="' + color + '"/>' +
        '<text x="12" y="15" text-anchor="middle" font-size="9" font-family="Arial, sans-serif" fill="white">' + txt + '</text>' +
        '</svg>';
      return 'data:image/svg+xml;utf8,' + encodeURIComponent(svg);
    };
    const setBrandIcon = (url, alt, title, fallbackBg, fallbackLabel) => {
      const fb = svgDataUri(fallbackBg, fallbackLabel);
      providerBrandInlineEl.innerHTML = '<img src="' + url + '" alt="' + alt + '" title="' + title + '">';
      const img = providerBrandInlineEl.querySelector('img');
      if (img) {
        img.onerror = () => {
          img.onerror = null;
          img.src = fb;
        };
      }
    };
    const setGemmaIcon = () => {
      providerBrandInlineEl.innerHTML =
        '<svg viewBox="0 0 24 24" aria-hidden="true">' +
        '<circle cx="12" cy="12" r="9.5" fill="none" stroke="#c7d2fe" stroke-width="1.2"/>' +
        '<path d="M12 3.5v17M3.5 12h17M5.8 5.8l12.4 12.4M18.2 5.8L5.8 18.2" stroke="#e2e8f0" stroke-width="0.8"/>' +
        '<path d="M12 5.8l3.8 6.2L12 18.2 8.2 12z" fill="none" stroke="#3b82f6" stroke-width="1.6" stroke-linejoin="round"/>' +
        '</svg>';
    };
    const setLlamaIcon = () => {
      providerBrandInlineEl.innerHTML =
        '<svg viewBox="0 0 24 24" aria-hidden="true">' +
        '<path d="M6.2 12c1.6-3.5 3.1-3.5 5.8 0-2.7 3.5-4.2 3.5-5.8 0z" fill="none" stroke="#1d4ed8" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"/>' +
        '<path d="M12 12c1.6-3.5 3.1-3.5 5.8 0-2.7 3.5-4.2 3.5-5.8 0z" fill="none" stroke="#1d4ed8" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"/>' +
        '</svg>';
    };
    const iconMap = {
      openai: 'https://openai.com/favicon.ico',
      deepseek: 'https://www.deepseek.com/favicon.ico',
      gemini: 'https://www.gstatic.com/lamda/images/gemini_sparkle_aurora_33f86dc0c0257da337c63.svg',
      nvidia: 'https://www.nvidia.com/favicon.ico',
      lmstudio: 'https://lmstudio.ai/favicon.ico',
      qwen: 'https://upload.wikimedia.org/wikipedia/commons/6/69/Qwen_logo.svg',
      llama: 'https://upload.wikimedia.org/wikipedia/commons/thumb/8/89/Meta_Platforms_Inc._logo.svg/64px-Meta_Platforms_Inc._logo.svg.png',
      mistral: 'https://mistral.ai/favicon.ico',
      gemma: 'https://ai.google.dev/favicon.ico',
      claude: 'https://www.anthropic.com/favicon.ico',
      cohere: 'https://cohere.com/favicon.ico',
      grok: 'https://x.ai/favicon.ico',
      yi: 'https://www.lingyiwanwu.com/favicon.ico'
    };
    if (provider === 'chatgpt') {
      setBrandIcon(iconMap.openai, 'OpenAI', 'ChatGPT', '#10a37f', 'OA');
      return;
    }
    if (provider === 'codex_local') {
      setBrandIcon(iconMap.openai, 'Codex', 'Codex Local', '#111827', 'CX');
      return;
    }
    if (provider === 'deepseek') {
      setBrandIcon(iconMap.deepseek, 'DeepSeek', 'DeepSeek', '#2563eb', 'DS');
      return;
    }
    if (provider === 'qwen') {
      setBrandIcon(iconMap.qwen, 'QWEN', 'QWEN', '#ef4444', 'QW');
      return;
    }
    if (provider === 'gemini') {
      setBrandIcon(iconMap.gemini, 'Gemini', 'Gemini', '#3b82f6', 'GM');
      return;
    }
    if (provider === 'nvidia') {
      setBrandIcon(iconMap.nvidia, 'NVIDIA', 'NVIDIA', '#76b900', 'NV');
      return;
    }

    const localModelRaw = (selectedModel(id('local_model_select'), id('local_model_custom')).toLowerCase().trim());
    const vendorPrefix = (localModelRaw.split(/[-_/:.\s]/).filter(Boolean)[0] || 'lmstudio');
    const vendorAliasMap = {
      google: 'gemma',
      meta: 'llama',
      'meta-llama': 'llama',
      mixtral: 'mistral',
      'command-r': 'cohere',
      moonshot: 'kimi'
    };
    const localVendor = vendorAliasMap[vendorPrefix] || vendorPrefix;
    const fallbackMap = {
      qwen: ['#ef4444', 'QW'],
      deepseek: ['#2563eb', 'DS'],
      llama: ['#0ea5e9', 'LL'],
      mistral: ['#f59e0b', 'MS'],
      gemma: ['#3b82f6', 'GM'],
      claude: ['#8b5cf6', 'CL'],
      cohere: ['#0891b2', 'CO'],
      grok: ['#111827', 'GX'],
      yi: ['#059669', 'YI'],
      glm: ['#0f766e', 'GL'],
      baichuan: ['#0ea5e9', 'BC'],
      internlm: ['#9333ea', 'IL'],
      doubao: ['#1d4ed8', 'DB'],
      kimi: ['#ec4899', 'KM'],
      phi: ['#4f46e5', 'PH'],
      lmstudio: ['#334155', 'LM']
    };
    const fb = fallbackMap[localVendor] || fallbackMap.lmstudio;
    if (localVendor === 'gemma') {
      setGemmaIcon();
      return;
    }
    if (localVendor === 'llama') {
      setLlamaIcon();
      return;
    }
    const icon = iconMap[localVendor] || iconMap.lmstudio;
    setBrandIcon(icon, localVendor, localVendor.toUpperCase(), fb[0], fb[1]);
  }

  function updateApiKeyState() {
    const savedTxt = UI_LANG === 'en' ? 'Saved' : '已保存';
    const missTxt = UI_LANG === 'en' ? 'Not saved' : '未保存';
    const state = [
      `Codex Local: ${UI_LANG === 'en' ? 'No API Key required' : '无需 API Key'}`,
      `ChatGPT Key: ${(savedKeyState.chatgpt || !!apiKeys.chatgpt) ? savedTxt : missTxt}`,
      `DeepSeek Key: ${(savedKeyState.deepseek || !!apiKeys.deepseek) ? savedTxt : missTxt}`,
      `QWEN Key: ${(savedKeyState.qwen || !!apiKeys.qwen) ? savedTxt : missTxt}`,
      `Gemini Key: ${(savedKeyState.gemini || !!apiKeys.gemini) ? savedTxt : missTxt}`,
      `NVIDIA Key: ${(savedKeyState.nvidia || !!apiKeys.nvidia) ? savedTxt : missTxt}`,
    ];
    id('api_key_state').textContent = state.join(' | ');
  }

  async function saveSettings() {
    const res = await fetch('/api/ai/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(getConfigFromUI())
    });
    if (!res.ok) {
      alert(UI_LANG === 'en' ? 'Save failed' : '保存失败');
      return;
    }
    const data = await res.json();
    if (data && data.has_keys) {
      savedKeyState = data.has_keys;
    }
    apiKeys = { chatgpt: '', deepseek: '', qwen: '', gemini: '', nvidia: '' };
    updateApiKeyState();
    id('llm_test_result').textContent = UI_LANG === 'en' ? 'Configuration saved.' : '模型配置已保存。';
  }

  async function testConnection() {
    const cfg = getConfigFromUI();
    const fd = new FormData();
    Object.entries(cfg).forEach(([k, v]) => {
      if (typeof v === 'boolean') return;
      fd.append(k, String(v == null ? '' : v));
    });
    fd.append('provider', cfg.provider);
    id('llm_test_result').textContent = UI_LANG === 'en' ? 'Testing connection...' : '连接测试中...';
    try {
      const res = await fetch('/api/ai/test_connection', { method: 'POST', body: fd });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        id('llm_test_result').textContent = data.error || data.detail || (UI_LANG === 'en' ? 'Connection test failed' : '连接测试失败');
        return;
      }
      id('llm_test_result').textContent = data.message || (UI_LANG === 'en' ? 'Connection test succeeded.' : '连接测试成功。');
    } catch (e) {
      id('llm_test_result').textContent = `${UI_LANG === 'en' ? 'Connection test failed' : '连接测试失败'}: ${e}`;
    }
  }

  function reviewPrompt(kind) {
    const systemSel = id('selected_system_prompt');
    const taskSel = id('selected_task_prompt');
    const name = kind === 'system' ? ((systemSel && systemSel.value) || '') : ((taskSel && taskSel.value) || '');
    if (!name) {
      alert(UI_LANG === 'en' ? 'No template selected' : '未选择模板');
      return;
    }
    const src = kind === 'system' ? systemPromptMap : taskPromptMap;
    const content = src && src[name] ? String(src[name]) : '';
    if (!content) {
      alert(UI_LANG === 'en' ? 'Template content is empty' : '模板内容为空');
      return;
    }
    const titlePrefix = kind === 'system'
      ? (UI_LANG === 'en' ? 'System template' : '系统模板')
      : (UI_LANG === 'en' ? 'Task template' : '任务模板');
    const labels = kind === 'system' ? systemPromptLabels : taskPromptLabels;
    const shownName = labels && labels[name] ? labels[name] : name;
    id('prompt_modal_title').textContent = `${titlePrefix}: ${shownName}`;
    id('prompt_modal_text').value = content;
    const modal = id('prompt_modal');
    if (modal) {
      modal.dataset.kind = kind;
      modal.dataset.name = name;
    }
    const delBtn = id('delete_prompt_btn');
    if (delBtn) delBtn.disabled = false;
    if (modal) modal.style.display = 'flex';
  }

  async function savePromptEdit() {
    const modal = id('prompt_modal');
    const textEl = id('prompt_modal_text');
    if (!modal || !textEl) return;
    const kind = String(modal.dataset.kind || '').trim();
    const name = String(modal.dataset.name || '').trim();
    const text = String(textEl.value || '').trim();
    if (!kind || !name) {
      alert(UI_LANG === 'en' ? 'No template selected' : '未选择模板');
      return;
    }
    if (!text) {
      alert(UI_LANG === 'en' ? 'Template content cannot be empty' : '模板内容不能为空');
      return;
    }
    const res = await fetch('/api/ai/prompt_update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind, name, text }),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      alert(data.detail || data.error || (UI_LANG === 'en' ? 'Save failed' : '保存失败'));
      return;
    }
    if (kind === 'system') {
      systemPromptMap = data.prompts || {};
    } else {
      taskPromptMap = data.prompts || {};
    }
    refreshPromptSelect(kind, kind === 'system' ? systemPromptMap : taskPromptMap, data.name || name);
    updatePromptUsageHints();
    modal.dataset.name = data.name || name;
    alert(UI_LANG === 'en' ? 'Prompt saved' : '提示词已保存');
  }

  async function deletePromptEdit() {
    const modal = id('prompt_modal');
    if (!modal) return;
    const kind = String(modal.dataset.kind || '').trim();
    const name = String(modal.dataset.name || '').trim();
    if (!kind || !name) {
      alert(UI_LANG === 'en' ? 'No template selected' : '未选择模板');
      return;
    }
    const ok = window.confirm(UI_LANG === 'en' ? `Delete custom prompt "${name}"?` : `确认删除自定义模板「${name}」吗？`);
    if (!ok) return;
    const res = await fetch('/api/ai/prompt_delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind, name }),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      alert(data.detail || data.error || (UI_LANG === 'en' ? 'Delete failed' : '删除失败'));
      return;
    }
    if (kind === 'system') {
      systemPromptMap = data.prompts || {};
    } else {
      taskPromptMap = data.prompts || {};
    }
    refreshPromptSelect(kind, kind === 'system' ? systemPromptMap : taskPromptMap, '');
    updatePromptUsageHints();
    modal.style.display = 'none';
    alert(UI_LANG === 'en' ? 'Custom prompt deleted' : '自定义模板已删除');
  }

  async function importPrompt() {
    const file = id('prompt_file').files[0];
    if (!file) {
      alert(UI_LANG === 'en' ? 'Please select a prompt file' : '请选择提示词文件');
      return;
    }
    const fd = new FormData();
    fd.append('kind', id('prompt_kind_select').value);
    fd.append('name', id('prompt_name').value || '');
    fd.append('prompt_file', file);
    const res = await fetch('/api/ai/import_prompt', { method: 'POST', body: fd });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      alert(data.detail || data.message || (UI_LANG === 'en' ? 'Import failed' : '导入失败'));
      return;
    }
    window.location.reload();
  }

  on('provider_select', 'change', updateProviderSection);
  on('chatgpt_model_select', 'change', updateProviderSection);
  on('codex_model_select', 'change', updateProviderSection);
  on('deepseek_model_select', 'change', updateProviderSection);
  on('qwen_model_select', 'change', updateProviderSection);
  on('gemini_model_select', 'change', updateProviderSection);
  on('nvidia_model_select', 'change', updateProviderSection);
  on('local_model_select', 'change', updateProviderSection);
  on('chatgpt_model_custom', 'input', updateProviderIcon);
  on('codex_model_custom', 'input', updateProviderIcon);
  on('deepseek_model_custom', 'input', updateProviderIcon);
  on('qwen_model_custom', 'input', updateProviderIcon);
  on('gemini_model_custom', 'input', updateProviderIcon);
  on('nvidia_model_custom', 'input', updateProviderIcon);
  on('local_model_custom', 'input', updateProviderIcon);
  on('save_llm_btn', 'click', saveSettings);
  on('test_llm_btn', 'click', testConnection);
  on('review_system_template_btn', 'click', () => reviewPrompt('system'));
  on('review_task_template_btn', 'click', () => reviewPrompt('task'));
  on('selected_system_prompt', 'change', updatePromptUsageHints);
  on('selected_task_prompt', 'change', updatePromptUsageHints);
  on('import_prompt_btn', 'click', importPrompt);
  on('import_api_key_btn', 'click', () => {
    const provider = id('provider_select').value;
    if (provider === 'codex_local' || provider === 'local') {
      alert(UI_LANG === 'en' ? 'This provider does not use an API Key.' : '该 provider 不使用 API Key。');
      return;
    }
    const key = window.prompt(UI_LANG === 'en' ? `Enter ${provider.toUpperCase()} API Key` : `请输入 ${provider.toUpperCase()} API Key`);
    if (!key) return;
    const map = {
      chatgpt: 'chatgpt',
      deepseek: 'deepseek',
      qwen: 'qwen',
      gemini: 'gemini',
      nvidia: 'nvidia'
    };
    const target = map[provider];
    if (target) apiKeys[target] = key;
    updateApiKeyState();
  });
  on('prompt_modal_close', 'click', () => {
    const modal = id('prompt_modal');
    if (modal) {
      modal.style.display = 'none';
      modal.dataset.kind = '';
      modal.dataset.name = '';
    }
  });
  on('save_prompt_edit_btn', 'click', savePromptEdit);
  on('delete_prompt_btn', 'click', deletePromptEdit);
  on('cancel_prompt_edit_btn', 'click', () => {
    const modal = id('prompt_modal');
    if (modal) {
      modal.style.display = 'none';
      modal.dataset.kind = '';
      modal.dataset.name = '';
    }
  });
  on('prompt_modal', 'click', (e) => {
    const modal = id('prompt_modal');
    if (modal && e.target === modal) modal.style.display = 'none';
  });
  on('prompt_file', 'change', () => {
    const fileEl = id('prompt_file');
    const fileNameEl = id('prompt_file_name');
    const f = fileEl && fileEl.files ? fileEl.files[0] : null;
    if (fileNameEl) fileNameEl.textContent = f ? f.name : (UI_LANG === 'en' ? 'No file selected' : '未选择文件');
  });

  const AI_TASK_ID = String(AI_SETTINGS_BOOT.aiTaskId || '');
  const AI_WORKSPACE_STORAGE_KEY = AI_TASK_ID ? `netlog.ai.workspace.v2.${AI_TASK_ID}` : '';
  const AI_LAST_TASK_STORAGE_KEY = 'netlog.ai.last_task_id';
  const DEBUG_TIME_RANGE_STORAGE_KEY = AI_TASK_ID ? `netlog.debug.timeRange.v1.${AI_TASK_ID}` : '';
  let currentAnalysisId = null;
  let latestAnalysisText = '';
  let analysisPreviewData = null;
  let analysisPreviewIndex = 0;
  let currentPreviewDeviceId = String(AI_SETTINGS_BOOT.initialPreviewDeviceId || '');
  let currentDeviceFilter = '';
  const selectedAiDeviceIds = new Set(Array.isArray(AI_SETTINGS_BOOT.initialAiDeviceIds) ? AI_SETTINGS_BOOT.initialAiDeviceIds : []);
  let workspaceRestoreDone = false;

  function readAiWorkspaceState() {
    try {
      const raw = window.localStorage.getItem(AI_WORKSPACE_STORAGE_KEY);
      if (!raw) return null;
      const data = JSON.parse(raw);
      return data && typeof data === 'object' ? data : null;
    } catch (e) {
      return null;
    }
  }

  function rememberLastAiTaskId() {
    try {
      if (AI_TASK_ID) window.localStorage.setItem(AI_LAST_TASK_STORAGE_KEY, AI_TASK_ID);
    } catch (e) {
      // ignore localStorage failures
    }
  }

  function readSharedDebugTimeRange() {
    if (!DEBUG_TIME_RANGE_STORAGE_KEY) return { start: '', end: '' };
    try {
      const raw = window.localStorage.getItem(DEBUG_TIME_RANGE_STORAGE_KEY);
      if (!raw) return { start: '', end: '' };
      const data = JSON.parse(raw);
      return {
        start: String(data && data.start || '').trim(),
        end: String(data && data.end || '').trim(),
      };
    } catch (e) {
      return { start: '', end: '' };
    }
  }

  function clearSharedDebugTimeRange() {
    if (!DEBUG_TIME_RANGE_STORAGE_KEY) return;
    try {
      window.localStorage.removeItem(DEBUG_TIME_RANGE_STORAGE_KEY);
    } catch (e) {
      // ignore localStorage failures
    }
  }

  function refreshAnalysisTimeRangeUi() {
    const box = id('analysis-time-range-box');
    const textEl = id('analysis-time-range-text');
    const clearBtn = id('analysis-time-range-clear');
    if (!box || !textEl || !clearBtn) return;
    const range = readSharedDebugTimeRange();
    const start = String(range.start || '').trim();
    const end = String(range.end || '').trim();
    const hasRange = !!(start || end);
    textEl.textContent = hasRange
      ? `${start || '-'} ~ ${end || '-'}`
      : (UI_LANG === 'en' ? 'Full log time range' : '使用全部日志时间范围');
    box.classList.toggle('is-empty', !hasRange);
    clearBtn.disabled = !hasRange;
  }

  function writeAiWorkspaceState() {
    try {
      const state = {
        task_id: AI_TASK_ID,
        provider: id('provider_select')?.value || '',
        chatgpt_model_select: id('chatgpt_model_select')?.value || '',
        chatgpt_model_custom: id('chatgpt_model_custom')?.value || '',
        codex_model_select: id('codex_model_select')?.value || '',
        codex_model_custom: id('codex_model_custom')?.value || '',
        codex_cli_path: id('codex_cli_path')?.value || '',
        deepseek_model_select: id('deepseek_model_select')?.value || '',
        deepseek_model_custom: id('deepseek_model_custom')?.value || '',
        qwen_model_select: id('qwen_model_select')?.value || '',
        qwen_model_custom: id('qwen_model_custom')?.value || '',
        gemini_model_select: id('gemini_model_select')?.value || '',
        gemini_model_custom: id('gemini_model_custom')?.value || '',
        nvidia_model_select: id('nvidia_model_select')?.value || '',
        nvidia_model_custom: id('nvidia_model_custom')?.value || '',
        local_base_url: id('local_base_url')?.value || '',
        local_model_select: id('local_model_select')?.value || '',
        local_model_custom: id('local_model_custom')?.value || '',
        selected_system_prompt: id('selected_system_prompt')?.value || '',
        selected_task_prompt: id('selected_task_prompt')?.value || '',
        system_prompt_extra: id('system_prompt_extra')?.value || '',
        task_prompt_extra: id('task_prompt_extra')?.value || '',
        batched_analysis: !!id('batched_analysis')?.checked,
        fragmented_analysis: !!id('fragmented_analysis')?.checked,
        text_compression_strategy: id('text_compression_strategy')?.value || 'template_vars',
        sql_log_inclusion_mode: id('sql_log_inclusion_mode')?.value || 'final_only',
        analysis_parallelism: id('analysis_parallelism')?.value || '2',
        chunk_parallelism: id('chunk_parallelism')?.value || '1',
        max_tokens_per_chunk: id('max_tokens_per_chunk')?.value || '4500',
        max_chunks_per_device: id('max_chunks_per_device')?.value || '12',
        chunk_strategy: id('chunk_strategy')?.value || 'hybrid',
        analysis_retries: id('analysis_retries')?.value || '1',
        llm_call_timeout_sec: id('llm_call_timeout_sec')?.value || '240',
        analysis_preview_strategy: id('analysis-preview-strategy')?.value || 'use_current',
        analysis_time_start: readSharedDebugTimeRange().start,
        analysis_time_end: readSharedDebugTimeRange().end,
        selected_device_ids: Array.from(selectedAiDeviceIds),
        current_preview_device_id: currentPreviewDeviceId || '',
        current_device_filter: currentDeviceFilter || '',
        analysis_precheck_text: id('analysis-precheck-box')?.textContent || '',
      };
      window.localStorage.setItem(AI_WORKSPACE_STORAGE_KEY, JSON.stringify(state));
    } catch (e) {
      // ignore localStorage failures
    }
  }

  function restoreAiWorkspaceState() {
    const state = readAiWorkspaceState();
    if (!state || state.task_id !== AI_TASK_ID) return;
    const setValue = (key, fallback = '') => {
      const node = id(key);
      if (node && Object.prototype.hasOwnProperty.call(state, key)) {
        node.value = state[key] == null ? fallback : String(state[key]);
      }
    };
    const setChecked = (key) => {
      const node = id(key);
      if (node && Object.prototype.hasOwnProperty.call(state, key)) {
        node.checked = !!state[key];
      }
    };
    setValue('provider_select');
    setValue('chatgpt_model_select');
    setValue('chatgpt_model_custom');
    setValue('codex_model_select');
    setValue('codex_model_custom');
    setValue('codex_cli_path');
    setValue('deepseek_model_select');
    setValue('deepseek_model_custom');
    setValue('qwen_model_select');
    setValue('qwen_model_custom');
    setValue('gemini_model_select');
    setValue('gemini_model_custom');
    setValue('nvidia_model_select');
    setValue('nvidia_model_custom');
    setValue('local_base_url');
    setValue('local_model_select');
    setValue('local_model_custom');
    setValue('selected_system_prompt');
    setValue('selected_task_prompt');
    setValue('system_prompt_extra');
    setValue('task_prompt_extra');
    setChecked('batched_analysis');
    setChecked('fragmented_analysis');
    setValue('text_compression_strategy', 'template_vars');
    setValue('sql_log_inclusion_mode', 'final_only');
    setValue('analysis_parallelism', '2');
    setValue('chunk_parallelism', '1');
    setValue('max_tokens_per_chunk', '4500');
    setValue('max_chunks_per_device', '12');
    setValue('chunk_strategy', 'hybrid');
    setValue('analysis_retries', '1');
    setValue('llm_call_timeout_sec', '240');
    setValue('analysis-preview-strategy', 'use_current');

    const savedIds = Array.isArray(state.selected_device_ids) ? state.selected_device_ids.map((x) => String(x || '').trim()).filter(Boolean) : [];
    if (savedIds.length) {
      selectedAiDeviceIds.clear();
      savedIds.forEach((x) => selectedAiDeviceIds.add(x));
    }
    currentPreviewDeviceId = String(state.current_preview_device_id || currentPreviewDeviceId || '');
    currentDeviceFilter = String(state.current_device_filter || '');
    if (id('ai-device-filter')) id('ai-device-filter').value = currentDeviceFilter;
    const precheck = String(state.analysis_precheck_text || '').trim();
    if (precheck && id('analysis-precheck-box')) {
      id('analysis-precheck-box').textContent = precheck;
    }
    updateProviderSection();
    updateProviderIcon();
    refreshAnalysisTimeRangeUi();
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

  function enhanceDeviceRefs(root) {
    if (!root) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        const parent = node.parentElement;
        if (!parent) return NodeFilter.FILTER_REJECT;
        const tag = parent.tagName;
        if (['A', 'CODE', 'PRE', 'SCRIPT', 'STYLE', 'TEXTAREA'].includes(tag)) return NodeFilter.FILTER_REJECT;
        if (!String(node.nodeValue || '').match(/\bdev-\d+\b/)) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    const re = /\b(dev-\d+)\b/g;
    nodes.forEach((node) => {
      const source = String(node.nodeValue || '');
      let match;
      let last = 0;
      let changed = false;
      const frag = document.createDocumentFragment();
      while ((match = re.exec(source)) !== null) {
        const deviceId = match[1];
        const info = aiDeviceInfoMap[deviceId];
        if (!info) continue;
        changed = true;
        if (match.index > last) frag.appendChild(document.createTextNode(source.slice(last, match.index)));
        const chip = document.createElement('span');
        chip.className = 'device-ref-chip';
        chip.textContent = info.device_name || deviceId;
        chip.title = [
          `device_id: ${info.device_id || deviceId}`,
          `device_name: ${info.device_name || '-'}`,
          `ip: ${info.device_ip || '-'}`,
          `vendor: ${info.vendor || '-'}`,
          `status: ${info.status || '-'}`,
          `log_source: ${info.log_source || '-'}`,
          `hits: ${info.hits || '-'}`,
        ].join('\n');
        frag.appendChild(chip);
        last = match.index + deviceId.length;
      }
      if (!changed) return;
      if (last < source.length) frag.appendChild(document.createTextNode(source.slice(last)));
      node.parentNode.replaceChild(frag, node);
    });
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
        if (!inCode) { inCode = true; out.push('<pre><code>'); }
        else { inCode = false; out.push('</code></pre>'); }
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

  function getSelectedAiDeviceIds() {
    return Array.from(selectedAiDeviceIds);
  }

  function getVisibleAiRows() {
    return Array.from(document.querySelectorAll('.ai-device-row')).filter((row) => !row.classList.contains('is-hidden-by-filter'));
  }

  function applyAiDeviceFilter() {
    const keyword = String(currentDeviceFilter || '').trim().toLowerCase();
    const rows = Array.from(document.querySelectorAll('.ai-device-row'));
    rows.forEach((row) => {
      const cells = row.querySelectorAll('td');
      const deviceName = (cells[3]?.textContent || '').trim().toLowerCase();
      const matched = !keyword || deviceName.includes(keyword);
      row.classList.toggle('is-hidden-by-filter', !matched);
    });
    const visibleRows = getVisibleAiRows();
    if (visibleRows.length && (!currentPreviewDeviceId || !visibleRows.some((row) => row.dataset.deviceId === currentPreviewDeviceId))) {
      currentPreviewDeviceId = visibleRows[0].dataset.deviceId || '';
    }
    document.querySelectorAll('.ai-device-row').forEach((row) => {
      row.classList.toggle('is-current', row.dataset.deviceId === currentPreviewDeviceId && !row.classList.contains('is-hidden-by-filter'));
    });
  }

  function syncAiDeviceSelectionUi() {
    const allBox = id('ai-select-all');
    document.querySelectorAll('.ai-device-select').forEach((node) => {
      const deviceId = node.dataset.deviceId || '';
      node.checked = selectedAiDeviceIds.has(deviceId);
      node.addEventListener('change', () => {
        if (node.checked) selectedAiDeviceIds.add(deviceId);
        else selectedAiDeviceIds.delete(deviceId);
        if (allBox) {
          const allItems = Array.from(document.querySelectorAll('.ai-device-select'));
          allBox.checked = allItems.length > 0 && allItems.every((item) => item.checked);
          allBox.indeterminate = allItems.some((item) => item.checked) && !allBox.checked;
        }
        writeAiWorkspaceState();
      });
    });
    if (allBox) {
      const allItems = Array.from(document.querySelectorAll('.ai-device-select'));
      allBox.checked = allItems.length > 0 && allItems.every((item) => item.checked);
      allBox.indeterminate = allItems.some((item) => item.checked) && !allBox.checked;
      allBox.addEventListener('change', () => {
        if (allBox.checked) {
          allItems.forEach((item) => {
            item.checked = true;
            selectedAiDeviceIds.add(item.dataset.deviceId || '');
          });
        } else {
          allItems.forEach((item) => {
            item.checked = false;
            selectedAiDeviceIds.delete(item.dataset.deviceId || '');
          });
        }
        allBox.indeterminate = false;
        writeAiWorkspaceState();
      });
    }
    document.querySelectorAll('.ai-device-row').forEach((row) => {
      row.addEventListener('click', (event) => {
        if (event.target && event.target.closest('.ai-device-select')) return;
        if (row.classList.contains('is-hidden-by-filter')) return;
        currentPreviewDeviceId = row.dataset.deviceId || '';
        document.querySelectorAll('.ai-device-row').forEach((r) => r.classList.toggle('is-current', r === row));
        writeAiWorkspaceState();
      });
    });
    applyAiDeviceFilter();
  }

  function updateAnalysisProgress(visible, percent, text) {
    const box = id('analysis-progress-box');
    const txt = id('analysis-progress-text');
    const fill = id('analysis-progress-fill');
    if (box) box.style.display = visible ? '' : 'none';
    if (txt) txt.textContent = text || `${UI_LANG === 'en' ? 'Progress' : '进度'}: ${percent || 0}%`;
    if (fill) fill.style.width = `${Math.max(0, Math.min(100, Number(percent || 0)))}%`;
  }

  function refreshAiStatusModelLine(suffix) {
    const cfg = getConfigFromUI();
    const provider = cfg.provider || '-';
    const modelMap = {
      chatgpt: cfg.chatgpt_model,
      codex_local: cfg.codex_model,
      deepseek: cfg.deepseek_model,
      qwen: cfg.qwen_model,
      gemini: cfg.gemini_model,
      nvidia: cfg.nvidia_model,
      local: cfg.local_model,
    };
    const text = `${UI_LANG === 'en' ? 'Current model' : '当前模型'}: ${provider} | ${modelMap[provider] || '-'}${suffix ? ` | ${suffix}` : ''}`;
    const statusEl = id('ai-status');
    if (statusEl) statusEl.textContent = text;
  }

  function setAnalysisRunState(running) {
    const startBtn = id('ai-start-btn');
    const stopBtn = id('ai-stop-btn');
    if (startBtn) startBtn.disabled = !!running;
    if (stopBtn) stopBtn.disabled = !running;
  }

  async function startTaskAnalysis() {
    const btn = id('ai-start-btn');
    const outEl = id('ai-output');
    setAnalysisRunState(true);
    if (outEl) outEl.innerHTML = '';
    const selectedDeviceIds = getSelectedAiDeviceIds();
    if (!selectedDeviceIds.length) {
      alert(UI_LANG === 'en' ? 'Select at least one device for AI analysis' : '请至少勾选一台设备用于 AI 分析');
      setAnalysisRunState(false);
      return;
    }
    refreshAiStatusModelLine(UI_LANG === 'en' ? 'starting...' : '启动中...');
    updateAnalysisProgress(true, 0, `${UI_LANG === 'en' ? 'Progress' : '进度'}: 0%`);
    const payload = {
      ...getConfigFromUI(),
      selected_device_ids: selectedDeviceIds,
      batched_analysis: !!id('batched_analysis')?.checked,
      fragmented_analysis: !!id('fragmented_analysis')?.checked,
      text_compression_strategy: id('text_compression_strategy')?.value || 'template_vars',
      sql_log_inclusion_mode: id('sql_log_inclusion_mode')?.value || 'final_only',
      analysis_parallelism: parseInt(id('analysis_parallelism')?.value || '2', 10),
      chunk_parallelism: parseInt(id('chunk_parallelism')?.value || '1', 10),
      max_tokens_per_chunk: parseInt(id('max_tokens_per_chunk')?.value || '4500', 10),
      max_chunks_per_device: parseInt(id('max_chunks_per_device')?.value || '12', 10),
      chunk_strategy: id('chunk_strategy')?.value || 'hybrid',
      analysis_retries: parseInt(id('analysis_retries')?.value || '1', 10),
      llm_call_timeout_sec: parseInt(id('llm_call_timeout_sec')?.value || '240', 10),
      analysis_time_start: readSharedDebugTimeRange().start,
      analysis_time_end: readSharedDebugTimeRange().end,
    };
    const res = await fetch(`/api/tasks/${AI_TASK_ID}/analysis/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      refreshAiStatusModelLine(UI_LANG === 'en' ? 'start failed' : '启动失败');
      setAnalysisRunState(false);
      return;
    }
    currentAnalysisId = data.analysis_id;
    refreshAiStatusModelLine(`${UI_LANG === 'en' ? 'running' : '运行中'} (${currentAnalysisId})`);
    writeAiWorkspaceState();
    setTimeout(pollAnalysis, 1000);
  }

  async function pollAnalysis() {
    if (!currentAnalysisId) return;
    const res = await fetch(`/api/analysis/${currentAnalysisId}`);
    if (!res.ok) {
      refreshAiStatusModelLine(UI_LANG === 'en' ? 'query failed' : '查询失败');
      setAnalysisRunState(false);
      return;
    }
    const data = await res.json();
    const p = Number(data.progress_percent || 0);
    const progressLine = data.progress_text
      ? `${UI_LANG === 'en' ? 'Progress' : '进度'}: ${p}% | ${data.progress_text}`
      : `${UI_LANG === 'en' ? 'Progress' : '进度'}: ${p}%`;
    refreshAiStatusModelLine(`${data.status || '-'} | provider=${data.provider_used || '-'} | model=${data.model_used || '-'}`);
    updateAnalysisProgress(true, p, progressLine);
    if (data.status === 'success' || data.status === 'failed') {
      latestAnalysisText = data.result || '';
      id('ai-output').innerHTML = renderMarkdown(data.result || data.error || '');
      enhanceDeviceRefs(id('ai-output'));
      setAnalysisRunState(false);
      loadAnalysisHistory();
      writeAiWorkspaceState();
      return;
    }
    writeAiWorkspaceState();
    setTimeout(pollAnalysis, 2000);
  }

  async function stopTaskAnalysis() {
    const stopBtn = id('ai-stop-btn');
    if (stopBtn) stopBtn.disabled = true;
    try {
      const res = await fetch(`/api/tasks/${AI_TASK_ID}/analysis/stop`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        refreshAiStatusModelLine(UI_LANG === 'en' ? 'stop failed' : '停止失败');
        setAnalysisRunState(!!currentAnalysisId);
        return;
      }
      refreshAiStatusModelLine(UI_LANG === 'en' ? 'stopped' : '已停止');
      updateAnalysisProgress(true, Number((data.status || {}).progress_percent || 1), (data.status || {}).progress_text || (UI_LANG === 'en' ? 'Stopped' : '已停止'));
      currentAnalysisId = String(data.analysis_id || currentAnalysisId || '');
      setAnalysisRunState(false);
      loadAnalysisHistory();
      writeAiWorkspaceState();
    } catch (e) {
      refreshAiStatusModelLine(`${UI_LANG === 'en' ? 'stop failed' : '停止失败'}: ${e}`);
      setAnalysisRunState(!!currentAnalysisId);
    }
  }

  async function runPrecheck() {
    const box = id('analysis-precheck-box');
    if (box) box.textContent = UI_LANG === 'en' ? 'Estimate: calculating...' : '分析预估：计算中...';
    const selectedDeviceIds = getSelectedAiDeviceIds();
    if (!selectedDeviceIds.length) {
      if (box) box.textContent = UI_LANG === 'en' ? 'Select at least one device for AI analysis' : '请至少勾选一台设备用于 AI 分析';
      return;
    }
    const payload = {
      ...getConfigFromUI(),
      selected_device_ids: selectedDeviceIds,
      batched_analysis: !!id('batched_analysis')?.checked,
      fragmented_analysis: !!id('fragmented_analysis')?.checked,
      text_compression_strategy: id('text_compression_strategy')?.value || 'template_vars',
      sql_log_inclusion_mode: id('sql_log_inclusion_mode')?.value || 'final_only',
      analysis_parallelism: parseInt(id('analysis_parallelism')?.value || '2', 10),
      chunk_parallelism: parseInt(id('chunk_parallelism')?.value || '1', 10),
      max_tokens_per_chunk: parseInt(id('max_tokens_per_chunk')?.value || '4500', 10),
      max_chunks_per_device: parseInt(id('max_chunks_per_device')?.value || '12', 10),
      chunk_strategy: id('chunk_strategy')?.value || 'hybrid',
      analysis_retries: parseInt(id('analysis_retries')?.value || '1', 10),
      llm_call_timeout_sec: parseInt(id('llm_call_timeout_sec')?.value || '240', 10),
      analysis_time_start: readSharedDebugTimeRange().start,
      analysis_time_end: readSharedDebugTimeRange().end,
    };
    const res = await fetch(`/api/tasks/${AI_TASK_ID}/analysis/precheck`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      if (box) box.textContent = `${UI_LANG === 'en' ? 'Estimate failed' : '分析预估失败'}: ${data.error || data.detail || '-'}`;
      writeAiWorkspaceState();
      return;
    }
    if (box) box.textContent = data.line || '';
    writeAiWorkspaceState();
  }

  function renderPreviewUnit() {
    const data = analysisPreviewData || {};
    const units = Array.isArray(data.units) ? data.units : [];
    const unit = units[analysisPreviewIndex] || null;
    if (!unit) return;
    const sqlParts = Array.isArray(unit.attached_sql_sections) && unit.attached_sql_sections.length ? unit.attached_sql_sections.join(',') : 'none';
    id('analysis-preview-meta').textContent = [
      `${data.provider || '-'} | ${data.model_used || '-'}`,
      `strategy=${data.compression_strategy || 'template_vars'}`,
      `sql=${data.sql_log_inclusion_mode || 'final_only'}`,
      `time=${data.analysis_time_start || '-'}~${data.analysis_time_end || '-'}`,
      `sql_sections=${sqlParts}`,
      `scope=${unit.scope || '-'}`,
      `unit=${analysisPreviewIndex + 1}/${units.length}`,
      `tokens≈${unit.estimated_tokens || 0}`,
    ].join(' | ');
    id('analysis-preview-system').value = data.system_prompt_text || '';
    id('analysis-preview-task').value = unit.task_prompt_text || '';
    id('analysis-preview-content').value = unit.report_text || '';
  }

  function openPreviewModal() {
    const modal = id('analysis-preview-modal');
    if (modal) {
      modal.style.display = 'flex';
      document.body.style.overflow = 'hidden';
    }
  }

  function closePreviewModal() {
    const modal = id('analysis-preview-modal');
    if (modal) modal.style.display = 'none';
    document.body.style.overflow = '';
  }

  function clearAnalysisTimeRange() {
    clearSharedDebugTimeRange();
    refreshAnalysisTimeRangeUi();
    writeAiWorkspaceState();
  }

  async function runAnalysisPreview() {
    const selectedDeviceIds = getSelectedAiDeviceIds();
    if (!selectedDeviceIds.length) {
      alert(UI_LANG === 'en' ? 'Select at least one device for AI analysis' : '请至少勾选一台设备用于 AI 分析');
      return;
    }
    const payload = {
      ...getConfigFromUI(),
      selected_device_ids: selectedDeviceIds,
      preview_device_id: currentPreviewDeviceId || selectedDeviceIds[0],
      batched_analysis: !!id('batched_analysis')?.checked,
      fragmented_analysis: !!id('fragmented_analysis')?.checked,
      text_compression_strategy: (id('analysis-preview-strategy')?.value && id('analysis-preview-strategy').value !== 'use_current')
        ? id('analysis-preview-strategy').value
        : (id('text_compression_strategy')?.value || 'template_vars'),
      sql_log_inclusion_mode: id('sql_log_inclusion_mode')?.value || 'final_only',
      analysis_parallelism: parseInt(id('analysis_parallelism')?.value || '2', 10),
      chunk_parallelism: parseInt(id('chunk_parallelism')?.value || '1', 10),
      max_tokens_per_chunk: parseInt(id('max_tokens_per_chunk')?.value || '4500', 10),
      max_chunks_per_device: parseInt(id('max_chunks_per_device')?.value || '12', 10),
      chunk_strategy: id('chunk_strategy')?.value || 'hybrid',
      analysis_retries: parseInt(id('analysis_retries')?.value || '1', 10),
      llm_call_timeout_sec: parseInt(id('llm_call_timeout_sec')?.value || '240', 10),
      analysis_time_start: readSharedDebugTimeRange().start,
      analysis_time_end: readSharedDebugTimeRange().end,
    };
    id('analysis-preview-meta').textContent = UI_LANG === 'en' ? 'Loading preview...' : '预览加载中...';
    openPreviewModal();
    const res = await fetch(`/api/tasks/${AI_TASK_ID}/analysis/preview`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      id('analysis-preview-meta').textContent = `${UI_LANG === 'en' ? 'Preview failed' : '预览失败'}: ${data.error || data.detail || '-'}`;
      return;
    }
    analysisPreviewData = data;
    analysisPreviewIndex = 0;
    renderPreviewUnit();
    writeAiWorkspaceState();
  }

  function saveAnalysis() {
    const text = latestAnalysisText || (id('ai-output')?.innerText || '');
    if (!text.trim()) {
      alert(UI_LANG === 'en' ? 'No analysis result to save' : '暂无可保存的分析结果');
      return;
    }
    const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `analysis_${AI_TASK_ID}.txt`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(a.href);
  }

  function renderHistoryList(items) {
    const box = id('analysis-history-list');
    if (!box) return;
    if (!items || !items.length) {
      box.textContent = UI_LANG === 'en' ? 'No history yet.' : '暂无历史记录。';
      return;
    }
    box.innerHTML = items.map((x) => {
      const md = x.markdown_file ? `<a href="/api/tasks/${AI_TASK_ID}/analysis/history/${encodeURIComponent(x.markdown_file)}" target="_blank">.md</a>` : '';
      const js = x.json_file ? `<a href="/api/tasks/${AI_TASK_ID}/analysis/history/${encodeURIComponent(x.json_file)}" target="_blank">.json</a>` : '';
      return `<div class="history-row"><code>${x.analysis_id || '-'}</code> | ${x.created_at || '-'} | ${x.status || '-'} | ${x.provider_used || '-'} | ${x.model_used || '-'} ${md} ${js}</div>`;
    }).join('');
  }

  async function loadAnalysisHistory() {
    const box = id('analysis-history-list');
    if (box) box.textContent = UI_LANG === 'en' ? 'Loading history...' : '加载历史中...';
    const res = await fetch(`/api/tasks/${AI_TASK_ID}/analysis/history?limit=12`);
    const data = await res.json();
    renderHistoryList((data && data.items) ? data.items : []);
  }

  async function restoreAnalysisState() {
    const activeRes = await fetch(`/api/tasks/${AI_TASK_ID}/analysis/active`);
    if (activeRes.ok) {
      const activeData = await activeRes.json();
      if (activeData && activeData.active && activeData.analysis_id) {
        currentAnalysisId = String(activeData.analysis_id);
        id('ai-start-btn').disabled = true;
        writeAiWorkspaceState();
        setTimeout(pollAnalysis, 600);
        return;
      }
    }
    const latestRes = await fetch(`/api/tasks/${AI_TASK_ID}/analysis/latest`);
    if (!latestRes.ok) return;
    const latestData = await latestRes.json();
    if (!latestData || !latestData.found || !latestData.status) return;
    const data = latestData.status;
    latestAnalysisText = data.result || '';
    if (data.result || data.error) {
      id('ai-output').innerHTML = renderMarkdown(data.result || data.error || '');
      enhanceDeviceRefs(id('ai-output'));
    }
    const p = Number(data.progress_percent || 0);
    if (data.progress_text || p > 0) {
      const progressLine = data.progress_text ? `${UI_LANG === 'en' ? 'Progress' : '进度'}: ${p}% | ${data.progress_text}` : `${UI_LANG === 'en' ? 'Progress' : '进度'}: ${p}%`;
      updateAnalysisProgress(true, p, progressLine);
    }
    refreshAiStatusModelLine(`${data.status || '-'} | provider=${data.provider_used || '-'} | model=${data.model_used || '-'}`);
    setAnalysisRunState(String(data.status || '') === 'running');
    writeAiWorkspaceState();
  }

  on('analysis-precheck-btn', 'click', runPrecheck);
  on('analysis-preview-btn', 'click', runAnalysisPreview);
  on('ai-start-btn', 'click', startTaskAnalysis);
  on('ai-stop-btn', 'click', stopTaskAnalysis);
  on('save-analysis-btn', 'click', saveAnalysis);
  on('analysis-time-range-clear', 'click', clearAnalysisTimeRange);
  on('analysis-preview-refresh', 'click', runAnalysisPreview);
  on('analysis-preview-close', 'click', closePreviewModal);
  on('analysis-preview-modal', 'click', (e) => {
    const modal = id('analysis-preview-modal');
    if (modal && e.target === modal) closePreviewModal();
  });
  on('analysis-preview-prev', 'click', () => {
    const units = (analysisPreviewData && analysisPreviewData.units) || [];
    if (!units.length) return;
    analysisPreviewIndex = (analysisPreviewIndex - 1 + units.length) % units.length;
    renderPreviewUnit();
  });
  on('analysis-preview-next', 'click', () => {
    const units = (analysisPreviewData && analysisPreviewData.units) || [];
    if (!units.length) return;
    analysisPreviewIndex = (analysisPreviewIndex + 1) % units.length;
    renderPreviewUnit();
  });
  ['provider_select','chatgpt_model_select','codex_model_select','deepseek_model_select','qwen_model_select','gemini_model_select','nvidia_model_select','local_model_select'].forEach((name) => {
    on(name, 'change', () => refreshAiStatusModelLine(UI_LANG === 'en' ? 'not started' : '未启动'));
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closePreviewModal();
  });
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refreshAnalysisTimeRangeUi();
  });
  window.addEventListener('storage', (event) => {
    if (event.key === DEBUG_TIME_RANGE_STORAGE_KEY) refreshAnalysisTimeRangeUi();
  });
  on('ai-device-filter', 'keydown', (event) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    currentDeviceFilter = id('ai-device-filter')?.value || '';
    applyAiDeviceFilter();
    writeAiWorkspaceState();
  });
  [
    'provider_select','chatgpt_model_select','chatgpt_model_custom','codex_model_select','codex_model_custom',
    'codex_cli_path','deepseek_model_select','deepseek_model_custom','qwen_model_select','qwen_model_custom',
    'gemini_model_select','gemini_model_custom','nvidia_model_select','nvidia_model_custom','local_base_url',
    'local_model_select','local_model_custom','selected_system_prompt','selected_task_prompt','system_prompt_extra',
    'task_prompt_extra','batched_analysis','fragmented_analysis','text_compression_strategy','sql_log_inclusion_mode',
    'analysis_parallelism','chunk_parallelism','max_tokens_per_chunk','max_chunks_per_device','chunk_strategy',
    'analysis_retries','llm_call_timeout_sec','analysis-preview-strategy'
  ].forEach((fieldId) => {
    const node = id(fieldId);
    if (!node) return;
    const eventName = (node.tagName === 'SELECT' || node.type === 'checkbox' || node.type === 'number') ? 'change' : 'input';
    on(fieldId, eventName, writeAiWorkspaceState);
  });
  if (AI_TASK_ID) {
    rememberLastAiTaskId();
    restoreAiWorkspaceState();
    refreshAnalysisTimeRangeUi();
    syncAiDeviceSelectionUi();
    updatePromptUsageHints();
    loadAnalysisHistory();
    restoreAnalysisState();
    setAnalysisRunState(false);
    refreshAiStatusModelLine(UI_LANG === 'en' ? 'not started' : '未启动');
    workspaceRestoreDone = true;
    window.addEventListener('beforeunload', writeAiWorkspaceState);
  }

  updateProviderSection();
  updateProviderIcon();
  updateApiKeyState();
  updatePromptUsageHints();
