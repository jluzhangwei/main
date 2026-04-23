const INDEX_BOOT = window.NETLOG_INDEX_BOOTSTRAP || {};
const INDEX_TEXT = INDEX_BOOT.texts || {};
const STORAGE_KEY = "netlog_extractor_create_form_v2";
const DEFAULT_FORM_STATE = {
  debug_mode: true,
  sql_query_mode: true,
  sql_only_mode: true,
};

function getPersistFields() {
  return [
    "default_username",
    "jump_mode",
    "vendor_hint",
    "jump_host",
    "jump_port",
    "smc_command",
    "batch_text",
    "start_time",
    "end_time",
    "context_lines",
    "concurrency",
    "per_device_timeout",
    "debug_mode",
    "sql_query_mode",
    "sql_only_mode",
    "db_host",
    "db_port",
    "db_user",
    "db_password",
    "db_name",
  ];
}

function saveFormState() {
  const form = document.querySelector("form.hc-form");
  if (!form) return;
  const payload = {};
  for (const name of getPersistFields()) {
    const el = form.querySelector(`[name="${name}"]`);
    if (!el) continue;
    if (el.type === "checkbox") payload[name] = !!el.checked;
    else payload[name] = el.value == null ? "" : el.value;
  }
  localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
}

function restoreFormState() {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) {
    const form = document.querySelector("form.hc-form");
    if (!form) return;
    for (const [name, value] of Object.entries(DEFAULT_FORM_STATE)) {
      const el = form.querySelector(`[name="${name}"]`);
      if (el && el.type === "checkbox") el.checked = !!value;
    }
    return;
  }
  let payload = null;
  try {
    payload = JSON.parse(raw);
  } catch (e) {
    return;
  }
  const form = document.querySelector("form.hc-form");
  if (!form || !payload) return;
  for (const name of getPersistFields()) {
    const el = form.querySelector(`[name="${name}"]`);
    if (!el) continue;
    if (el.type === "checkbox") {
      if (name in payload) el.checked = !!payload[name];
      else if (name in DEFAULT_FORM_STATE) el.checked = !!DEFAULT_FORM_STATE[name];
      else el.checked = false;
    } else if (typeof payload[name] === "string" || typeof payload[name] === "number") {
      el.value = String(payload[name]);
    }
  }
}

function toggleSmc() {
  const mode = document.getElementById("jump_mode").value;
  const sqlOnly = !!(document.getElementById("sql_only_mode") && document.getElementById("sql_only_mode").checked);
  const jumpMode = document.getElementById("jump_mode");
  const credentialRow = document.getElementById("credential-row");
  const usernameInput = document.getElementById("default_username");
  const passwordInput = document.getElementById("default_password");
  const credentialTip = document.getElementById("credential_tip");
  const hostRow = document.getElementById("smc-host-row");
  const box = document.getElementById("smc-box");
  const input = document.getElementById("smc_command");
  const tip = document.getElementById("smc_command_tip");
  const jumpHostInput = document.querySelector('[name="jump_host"]');
  const jumpPortInput = document.querySelector('[name="jump_port"]');
  const disableCredentials = sqlOnly || mode === "smc_pam_nd";
  if (jumpMode) jumpMode.disabled = sqlOnly;
  if (credentialRow) credentialRow.classList.toggle("disabled-credential", disableCredentials);
  if (usernameInput) {
    usernameInput.required = !disableCredentials;
    usernameInput.disabled = disableCredentials;
  }
  if (passwordInput) {
    passwordInput.required = !disableCredentials;
    passwordInput.disabled = disableCredentials;
  }
  if (credentialTip) {
    credentialTip.textContent = sqlOnly
      ? INDEX_TEXT.sql_only_credential_tip || ""
      : disableCredentials
        ? INDEX_TEXT.pam_nd_credential_tip || ""
        : "";
  }
  if (hostRow) {
    hostRow.style.display = !sqlOnly && mode === "smc" ? "block" : "none";
    hostRow.classList.toggle("section-disabled", sqlOnly);
  }
  if (box) {
    box.style.display = !sqlOnly && (mode === "smc" || mode === "smc_pam_nd") ? "block" : "none";
    box.classList.toggle("section-disabled", sqlOnly);
  }
  if (jumpHostInput) jumpHostInput.disabled = sqlOnly || mode !== "smc";
  if (jumpPortInput) jumpPortInput.disabled = sqlOnly || mode !== "smc";
  if (input) input.disabled = sqlOnly || !(mode === "smc" || mode === "smc_pam_nd");
  if (!input || !tip) return;
  if (sqlOnly) {
    tip.textContent = INDEX_TEXT.sql_only_smc_tip || "";
    return;
  }
  if (mode === "smc_pam_nd") {
    input.placeholder = "smc pam nd ssh {device_ip}";
    tip.textContent = INDEX_TEXT.pam_nd_command_tip || "";
  } else {
    input.placeholder = "smc server toc {jump_host}";
    tip.textContent = INDEX_TEXT.smc_command_tip || "";
  }
}

function toggleDebugExtras() {
  const debug = document.getElementById("debug_mode");
  const sqlMode = document.getElementById("sql_query_mode");
  const sqlOnly = document.getElementById("sql_only_mode");
  const debugBox = document.getElementById("debug-options-box");
  const sqlBox = document.getElementById("sql-config-box");
  const debugEnabled = !!(debug && debug.checked);
  const sqlEnabled = !!(sqlMode && sqlMode.checked);
  if (debugBox) debugBox.style.display = debugEnabled ? "block" : "none";
  if (sqlBox) sqlBox.style.display = debugEnabled && sqlEnabled ? "block" : "none";
  if (sqlMode) sqlMode.disabled = !debugEnabled;
  if (sqlOnly) {
    sqlOnly.disabled = !(debugEnabled && sqlEnabled);
    if (!debugEnabled || !sqlEnabled) sqlOnly.checked = false;
  }
  document
    .querySelectorAll('[name="db_host"], [name="db_port"], [name="db_user"], [name="db_password"], [name="db_name"]')
    .forEach((el) => {
      el.disabled = !(debugEnabled && sqlEnabled);
    });
  const testBtn = document.getElementById("test_sql_btn");
  if (testBtn) testBtn.disabled = !(debugEnabled && sqlEnabled);
  toggleSmc();
}

async function testSqlConnection() {
  const resultEl = document.getElementById("sql_test_result");
  const btn = document.getElementById("test_sql_btn");
  if (resultEl) resultEl.textContent = INDEX_TEXT.testing_sql || "Testing SQL connection...";
  if (btn) btn.disabled = true;
  try {
    const payload = {
      db_host: document.querySelector('[name="db_host"]')?.value || "",
      db_port: Number(document.querySelector('[name="db_port"]')?.value || "0") || 0,
      db_user: document.querySelector('[name="db_user"]')?.value || "",
      db_password: document.querySelector('[name="db_password"]')?.value || "",
      db_name: document.querySelector('[name="db_name"]')?.value || "",
    };
    const res = await fetch("/api/sql/log-server/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || data.message || "SQL test failed");
    if (resultEl) resultEl.textContent = `${data.message} | ${data.table} | columns=${data.column_count}`;
  } catch (e) {
    if (resultEl) resultEl.textContent = String(e && e.message ? e.message : e);
  } finally {
    toggleDebugExtras();
  }
}

restoreFormState();
toggleSmc();
toggleDebugExtras();

const form = document.querySelector("form.hc-form");
if (form) {
  form.addEventListener("input", saveFormState);
  form.addEventListener("change", saveFormState);
  form.addEventListener("submit", saveFormState);
}
const debugMode = document.getElementById("debug_mode");
const sqlQueryMode = document.getElementById("sql_query_mode");
const sqlOnlyMode = document.getElementById("sql_only_mode");
const testSqlBtn = document.getElementById("test_sql_btn");
if (debugMode) debugMode.addEventListener("change", toggleDebugExtras);
if (sqlQueryMode) sqlQueryMode.addEventListener("change", toggleDebugExtras);
if (sqlOnlyMode) sqlOnlyMode.addEventListener("change", toggleSmc);
if (testSqlBtn) testSqlBtn.addEventListener("click", testSqlConnection);
