const INDEX_BOOT = window.NETLOG_INDEX_BOOTSTRAP || {};
const INDEX_TEXT = INDEX_BOOT.texts || {};
const PREFILL_STATE = INDEX_BOOT.prefill || {};
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
  delete payload.db_host;
  delete payload.db_port;
  delete payload.db_user;
  delete payload.db_password;
  delete payload.db_name;
  localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
}

function parsePrefillCheckbox(value) {
  const raw = String(value == null ? "" : value).trim().toLowerCase();
  return raw === "1" || raw === "true" || raw === "on" || raw === "yes";
}

function applyPrefillState() {
  const form = document.querySelector("form.hc-form");
  if (!form || !PREFILL_STATE || typeof PREFILL_STATE !== "object") return;
  for (const [name, value] of Object.entries(PREFILL_STATE)) {
    if (value == null) continue;
    const el = form.querySelector(`[name="${name}"]`);
    if (!el) continue;
    if (el.type === "checkbox") {
      el.checked = parsePrefillCheckbox(value);
    } else {
      el.value = String(value);
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
  const debugEnabled = !!(debug && debug.checked);
  const sqlEnabled = !!(sqlMode && sqlMode.checked);
  if (debugBox) debugBox.style.display = debugEnabled ? "block" : "none";
  if (sqlMode) sqlMode.disabled = !debugEnabled;
  if (sqlOnly) {
    sqlOnly.disabled = !(debugEnabled && sqlEnabled);
    if (!debugEnabled || !sqlEnabled) sqlOnly.checked = false;
  }
  toggleSmc();
}

restoreFormState();
applyPrefillState();
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
if (debugMode) debugMode.addEventListener("change", toggleDebugExtras);
if (sqlQueryMode) sqlQueryMode.addEventListener("change", toggleDebugExtras);
if (sqlOnlyMode) sqlOnlyMode.addEventListener("change", toggleSmc);
