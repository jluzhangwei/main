  (function () {
    const btn = document.getElementById('lang_toggle_btn');
    if (!btn) return;
    btn.addEventListener('click', () => {
      const url = new URL(window.location.href);
      const cur = (url.searchParams.get('lang') || 'zh').toLowerCase();
      const target = cur.startsWith('en') ? 'zh' : 'en';
      try { localStorage.setItem('netlog_ui_lang', target); } catch (e) {}
      url.searchParams.set('lang', target);
      window.location.href = url.toString();
    });
  })();

  (function () {
    const aiNav = document.getElementById('ai_log_analysis_nav');
    if (!aiNav) return;
    const storageKey = 'netlog.ai.last_task_id';
    const buildAiUrl = () => {
      const url = new URL(aiNav.href, window.location.origin);
      const currentUrl = new URL(window.location.href);
      const explicitTaskId = (currentUrl.searchParams.get('task_id') || '').trim();
      let taskId = explicitTaskId;
      if (!taskId) {
        try {
          taskId = (window.localStorage.getItem(storageKey) || '').trim();
        } catch (e) {
          taskId = '';
        }
      }
      if (taskId) url.searchParams.set('task_id', taskId);
      else url.searchParams.delete('task_id');
      return url.toString();
    };
    aiNav.href = buildAiUrl();
    aiNav.addEventListener('click', (event) => {
      aiNav.href = buildAiUrl();
    });
  })();
