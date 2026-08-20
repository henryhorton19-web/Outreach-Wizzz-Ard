/* Outreach Wizz-ard — front-end controller (vanilla JS, no build step).
   Talks to the local FastAPI server. Every /api/* request carries the per-launch session token
   the server injected into the page. Drafts are reviewed and edited here; the reviewer's edit is
   the final word, sent to the server verbatim. */

"use strict";

// Read the injected token once, then remove it from the global so injected scripts can't read it.
const TOKEN = window.__WIZZARD_TOKEN__ || window.__PARIS_TOKEN__;
try { delete window.__WIZZARD_TOKEN__; delete window.__PARIS_TOKEN__; } catch (_) { window.__WIZZARD_TOKEN__ = undefined; }
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const PROVIDER_LABEL = { gemini: "Gemini", anthropic: "Claude", stub: "Offline demo" };

// Voices are editable data fetched from the server (no static list). voiceLabel looks up the
// display name; SITUATION_LABEL names the three auto-routing situations for the editor + summaries.
let allVoices = [];
const SITUATION_LABEL = {
  no_role_small: "No role · small",
  role_small:    "Role · small",
  role_large:    "Role · large",
};
function voiceById(id) { return allVoices.find(v => v.id === id) || null; }
function voiceLabel(id) { const v = voiceById(id); return v ? v.display_name : (id || "—"); }

// targets keyed by slug (single source of truth). Map preserves insertion order.
const companies = new Map();
function companyList() { return Array.from(companies.values()); }

const state = {
  status: null,
  queue: [],
  open: new Set(),        // slugs whose drawer is expanded
  defaultVoice: "",       // fallback voice id (server settings)
  editingVoiceId: null,   // voice id currently open in the editor (null = new)
  view: "workspace",      // active working view (tab strip single source of truth)
  followups: null,
  perfKind: "outreach",
  triageBucket: "replied",
  triageData: null,
};

let selectedDefaultAttachment = "";   // module-scope, near other UI state
let allAttachments = [];

function getEffectiveAttachments(cs) {
  const override = cs.attachments || [];
  if (override.length > 0) {
    if (override.includes("__none__")) {
      return [];
    }
    return override;
  }
  const settings = (state.status && state.status.settings) || {};
  return (settings.attach_by_default !== false) ? (settings.default_attachments || []) : [];
}

/* ---------- API ---------- */
async function api(path, { method = "GET", body = null, raw = false, form = null } = {}) {
  const opts = { method, headers: { "x-wizzard-token": TOKEN } };
  if (form != null) { opts.body = form; }
  else if (body != null) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  const res = await fetch(path, opts);
  if (raw) return res;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `${res.status} ${res.statusText}`);
  return data;
}

/* ---------- toast ---------- */
let toastTimer = null;
function toast(msg, isErr = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast show" + (isErr ? " err" : "");
  t.setAttribute("aria-live", isErr ? "assertive" : "polite");
  t.setAttribute("role", isErr ? "alert" : "status");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.className = "toast"; }, 2800);
}

/* ---------- dialog (replaces window.confirm) ---------- */
function dialog({ title = "", message = "", options = [{ label: "OK", value: true, primary: true }] } = {}) {
  return new Promise(resolve => {
    const prevFocus = document.activeElement;
    const scrim = document.createElement("div");
    scrim.className = "modal-scrim dialog-scrim";
    const box = document.createElement("div");
    box.className = "modal dialog-box";
    box.style.maxWidth = "440px";
    if (title) { const h = document.createElement("h2"); h.style.fontSize = "18px"; h.textContent = title; box.appendChild(h); }
    const p = document.createElement("p"); p.className = "lede"; p.style.whiteSpace = "pre-wrap"; p.style.marginBottom = "20px"; p.textContent = message; box.appendChild(p);
    const actions = document.createElement("div"); actions.className = "modal-actions";
    function close(val) { document.removeEventListener("keydown", onKey, true); scrim.remove(); if (prevFocus && prevFocus.focus) prevFocus.focus(); resolve(val); }
    function onKey(e) { if (e.key === "Escape") { e.preventDefault(); close(false); } }
    options.forEach(opt => {
      const b = document.createElement("button");
      b.className = "btn " + (opt.primary ? "primary" : (opt.danger ? "danger" : "ghost"));
      b.textContent = opt.label;
      b.onclick = () => close(opt.value);
      actions.appendChild(b);
    });
    box.appendChild(actions); scrim.appendChild(box); document.body.appendChild(scrim);
    document.addEventListener("keydown", onKey, true);
    const first = actions.querySelector(".primary, .danger") || actions.querySelector("button");
    if (first) first.focus();
    scrim.addEventListener("click", e => { if (e.target === scrim) close(false); });
  });
}

/* ---------- helpers ---------- */
function esc(s) { return (s == null ? "" : String(s)).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function timeAgo(isoStr) {
  if (!isoStr) return "";
  try {
    const then = new Date(isoStr).getTime();
    const ms = Date.now() - then;
    if (isNaN(ms)) return isoStr;
    // Clock skew between the machine and whatever set the timestamp is the only way
    // this can go negative in practice, since last_run_at is always server-generated.
    // Rounding it to "just now" would misreport a future time as having just happened.
    if (ms < 0) return "just now (clock skew)";
    const mins = Math.floor(ms / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    return `${days}d ago`;
  } catch (e) {
    return isoStr;
  }
}
function shortUrl(u) { try { const x = new URL(u); return x.hostname.replace(/^www\./, "") + (x.pathname.length > 1 ? x.pathname : ""); } catch { return u; } }
function wordCount(s) { return (s || "").trim() ? (s.trim().match(/\S+/g) || []).length : 0; }

/* ================= STATUS + STARTUP ================= */
async function refreshStatus() {
  state.status = await api("/api/status");
  const prov = state.status.provider;
  $("#providerPill").textContent = "provider: " + (PROVIDER_LABEL[prov] || prov);
  renderStatusStrip();
  return state.status;
}

function renderStatusStrip() {
  const strip = $("#statusStrip");
  if (!strip) return;
  const degraded = state.status && state.status.degraded;
  if (!degraded) { strip.classList.add("hidden"); return; }
  strip.classList.remove("hidden");
  strip.classList.add("is-error");
  $("#statusText").textContent = degraded;
  strip.setAttribute("aria-live", "assertive");
}

async function fetchVoices() {
  try {
    const kind = state.voiceKind || "outreach";
    const r = await api(`/api/voices?kind=${kind}`);
    allVoices = r.voices || [];
    const d = await api("/api/default_voice");
    state.defaultVoice = d.default_voice || "";
  } catch (e) { /* keep whatever we have */ }
}

function needsKey(st) {
  return st.provider !== "stub" && !st.provider_key_present;
}

function openStartup() {
  const st = state.status;
  $("#startProvider").value = st.provider;
  syncStartupKeyField();
  $("#startupModal").classList.remove("hidden");
}
function syncStartupKeyField() {
  const prov = $("#startProvider").value;
  const isStub = prov === "stub";
  $("#keyField").style.display = isStub ? "none" : "";
  $("#keyLabel").textContent = (prov === "anthropic" ? "Claude" : "Gemini") + " API key";
  const present = prov === "gemini" ? state.status.gemini_key_present
                : prov === "anthropic" ? state.status.anthropic_key_present : true;
  $("#keyDesc").textContent = present
    ? "A key is already saved on this machine. Leave blank to keep it."
    : "Used only from this machine to draft. Never stored in the batch or the audit trail.";
}

async function doStart() {
  const prov = $("#startProvider").value;
  const key = $("#keyInput").value.trim();
  const apolloKey = $("#apolloKeyInput").value.trim();
  const remember = $("#rememberKey").checked;
  try {
    await api("/api/settings", { method: "POST", body: { provider: prov } });
    if (prov !== "stub" && key) {
      await api("/api/keys", { method: "POST", body: { provider: prov, key, remember } });
    }
    if (apolloKey) {
      await api("/api/keys", { method: "POST", body: { provider: "apollo", key: apolloKey, remember } });
    }
    await refreshStatus();
    if (needsKey(state.status)) { $("#startupNote").textContent = "That provider still has no key. Paste one to continue."; return; }
    $("#startupModal").classList.add("hidden");
    $("#keyInput").value = "";
    $("#apolloKeyInput").value = "";
    toast("Ready to draft");
  } catch (e) { $("#startupNote").textContent = e.message; }
}

async function refreshAttachmentsPanel() {
  const r = await api("/api/attachments");
  allAttachments = r.attachments || [];
  selectedDefaultAttachment = (r.default_attachments && r.default_attachments[0]) || "";
  const list = $("#attachmentsList");
  if (!list) return;
  list.innerHTML = "";
  allAttachments.forEach(a => {
    const row = document.createElement("div");
    row.className = "attachment-row";
    const pick = document.createElement("input");
    pick.type = "radio"; pick.name = "defaultAttachment";
    pick.checked = a.name === selectedDefaultAttachment;
    pick.onchange = () => { selectedDefaultAttachment = a.name; };
    const label = document.createElement("span");
    label.className = "attachment-name";
    label.textContent = `${a.name} (${Math.round(a.size / 1024)} KB)`;
    const del = document.createElement("button");
    del.type = "button"; del.className = "btn ghost small"; del.textContent = "×";
    del.title = "Delete";
    del.onclick = async () => {
      try { await api(`/api/attachments/${encodeURIComponent(a.name)}`, { method: "DELETE" }); await refreshAttachmentsPanel(); }
      catch (e) { toast(e.message, true); }
    };
    row.append(pick, label, del);
    list.appendChild(row);
  });
}

async function doUploadAttachment(file) {
  const form = new FormData(); form.append("file", file);
  try { await api("/api/attachments", { method: "POST", form }); await refreshAttachmentsPanel(); toast(`Added ${file.name}`); }
  catch (e) { toast(e.message, true); }
}


/* ================= SETTINGS ================= */
// Reveal a modal in the click handler's OWN task, before any `await`. A display flip
// (display:none -> flex) made in a post-await microtask isn't composited by WKWebView /
// WebView2 until the next input event — which made "tap Voices -> nothing, then tap
// Settings -> both open". Revealing first guarantees the paint (and shows the shell
// instantly while content loads).
function showModal(id) { $("#" + id).classList.remove("hidden"); }

async function openSettings() {
  showModal("settingsModal");
  const st = state.status;
  $("#setProvider").value = st.provider;
  $("#setGeminiModel").value = st.models.gemini || "";
  $("#setAnthropicModel").value = st.models.anthropic || "";
  $("#setMaxWeb").value = (st.settings && st.settings.max_web_searches) || 4;
  $("#setTrackerPath").value = st.tracker_path || "";
  $("#setEmlDir").value = (st.settings && st.settings.eml_dir) || "";
  $("#settingsNote").textContent = "";
  await refreshAttachmentsPanel();
  $("#setAttachDefault").checked = (st.settings?.attach_by_default) !== false;
  // follow-up settings
  const fs = st.settings || {};
  $("#setFuEnabled").checked = fs.follow_up_enabled !== false;
  $("#setFuMaxSteps").value = String(fs.follow_up_max_steps || 1);
  $("#setFuDelays").value = (fs.follow_up_delay_days || [3, 7, 7]).join(", ");
  const syncFuDisabled = () => {
    const off = !$("#setFuEnabled").checked;
    $("#setFuMaxSteps").disabled = off;
    $("#setFuDelays").disabled = off;
    $("#setFuStepsField").style.opacity = off ? ".5" : "";
    $("#setFuDelaysField").style.opacity = off ? ".5" : "";
  };
  $("#setFuEnabled").onchange = syncFuDisabled;
  syncFuDisabled();

  // inbox (Phase 5)
  $("#setImapEnabled").checked = !!fs.imap_enabled;
  $("#setImapHost").value = fs.imap_host || "";
  $("#setImapPort").value = String(fs.imap_port || 993);
  $("#setImapSsl").checked = fs.imap_ssl !== false;
  $("#setImapUser").value = fs.imap_username || "";
  $("#setImapPass").value = "";
  $("#setImapMailboxes").value = (fs.imap_mailboxes || ["INBOX"]).join(", ");
  $("#setImapPoll").value = String(fs.imap_poll_minutes || 0);
  const syncImap = () => $("#imapFields").classList.toggle("hidden", !$("#setImapEnabled").checked);
  $("#setImapEnabled").onchange = syncImap; syncImap();
  $("#imapTestResult").textContent = "";

  // learning + thresholds + send-window (Phases 7, 3, 2, 6c)
  $("#setLearningRouting").value = fs.voice_learning_routing || "off";
  $("#setLearningMode").value = fs.voice_learning_mode || "off";
  $("#setLearningPromote").checked = !!fs.voice_learning_promote;
  $("#setMinN").value = String(fs.voice_stats_min_n || 15);
  $("#setStaleDays").value = String(fs.pipeline_stale_days || 7);
  $("#setMaxBounce").value = String(fs.max_bounce_retries != null ? fs.max_bounce_retries : 3);
  $("#setSendWindow").checked = fs.send_window_advisory !== false;
  // Stage E & G: exclusion layer & org voice learning
  if ($("#setExclusionEnabled")) $("#setExclusionEnabled").checked = fs.exclusion_enabled !== false;
  if ($("#setAllowOrgVoiceLearning")) $("#setAllowOrgVoiceLearning").checked = !!fs.allow_org_voice_learning;
  try {
    const exInfo = await api("/api/exclusion");
    if (exInfo && exInfo.total != null && $("#settingExclusionStats")) {
      $("#settingExclusionStats").textContent = `${exInfo.total.toLocaleString()} excluded`;
    }
  } catch (e) {}
}
async function saveSettings() {
  const prov = $("#setProvider").value;
  const payload = {
    provider: prov,
    gemini_model: $("#setGeminiModel").value.trim(),
    anthropic_model: $("#setAnthropicModel").value.trim(),
    max_web_searches: parseInt($("#setMaxWeb").value, 10) || 4,
    default_attachments: selectedDefaultAttachment ? [selectedDefaultAttachment] : [],
    attach_by_default: $("#setAttachDefault").checked,
    eml_dir: $("#setEmlDir").value.trim(),
    follow_up_enabled: $("#setFuEnabled").checked,
    follow_up_max_steps: parseInt($("#setFuMaxSteps").value, 10) || 1,
    follow_up_delay_days: ($("#setFuDelays").value.split(",")
      .map(s => parseInt(s.trim(), 10)).filter(n => !isNaN(n) && n >= 0)) .length
      ? $("#setFuDelays").value.split(",").map(s => parseInt(s.trim(), 10)).filter(n => !isNaN(n) && n >= 0)
      : [3, 7, 7],
    // inbox (Phase 5)
    imap_enabled: $("#setImapEnabled").checked,
    imap_host: $("#setImapHost").value.trim(),
    imap_port: parseInt($("#setImapPort").value, 10) || 993,
    imap_ssl: $("#setImapSsl").checked,
    imap_username: $("#setImapUser").value.trim(),
    imap_mailboxes: $("#setImapMailboxes").value.split(",").map(s => s.trim()).filter(Boolean),
    imap_poll_minutes: parseInt($("#setImapPoll").value, 10) || 0,
    // learning + thresholds + send-window
    voice_learning_routing: $("#setLearningRouting").value,
    voice_learning_mode: $("#setLearningMode").value,
    voice_learning_promote: $("#setLearningPromote").checked,
    voice_stats_min_n: parseInt($("#setMinN").value, 10) || 15,
    pipeline_stale_days: parseInt($("#setStaleDays").value, 10) || 7,
    max_bounce_retries: (() => { const n = parseInt($("#setMaxBounce").value, 10); return isNaN(n) ? 3 : Math.max(0, Math.min(5, n)); })(),
    send_window_advisory: $("#setSendWindow").checked,
    // Stage E & G settings
    exclusion_enabled: $("#setExclusionEnabled") ? $("#setExclusionEnabled").checked : true,
    allow_org_voice_learning: $("#setAllowOrgVoiceLearning") ? $("#setAllowOrgVoiceLearning").checked : false,
  };
  const key = $("#setKeyInput").value.trim();
  const apolloKey = $("#setApolloKeyInput").value.trim();
  const imapPass = $("#setImapPass").value.trim();
  const remember = $("#setRemember").checked;
  const trackerPath = $("#setTrackerPath").value.trim();
  try {
    await api("/api/settings", { method: "POST", body: payload });
    if (prov !== "stub" && key) await api("/api/keys", { method: "POST", body: { provider: prov, key, remember } });
    if (apolloKey) await api("/api/keys", { method: "POST", body: { provider: "apollo", key: apolloKey, remember } });
    if (imapPass) await api("/api/keys", { method: "POST", body: { provider: "imap", key: imapPass, remember } });
    await api("/api/tracker_path", { method: "POST", body: { path: trackerPath } });
    await refreshStatus();
    $("#setKeyInput").value = "";
    $("#setApolloKeyInput").value = "";
    $("#setImapPass").value = "";
    $("#settingsModal").classList.add("hidden");
    toast("Settings saved");
  } catch (e) { $("#settingsNote").textContent = e.message; }
}

/* ================= INGEST ================= */
function updateNameCount() {
  const n = $("#namesInput").value.split("\n").map(s => s.trim()).filter(Boolean).length;
  $("#nameCount").textContent = n ? `${n} name${n === 1 ? "" : "s"}` : "";
}
function showIngestBanner(result) {
  const bits = [];
  if (result.added) bits.push(`${result.added} added`);
  if (result.skipped_duplicates && result.skipped_duplicates.length) bits.push(`${result.skipped_duplicates.length} already present`);
  if (result.already_contacted && result.already_contacted.length) bits.push(`${result.already_contacted.length} already contacted`);
  if (result.suppressed && result.suppressed.length) bits.push(`${result.suppressed.length} on do-not-contact`);
  if (result.over_cap && result.over_cap.length) bits.push(`${result.over_cap.length} over the queue cap`);
  if (!bits.length) return;
  $("#ingestBannerText").textContent = bits.join(" · ") + " — skipped items were not queued.";
  $("#ingestBanner").classList.remove("hidden");
}
async function doIngest() {
  const text = $("#namesInput").value;
  if (!text.trim()) { toast("Paste at least one name", true); return; }
  const list_id = state.activeListId || "default";
  try {
    const r = await api("/api/ingest", { method: "POST", body: { text, list_id } });
    state.queue = r.queue;
    $("#namesInput").value = ""; updateNameCount();
    showIngestBanner(r);
    renderQueue();
    fetchLists();
    toast(`${r.added} added to queue`);
  } catch (e) { toast(e.message, true); }
}
async function doUpload(file) {
  const form = new FormData(); form.append("file", file);
  try {
    const r = await api(`/api/ingest_file?list_id=${encodeURIComponent(state.activeListId || "default")}`, { method: "POST", form });
    state.queue = r.queue; showIngestBanner(r); renderQueue(); fetchLists();
    toast(`${r.added} added from ${file.name}`);
  } catch (e) { toast(e.message, true); }
}

/* ================= NAMED LISTS ================= */
async function fetchLists() {
  try {
    const res = await api("/api/lists");
    state.lists = res.lists || [];
    if (res.active) state.activeListId = res.active;
    renderListSelect();
  } catch (e) {
    console.error("Failed to fetch lists:", e);
    renderListSelectError();
  }
}

function renderListSelect() {
  const sel = $("#listSelect");
  if (!sel) return;
  const lists = (state.lists && state.lists.length) ? state.lists : [{ id: "default", name: "Default List", count: (state.queue || []).length }];
  // The name is already shown by #activeListNameDisplay and the count by #queueCount.
  // Repeating both in the option text duplicated identity inside a 280px header.
  sel.innerHTML = lists.map(l => {
    const off = l.unavailable ? " disabled" : "";
    const why = l.unavailable ? ` title="${esc(l.reason || "unavailable")}"` : "";
    const on = l.id === (state.activeListId || "default") ? " selected" : "";
    return `<option value="${esc(l.id)}"${on}${off}${why}>${esc(l.name)}${l.unavailable ? " (unavailable)" : ""}</option>`;
  }).join("");

  const activeObj = lists.find(l => l.id === (state.activeListId || "default")) || lists[0];
  const activeName = activeObj ? activeObj.name : "Default List";

  if ($("#activeListNameDisplay")) $("#activeListNameDisplay").textContent = activeName;
  if ($("#activeListPill")) $("#activeListPill").textContent = `list: ${activeName}`;
}

function renderListSelectError() {
  const sel = $("#listSelect");
  if (sel) {
    sel.innerHTML = '<option value="" disabled selected>Lists unavailable — reload</option>';
  }
}

async function switchList(listId) {
  state.activeListId = listId;
  try {
    const r = await api("/api/lists/active", { method: "POST", body: { id: listId } });
    if (r.lists) state.lists = r.lists;
    if (r.active) state.activeListId = r.active;
    const qRes = await api(`/api/queue?list_id=${encodeURIComponent(listId)}`);
    state.queue = qRes.queue;
    renderQueue();
    renderListSelect();
  } catch (e) {
    toast("Failed to switch list: " + e.message, true);
  }
}

async function createNamedList() {
  const name = prompt("Enter a name for the new company list (e.g. Growth Funds Paris):");
  if (!name || !name.trim()) return;
  try {
    const res = await api("/api/lists", { method: "POST", body: { name: name.trim() } });
    state.lists = res.lists;
    state.activeListId = res.active || res.list.id;
    const qRes = await api(`/api/queue?list_id=${encodeURIComponent(state.activeListId)}`);
    state.queue = qRes.queue;
    renderQueue();
    renderListSelect();
    toast(`Created list "${res.list.name}"`);
  } catch (e) {
    toast("Failed to create list: " + e.message, true);
  }
}

/* ================= QUEUE ================= */
function humanBadgeLabel(str) {
  if (!str) return "";
  const map = {
    cat_a: "Category A", cat_b: "Category B", cat_c: "Category C",
    b2b_saas: "B2B SaaS", fintech: "Fintech", healthtech: "Healthtech",
    deeptech: "Deeptech", ai_ml: "AI / ML", seed: "Seed Stage",
    series_a: "Series A", series_b: "Series B", growth: "Growth Stage",
    sourcing: "Sourced",
  };
  return map[str.toLowerCase()] || str.replace(/_/g, " ").replace(/\b\w/g, l => l.toUpperCase());
}

function renderQueue() {
  const list = $("#queueList");
  list.innerHTML = "";
  $("#queueCount").textContent = state.queue.length ? `${state.queue.length} targets` : "0 targets";
  $("#queueEmpty").classList.toggle("hidden", state.queue.length > 0);
  state.queue.forEach(rec => {
    const el = document.createElement("div");
    el.className = "qrow";
    el.setAttribute("data-slug", rec.slug);
    const m = rec.meta || {};
    const chips = [];
    if (m.employees_band) chips.push(humanBadgeLabel(m.employees_band));
    if (m.funding_heat || m.signal_basis || m.discovery_label) chips.push(humanBadgeLabel(m.funding_heat || m.signal_basis || m.discovery_label));
    if (m.hq_city || m.hq_country) chips.push([m.hq_city, m.hq_country].filter(Boolean).join(", "));
    // Screening no longer decides whether a company is queued, so its verdict is shown to
    // the reviewer instead. A weak signal is worth seeing, not worth withholding.
    const screenNote = m.screen_reason || (
      m.screen_verdict && m.screen_verdict !== "accept" ? m.screen_verdict : "");

    const chipHtml = chips.map(c => `<span class="tag neutral">${esc(c)}</span>`).join("")
      + (screenNote ? `<span class="tag caution" title="Flagged by automatic screening. Review and decide.">${esc(screenNote)}</span>` : "");

    const activeVoiceId = state.sessionVoice || state.defaultVoice || (allVoices[0] ? allVoices[0].id : "");
    const activeVoice = voiceById(activeVoiceId);
    const isExemplar = activeVoice && activeVoice.learning === "exemplar";

    el.innerHTML = `
      <div class="qrow-info">
        <div class="qrow-name">
          ${esc(rec.name)}
          ${rec.crm_id || rec.ref ? `<span class="qrow-ref">${esc(rec.crm_id || rec.ref)}</span>` : ""}
        </div>
        <div class="qrow-chips">
          ${chipHtml}
          <span class="website-input-wrap"><input type="text" class="tag-add-input" data-act="website" placeholder="website (optional)" value="${esc(rec.website || "")}" autocomplete="off" spellcheck="false" /><span class="website-save-status"></span></span>
        </div>
      </div>
      <div class="qrow-act" style="display:flex; align-items:center; gap:8px;">
        <button class="btn ghost small" data-act="draft">Draft &rarr;</button>
        ${isExemplar ? `<button class="btn ghost small" data-act="blank">Write it myself</button>` : ""}
        <button class="qrow-remove-btn" data-act="remove" aria-label="Remove target from queue" title="Remove">&times;</button>
      </div>`;
    wireWebsiteInput(el.querySelector('[data-act="website"]'), {
      currentValue: rec.website || "",
      save: async (value) => {
        const list_id = state.activeListId || "default";
        const r = await api(`/api/queue/${rec.slug}/website?list_id=${encodeURIComponent(list_id)}`, {
          method: "PUT",
          body: { website: value }
        });
        state.queue = r.queue;
        return r;
      },
      onSaved: () => {},
      refocus: () => $("#queueList").querySelector(`[data-slug="${rec.slug}"] [data-act="website"]`)
    });
    el.querySelector('[data-act="draft"]').onclick = async (evt) => {
      const btn = evt.currentTarget;
      const pending = el.querySelector('[data-act="website"]')?._pendingSave;
      if (pending) {
        const original = btn.textContent;
        btn.disabled = true; btn.textContent = "Saving\u2026";
        try { await pending; } catch (e) { /* field shows its own error */ }
        btn.disabled = false; btn.textContent = original;
      }
      draftFromQueue(rec.slug);
    };
    if (isExemplar) {
      el.querySelector('[data-act="blank"]').onclick = () => authorBlankFromQueue(rec.slug);
    }
    el.querySelector('[data-act="remove"]').onclick = () => removeFromQueue(rec.slug);
    list.appendChild(el);
  });
}

async function authorBlankFromQueue(slug) {
  if (state.status && needsKey(state.status)) { openStartup(); return; }
  const list_id = state.activeListId || "default";
  try {
    const r = await api(`/api/queue/${slug}/draft?list_id=${encodeURIComponent(list_id)}`, { method: "POST" });
    state.queue = r.queue; renderQueue(); fetchLists();
    ingestCompany(r.company);
    const activeVoiceId = state.sessionVoice || state.defaultVoice || (allVoices[0] ? allVoices[0].id : "");
    const updated = await api(`/api/companies/${slug}/blank`, { method: "POST", body: { voice: activeVoiceId } });
    companies.set(slug, updated);
    renderDrafts();
    refreshCost();
  } catch (e) { toast(e.message, true); }
}
/**
 * One website input plus its own save button, shared by the queue row and the drafted card.
 *
 * Plan 34 replaces Plan 33's blur-to-save with an explicit button. Blur is not an intention: clicking
 * the Draft button, another row, or the scrollbar all committed a value the person never asked to
 * save, and left no moment where an action produced a visible result. The button is now the only
 * thing that saves, and it is also where the result is shown.
 *
 * `input._pendingSave` is retained from Plan 33 as a BACKSTOP, not the mechanism: someone can still
 * type a website and click "Draft ->" without pressing save, and `draftFromQueue` awaits this promise
 * to keep that case correct.
 *
 * `onSaved` is deliberately NOT wired to a full re-render here. Rebuilding the list destroys the very
 * input the person is using (see Plan 34 Task 1); callers patch their own row instead.
 */
function wireWebsiteInput(input, { currentValue, save, onSaved, refocus }) {
  if (!input) return;
  const wrap = input.closest(".website-input-wrap") || input.parentElement;
  let saved = currentValue || "";

  let btn = wrap.querySelector('[data-act="website-save"]');
  if (!btn) {
    btn = document.createElement("button");
    btn.type = "button";
    btn.className = "website-save-btn";
    btn.setAttribute("data-act", "website-save");
    btn.setAttribute("aria-label", "Save website");
    btn.textContent = "\u2713";
    input.insertAdjacentElement("afterend", btn);
  }
  const status = wrap.querySelector(".website-save-status");

  const dirty = () => input.value.trim() !== saved;
  const setIdle = () => {
    btn.disabled = !dirty();
    btn.textContent = "\u2713";
    btn.title = dirty() ? "Save website" : "Nothing to save";
    btn.classList.remove("is-saving", "is-error");
  };

  input._pendingSave = null;

  const commit = () => {
    if (!dirty()) { setIdle(); return null; }
    const value = input.value.trim();
    btn.disabled = true;
    btn.textContent = "\u2026";
    btn.title = "Saving";
    btn.classList.add("is-saving");
    btn.classList.remove("is-error");
    if (status) status.textContent = "";
    const p = (async () => {
      try {
        const r = await save(value);
        saved = value;
        btn.classList.remove("is-saving");
        btn.textContent = "\u2713";
        btn.title = "Saved";
        if (status) {
          status.textContent = "Saved";
          setTimeout(() => { if (status) status.textContent = ""; }, 1500);
        }
        if (onSaved) onSaved(r);
        setIdle();
        return r;
      } catch (e) {
        btn.classList.remove("is-saving");
        btn.classList.add("is-error");
        btn.textContent = "!";
        const rawErr = (e && typeof e.message === "string" && e.message) ? e.message : (typeof e === "string" ? e : "Invalid website URL");
        const errMsg = typeof rawErr === "string" ? rawErr : "Invalid website URL";
        btn.title = errMsg;
        btn.disabled = false;
        if (status) status.textContent = errMsg.length > 15 ? "Invalid URL" : errMsg;
        toast(errMsg, true);
        const fresh = (refocus && refocus()) || input;
        if (fresh) fresh.focus();
        throw e;
      } finally {
        input._pendingSave = null;
      }
    })();
    input._pendingSave = p;
    return p;
  };

  btn.onclick = (evt) => {
    evt.preventDefault();
    evt.stopPropagation();
    commit().catch(() => {});
  };
  input.oninput = () => setIdle();
  input.onclick = (evt) => evt.stopPropagation();
  input.onmousedown = (evt) => evt.stopPropagation();
  input.onkeydown = (evt) => {
    evt.stopPropagation();
    if (evt.key === "Enter") { evt.preventDefault(); commit().catch(() => {}); }
    if (evt.key === "Escape") { evt.preventDefault(); input.value = saved; setIdle(); }
  };
  input.onblur = null;   // Plan 34: blur no longer saves. The button is the only commit path.
  setIdle();
}

async function draftFromQueue(slug) {
  if (state.status && needsKey(state.status)) { openStartup(); return; }
  // Plan 33 (A1): the website field saves on blur, which a click on this very button triggers a
  // fraction of a second before the click handler runs. Awaiting the pending save -- not a delay,
  // the actual promise the blur produced -- is what closes the race. This is the fix for Revox
  // drafting with website: null.
  const row = document.querySelector(`[data-slug="${slug}"] [data-act="website"]`);
  if (row && row._pendingSave) {
    try { await row._pendingSave; } catch (e) { /* surfaced by the field itself; draft proceeds */ }
  }
  const list_id = state.activeListId || "default";
  try {
    const r = await api(`/api/queue/${slug}/draft?list_id=${encodeURIComponent(list_id)}`, { method: "POST" });
    state.queue = r.queue; renderQueue(); fetchLists();
    ingestCompany(r.company);
    renderDrafts();
    // now actually run the pipeline for this row
    await runDraft(slug);
  } catch (e) { toast(e.message, true); }
}
async function removeFromQueue(slug) {
  const list_id = state.activeListId || "default";
  try { const r = await api(`/api/queue/${slug}?list_id=${encodeURIComponent(list_id)}`, { method: "DELETE" }); state.queue = r.queue; renderQueue(); fetchLists(); }
  catch (e) { toast(e.message, true); }
}
async function clearQueue() {
  if (!state.queue.length) return;
  const ok = await dialog({ title: "Clear queue?", message: "Remove all queued targets? Drafts are unaffected.", options: [{ label: "Cancel", value: false }, { label: "Clear", value: true, danger: true }] });
  if (!ok) return;
  const list_id = state.activeListId || "default";
  await api(`/api/queue/clear?list_id=${encodeURIComponent(list_id)}`, { method: "POST" });
  state.queue = []; renderQueue(); fetchLists(); toast("Queue cleared");
}
async function draft5() {
  if (state.status && needsKey(state.status)) { openStartup(); return; }
  // The queue shown is the ACTIVE list's. Omitting list_id made the server look in
  // "default", so every slug 404'd as "target not in queue".
  const list_id = state.activeListId || "default";
  const slugs = state.queue.slice(0, 5).map(r => r.slug);
  if (!slugs.length) { toast("Queue is empty", true); return; }

  const btn = $("#draft5Btn");
  const label = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = `Drafting ${slugs.length}\u2026`; }

  const promoted = [];
  const failures = [];
  for (const slug of slugs) {
    try {
      const r = await api(`/api/queue/${slug}/draft?list_id=${encodeURIComponent(list_id)}`,
                          { method: "POST" });
      state.queue = r.queue;
      ingestCompany(r.company);
      promoted.push(slug);
    } catch (e) {
      failures.push(`${slug}: ${e.message}`);
    }
  }
  renderQueue(); renderDrafts();
  if (btn) { btn.disabled = false; btn.textContent = label; }

  // One summary, not one toast per failure: toasts overwrite each other inside
  // 2.8s, so a loop of five errors was invisible.
  if (failures.length) {
    console.warn("draft5 could not stage:", failures);
    toast(`${failures.length} of ${slugs.length} could not be staged \u2014 ${failures[0]}`, true);
  }
  // Only run the pipeline for rows that actually reached the drafts store.
  // Previously every slug was passed to runDraft regardless, so a failed
  // promotion produced a second 404 ("unknown target") for the same row.
  // Send the slugs we actually promoted. Without this the server rebuilt its own
  // work set from the whole batch, so "Draft 5" with 4 already-drafted companies
  // produced a 9-company run.
  const r = await api("/api/draft", { method: "POST", body: { reuse_cache: true, slugs: promoted } });
  if (r && r.job_id) {
    trackDraftJob(r.job_id);
  } else {
    await Promise.all(promoted.map(runDraft));
  }
}

async function draftAllInQueue() {
  if (state.status && needsKey(state.status)) { openStartup(); return; }
  const list_id = state.activeListId || "default";
  const slugs = state.queue.map(r => r.slug);
  if (!slugs.length) { toast("Queue is empty", true); return; }

  const btn = $("#draftAllBtn");
  const label = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = `Drafting all (${slugs.length})\u2026`; }

  const promoted = [];
  const failures = [];
  for (const slug of slugs) {
    try {
      const r = await api(`/api/queue/${slug}/draft?list_id=${encodeURIComponent(list_id)}`,
                          { method: "POST" });
      state.queue = r.queue;
      ingestCompany(r.company);
      promoted.push(slug);
    } catch (e) {
      failures.push(`${slug}: ${e.message}`);
    }
  }
  renderQueue(); renderDrafts();
  if (btn) { btn.disabled = false; btn.textContent = label; }

  if (failures.length) {
    console.warn("draftAllInQueue could not stage:", failures);
    toast(`${failures.length} of ${slugs.length} could not be staged \u2014 ${failures[0]}`, true);
  }
  if (!promoted.length) return;
  const r = await api("/api/draft", { method: "POST", body: { reuse_cache: true, slugs: promoted } });
  if (r && r.job_id) {
    trackDraftJob(r.job_id);
  } else {
    await Promise.all(promoted.map(runDraft));
  }
}

let activeDraftJobId = null;
let draftJobPollTimer = null;
let activeSourcingJobId = null;
let sourcingJobPollTimer = null;

// Mirrors trackDraftJob deliberately: same poll interval, same terminal-state check,
// same cleanup. The sourcing run is now threaded, so it can be watched while it works.
function trackSourcingJob(jobId) {
  activeSourcingJobId = jobId;
  updateSourcingUIState(true);

  if (sourcingJobPollTimer) clearInterval(sourcingJobPollTimer);

  sourcingJobPollTimer = setInterval(async () => {
    try {
      const res = await api(`/api/source/research/${jobId}`);
      const job = res.job || res;
      renderSourcingJobProgress(job);
      // The target list fills as the run goes, so refresh it every tick.
      await refreshQueue();
      if (job.status === "done" || job.status === "cancelled" || job.status === "error") {
        clearInterval(sourcingJobPollTimer);
        sourcingJobPollTimer = null;
        activeSourcingJobId = null;
        updateSourcingUIState(false);
        renderSourcingReport(job);
        if (job.status === "cancelled") {
          toast(`Sourcing stopped. ${job.counts?.queued || 0} companies kept.`);
        } else if (job.status === "error") {
          toast("Sourcing failed. See the run report.");
        }
      }
    } catch (e) {
      clearInterval(sourcingJobPollTimer);
      sourcingJobPollTimer = null;
      activeSourcingJobId = null;
      updateSourcingUIState(false);
    }
  }, 1000);
}

async function stopSourcingNow() {
  if (!activeSourcingJobId) return;
  try {
    await api(`/api/source/research/${activeSourcingJobId}/cancel`, { method: "POST" });
    // Do not clear the timer here. The next poll sees "cancelled" and runs the same
    // cleanup as a normal finish, so there is one code path rather than two.
  } catch (e) {
    toast("Could not stop the run.");
  }
}

// Defaults to the active list, so anyone who ignores this control gets the behaviour
// they had before. GET /api/lists already returns {active, lists}.
async function refreshSourcingListSelect() {
  const el = $("#sourcingListSelect");
  if (!el) return;
  try {
    const res = await api("/api/lists");
    const active = res.active || "default";
    el.innerHTML = (res.lists || [])
      .map(l => `<option value="${l.id}"${l.id === active ? " selected" : ""}>${l.name || l.id}</option>`)
      .join("");
  } catch (e) { /* leave it empty rather than blocking the panel */ }
}

function updateSourcingUIState(isSourcing) {
  const stopBtn = $("#sourcingStopBtn");
  if (stopBtn) {
    if (isSourcing) stopBtn.classList.remove("hidden");
    else stopBtn.classList.add("hidden");
  }
  const runBtn = $("#runSourcingBtn");
  if (runBtn) {
    runBtn.disabled = isSourcing;
  }
  const listSel = $("#sourcingListSelect");
  if (listSel) {
    listSel.disabled = isSourcing;
  }
}

function renderSourcingJobProgress(job) {
  const statusEl = $("#sourcingStatusText");
  if (!statusEl) return;
  if (!job) return;
  const stage = job.stage || "Harvesting";
  const counts = job.counts || {};
  const checked = counts.checked || counts.harvested || 0;
  const queued = counts.queued || 0;
  statusEl.textContent = `🚀 Sourcing (${stage}): checked ${checked}, queued ${queued}...`;
}

function trackDraftJob(jobId) {
  activeDraftJobId = jobId;
  updateDraftingUIState(true);

  if (draftJobPollTimer) clearInterval(draftJobPollTimer);

  draftJobPollTimer = setInterval(async () => {
    try {
      const job = await api(`/api/draft/job/${jobId}`);
      renderDraftJobProgress(job);
      if (job.state === "done" || job.state === "cancelled" || job.state === "error") {
        clearInterval(draftJobPollTimer);
        draftJobPollTimer = null;
        activeDraftJobId = null;
        updateDraftingUIState(false);
        await refreshDrafts();
        await refreshQueue();
        if (job.state === "done") toast(`Batch drafting complete (${job.done} drafted)`);
        else if (job.state === "cancelled") toast("Batch drafting cancelled");
      }
    } catch (e) {
      clearInterval(draftJobPollTimer);
      draftJobPollTimer = null;
      activeDraftJobId = null;
      updateDraftingUIState(false);
    }
  }, 1000);
}

function renderDraftJobProgress(job) {
  const strip = $("#statusStrip");
  if (!strip) return;
  if (!job || job.state !== "running") {
    renderStatusStrip();
    return;
  }
  strip.classList.remove("hidden");
  strip.classList.remove("is-error");
  const textEl = $("#statusText");
  if (textEl) {
    textEl.textContent = `Drafting ${job.done}/${job.total}` + (job.current_slug ? ` — ${job.current_slug}` : "");
  }
  const actionBtn = $("#statusAction");
  if (actionBtn) {
    actionBtn.textContent = "Cancel";
    actionBtn.classList.remove("hidden");
    actionBtn.onclick = async () => {
      await api(`/api/draft/job/${job.job_id}/cancel`, { method: "POST" });
    };
  }
}

function updateDraftingUIState(isDrafting) {
  $$(".redraftSame, #draft5Btn, #emptyDraft5Btn").forEach(btn => {
    if (btn) {
      btn.disabled = isDrafting;
      btn.title = isDrafting ? "Batch drafting is currently in progress." : "";
    }
  });
}

/* ================= DRAFTS ================= */
function ingestCompany(cs) { companies.set(cs.slug, cs); }

async function runDraft(slug) {
  const cs = companies.get(slug);
  if (cs) { cs.state = "input"; cs._working = true; renderDrafts(); }
  try {
    const updated = await api(`/api/draft/${slug}`, { method: "POST", body: { reuse_cache: true } });
    updated._working = false;
    companies.set(slug, updated);
  } catch (e) {
    const c = companies.get(slug); if (c) { c.state = "error"; c.error = e.message; c._working = false; }
    toast(e.message, true);
  }
  renderDrafts();
  refreshCost();
}

async function refreshDrafts() {
  const r = await api("/api/drafts");
  companies.clear();
  r.drafts.forEach(ingestCompany);
  renderDrafts();
}

function stateLabel(cs) {
  if (cs._working) return "researching…";
  return ({
    input: "queued", researched: "researched", drafted: "ready to review",
    in_review: "in review", edited: "edited", approved: "approved",
    verifying: "staging…", ready: "sent", error: "error",
  })[cs.state] || cs.state;
}

function renderDrafts() {
  const results = $("#draftsList");
  const list = companyList().filter(cs => cs.state !== "ready");
  $("#draftsCount").textContent = list.length ? list.length : "";
  $("#draftsEmpty").classList.toggle("hidden", list.length > 0);

  const openSlugs = new Set(state.open);
  results.innerHTML = "";
  list.forEach(cs => {
    const tpl = $("#rowTpl").content.cloneNode(true);
    const row = tpl.querySelector(".row");
    row.dataset.slug = cs.slug;
    row.classList.toggle("is-error", cs.state === "error");
    row.classList.toggle("open", openSlugs.has(cs.slug));

    row.querySelector(".co-name").textContent = cs.name;
    const refEl = row.querySelector(".co-crm");
    refEl.textContent = [cs.voice ? voiceLabel(cs.voice) : "", cs.ref || ""].filter(Boolean).join(" · ");

    const contact = cs.contact || {};
    row.querySelector(".c-name").textContent = contact.name || (cs.state === "error" ? "" : "—");
    row.querySelector(".c-mail").textContent = contact.email || "";
    const badges = row.querySelector(".badges");
    badges.innerHTML = "";
    if (cs.disqualified) badges.innerHTML += `<span class="badge badge-warn">disqualified</span>`;
    if (cs.contact_unverified) badges.innerHTML += `<span class="badge">contact unverified</span>`;
    if (cs.was_edited) badges.innerHTML += `<span class="badge badge-ok">edited</span>`;
    if (cs.research_capped) badges.innerHTML += `<span class="badge badge-warn">research partial</span>`;

    const dc = cs.draft_confidence || {};
    if (dc.contact && dc.contact !== "found") {
      badges.innerHTML += `<span class="badge badge-warn">contact: ${esc(dc.contact)}</span>`;
    }
    if (dc.research && dc.research !== "full") {
      badges.innerHTML += `<span class="badge badge-warn">research: ${esc(dc.research)}</span>`;
    }
    if (dc.link && dc.link === "weak") {
      badges.innerHTML += `<span class="badge badge-warn">link: weak</span>`;
    }

    // Plan 34: company-website moved to .cell-act

    const pill = row.querySelector(".pill");
    pill.textContent = cs.status_pill || stateLabel(cs);
    pill.className = "pill" + (cs.state === "error" || cs.disqualified ? " pill-err" : cs.state === "drafted" ? " pill-ok" : "");

    // actions
    const act = row.querySelector(".cell-act");
    act.innerHTML = "";
    if (["input", "researched", "error", "drafted", "edited"].includes(cs.state)) {
      const wrap = document.createElement("span");
      wrap.className = "website-input-wrap";
      const inp = document.createElement("input");
      inp.type = "text";
      inp.className = "tag-add-input";
      inp.setAttribute("data-act", "company-website");
      inp.autocomplete = "off";
      inp.spellcheck = false;
      inp.value = cs.website || "";
      inp.placeholder = (cs.state === "input") ? "website (optional)" : "website — will re-research";
      const st = document.createElement("span");
      st.className = "website-save-status";
      wrap.appendChild(inp);
      wrap.appendChild(st);
      act.appendChild(wrap);
      wireWebsiteInput(inp, {
        currentValue: cs.website || "",
        save: async (value) => {
          const prevState = cs.state;
          const r = await api(`/api/companies/${cs.slug}/website`, {
            method: "PUT",
            body: { website: value }
          });
          if (r.invalidated) {
            if (["drafted", "edited"].includes(prevState)) {
              toast("Website saved. This draft was written for the old company — redraft it.");
            } else {
              toast("Website saved. This target will be re-researched on the next draft.");
            }
          }
          return r;
        },
        onSaved: (r) => {
          if (r && r.company) {
            companies.set(cs.slug, r.company);
            if (r.invalidated) {
              runDraft(cs.slug);
            } else {
              renderDrafts();
            }
          }
        }
      });
    }
    if (cs.state === "drafted" || cs.state === "edited") {
      const approve = document.createElement("button");
      approve.className = "btn primary small"; approve.textContent = "Approve";
      approve.onclick = (e) => { e.stopPropagation(); approveOne(cs.slug); };
      act.appendChild(approve);

      const eff = getEffectiveAttachments(cs);
      if (eff.length) {
        const chip = document.createElement("span");
        chip.className = "attach-chip";
        chip.textContent = `📎 ${eff.join(", ")}`;
        act.appendChild(chip);
      }
    } else if (cs.state === "error") {
      const blockers = cs.blockers || [];
      const needsContact = blockers.some(b => b.kind === "needs_contact");
      const needsResearch = blockers.some(b => b.kind === "needs_research") || blockers.length === 0;

      if (needsContact) {
        const nameIn = document.createElement("input");
        nameIn.className = "tag-add-input";
        nameIn.placeholder = "Contact Name";
        nameIn.title = "Contact Full Name";

        const emailIn = document.createElement("input");
        emailIn.className = "tag-add-input";
        emailIn.placeholder = "Email (optional)";
        emailIn.title = "Contact Email";

        const saveBtn = document.createElement("button");
        saveBtn.className = "btn ghost small";
        saveBtn.textContent = "Add contact";
        saveBtn.onclick = async (e) => {
          e.stopPropagation();
          const nameVal = nameIn.value.trim();
          const emailVal = emailIn.value.trim();
          if (!nameVal && !emailVal) {
            toast("Enter a name, email, or both", true);
            return;
          }
          try {
            const res = await api(`/api/companies/${cs.slug}/contact`, {
              method: "PUT",
              body: { name: nameVal, email: emailVal }
            });
            if (res.domain_mismatch) {
              toast("Address domain does not match company domain", "warning");
            }
            if (res.company) {
              companies.set(cs.slug, res.company);
            }
            renderDrafts();
            runDraft(cs.slug);
          } catch (err) {
            toast(err.message, true);
          }
        };
        act.appendChild(nameIn);
        act.appendChild(emailIn);
        act.appendChild(saveBtn);
      }
      if (needsResearch) {
        const retry = document.createElement("button");
        retry.className = "btn ghost small"; retry.textContent = "Retry with fresh research";
        retry.title = "Previous research will be discarded and run again";
        retry.onclick = (e) => { e.stopPropagation(); runDraft(cs.slug); };
        act.appendChild(retry);
      }
    }
    const del = document.createElement("button");
    del.className = "icon-btn small"; del.title = "Delete"; del.innerHTML = "&times;";
    del.onclick = (e) => { e.stopPropagation(); deleteDraft(cs.slug); };
    act.appendChild(del);

    // drawer toggle
    // Plan 34 (A): .badges lives inside .row-main, whose click handler toggles the drawer and calls
    // renderDrafts(), which rebuilds every row. Any input placed in that region is destroyed by the
    // very click that reaches it -- which is why the card's website field could not be edited at all.
    // Interactive controls opt out of the drawer toggle; everything else in the row still opens it.
    row.querySelector(".row-main").onclick = (evt) => {
      if (evt.target.closest("input, button, select, textarea, label, .website-input-wrap")) return;
      toggleDrawer(cs.slug);
    };

    const drawer = row.querySelector(".drawer");
    if (openSlugs.has(cs.slug)) drawer.appendChild(buildDrawer(cs));

    results.appendChild(tpl);
  });
}

function toggleDrawer(slug) {
  if (state.open.has(slug)) state.open.delete(slug); else state.open.add(slug);
  renderDrafts();
}

function buildDrawer(cs) {
  const wrap = document.createElement("div");
  wrap.className = "drawer-inner";
  if (cs.state === "error") {
    const blockers = cs.blockers || [];
    const needsContact = blockers.some(b => b.kind === "needs_contact");
    const needsResearch = blockers.some(b => b.kind === "needs_research") || blockers.length === 0;

    const resDiv = document.createElement("div");
    resDiv.className = "research";
    const failDiv = document.createElement("div");
    failDiv.className = "research-fail";
    failDiv.textContent = cs.error || "Draft failed.";
    resDiv.appendChild(failDiv);

    if (needsContact) {
      const nameIn = document.createElement("input");
      nameIn.className = "tag-add-input";
      nameIn.placeholder = "Contact Name";

      const emailIn = document.createElement("input");
      emailIn.className = "tag-add-input";
      emailIn.placeholder = "Email (optional)";

      const saveBtn = document.createElement("button");
      saveBtn.className = "btn ghost small";
      saveBtn.textContent = "Add contact";
      saveBtn.onclick = async () => {
        const nameVal = nameIn.value.trim();
        const emailVal = emailIn.value.trim();
        if (!nameVal && !emailVal) {
          toast("Enter a name, email, or both", true);
          return;
        }
        try {
          const res = await api(`/api/companies/${cs.slug}/contact`, {
            method: "PUT",
            body: { name: nameVal, email: emailVal }
          });
          if (res.domain_mismatch) {
            toast("Address domain does not match company domain", "warning");
          }
          if (res.company) {
            companies.set(cs.slug, res.company);
          }
          renderDrafts();
          runDraft(cs.slug);
        } catch (err) {
          toast(err.message, true);
        }
      };
      resDiv.appendChild(nameIn);
      resDiv.appendChild(emailIn);
      resDiv.appendChild(saveBtn);
    }

    if (needsResearch) {
      const retryBtn = document.createElement("button");
      retryBtn.className = "btn ghost small";
      retryBtn.id = "retryBtn";
      retryBtn.title = "Previous research will be discarded and run again";
      retryBtn.textContent = "Retry with fresh research";
      retryBtn.onclick = () => runDraft(cs.slug);
      resDiv.appendChild(retryBtn);
    }

    wrap.appendChild(resDiv);
    return wrap;
  }

  const rs = cs.research_summary || {};
  const links = cs.links || [];
  // proofs carry staleness (Phase 1c): dot + word before each, color AND text (a11y)
  const staleWord = { fresh: "fresh", aging: "aging", stale: "stale" };
  const proofsDetailed = rs.proofs_detailed || (rs.proof_points || []).map(p => ({ fact: p, staleness: "" }));
  const proofHtml = proofsDetailed.map(p => {
    const s = (p.staleness || "").toLowerCase();
    const chip = s ? `<span class="stale-dot stale-${s}" title="${esc(staleWord[s] || s)}"></span><span class="stale-txt">${esc(staleWord[s] || s)}</span> ` : "";
    return `<li>${chip}${esc(p.fact)}</li>`;
  }).join("");
  const tractionHtml = (rs.traction_signals || []).map(p => `<li>${esc(p)}</li>`).join("");
  // why-this-voice / why-this-contact (Phase 1d): a quiet explanation, not an alert
  const whyVoice = cs.why_voice ? `<div class="rs-why"><span class="rs-lbl">Routing</span> ${esc(cs.why_voice)}</div>` : "";
  // per-target cost (Phase 1e)
  const cost = cs.cost || {};
  const costNote = (cost.estimate > 0) ? `<span class="cost-note" title="in ${cost.in} · out ${cost.out} · cached ${cost.cached} tokens">${fmtCost(cost.estimate)}</span>` : "";
  const combinedTractionProof = tractionHtml + proofHtml;
  const recent = rs.recent_point ? `<div class="rs-recent"><span class="rs-lbl">Recent</span> ${esc(rs.recent_point)}</div>` : "";
  const read = rs.situation_read ? `<div class="rs-read"><span class="rs-lbl">Read</span> ${esc(rs.situation_read)}</div>` : "";
  const thesisHtml = (rs.thesis && rs.thesis.market_shift) ? `<div class="rs-read"><span class="rs-lbl">Thesis</span> ${esc(rs.thesis.market_shift)} ${esc(rs.thesis.company_positioning||"")}</div>` : "";
  const statedPlan = (rs.stated_plan && rs.stated_plan.detail) ? `<div class="rs-read"><span class="rs-lbl">Stated Plan</span> ${esc(rs.stated_plan.detail)}</div>` : "";
  const earnedObs = (rs.earned_observation && rs.earned_observation.read) ? `<div class="rs-read" style="background:var(--bg2); border-left:3px solid var(--accent); padding:4px 0 4px 8px; margin:4px 0;"><span class="rs-lbl">Earned Observation</span> <em>${esc(rs.earned_observation.read)}</em></div>` : "";
  const tied = (rs.evidence_tied || []).length ? `<div class="rs-tie"><span class="rs-lbl">Tied in</span> ${rs.evidence_tied.map(esc).join(", ")}</div>` : "";
  const SRC_SHOWN = 4;
  const linksHtml = links.length ? (() => {
    const chip = u => `<a class="src-chip" href="${esc(u)}" target="_blank" rel="noopener" title="${esc(u)}">${esc(shortUrl(u))}</a>`;
    const head = links.slice(0, SRC_SHOWN).map(chip).join("");
    const rest = links.slice(SRC_SHOWN);
    const more = rest.length
      ? `<details class="src-more"><summary>+${rest.length} more</summary><div class="src-wrap">${rest.map(chip).join("")}</div></details>`
      : "";
    return `<div class="rs-src"><span class="rs-lbl">Sources</span><div class="src-wrap">${head}</div>${more}</div>`;
  })() : "";

  // voice picker (redraft) — all voices
  const voiceOpts = allVoices.map(v => `<option value="${v.id}"${v.id === cs.voice ? " selected" : ""}>${esc(v.display_name)}</option>`).join("");

  // attachments selector
  const curAtts = cs.attachments || [];
  let attVal = "__default__";
  if (curAtts.length > 0) {
    attVal = curAtts.includes("__none__") ? "__none__" : curAtts[0];
  }
  const defaultDesc = selectedDefaultAttachment ? `Use default (${selectedDefaultAttachment})` : "Use default (none)";
  let attOpts = `<option value="__default__">${esc(defaultDesc)}</option>`;
  attOpts += `<option value="__none__"${attVal === "__none__" ? " selected" : ""}>No attachment</option>`;
  allAttachments.forEach(a => {
    attOpts += `<option value="${esc(a.name)}"${a.name === attVal ? " selected" : ""}>Attach ${esc(a.name)}</option>`;
  });

  // Domain & Contact confidence details (Execution Plan 4, Stage C)
  const contact = cs.contact || (cs.cache || {}).contact || {};
  const emailMethod = contact.email_method || (contact.email_source_url ? "found_on_page" : (contact.email ? "pattern_guess" : "not_found"));
  const sourceUrl = contact.email_source_url || "";
  const resolvedDom = cs.company_domain || cs.recipient_domain || (cs.cache || {}).company?.resolved_domain || "";
  const domSource = cs.domain_source || (cs.cache || {}).company?.domain_source || (resolvedDom ? "given" : "unresolved");

  let badgeHtml = "";
  if (contact.email_note) {
    badgeHtml = `<span class="tag badge-warn" title="${esc(contact.email_note)}">Shared Inbox</span>`;
  } else if (emailMethod === "found_on_page") {
    badgeHtml = `<a class="tag pill-ok" href="${esc(sourceUrl || "#")}" target="_blank" rel="noopener" title="Source: ${esc(sourceUrl || "Confirmed on page")}">Verified</a>`;
  } else if (emailMethod === "pattern_guess") {
    badgeHtml = `<span class="tag badge-warn" title="Pattern guess at ${esc(resolvedDom || "domain")} — not confirmed on a page">Guessed</span>`;
  } else {
    badgeHtml = `<span class="tag pill-err" title="No contact email confirmed">No contact found</span>`;
  }

  const domTagStyle = domSource === "unresolved" ? "color:var(--error); font-weight:600;" : "color:var(--ink-soft);";
  const domLineHtml = `<div class="rs-read"><span class="rs-lbl">Domain</span> ${esc(resolvedDom || "Unresolved")} <span class="tag" style="${domTagStyle} font-size:11px;">(${esc(domSource)})</span></div>`;

  wrap.innerHTML = `
    <div class="drawer-grid">
      <div class="research">
        <div class="research-top">
          <span class="eyebrow">Research</span>
          <div style="display: flex; gap: 8px; flex-wrap: wrap; margin-top: 4px;">
            <div class="voice-pick" style="flex: 1; min-width: 120px;">
              <label>Voice</label>
              <select class="voiceSel" style="width: 100%;">${voiceOpts}</select>
            </div>
            <div class="voice-pick" style="flex: 1; min-width: 120px;">
              <label>Attachment</label>
              <select class="attachSel" style="width: 100%;">${attOpts}</select>
            </div>
          </div>
        </div>
        ${whyVoice}
        ${cs.research_capped ? `<div class="rs-flag"><span class="rs-lbl rs-lbl-warn">Research partial</span> Stopped early or thin — verify the facts and contact before sending.</div>` : ""}
        ${domLineHtml}
        ${rs.what_they_do ? `<div class="rs-what">${esc(rs.what_they_do)}</div>` : ""}
        ${thesisHtml}
        ${rs.role_title
          ? `<div class="rs-read"><span class="rs-lbl">Role</span> ${esc(rs.role_title)}${rs.role_source ? ` <a href="${esc(rs.role_source)}" target="_blank" rel="noopener">(source)</a>` : ` <span class="rs-warn">(unsourced)</span>`}</div>`
          : (cs.role_exists === false
              ? `<div class="rs-read"><span class="rs-lbl">Role</span> No advertised role — create-the-seat</div>`
              : `<div class="rs-read"><span class="rs-lbl">Role</span> <span class="rs-warn">not determined</span></div>`)}
        ${cs.company_size
          ? `<div class="rs-read"><span class="rs-lbl">Size</span> ${esc(cs.company_size)}${rs.company_size_evidence ? ` — ${esc(rs.company_size_evidence)}` : ` <span class="rs-warn">(no evidence captured — verify)</span>`}</div>`
          : (rs.company_size_evidence
              ? `<div class="rs-read"><span class="rs-lbl">Size</span> ${esc(rs.company_size_evidence)}</div>`
              : `<div class="rs-read"><span class="rs-lbl">Size</span> <span class="rs-warn">not determined</span></div>`)}
        ${earnedObs}
        ${statedPlan}
        ${combinedTractionProof ? `<div class="rs-proof"><span class="rs-lbl">Traction & Proof</span><ul>${combinedTractionProof}</ul></div>` : ""}
        ${recent}${read}${tied}${linksHtml}
        ${(cs.notes || []).length ? `<div class="notes">${cs.notes.map(n => `<div class="note note-${n.severity}">${esc(n.text)}</div>`).join("")}</div>` : ""}
        ${(rs.research_failures && rs.research_failures.length) ? `<div class="rs-fails"><span class="rs-lbl">Research gaps</span><ul>${rs.research_failures.map(f => `<li>${esc(f)}</li>`).join("")}</ul></div>` : ""}
      </div>
      <div class="letter-wrap">
        <div class="letter">
          <div class="letter-head">
            <div class="letter-subject">Subject: <input type="text" class="subjectInput" value="${esc(cs.subject || "")}" /></div>
            <div class="c-to" style="display:flex; align-items:center; gap:8px;">To: <input type="text" class="toInput" style="flex:1; border:none; background:transparent; font-weight:600;" value="${esc(contact.email || cs.sent_to || "")}" /> ${badgeHtml}</div>
          </div>
          <div class="letter-body">
            <textarea class="emailEdit" spellcheck="true">${esc(cs.final_email || cs.machine_email || "")}</textarea>
          </div>
        </div>
        <div class="letter-actions">
          <button class="btn ghost small saveEdit">Save edit</button>
          <button class="btn ghost small resetEdit">Restore original</button>
          <button class="btn ghost small compareBtn">Compare with original</button>
          <button class="btn ghost small redraftSame" title="Redraft using the current voice">Redraft</button>
          <div class="wc"></div>
          ${costNote}
        </div>
        <div class="compare hidden"></div>
      </div>
    </div>`;

  const emailEdit = wrap.querySelector(".emailEdit");
  const subjectInput = wrap.querySelector(".subjectInput");
  const wc = wrap.querySelector(".wc");
  const setWc = () => { wc.textContent = wordCount(emailEdit.value) + " words"; };
  setWc(); emailEdit.addEventListener("input", setWc);

  wrap.querySelector(".saveEdit").onclick = async () => {
    try {
      const toInput = wrap.querySelector(".toInput");
      const newEmail = toInput ? toInput.value.trim() : "";
      const at = newEmail.lastIndexOf("@");
      const newDomain = at > 0 ? newEmail.substring(at + 1).toLowerCase() : "";
      if (newDomain && cs.recipient_domain && newDomain !== cs.recipient_domain) {
        const okDom = await dialog({
          title: "Update company domain?",
          message: `The email address uses @${newDomain}, which differs from the company's pinned domain (@${cs.recipient_domain}). Use ${newDomain} as this company's domain for future redrafts?`,
          options: [{ label: "Keep current domain", value: false }, { label: `Update domain to ${newDomain}`, value: true, primary: true }],
        });
        if (okDom) {
          cs.recipient_domain = newDomain;
          if (cs.cache && cs.cache.company) cs.cache.company.resolved_domain = newDomain;
        }
      }
      // Send the edited address. Previously this mutated the LOCAL copy only and the
      // PUT carried just subject + body, so the server never saw it -- and the drawer
      // showed the new address while approve sent to the old one.
      const updated = await api(`/api/companies/${cs.slug}/email`, {
        method: "PUT",
        body: { subject: subjectInput.value, email: emailEdit.value, contact_email: newEmail },
      });
      updated.recipient_domain = cs.recipient_domain;
      companies.set(cs.slug, updated); toast("Edit saved"); renderDrafts();
    } catch (e) { toast(e.message, true); }
  };
  wrap.querySelector(".resetEdit").onclick = async () => {
    try { const updated = await api(`/api/companies/${cs.slug}/reset`, { method: "POST" }); companies.set(cs.slug, updated); toast("Restored original"); renderDrafts(); }
    catch (e) { toast(e.message, true); }
  };
  wrap.querySelector(".compareBtn").onclick = () => {
    const box = wrap.querySelector(".compare");
    if (!box.classList.contains("hidden")) { box.classList.add("hidden"); return; }
    box.innerHTML = `<div class="cmp-col"><span class="eyebrow">Original</span><pre>${esc(cs.machine_email || "")}</pre></div>
                     <div class="cmp-col"><span class="eyebrow">Current</span><pre>${esc(emailEdit.value)}</pre></div>`;
    box.classList.remove("hidden");
  };
  wrap.querySelector(".redraftSame").onclick = async () => {
    const isEdited = (emailEdit.value !== (cs.machine_email || "")) || (subjectInput.value !== (cs.subject || ""));
    if (isEdited) {
      const ok = await dialog({
        title: "Overwrite unsaved edits?",
        message: "You have modified this email draft. Redrafting will replace your edits with a freshly generated draft. Continue?",
        options: [{ label: "Cancel", value: false }, { label: "Redraft & overwrite", value: true, danger: true }]
      });
      if (!ok) return;
    }
    try {
      const r = await api(`/api/companies/${cs.slug}/redraft`, { method: "POST", body: { voice: cs.voice, reuse_cache: Boolean(cs.cache) } });
      companies.set(cs.slug, r.company);
      toast(cs.voice ? `Redrafted as ${voiceLabel(cs.voice)}` : "Redrafted");
      renderDrafts();
    } catch (e2) {
      toast(e2.message, true);
    }
  };
  wrap.querySelector(".voiceSel").onchange = async (e) => {
    const val = e.target.value;
    const voice = val === "__auto__" ? null : val;
    if (voice === cs.voice) return;
    try {
      const r = await api(`/api/companies/${cs.slug}/redraft`, { method: "POST", body: { voice, reuse_cache: Boolean(cs.cache) } });
      companies.set(cs.slug, r.company); toast(voice ? `Redrafted as ${voiceLabel(voice)}` : "Redrafted (auto)"); renderDrafts();
    } catch (e2) { toast(e2.message, true); }
  };
  wrap.querySelector(".attachSel").onchange = async (e) => {
    const val = e.target.value;
    let names = [];
    if (val === "__none__") {
      names = ["__none__"];
    } else if (val !== "__default__") {
      names = [val];
    }
    try {
      const updated = await api(`/api/companies/${cs.slug}/attachments`, { method: "PUT", body: { names } });
      companies.set(cs.slug, updated);
      toast("Attachment override saved");
      renderDrafts();
    } catch (err) {
      toast(err.message, true);
    }
  };
  return wrap;
}

async function deleteDraft(slug) {
  const ok = await dialog({ title: "Delete draft?", message: "Remove this draft? This cannot be undone.", options: [{ label: "Cancel", value: false }, { label: "Delete", value: true, danger: true }] });
  if (!ok) return;
  try { await api(`/api/companies/${slug}`, { method: "DELETE" }); companies.delete(slug); state.open.delete(slug); renderDrafts(); }
  catch (e) { toast(e.message, true); }
}
async function clearDrafts() {
  if (!companyList().length) return;
  const ok = await dialog({ title: "Clear drafts?", message: "Remove all active drafts? Sent emails are unaffected.", options: [{ label: "Cancel", value: false }, { label: "Clear", value: true, danger: true }] });
  if (!ok) return;
  await api("/api/drafts/clear", { method: "POST" });
  companies.clear(); state.open.clear(); renderDrafts(); toast("Drafts cleared");
}

async function approveOne(slug) {
  const cs = companies.get(slug);
  const contact = (cs || {}).contact || ((cs || {}).cache || {}).contact || {};
  const emailMethod = contact.email_method || (contact.email_source_url ? "found_on_page" : (contact.email ? "pattern_guess" : "not_found"));
  const contactEmail = contact.email || (cs ? cs.sent_to : "");
  const recipientDom = (cs ? cs.recipient_domain : "") || (contactEmail.includes("@") ? contactEmail.split("@")[1] : "domain");

  if (!contactEmail) {
    toast("Cannot approve target with no contact email. Add a contact email first.", true);
    return;
  }

  let guessNote = "";
  if (emailMethod === "pattern_guess") {
    guessNote = `\n\n⚠️ UNCONFIRMED EMAIL: This contact is an unverified pattern guess at @${recipientDom}. Are you sure you want to send to an unconfirmed address?`;
  }

  const dqNote = cs && cs.disqualified ? "\n\nThis target is marked disqualified (work mode or language). Approve anyway?" : "";
  const eff = getEffectiveAttachments(cs);
  const attachNote = eff.length ? `\n\nThe email will include ${eff.join(", ")}.` : "";
  // send-window advisory (Phase 6c): a non-blocking hint, never a block
  let windowNote = "";
  try { const w = await api("/api/send_window"); if (w.advise) windowNote = `\n\n${w.message}`; } catch (e) {}
  const ok = await dialog({
    title: emailMethod === "pattern_guess" ? "Approve unconfirmed contact?" : "Approve and stage?",
    message: `Stage the email for ${cs ? cs.name : "this target"} as a .eml file and write it to your tracker. Nothing sends until you open it and press send.${guessNote}${dqNote}${attachNote}${windowNote}`,
    options: [{ label: "Cancel", value: false }, { label: emailMethod === "pattern_guess" ? "Confirm & Stage" : "Approve", value: true, primary: true }],
  });
  if (!ok) return;
  try {
    const r = await api(`/api/companies/${slug}/approve`, { method: "POST" });
    companies.delete(slug); state.open.delete(slug); renderDrafts();
    state.followups = null; await updateFollowupsBadge();   // approval may have enrolled a follow-up
    if (state.view === "followups") await refreshFollowups();
    refreshCost();
    const ap = r.apollo || {};
    const bits = ["Approved and staged"];
    if (ap.opened) bits.push(`opened ${ap.opened} in your mail app`);
    // Staging always succeeds; if Apollo verification or the mail-open had a problem
    // (bad key, rate limit, no mail handler), surface the reason as a warning toast.
    const isProblem = Boolean(ap.api_error) || Boolean(ap.failed);
    if (isProblem && ap.note) bits.push(ap.note);
    toast(bits.join(" · "), isProblem);
  } catch (e) { toast(e.message, true); }
}

/* ================= ARCHIVE ================= */
// Human-readable outcome label for a Sent card.
function outcomeLabel(rec) {
  if (rec.pipeline_flag === "no_response") return "no response";
  const rs = rec.reply_state || "awaiting";
  if (rs === "bounced_exhausted") return "bounced (all addresses)";
  return rs; // awaiting | replied | bounced
}

async function openArchive() {
  showModal("archiveModal");
  try {
    const r = await api("/api/archive");
    const list = $("#archiveList"); list.innerHTML = "";
    if (!r.archive.length) { list.innerHTML = `<p class="lede">No sent emails yet.</p>`; }
    r.archive.slice().reverse().forEach(rec => {
      const el = document.createElement("div"); el.className = "arch-card";
      const label = outcomeLabel(rec);
      const marked = rec.outcome_source === "manual"
        ? ` <span class="badge" title="You marked this by hand">marked</span>` : "";
      const badgeCls = label === "replied" ? "badge badge-ok" : "badge";
      el.innerHTML = `<div class="arch-head"><strong>${esc(rec.name)}</strong> <span class="arch-sub">${esc(voiceLabel(rec.voice))} · ${esc((rec.contact || {}).email || "")}</span></div>
        <div class="arch-subject">${esc(rec.subject || "")}</div>
        <div class="arch-outcome"><span class="${badgeCls}">${esc(label)}</span>${marked}</div>
        <pre class="arch-body">${esc(rec.final_email || "")}</pre>`;

      // inline outcome controls — only when we found a matching send record
      if (rec.sent_id) {
        const bar = document.createElement("div"); bar.className = "arch-actions";
        const rs = rec.reply_state || "awaiting";
        const isNoResp = rec.pipeline_flag === "no_response";
        const addBtn = (outcome, text) => {
          const b = document.createElement("button");
          b.className = "linklike" + (outcome === "replied" ? " ok" : "");
          b.textContent = text;
          b.onclick = () => markSentOutcome(rec.sent_id, outcome);
          bar.appendChild(b);
        };
        // show the actions that make sense given the current state
        if (rs !== "replied") addBtn("replied", "mark replied");
        if (rs !== "bounced" && rs !== "bounced_exhausted") addBtn("bounced", "mark bounced");
        if (!isNoResp && rs === "awaiting") addBtn("no_response", "mark no-response");
        if (isNoResp) addBtn("reopen", "reopen");
        // reset is offered whenever an outcome (reply/bounce) has been set
        if (rs === "replied" || rs === "bounced" || rs === "bounced_exhausted") addBtn("awaiting", "reset");
        el.appendChild(bar);
      }

      list.appendChild(el);
    });
  } catch (e) { toast(e.message, true); }
}

// Mark a Sent card's outcome inline. Same endpoint + effects as the Triage menu. After it lands,
// re-open the archive so the card's badge/buttons update, and refresh other views if present.
async function markSentOutcome(sentId, outcome) {
  try {
    const res = await api(`/api/sent/${encodeURIComponent(sentId)}/outcome`, { method: "POST", body: { outcome } });
    let msg = { replied: "Marked replied", bounced: "Marked bounced", awaiting: "Reset to awaiting",
                no_response: "Marked no-response", reopen: "Reopened" }[outcome] || "Updated";
    if (outcome === "bounced") {
      if (res && res.retry && res.retry.email) {
        const who = res.retry.person ? ` (${res.retry.person})` : "";
        msg += ` — retry to ${res.retry.email}${who} staged in Drafts`;
      } else if (res && res.exhausted) {
        msg += " — no more addresses to try";
      }
    }
    toast(msg);
    await openArchive();                         // re-render the Sent list with the new state
    if (typeof refreshTriage === "function") { try { await refreshTriage(); } catch (e) {} }
    if (typeof refreshDrafts === "function") { try { await refreshDrafts(); } catch (e) {} }
  } catch (e) { toast(e.message, true); }
}
async function clearArchive() {
  const ok = await dialog({ title: "Clear sent?", message: "Remove all archived emails?", options: [{ label: "Cancel", value: false }, { label: "Clear", value: true, danger: true }] });
  if (!ok) return;
  await api("/api/archive/clear", { method: "POST" }); openArchive(); toast("Cleared");
}

/* ================= FOLLOW-UPS (work queue) ================= */

// Unified view router. The tab strip is the single source of truth for "where am I working".
// Workspace = the split (ingest + queue + drafts); the others are full-width views.
const VIEWS = ["workspace", "followups", "pipeline", "performance", "triage", "profile"];

/* ---------- topbar tab strip ---------- */
function initTabStripScroll() {
  const strip = $("#topbarTabs");
  if (!strip) return;
  // A vertical wheel gesture is the only scroll a plain mouse can produce.
  // Without this translation the strip is unreachable without a trackpad.
  strip.addEventListener("wheel", (e) => {
    if (Math.abs(e.deltaY) <= Math.abs(e.deltaX)) return;   // real h-scroll: pass through
    if (strip.scrollWidth <= strip.clientWidth) return;      // nothing hidden
    e.preventDefault();
    strip.scrollLeft += e.deltaY;
  }, { passive: false });
}

function scrollActiveTabIntoView() {
  const active = $("#topbarTabs .topbar-tab.is-active");
  if (active && active.scrollIntoView) {
    active.scrollIntoView({ block: "nearest", inline: "nearest" });
  }
}

function showView(name) {
  if (!VIEWS.includes(name)) name = "workspace";
  state.view = name;
  const isWorkspace = name === "workspace";
  const ingest = $("#ingestPanel"), split = $("#splitLayout"), banner = $("#ingestBanner");
  if (ingest) ingest.classList.toggle("hidden", !isWorkspace);
  if (split) split.classList.toggle("hidden", !isWorkspace);
  if (!isWorkspace && banner) banner.classList.add("hidden");
  $("#followupsView").classList.toggle("hidden", name !== "followups");
  $("#pipelineView").classList.toggle("hidden", name !== "pipeline");
  $("#performanceView").classList.toggle("hidden", name !== "performance");
  $("#triageView").classList.toggle("hidden", name !== "triage");
  $("#profileView").classList.toggle("hidden", name !== "profile");
  // tab strip a11y + active state
  $$(".topbar-tab").forEach(t => {
    const on = t.dataset.view === name;
    t.classList.toggle("is-active", on);
    t.setAttribute("aria-selected", on ? "true" : "false");
  });
  scrollActiveTabIntoView();
  if (name === "followups") refreshFollowups();
  else if (name === "pipeline") refreshPipeline();
  else if (name === "performance") refreshPerformance();
  else if (name === "triage") refreshTriage();
  else if (name === "profile") refreshProfileTab();
}

// keep the old name working as a thin wrapper (called from follow-up row handlers)
function showFollowupsView(on) { showView(on ? "followups" : "workspace"); }

async function refreshFollowups() {
  const list = $("#followupsList");
  if (list) list.innerHTML = '<div class="col-empty shimmer" style="padding:24px; text-align:center;">Loading follow-ups\u2026</div>';
  try {
    const r = await api("/api/followups");
    state.followups = r.followups || [];
    renderFollowups();
  } catch (e) {
    state.followups = [];
    if (list) list.innerHTML = `<div class="col-empty" style="padding:24px; text-align:center;"><p>${esc(e.message)}</p><button class="btn ghost small" onclick="refreshFollowups()">Retry</button></div>`;
  }
  updateFollowupsBadge();
}

async function updateFollowupsBadge() {
  let items = state.followups;
  if (!items) {
    try { const r = await api("/api/followups"); items = r.followups || []; state.followups = items; }
    catch (e) { items = []; }
  }
  const due = items.filter(f => f.is_due && f.status === "pending").length;
  const badge = $("#followupsBadge");
  if (!badge) return;
  badge.textContent = due ? String(due) : "";
  badge.classList.toggle("hidden", due === 0);
}

function renderFollowups() {
  const list = $("#followupsList");
  const items = state.followups || [];
  $("#followupsEmpty").classList.toggle("hidden", items.length > 0);
  list.innerHTML = "";
  items.forEach(f => {
    const tpl = $("#fuRowTpl").content.cloneNode(true);
    const row = tpl.querySelector(".fu-row");
    row.classList.toggle("is-due", !!f.is_due);
    row.querySelector(".fu-name").textContent = f.name + (f.step > 1 ? `  ·  follow-up #${f.step}` : "");
    const who = [f.contact_name, f.contact_email].filter(Boolean).join(" · ");
    row.querySelector(".fu-sub").textContent = who || (f.original_subject ? `re: ${f.original_subject}` : "");
    row.querySelector(".fu-elapsed").textContent = f.elapsed_label ? `sent ${f.elapsed_label}` : "";
    const due = row.querySelector(".fu-due");
    due.textContent = f.due_label || "";
    due.classList.toggle("fu-due-now", !!f.is_due);

    const act = row.querySelector(".fu-row-act");
    if (f.status === "pending") {
      const draft = document.createElement("button");
      draft.className = "btn primary small"; draft.textContent = "Draft follow-up";
      draft.onclick = () => draftFollowup(f.id);
      act.appendChild(draft);
    } else if (f.status === "drafted") {
      const go = document.createElement("button");
      go.className = "btn ghost small"; go.textContent = "Open in Drafts";
      go.onclick = () => { showFollowupsView(false); if (f.draft_slug) { state.open.add(f.draft_slug); renderDrafts(); } };
      act.appendChild(go);
      const badge = document.createElement("span");
      badge.className = "badge badge-ok"; badge.textContent = "drafted";
      act.appendChild(badge);
    }
    const dismiss = document.createElement("button");
    dismiss.className = "icon-btn small"; dismiss.title = "Dismiss"; dismiss.innerHTML = "&times;";
    dismiss.onclick = () => dismissFollowup(f.id);
    act.appendChild(dismiss);

    list.appendChild(tpl);
  });
}

async function draftFollowup(fid) {
  try {
    const r = await api(`/api/followups/${encodeURIComponent(fid)}/draft`, { method: "POST", body: { reuse_cache: true } });
    const cs = r.company || {};
    if (cs.state === "error") { toast(cs.error || "Follow-up draft failed", true); await refreshFollowups(); return; }
    await refreshDrafts();
    await refreshFollowups();
    showFollowupsView(false);
    if (cs.slug) { state.open.add(cs.slug); renderDrafts(); }
    toast("Follow-up drafted — review and approve like any email");
  } catch (e) { toast(e.message, true); }
}

async function dismissFollowup(fid) {
  const ok = await dialog({
    title: "Dismiss follow-up?",
    message: "Remove this follow-up from the queue. Approving the original again would re-create it.",
    options: [{ label: "Cancel", value: false }, { label: "Dismiss", value: true, danger: true }],
  });
  if (!ok) return;
  try { await api(`/api/followups/${encodeURIComponent(fid)}/dismiss`, { method: "POST" }); await refreshFollowups(); toast("Follow-up dismissed"); }
  catch (e) { toast(e.message, true); }
}

async function clearFollowups() {
  if (!(state.followups || []).length) return;
  const ok = await dialog({
    title: "Clear all follow-ups?",
    message: "Remove every queued follow-up. Approved follow-ups already staged as .eml are unaffected.",
    options: [{ label: "Cancel", value: false }, { label: "Clear all", value: true, danger: true }],
  });
  if (!ok) return;
  try { await api("/api/followups/clear", { method: "POST" }); await refreshFollowups(); toast("Follow-ups cleared"); }
  catch (e) { toast(e.message, true); }
}

/* ================= EXPORT (Phase 1b) ================= */

async function doExport() {
  // stream the CSV straight from the endpoint via a token-carrying fetch, then trigger a download
  try {
    const res = await fetch(`/api/export?fmt=csv&scope=drafts`, { headers: { "x-wizzard-token": TOKEN } });
    if (!res.ok) throw new Error(`export failed (${res.status})`);
    const blob = await res.blob();
    const cd = res.headers.get("content-disposition") || "";
    const m = cd.match(/filename="([^"]+)"/);
    const name = m ? m[1] : "outreach_wizzard.csv";
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a"); a.href = url; a.download = name; a.click();
    URL.revokeObjectURL(url);
    const rows = Math.max(0, (await blob.text()).trim().split("\n").length - 1);
    toast(`Exported ${rows} row${rows === 1 ? "" : "s"}`);
  } catch (e) { toast(e.message, true); }
}

/* ================= COST METER (Phase 1e) ================= */

function fmtCost(n) { return "~$" + (Number(n) || 0).toFixed(n >= 1 ? 2 : 4); }

async function refreshCost() {
  try {
    const c = await api("/api/cost");
    const meter = $("#costMeter");
    if (!meter) return;
    const drafts = c.drafts || 0;
    // Unit economics rather than a running total. A total falls when you simply
    // draft less, which looks like an improvement and is not one; cost per draft
    // only falls when a draft genuinely gets cheaper. The absolute figures are one
    // tap away in the popover.
    meter.textContent = drafts
      ? `${fmtCost(c.per_draft)}/draft · ${drafts}`
      : (c.has_unattributed_spend ? `${fmtCost(c.cost)} · 0 drafts` : "~$0.00 · 0 drafts");
    meter.title = `${fmtCost(c.cost)} total across ${drafts} draft${drafts === 1 ? "" : "s"}`
                + ` · in ${c.in || 0} · out ${c.out || 0} · cached ${c.cached || 0} tokens`
                + ` · click for a breakdown`;
    meter.classList.toggle("hidden", !(c.cost || drafts));
    state._cost = c;
  } catch (e) { /* cost is optional; never block */ }
}

async function openCostPopover() {
  const c = state._cost || await api("/api/cost");
  const byModel = c.by_model || {};
  const lines = Object.keys(byModel).map(m =>
    `${m}: ${fmtCost(byModel[m].cost)} (${byModel[m].in}/${byModel[m].out} tok)`).join("\n") || "No usage yet.";

  // The absolutes the headline no longer carries, plus cost per APPROVED draft.
  // That divides by outcomes rather than attempts, so it counts the money spent on
  // drafts that were never good enough to send. It lags the headline, because a
  // draft can sit unapproved for days, which is why it lives here and not up top.
  const unit = [
    `Per draft:     ${fmtCost(c.per_draft || 0)}   (${c.drafts || 0} drafts)`,
    (c.approved ? `Per approved:  ${fmtCost(c.per_approved || 0)}   (${c.approved} approved)`
                : `Per approved:  not yet, nothing approved`),
    `Total:         ${fmtCost(c.cost || 0)}`,
    `Tokens:        in ${c.in || 0} · out ${c.out || 0} · cached ${c.cached || 0}`,
  ].join("\n");

  const ok = await dialog({
    title: "Cost per draft",
    message: `${unit}\n\nBy model:\n${lines}`,
    options: [{ label: "Reset session", value: "reset", danger: true }, { label: "Close", value: false, primary: true }],
  });
  if (ok === "reset") { await api("/api/cost/reset", { method: "POST" }); await refreshCost(); toast("Session cost reset"); }
}

/* ================= PIPELINE BOARD (Phase 2) ================= */

async function refreshPipeline() {
  const cols = $("#pipelineCols");
  if (cols) cols.innerHTML = '<div class="col-empty shimmer" style="padding:24px; text-align:center;">Loading pipeline board\u2026</div>';
  const listFilter = $("#pipeListFilter");
  const listId = listFilter ? listFilter.value : "";
  try {
    state.pipeline = await api(`/api/pipeline?list_id=${encodeURIComponent(listId)}`);
  } catch (e) {
    state.pipeline = null;
    if (cols) cols.innerHTML = `<div class="col-empty" style="padding:24px; text-align:center;"><p>${esc(e.message)}</p><button class="btn ghost small" onclick="refreshPipeline()">Retry</button></div>`;
    toast(e.message, true);
    return;
  }
  if (listFilter) {
    const currentListVal = listFilter.value;
    const lists = state.lists || [];
    let opts = '<option value="">All lists</option>';
    lists.forEach(l => {
      opts += `<option value="${esc(l.id)}">${esc(l.name || l.id)}</option>`;
    });
    opts += '<option value="unassigned">Unassigned</option>';
    listFilter.innerHTML = opts;
    listFilter.value = currentListVal;
  }
  // populate the voice filter once from the cards present
  const sel = $("#pipeVoiceFilter");
  const voices = new Set();
  Object.values(state.pipeline.columns).forEach(col => col.forEach(c => c.voice && voices.add(c.voice)));
  const cur = sel.value;
  sel.innerHTML = '<option value="">all voices</option>' +
    Array.from(voices).map(v => `<option value="${esc(v)}">${esc(voiceLabel(v))}</option>`).join("");
  sel.value = cur;
  renderPipeline();
}

function renderPipeline() {
  const board = state.pipeline;
  if (!board) return;
  const s = board.summary;
  $("#pipelineSummary").innerHTML =
    `<b>${s.sent}</b> sent · <b>${s.replied}</b> replied · <b>${s.bounced}</b> bounced · <b>${s.reply_pct}%</b> reply rate`;
  const vf = $("#pipeVoiceFilter").value;
  const staleOnly = $("#pipeStaleOnly").checked;
  const cols = $("#pipelineCols");
  cols.innerHTML = "";
  board.order.forEach(stage => {
    let cards = board.columns[stage] || [];
    if (vf) cards = cards.filter(c => c.voice === vf);
    if (staleOnly) cards = cards.filter(c => c.stale);
    const col = document.createElement("div");
    col.className = "board-col";
    col.innerHTML = `<header>${esc(board.labels[stage])} <span class="col-count">${cards.length}</span></header>`;
    const body = document.createElement("div"); body.className = "board-col-body";
    if (!cards.length) {
      const empty = document.createElement("div"); empty.className = "board-empty";
      empty.textContent = stage === "replied" ? "No replies yet — connect an inbox in Settings to track them."
        : stage === "bounced" ? "No bounces. Good."
        : stage === "sent" ? "Nothing sent yet." : "Nothing here yet.";
      body.appendChild(empty);
    }
    cards.forEach(c => body.appendChild(pipelineCard(c, stage)));
    col.appendChild(body);
    cols.appendChild(col);
  });
}

function pipelineCard(c, stage) {
  const card = document.createElement("div");
  card.className = "board-card" + (c.stale ? " is-stale" : "");
  const voice = c.voice ? `<span class="pill">${esc(voiceLabel(c.voice))}</span>` : "";
  const quiet = c.stale ? `<span class="board-quiet">quiet ${c.quiet_days}d</span>` : "";
  card.innerHTML = `<div class="board-card-name">${esc(c.name)}</div>
    <div class="board-card-sub">${voice} ${c.contact ? esc(c.contact) : ""} ${quiet}</div>`;
  const acts = document.createElement("div"); acts.className = "board-card-acts";
  (c.actions || []).forEach(a => {
    if (a === "outcome_menu") {
      const sel = document.createElement("select");
      sel.className = "mini-select";
      sel.innerHTML = `<option value="">Mark outcome...</option><option value="replied">Replied</option><option value="bounced">Bounced</option><option value="no_response">No response</option>`;
      sel.onchange = () => { if (sel.value) markPipelineOutcome(c.sent_id, sel.value); sel.value = ""; };
      acts.appendChild(sel);
    } else {
      const b = document.createElement("button"); b.className = "linklike";
      if (a === "open") { b.textContent = "open"; b.onclick = () => { showView("workspace"); state.open.add(c.slug); renderDrafts(); }; }
      else if (a === "draft") { b.textContent = "draft"; b.onclick = () => { showView("workspace"); draftFromQueue(c.slug); }; }
      else if (a === "approve") { b.textContent = "approve"; b.onclick = () => approveOne(c.slug); }
      else if (a === "reopen") { b.textContent = "reopen"; b.onclick = () => markPipeline(c.slug, "reopen"); }
      else if (a === "draft_reply") { b.textContent = "draft reply"; b.onclick = () => showView("triage"); }
      else if (a === "view_retry") { b.textContent = "view retry"; b.onclick = () => { showView("workspace"); renderDrafts(); }; }
      if (b.textContent) acts.appendChild(b);
    }
  });
  card.appendChild(acts);
  return card;
}

async function markPipeline(slug, flag) {
  try { await api(`/api/pipeline/${encodeURIComponent(slug)}/mark`, { method: "POST", body: { flag } }); await refreshPipeline(); toast(flag === "reopen" ? "Reopened" : "Marked no-response"); }
  catch (e) { toast(e.message, true); }
}

async function markPipelineOutcome(sentId, outcome) {
  try {
    const res = await api(`/api/sent/${encodeURIComponent(sentId)}/outcome`, { method: "POST", body: { outcome } });
    let msg = { replied: "Marked replied", bounced: "Marked bounced", no_response: "Marked no-response" }[outcome] || "Updated";
    if (outcome === "bounced" && res && res.retry && res.retry.email) {
      msg += ` — retry to ${res.retry.email} staged in Drafts`;
    }
    toast(msg);
    await refreshPipeline();
    if (typeof refreshTriage === "function") { try { await refreshTriage(); } catch (e) {} }
    if (typeof refreshDrafts === "function") { try { await refreshDrafts(); } catch (e) {} }
  } catch (e) { toast(e.message, true); }
}

/* ================= VOICE PERFORMANCE (Phase 3) ================= */

function setPerfKind(kind) {
  state.perfKind = kind;
  $("#perfKindOutreach").classList.toggle("is-active", kind === "outreach");
  $("#perfKindOutreach").setAttribute("aria-selected", kind === "outreach");
  $("#perfKindFollowup").classList.toggle("is-active", kind === "followup");
  $("#perfKindFollowup").setAttribute("aria-selected", kind === "followup");
  refreshPerformance();
}

async function refreshPerformance() {
  const wrap = $("#perfTableWrap");
  if (wrap) wrap.innerHTML = '<div class="col-empty shimmer" style="padding:24px; text-align:center;">Loading performance stats\u2026</div>';
  let data;
  try { data = await api(`/api/voice_stats?kind=${state.perfKind}`); }
  catch (e) {
    if (wrap) wrap.innerHTML = `<div class="col-empty" style="padding:24px; text-align:center;"><p>${esc(e.message)}</p><button class="btn ghost small" onclick="refreshPerformance()">Retry</button></div>`;
    toast(e.message, true);
    return;
  }
  const rows = data.voices || [];
  $("#perfEmpty").classList.toggle("hidden", rows.length > 0);
  const best = data.best;
  $("#perfSummary").innerHTML = best
    ? `Best (min n=${data.min_n}): <b>${esc(best.display_name)}</b> ${pctCI(best)}`
    : `<span class="nodata">Not enough data yet — every voice is below the ${data.min_n}-send minimum.</span>`;
  if (!rows.length) { wrap.innerHTML = ""; return; }
  const body = rows.map(v => {
    const rate = v.enough_data ? pctCI(v) : `<span class="nodata">not enough data yet</span>`;
    const bounce = `${Math.round((v.bounce_rate || 0) * 100)}%`;
    const bars = editBar(v.edit_intensity);
    return `<tr class="${v.enough_data ? "" : "muted"}">
      <td>${esc(v.display_name)}</td><td>${v.sent}</td>
      <td>${rate}</td><td>${bounce}</td><td class="edit-bar" title="edit intensity">${bars}</td></tr>`;
  }).join("");
  wrap.innerHTML = `<table class="perf-table"><thead><tr>
    <th>Voice</th><th>Sent</th><th>Reply rate</th><th>Bounce</th><th>Edits</th>
    </tr></thead><tbody>${body}</tbody></table>`;
}

function pctCI(v) {
  if (!v.enough_data || v.reply_rate == null) return `<span class="nodata">not enough data yet</span>`;
  const pct = Math.round(v.reply_rate * 100);
  const lo = Math.round(v.reply_ci[0] * 100), hi = Math.round(v.reply_ci[1] * 100);
  return `${pct}% <span class="ci">(${lo}–${hi}%)</span> <span class="n">n=${v.reply_denom}</span>`;
}

function editBar(intensity) {
  if (intensity == null) return '<span class="nodata">—</span>';
  const filled = Math.round(intensity * 5);
  return "▓".repeat(filled) + "░".repeat(5 - filled);
}

/* ================= TRIAGE (Phase 6a) ================= */

async function refreshTriage() {
  const list = $("#triageList");
  if (list) list.innerHTML = '<div class="col-empty shimmer" style="padding:24px; text-align:center;">Loading triage items\u2026</div>';
  try { state.triageData = await api("/api/triage"); }
  catch (e) {
    if (list) list.innerHTML = `<div class="col-empty" style="padding:24px; text-align:center;"><p>${esc(e.message)}</p><button class="btn ghost small" onclick="refreshTriage()">Retry</button></div>`;
    toast(e.message, true);
    return;
  }
  const c = state.triageData.counts || {};
  $("#triageCountReplied").textContent = c.replied || "";
  $("#triageCountBounced").textContent = c.bounced || "";
  $("#triageCountQuiet").textContent = c.gone_quiet || "";
  const aw = $("#triageCountAwaiting"); if (aw) aw.textContent = c.awaiting || "";
  updateTriageBadge();
  renderTriage();
}

function setTriageBucket(bucket) {
  state.triageBucket = bucket;
  $$("#triageView .triage-filter .vk-seg").forEach(b => {
    const on = b.dataset.bucket === bucket;
    b.classList.toggle("is-active", on); b.setAttribute("aria-selected", on);
  });
  renderTriage();
}

// One outcome button. Marking fires the SAME backend effects the automated sweep fires.
function outcomeBtn(it, outcome, label, cls) {
  const b = document.createElement("button");
  b.className = "linklike" + (cls ? " " + cls : "");
  b.textContent = label;
  b.onclick = () => markOutcome(it.id, outcome);
  return b;
}

function renderTriage() {
  const data = state.triageData; if (!data) return;
  const items = data[state.triageBucket] || [];
  const list = $("#triageList");
  $("#triageEmpty").classList.toggle("hidden", items.length > 0);
  list.innerHTML = "";
  items.forEach(it => {
    const row = document.createElement("div"); row.className = "triage-row";
    row.dataset.id = it.id;
    const age = it.age_days ? `${it.age_days}d ago` : "recent";
    const src = it.outcome_source === "manual"
      ? `<span class="badge" title="You marked this by hand">marked</span> ` : "";
    let subLine = `${esc(it.sent_to || "")} · ${esc(it.subject || "")} · ${age}`;
    // bounced rows: show who the next retry would target (different person + format)
    if (state.triageBucket === "bounced" && it.next_rung) {
      const who = it.next_rung.person ? `${esc(it.next_rung.person)} ` : "";
      const tier = it.next_rung.tier === "alt_person" ? " (different person)" : "";
      subLine += ` · next: ${who}&lt;${esc(it.next_rung.email)}&gt;${tier}`;
    }
    row.innerHTML = `<div class="triage-main"><div class="triage-name">${src}${esc(it.name)}</div>
      <div class="triage-sub">${subLine}</div></div>`;
    const act = document.createElement("div"); act.className = "triage-act";

    if (state.triageBucket === "replied") {
      const b = document.createElement("span"); b.className = "badge badge-ok"; b.textContent = "replied"; act.appendChild(b);
      act.appendChild(outcomeBtn(it, "awaiting", "not a reply — reset"));
    } else if (state.triageBucket === "bounced") {
      if (!it.exhausted) {
        const v = document.createElement("button"); v.className = "linklike";
        v.textContent = "view retry in Drafts";
        v.onclick = () => { showView("workspace"); renderDrafts(); };
        act.appendChild(v);
      }
      const r = document.createElement("button"); r.className = "linklike";
      r.textContent = it.exhausted ? "retarget to a different person…" : "retarget elsewhere…";
      r.onclick = () => openRetargetDialog(it);
      act.appendChild(r);
      act.appendChild(outcomeBtn(it, "awaiting", "not a bounce — reset"));
    } else {
      // gone_quiet + awaiting: the full manual-detection menu on any live send
      act.appendChild(outcomeBtn(it, "replied", "mark replied", "ok"));
      act.appendChild(outcomeBtn(it, "bounced", "mark bounced"));
      if (it.pipeline_flag !== "no_response")
        act.appendChild(outcomeBtn(it, "no_response", "mark no-response"));
      else
        act.appendChild(outcomeBtn(it, "reopen", "reopen"));
    }
    row.appendChild(act);
    list.appendChild(row);
  });
}

// Mark a send's outcome by hand → same effects as a sweep. Refresh triage + drafts (a bounce may
// have staged a retry). Toast names the retry target when a different person was reached.
async function markOutcome(sentId, outcome) {
  try {
    const r = await api(`/api/sent/${encodeURIComponent(sentId)}/outcome`, { method: "POST", body: { outcome } });
    let msg = { replied: "Marked replied", bounced: "Marked bounced", awaiting: "Reset to awaiting",
                no_response: "Marked no-response", reopen: "Reopened" }[outcome] || "Updated";
    if (outcome === "bounced") {
      if (r.retry && r.retry.email) {
        const who = r.retry.person ? ` (${r.retry.person})` : "";
        msg += ` — retry to ${r.retry.email}${who} staged in Drafts`;
      } else if (r.exhausted) {
        msg += " — no more addresses; use “retarget to a different person”";
      }
    }
    toast(msg);
    await refreshTriage();
    await refreshDrafts();
  } catch (e) { toast(e.message, true); }
}

// Stage a bounce re-draft to a person the operator names (the backstop when the ladder is spent).
async function openRetargetDialog(it) {
  const scrim = document.createElement("div"); scrim.className = "modal-scrim";
  scrim.innerHTML = `
    <div class="modal" style="max-width:440px;">
      <h3 style="margin-top:0;">Retarget ${esc(it.name)}</h3>
      <p class="hint">Stage a fresh draft to a different person. It re-addresses the email to them and
        drops into Drafts for your review — nothing sends.</p>
      <div class="field"><label>Email <span style="color:var(--error)">*</span></label>
        <input type="email" id="rtEmail" placeholder="person@company.com" autocomplete="off" /></div>
      <div class="field" style="display:flex; gap:10px;">
        <div style="flex:1;"><label>Name</label><input type="text" id="rtName" placeholder="Jordan Lee" /></div>
        <div style="flex:1;"><label>Title</label><input type="text" id="rtTitle" placeholder="Head of Ops" /></div>
      </div>
      <div class="modal-actions" style="display:flex; gap:8px; justify-content:flex-end; margin-top:14px;">
        <button class="btn ghost" id="rtCancel">Cancel</button>
        <button class="btn primary" id="rtGo">Stage draft</button>
      </div>
      <div class="note-line" id="rtNote" style="margin-top:8px;"></div>
    </div>`;
  document.body.appendChild(scrim);
  const close = () => scrim.remove();
  scrim.querySelector("#rtCancel").onclick = close;
  scrim.addEventListener("click", (e) => { if (e.target === scrim) close(); });
  scrim.querySelector("#rtGo").onclick = async () => {
    const email = scrim.querySelector("#rtEmail").value.trim();
    if (!email || !email.includes("@")) { scrim.querySelector("#rtNote").textContent = "A valid email is required."; return; }
    const body = { email, name: scrim.querySelector("#rtName").value.trim(), title: scrim.querySelector("#rtTitle").value.trim() };
    try {
      const r = await api(`/api/sent/${encodeURIComponent(it.id)}/retarget`, { method: "POST", body });
      close(); toast(`Retarget to ${r.email} staged in Drafts`);
      await refreshTriage(); await refreshDrafts(); showView("workspace"); renderDrafts();
    } catch (e) { scrim.querySelector("#rtNote").textContent = e.message; }
  };
  setTimeout(() => scrim.querySelector("#rtEmail").focus(), 30);
}

async function updateTriageBadge() {
  let n = 0;
  if (state.triageData) n = (state.triageData.counts.replied || 0) + (state.triageData.counts.bounced || 0);
  const badge = $("#triageBadge");
  if (!badge) return;
  badge.textContent = n ? String(n) : "";
  badge.classList.toggle("hidden", n === 0);
}

async function runSweep(btn) {
  if (btn) { btn.disabled = true; btn.textContent = "Checking…"; }
  const status = $("#triageStatus");
  try {
    const r = await api("/api/inbox/sweep", { method: "POST" });
    const parts = [`${r.replied} repl${r.replied === 1 ? "y" : "ies"}`, `${r.bounced} bounce${r.bounced === 1 ? "" : "s"}`];
    if (r.retries && r.retries.length) parts.push(`${r.retries.length} retry draft${r.retries.length === 1 ? "" : "s"} staged`);
    toast(parts.join(", "));
    if (status) status.textContent = `Last checked just now — scanned ${r.scanned} messages.`;
    await refreshTriage(); await refreshDrafts();
  } catch (e) { toast(e.message, true); if (status) status.textContent = e.message; }
  finally { if (btn) { btn.disabled = false; btn.textContent = "Check for replies"; } }
}

/* ================= SUPPRESSION MANAGER (Phase 4a) ================= */

async function openSuppressions() {
  showModal("suppModal");
  await refreshSuppressions();
}

async function refreshSuppressions() {
  let items = [];
  try { items = (await api("/api/suppressions")).suppressions || []; } catch (e) {}
  const wrap = $("#suppTableWrap");
  if (!items.length) { wrap.innerHTML = `<p class="hint">Nothing on the list yet.</p>`; return; }
  const rows = items.map(s => `<tr>
    <td>${esc(s.value)}</td>
    <td><span class="badge">${esc(s.reason || "manual")}</span></td>
    <td>${esc(s.source || "")}</td>
    <td>${esc((s.added_at || "").slice(0, 10))}</td>
    <td><button class="linklike supp-del" data-v="${esc(s.value)}">remove</button></td>
    </tr>`).join("");
  wrap.innerHTML = `<table class="supp-table"><thead><tr>
    <th>Value</th><th>Reason</th><th>Source</th><th>Added</th><th></th></tr></thead>
    <tbody>${rows}</tbody></table>`;
  wrap.querySelectorAll(".supp-del").forEach(b => {
    b.onclick = async () => {
      try { await api("/api/suppressions", { method: "DELETE", body: { value: b.dataset.v } }); await refreshSuppressions(); }
      catch (e) { toast(e.message, true); }
    };
  });
}

async function addSuppression() {
  const v = $("#suppInput").value.trim();
  if (!v) return;
  try { await api("/api/suppressions", { method: "POST", body: { value: v } }); $("#suppInput").value = ""; await refreshSuppressions(); toast("Added to do-not-contact"); }
  catch (e) { toast(e.message, true); }
}

/* ================= IMAP test (Phase 5) ================= */

async function testImap(btn) {
  const res = $("#imapTestResult");
  res.textContent = "Testing…";
  // save current inbox fields first so the test uses them
  await saveSettings();
  $("#settingsModal").classList.remove("hidden");  // saveSettings closes it; reopen for the result
  try {
    const r = await api("/api/inbox/test", { method: "POST" });
    res.textContent = r.ok ? `✓ ${r.detail}` : `✗ ${r.detail}`;
    res.style.color = r.ok ? "var(--capital)" : "var(--error)";
  } catch (e) { res.textContent = `✗ ${e.message}`; res.style.color = "var(--error)"; }
}

/* ================= KEYBOARD LAYER (Phase 4c) ================= */

let kbdFocusIdx = -1;

function typingInField() {
  const el = document.activeElement;
  return el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable);
}

function visibleDraftRows() { return $$("#results .row"); }

function moveKbdFocus(delta) {
  const rows = visibleDraftRows();
  if (!rows.length) return;
  rows.forEach(r => r.classList.remove("kbd-focus"));
  kbdFocusIdx = Math.max(0, Math.min(rows.length - 1, kbdFocusIdx + delta));
  const row = rows[kbdFocusIdx];
  row.classList.add("kbd-focus");
  row.scrollIntoView({ block: "nearest", behavior: (matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth") });
}

function focusedDraftSlug() {
  const rows = visibleDraftRows();
  if (kbdFocusIdx < 0 || kbdFocusIdx >= rows.length) return null;
  return rows[kbdFocusIdx].dataset.slug || null;
}

function handleShortcut(e) {
  if (typingInField() || e.metaKey || e.ctrlKey || e.altKey) {
    if (e.key === "Escape" && typingInField()) document.activeElement.blur();
    return;
  }
  if (state.view === "triage") { handleTriageShortcut(e); return; }
  if (state.view !== "workspace" && e.key !== "?") return;
  switch (e.key) {
    case "j": e.preventDefault(); moveKbdFocus(1); break;
    case "k": e.preventDefault(); moveKbdFocus(-1); break;
    case "a": { const s = focusedDraftSlug(); if (s) { e.preventDefault(); approveOne(s); } break; }
    case "e": { const s = focusedDraftSlug(); if (s) { e.preventDefault(); toggleDrawer(s); } break; }
    case "/": e.preventDefault(); { const t = $("#namesInput"); if (t) t.focus(); } break;
    case "?": e.preventDefault(); $("#shortcutModal").classList.remove("hidden"); break;
  }
}

// One-key triage. Reuses markOutcome / setTriageBucket, which already fire the
// same effects as the automatic sweep.
const TRIAGE_KEYS = { r: "replied", b: "bounced", n: "no_response", u: "reopen" };
const TRIAGE_BUCKETS = ["replied", "bounced", "gone_quiet", "awaiting"];
let triageFocusIdx = -1;

function handleTriageShortcut(e) {
  if (e.key === "?") { e.preventDefault(); $("#shortcutModal").classList.remove("hidden"); return; }
  const bucketIdx = "1234".indexOf(e.key);
  if (bucketIdx >= 0) { e.preventDefault(); setTriageBucket(TRIAGE_BUCKETS[bucketIdx]); return; }
  if (e.key === "j" || e.key === "k") { e.preventDefault(); moveTriageFocus(e.key === "j" ? 1 : -1); return; }
  const outcome = TRIAGE_KEYS[e.key];
  if (!outcome) return;
  const id = focusedTriageId();
  if (!id) return;
  e.preventDefault();
  markOutcome(id, outcome);
}

function visibleTriageRows() { return $$("#triageList .triage-row"); }

function moveTriageFocus(delta) {
  const rows = visibleTriageRows();
  if (!rows.length) return;
  rows.forEach(r => r.classList.remove("kbd-focus"));
  triageFocusIdx = Math.max(0, Math.min(rows.length - 1, triageFocusIdx + delta));
  const row = rows[triageFocusIdx];
  row.classList.add("kbd-focus");
  row.scrollIntoView({ block: "nearest", behavior: (matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth") });
}

function focusedTriageId() {
  const rows = visibleTriageRows();
  if (triageFocusIdx < 0 || triageFocusIdx >= rows.length) return null;
  return rows[triageFocusIdx].dataset.id || null;
}



/* ================= VOICES (block-schema editor) ================= */
// fact_scopes here is only a fallback for a failed /api/meta call; the live list
// comes from FACT_SCOPES on the server. It previously still named
// candidate_evidence and candidate_spine, renamed to profile_evidence and
// profile_spine, so a failed meta fetch offered two scopes that no longer exist.
let META = { experiences: [], tokens: [], fact_scopes: ["recent","target_proofs","situation_read","profile_evidence","profile_spine","custom_facts"],
             block_lengths: ["one_line","short","medium","body"], block_modes: ["fixed","ai"],
             situations: ["no_role_small","role_small","role_large"], candidate_first: "" };
let VE = { blocks: [] };        // working copy of the blocks being edited
let veLastFocus = null;         // last-focused token target element (for token insertion)

const SAMPLE = {
  name: "Alex", contact_first: "Alex", contact_full: "Alex Founder", company: "Acme",
  role: "Analyst", role_or_company: "Analyst", what_they_do: "B2B payments software",
  situation_read: "scaling their commercial team", recent: "their recent seed round",
  recent_short: "their seed round", proof_1: "they serve mid-market teams",
  proof_2: "they expanded into a second market", city: "Paris",
  candidate_name: "Your Name", candidate_first: "You",
};
function expAnchor(key) { const e = (META.experiences || []).find(x => x.key === key); return e ? e.anchor : null; }
function renderTokens(x) {
  return (x || "").replace(/\{(\w+)\}/g, (m, k) => {
    if (k === "relevant") return "[most relevant experience]";
    const a = expAnchor(k); if (a) return a;
    if (k in SAMPLE) return SAMPLE[k];
    return m;
  });
}
async function fetchMeta() { try { const r = await api("/api/meta"); META = Object.assign(META, r || {}); } catch (e) {} }
function cap(x) { return x.charAt(0).toUpperCase() + x.slice(1); }
function veGet(id) { const el = $("#" + id); if (!el) return ""; return el.type === "checkbox" ? el.checked : el.value; }
function veSet(id, val) { const el = $("#" + id); if (!el) return; if (el.type === "checkbox") el.checked = !!val; else el.value = (val == null ? "" : val); }

async function openVoicesManager() {
  showModal("voicesModal");
  if (!state.voiceKind) state.voiceKind = "outreach";
  await fetchVoices();
  const isFollowup = state.voiceKind === "followup";

  // segmented toggle
  $$("#voiceKindToggle .vk-seg").forEach(btn => {
    btn.classList.toggle("is-active", btn.dataset.kind === state.voiceKind);
    btn.onclick = async () => {
      if (state.voiceKind === btn.dataset.kind) return;
      state.voiceKind = btn.dataset.kind;
      await openVoicesManager();
    };
  });
  // routing selectors + hint only apply to the outreach set
  const routing = $(".voices-routing");
  if (routing) routing.style.display = isFollowup ? "none" : "";
  const fuHint = $("#followupVoiceHint");
  if (fuHint) fuHint.style.display = isFollowup ? "" : "none";

  if (!isFollowup) {
    const sessionSel = $("#sessionVoiceSel");
    const defaultSel = $("#defaultVoiceSel");
    const opt = (selId) => allVoices.map(v => `<option value="${v.id}"${v.id === selId ? " selected" : ""}>${esc(v.display_name)}</option>`).join("");
    const sessionVoice = (state.status && state.status.voice) || "";
    sessionSel.innerHTML = `<option value=""${sessionVoice ? "" : " selected"}>Default voice</option>` + opt(sessionVoice);
    defaultSel.innerHTML = opt(state.defaultVoice);
    sessionSel.onchange = async () => {
      try {
        await api("/api/session", { method: "POST", body: { voice: sessionSel.value || null } });
        await refreshStatus();
        toast(sessionSel.value ? `This session: ${voiceLabel(sessionSel.value)}` : "This session: Default voice");
      } catch (e) { toast(e.message, true); }
    };
    defaultSel.onchange = async () => {
      try {
        await api("/api/default_voice", { method: "POST", body: { voice: defaultSel.value } });
        state.defaultVoice = defaultSel.value;
        toast(`Default voice: ${voiceLabel(defaultSel.value)}`);
      } catch (e) { toast(e.message, true); }
    };
  }
  renderVoicesList();
}

function renderVoicesList() {
  const list = $("#voicesList");
  list.innerHTML = "";
  if (!allVoices.length) { list.innerHTML = `<p class="lede">No voices yet. Create one with + New voice.</p>`; return; }
  allVoices.forEach(v => {
    const sits = (v.situations || []).map(x => SITUATION_LABEL[x] || x).join(", ") || "manual-only";
    const el = document.createElement("div");
    el.className = "voice-row";
    el.innerHTML = `
      <div class="voice-row-main">
        <div class="voice-row-name">${esc(v.display_name)}</div>
        <div class="voice-row-sub">auto: ${esc(sits)}</div>
      </div>
      <div class="voice-row-act">
        <button class="btn ghost small" data-act="edit">Edit</button>
        <button class="icon-btn small" data-act="del" title="Delete">&times;</button>
      </div>`;
    el.querySelector('[data-act="edit"]').onclick = () => openVoiceEditor(v.id);
    el.querySelector('[data-act="del"]').onclick = () => deleteVoice(v.id, v.display_name);
    list.appendChild(el);
  });
}

async function deleteVoice(id, name) {
  const ok = await dialog({
    title: "Delete voice?",
    message: `Delete "${name}"? If a situation auto-matched to it, that situation falls back to the default voice.`,
    options: [{ label: "Cancel", value: false }, { label: "Delete", value: true, danger: true }],
  });
  if (!ok) return;
  try { await api(`/api/voices/${id}`, { method: "DELETE" }); await openVoicesManager(); toast("Voice deleted"); }
  catch (e) { toast(e.message, true); }
}

/* ---- block cards ---- */
const DEFAULT_STYLE = { formality:2, warmth:2, directness:3, sentence_length:"flowing",
  hedging:"neutral", humor:"none", person_focus:"recipient_first", proof_density:"single", notes:"", examples:[] };
const SCOPE_LABEL = { recent:"recent event", target_proofs:"their proof points", situation_read:"situation read",
  profile_evidence:"my selected evidence", profile_spine:"my spine", custom_facts:"custom facts" };
const LEN_LABEL = { one_line:"one line", short:"short", medium:"medium", body:"body (full length)" };

function starterBlocks() {
  return [
    { id:"greeting", label:"Greeting", mode:"fixed", text:"Hi {contact_first},", guidance:"", fact_scope:[], length:"short", optional:false },
    { id:"body", label:"Body", mode:"ai", text:"", guidance:"Tie one piece of my evidence to what they need. Lead with wanting to build inside a company rather than evaluate it from outside.", fact_scope:["target_proofs","profile_evidence","profile_spine","situation_read"], length:"body", optional:false },
    { id:"positioning", label:"Positioning", mode:"fixed", text:"I am seeking a part-time role alongside my studies.", guidance:"", fact_scope:[], length:"short", optional:false },
    { id:"close", label:"Close", mode:"fixed", text:"Open to a short call?", guidance:"", fact_scope:[], length:"one_line", optional:false },
  ];
}

function blockCardHTML(b, i, n) {
  const modeSel = (META.block_modes||["fixed","ai"]).map(m => `<option value="${m}"${m===b.mode?" selected":""}>${m==="ai"?"Written by AI":"Fixed text"}</option>`).join("");
  const lenSel = (META.block_lengths||[]).map(l => `<option value="${l}"${l===b.length?" selected":""}>${LEN_LABEL[l]||l}</option>`).join("");
  const scopes = (META.fact_scopes||[]).map(s => `<label class="chip-check"><input type="checkbox" data-scope="${s}"${(b.fact_scope||[]).includes(s)?" checked":""}/> ${SCOPE_LABEL[s]||s}</label>`).join("");
  const isAI = b.mode === "ai";
  return `<div class="ve-block" data-idx="${i}" data-bid="${esc(b.id||"")}">
    <div class="ve-block-head">
      <input type="text" class="bk-label" value="${esc(b.label||"")}" placeholder="Block name" />
      <div class="ve-block-btns">
        <button class="icon-btn small" data-act="up" title="Move up"${i===0?" disabled":""}>&#8593;</button>
        <button class="icon-btn small" data-act="down" title="Move down"${i===n-1?" disabled":""}>&#8595;</button>
        <button class="icon-btn small" data-act="rm" title="Remove block">&times;</button>
      </div>
    </div>
    <div class="ve-block-row">
      <select class="bk-mode">${modeSel}</select>
      <select class="bk-len">${lenSel}</select>
      <label class="chip-check"><input type="checkbox" class="bk-opt"${b.optional?" checked":""}/> optional</label>
    </div>
    <div class="bk-fixed" style="${isAI?"display:none;":""}">
      <textarea class="bk-text ve-tok-target" placeholder="Exact text (tokens allowed)">${esc(b.text||"")}</textarea>
    </div>
    <div class="bk-ai" style="${isAI?"":"display:none;"}">
      <textarea class="bk-guidance ve-tok-target" placeholder="What should the model write here?">${esc(b.guidance||"")}</textarea>
      <div class="desc" style="margin:8px 0 4px;">Facts this block may use (also: which absent fact makes an <em>optional</em> block skip)</div>
      <div class="chip-row bk-scopes">${scopes}</div>
    </div>
  </div>`;
}

function readBlocksFromDOM() {
  return $$("#veBlocks .ve-block").map(card => {
    const q = (sel) => card.querySelector(sel);
    return {
      id: card.dataset.bid || ("b_" + Math.random().toString(36).slice(2,7)),
      label: (q(".bk-label").value || "").trim(),
      mode: q(".bk-mode").value,
      text: q(".bk-text") ? q(".bk-text").value : "",
      guidance: q(".bk-guidance") ? q(".bk-guidance").value : "",
      fact_scope: $$(".bk-scopes input:checked", card).map(x => x.dataset.scope),
      length: q(".bk-len").value,
      optional: q(".bk-opt").checked,
    };
  });
}

function renderBlocks() {
  const wrap = $("#veBlocks");
  const n = VE.blocks.length;
  wrap.innerHTML = VE.blocks.map((b,i) => blockCardHTML(b,i,n)).join("");
  $$("#veBlocks .ve-block").forEach((card) => {
    const i = +card.dataset.idx;
    card.querySelector(".bk-mode").addEventListener("change", () => { VE.blocks = readBlocksFromDOM(); renderBlocks(); updateLivePreview(); });
    card.querySelector('[data-act="up"]').onclick = () => moveBlock(i, -1);
    card.querySelector('[data-act="down"]').onclick = () => moveBlock(i, +1);
    card.querySelector('[data-act="rm"]').onclick = () => removeBlock(i);
    card.querySelectorAll("input,textarea,select").forEach(el =>
      el.addEventListener((el.tagName === "SELECT" || el.type === "checkbox") ? "change" : "input", updateLivePreview));
  });
}
function moveBlock(i, dir) {
  VE.blocks = readBlocksFromDOM();
  const j = i + dir; if (j < 0 || j >= VE.blocks.length) return;
  const t = VE.blocks[i]; VE.blocks[i] = VE.blocks[j]; VE.blocks[j] = t;
  renderBlocks(); updateLivePreview();
}
function removeBlock(i) { VE.blocks = readBlocksFromDOM(); VE.blocks.splice(i, 1); renderBlocks(); updateLivePreview(); }
function addBlock() {
  VE.blocks = readBlocksFromDOM();
  VE.blocks.push({ id:"b_"+Date.now().toString(36), label:"New block", mode:"fixed", text:"", guidance:"", fact_scope:[], length:"short", optional:false });
  renderBlocks(); updateLivePreview();
}

/* ---- style panel ---- */
const STYLE_SLIDERS = [["formality","Formality","very casual → formal"],["warmth","Warmth","cool → warm"],["directness","Directness","diplomatic → blunt"]];
const STYLE_SELECTS = { sentence_length:["short","medium","flowing"], hedging:["hedged","neutral","assertive"],
  humor:["none","dry","light"], person_focus:["recipient_first","sender_first","balanced"], proof_density:["single","few","several"] };
const OPT_LABEL = { recipient_first:"recipient first", sender_first:"sender first", balanced:"balanced" };
function renderStyle(style) {
  const s = Object.assign({}, DEFAULT_STYLE, style || {});
  const sliders = STYLE_SLIDERS.map(([k,label,hint]) =>
    `<div class="field" style="margin-top:8px;"><label>${label} <span style="font-weight:normal;color:var(--ink-faint);">(${hint})</span></label>
     <input type="range" min="0" max="4" step="1" class="st-slider" data-k="${k}" value="${s[k]}" /></div>`).join("");
  const selects = Object.entries(STYLE_SELECTS).map(([k,opts]) =>
    `<div class="field" style="margin-top:8px; flex:1; min-width:150px;"><label>${cap(k.replace(/_/g," "))}</label>
     <select class="st-select" data-k="${k}">${opts.map(o=>`<option value="${o}"${o===s[k]?" selected":""}>${OPT_LABEL[o]||o}</option>`).join("")}</select></div>`).join("");
  $("#veStyle").innerHTML = sliders + `<div style="display:flex; gap:12px; flex-wrap:wrap;">${selects}</div>`;
  $$("#veStyle .st-slider, #veStyle .st-select").forEach(el => el.addEventListener("change", updateLivePreview));
}
function collectStyle() {
  const s = {};
  $$("#veStyle .st-slider").forEach(el => s[el.dataset.k] = parseInt(el.value,10));
  $$("#veStyle .st-select").forEach(el => s[el.dataset.k] = el.value);
  s.notes = veGet("veStyleNotes");
  s.examples = $$("#veExamples textarea").map(t => t.value.trim()).filter(Boolean);
  return s;
}

/* ---- evidence panel ---- */
const EV_MODES = [["","neutral"],["prefer","prefer"],["pin","pin"],["exclude","exclude"]];
function renderEvidence(ev) {
  ev = ev || {};
  const rows = (META.experiences || []).map(e => {
    let cur = "";
    if ((ev.pin||[]).includes(e.key)) cur = "pin";
    else if ((ev.exclude||[]).includes(e.key)) cur = "exclude";
    else if ((ev.prefer||[]).includes(e.key)) cur = "prefer";
    const opts = EV_MODES.map(([v,l]) => `<option value="${v}"${v===cur?" selected":""}>${l}</option>`).join("");
    return `<div class="ev-row"><span class="ev-key">${esc(e.key)}${e.optional?' <span class="ev-opt">optional</span>':''}</span><select class="ev-mode" data-key="${esc(e.key)}">${opts}</select></div>`;
  }).join("");
  $("#veEvidence").innerHTML = rows +
    `<div class="field" style="margin-top:10px; max-width:170px;"><label>How many to tie</label><input type="number" id="veEvCount" min="1" max="5" value="${ev.count||1}" /></div>`;
  $$("#veEvidence .ev-mode, #veEvidence #veEvCount").forEach(el => el.addEventListener("change", updateLivePreview));
}
function collectEvidence() {
  const prefer=[], pin=[], exclude=[];
  $$("#veEvidence .ev-mode").forEach(sel => {
    const k = sel.dataset.key;
    if (sel.value === "prefer") prefer.push(k);
    else if (sel.value === "pin") pin.push(k);
    else if (sel.value === "exclude") exclude.push(k);
  });
  return { prefer, pin, exclude, category_weights:{}, count: parseInt(veGet("veEvCount")||"1",10)||1,
           custom_facts: $$("#veCustomFacts textarea").map(t=>t.value.trim()).filter(Boolean),
           identity_note: veGet("veIdentityNote") };
}

/* ---- dynamic rows (examples / custom facts / variables) ---- */
function addTextRow(containerSel, val, ph, tall) {
  const row = document.createElement("div"); row.style.display="flex"; row.style.gap="6px";
  row.innerHTML = `<textarea class="ve-tok-target" style="flex:1; height:${tall?64:38}px;" placeholder="${esc(ph||"")}">${esc(val||"")}</textarea><button class="icon-btn small" title="Remove">&times;</button>`;
  row.querySelector("textarea").addEventListener("input", updateLivePreview);
  row.querySelector("button").onclick = () => { row.remove(); updateLivePreview(); };
  $(containerSel).appendChild(row);
}
function addVarRow(k, v) {
  const row = document.createElement("div"); row.style.display="flex"; row.style.gap="6px";
  row.innerHTML = `<input type="text" class="var-k" placeholder="key" style="flex:0 0 130px;" value="${esc(k||"")}"/><input type="text" class="var-v ve-tok-target" placeholder="value" style="flex:1;" value="${esc(v||"")}"/><button class="icon-btn small" title="Remove">&times;</button>`;
  row.querySelectorAll("input").forEach(el => el.addEventListener("input", updateLivePreview));
  row.querySelector("button").onclick = () => { row.remove(); updateLivePreview(); };
  $("#veVars").appendChild(row);
}
function collectVars() {
  const o = {};
  $$("#veVars > div").forEach(r => { const k=(r.querySelector(".var-k").value||"").trim(); const v=r.querySelector(".var-v").value; if (k && k !== "link_matcher_prompt") o[k]=v; });
  const lmp = ($("#veLinkMatcherPrompt") ? $("#veLinkMatcherPrompt").value : "").trim();
  o["link_matcher_prompt"] = lmp;
  return o;
}

/* ---- token palette ---- */
function renderTokenPalette() {
  const pal = $("#veTokenPalette");
  const chip = (t, kind, title) => `<button class="token-chip ${kind}" data-tok="${esc(t)}" title="${esc(title||"")}">{${esc(t)}}</button>`;
  const research = (META.tokens||[]).filter(t=>t.kind==="research").map(t=>chip(t.token,"research",t.help||"")).join("");
  const exps = (META.tokens||[]).filter(t=>t.kind==="experience").map(t=>chip(t.token,"experience",t.anchor||t.help||"")).join("");
  const rel = chip("relevant","relevant","the model picks the best-fitting experience for the point");
  pal.innerHTML = `<div class="token-group"><span class="token-lab">research</span>${research}</div>
                   <div class="token-group"><span class="token-lab">experiences</span>${exps} ${rel}</div>`;
  $$("#veTokenPalette .token-chip").forEach(b => b.onclick = () => insertToken(b.dataset.tok));
}
function insertToken(tok) {
  const t = "{" + tok + "}";
  const el = (veLastFocus && document.body.contains(veLastFocus)) ? veLastFocus : $("#veSubject");
  if (!el) return;
  const start = el.selectionStart != null ? el.selectionStart : el.value.length;
  const end = el.selectionEnd != null ? el.selectionEnd : el.value.length;
  el.value = el.value.slice(0,start) + t + el.value.slice(end);
  el.focus(); try { el.selectionStart = el.selectionEnd = start + t.length; } catch (_) {}
  updateLivePreview();
}

/* ---- fill / open / collect / save ---- */
function fillFromVoice(v) {
  v = v || {};
  veSet("veName", v.display_name || "");
  veSet("veSubject", v.subject || "");
  const sit = v.situations || [];
  veSet("veSit_no_role_small", sit.includes("no_role_small"));
  veSet("veSit_role_small", sit.includes("role_small"));
  veSet("veSit_role_large", sit.includes("role_large"));
  veSet("veLearning", v.learning || "patch");
  veSet("veLenMin", v.length_min || 70);
  veSet("veLenMax", v.length_max || 120);
  veSet("veAllowDashes", !!v.allow_dashes);
  veSet("veIdentityNote", (v.evidence || {}).identity_note || "");
  VE.blocks = (v.blocks && v.blocks.length) ? v.blocks.map(b => Object.assign({}, b)) : starterBlocks();
  renderBlocks();
  renderStyle(v.style || {});
  veSet("veStyleNotes", (v.style || {}).notes || "");
  $("#veExamples").innerHTML = ""; ((v.style || {}).examples || []).forEach(x => addTextRow("#veExamples", x, "Example email", true));
  $("#veCustomFacts").innerHTML = ""; ((v.evidence || {}).custom_facts || []).forEach(x => addTextRow("#veCustomFacts", x, "A true claim I can make", true));
  $("#veVars").innerHTML = ""; Object.entries(v.variables || {}).forEach(([k,val]) => {
    if (k !== "link_matcher_prompt") addVarRow(k, val);
  });
  veSet("veLinkMatcherPrompt", (v.variables || {}).link_matcher_prompt || "");
  renderEvidence(v.evidence || {});
}

function openVoiceEditor(id) {
  state.editingVoiceId = id || null;
  const v = id ? voiceById(id) : null;
  $("#veTitle").textContent = v ? "Edit voice" : "New voice";
  $("#veHeader").textContent = v ? v.display_name : "New voice";
  const sf = $("#veStartFrom");
  sf.innerHTML = `<option value="">Blank (starter template)</option>` + allVoices.map(x => `<option value="${x.id}">${esc(x.display_name)}</option>`).join("");
  sf.value = "";
  $("#veStartFromWrap").style.display = v ? "none" : "";
  renderTokenPalette();
  fillFromVoice(v || {});
  veLastFocus = null;
  $("#voiceEditorModal").classList.remove("hidden");
  updateLivePreview();
  // Layer 4 learning panel — only meaningful once a voice is saved (needs an id + edit history)
  const lw = $("#veLearningWrap");
  if (id) { lw.classList.remove("hidden"); loadLearningPanel(id); }
  else { lw.classList.add("hidden"); }
}

/* ---- Layer 4: learning-from-edits panel ---- */
function summarizePatch(p) {
  if (!p) return "";
  const bits = [];
  Object.entries(p.style_deltas || {}).forEach(([k, d]) => bits.push(`${k} ${d > 0 ? "+" : ""}${d}`));
  Object.entries(p.categorical || {}).forEach(([k, v]) => bits.push(`${k} → ${v}`));
  if ((p.notes_add || []).length) bits.push(`${p.notes_add.length} note(s) added`);
  if ((p.notes_remove || []).length) bits.push(`${p.notes_remove.length} note(s) removed`);
  if ((p.promote_examples || []).length) bits.push(`${p.promote_examples.length} example(s) promoted`);
  const bg = Object.keys(p.block_guidance || {});
  if (bg.length) bits.push(`guidance for ${bg.join(", ")}`);
  return bits.length ? bits.join(" · ") : "no structural change";
}

async function loadLearningPanel(id) {
  const body = $("#veLearningBody");
  if (!body) return;
  body.innerHTML = `<div class="muted">Loading…</div>`;
  let s;
  try { s = await api(`/api/voices/${encodeURIComponent(id)}/learning`); }
  catch (e) { body.innerHTML = `<div class="muted">Learning unavailable.</div>`; return; }
  if (!s || !s.ok) { body.innerHTML = `<div class="muted">No learning data yet.</div>`; return; }

  $("#veLearningDesc").textContent =
    s.mode === "off"
      ? "Learning is off. Turn it on in Settings, or use “Learn from my edits now” for a one-off suggestion."
      : `Mode: ${s.mode}${s.promote ? " · A/B on" : ""}. ${s.edits_since}/${s.min_edits} new edits since the last cycle.`;

  const parts = [];
  parts.push(`<div class="ve-learn-stat">Edits captured for this voice: <b>${s.pending_triples}</b>` +
             ` · learned changes applied: <b>${s.applied_count}</b>` +
             ` · saved versions: <b>${(s.versions || []).length}</b></div>`);

  (s.proposals || []).forEach(p => {
    parts.push(
      `<div class="ve-learn-card">
        <div class="ve-learn-head">Suggested change <span class="muted">(${p.n_edits} edits, ${p.n_replied} replied)</span></div>
        <div class="ve-learn-sum">${esc(summarizePatch(p.patch))}</div>
        ${p.rationale ? `<div class="muted" style="margin-top:4px;">${esc(p.rationale)}</div>` : ""}
        <div style="display:flex; gap:8px; margin-top:8px;">
          <button class="btn primary small" data-lp-apply="${esc(p.id)}">Accept</button>
          <button class="btn ghost small" data-lp-reject="${esc(p.id)}">Dismiss</button>
        </div>
      </div>`);
  });

  if (s.ab && s.ab.challenger_id) {
    const cs = s.ab.champion_stats || {}, ch = s.ab.challenger_stats || {};
    const pct = b => (b && b.enough_data && b.reply_rate != null) ? `${Math.round(b.reply_rate * 100)}% reply` : "gathering data";
    parts.push(
      `<div class="ve-learn-card">
        <div class="ve-learn-head">A/B test running</div>
        <div class="ve-learn-sum">Current voice: ${pct(cs)} · Challenger: ${pct(ch)}</div>
        <div class="muted" style="margin-top:4px;">The router sends some drafts to each; the winner is promoted automatically on a clear reply-rate gap, or use “Resolve A/B”.</div>
      </div>`);
  }

  if ((s.versions || []).length) {
    const rows = s.versions.slice(0, 8).map(v =>
      `<div class="ve-learn-ver">
        <span title="${esc(v.ts)}">${esc((v.note || "snapshot"))}</span>
        <button class="btn ghost small" data-lp-rollback="${esc(v.ts)}">Roll back</button>
      </div>`).join("");
    parts.push(`<div class="ve-learn-card"><div class="ve-learn-head">History</div>${rows}</div>`);
  }

  body.innerHTML = parts.join("");
  const vid = state.editingVoiceId;
  body.querySelectorAll("[data-lp-apply]").forEach(b => b.onclick = async () => {
    try { await api(`/api/voices/${encodeURIComponent(vid)}/proposals/${encodeURIComponent(b.dataset.lpApply)}/apply`, { method: "POST" });
      toast("Change applied"); await fetchVoices(); openVoiceEditor(vid); }
    catch (e) { toast(e.message, true); }
  });
  body.querySelectorAll("[data-lp-reject]").forEach(b => b.onclick = async () => {
    try { await api(`/api/voices/${encodeURIComponent(vid)}/proposals/${encodeURIComponent(b.dataset.lpReject)}/reject`, { method: "POST" });
      toast("Dismissed"); loadLearningPanel(vid); }
    catch (e) { toast(e.message, true); }
  });
  body.querySelectorAll("[data-lp-rollback]").forEach(b => b.onclick = async () => {
    try { await api(`/api/voices/${encodeURIComponent(vid)}/rollback`, { method: "POST", body: { ts: b.dataset.lpRollback } });
      toast("Rolled back"); await fetchVoices(); openVoiceEditor(vid); }
    catch (e) { toast(e.message, true); }
  });
}

function collectVoice() {
  const situations = [];
  if (veGet("veSit_no_role_small")) situations.push("no_role_small");
  if (veGet("veSit_role_small")) situations.push("role_small");
  if (veGet("veSit_role_large")) situations.push("role_large");
  const editing = state.editingVoiceId;
  const id = editing || ("v_" + Date.now().toString(36));
  const lo = parseInt(veGet("veLenMin")||"70",10) || 70;
  const hi = parseInt(veGet("veLenMax")||"120",10) || 120;
  return {
    id, display_name: (veGet("veName") || "").trim(),
    kind: (state.editingVoiceId ? ((voiceById(id) || {}).kind || state.voiceKind || "outreach")
                                : (state.voiceKind || "outreach")),
    learning: veGet("veLearning") || "patch",
    seeded_from: editing ? ((voiceById(id) || {}).seeded_from || "blank") : "blank",
    situations, subject: veGet("veSubject"),
    blocks: readBlocksFromDOM(),
    style: collectStyle(),
    evidence: collectEvidence(),
    length_min: lo, length_max: hi,
    variables: collectVars(),
    allow_dashes: veGet("veAllowDashes"),
  };
}

async function saveVoice() {
  const payload = collectVoice();
  if (!payload.display_name) { toast("Give the voice a name", true); return; }
  if (!payload.blocks.length) { toast("Add at least one block", true); return; }
  const editing = !!state.editingVoiceId;
  try {
    if (editing) await api(`/api/voices/${payload.id}`, { method: "PUT", body: payload });
    else await api("/api/voices", { method: "POST", body: payload });
    await fetchVoices();
    $("#voiceEditorModal").classList.add("hidden");
    if (!$("#voicesModal").classList.contains("hidden")) await openVoicesManager();
    toast(editing ? "Voice saved" : "Voice created");
  } catch (e) { toast(e.message, true); }
}

function updateLivePreview() {
  $("#pvSubject").textContent = renderTokens(veGet("veSubject")) || "(no subject)";
  const blocks = readBlocksFromDOM();
  const lines = [];
  blocks.forEach(b => {
    if (b.optional && (b.fact_scope||[]).length === 1 && b.fact_scope[0] === "recent") {
      // preview assumes a recent exists, so an optional recent block is shown
    }
    if (b.mode === "ai") {
      let txt = `[AI writes the ${b.label || b.id}`;
      if ((b.guidance||"").includes("{relevant}") || (b.text||"").includes("{relevant}")) txt += ", choosing your most relevant experience";
      txt += "]";
      lines.push(txt);
    } else {
      const t = renderTokens(b.text).trim();
      if (t) lines.push(t);
    }
  });
  $("#pvBody").textContent = lines.join("\n\n");
}

/* ================= WIRING ================= */
function wire() {
  $("#draftBtn").onclick = doIngest;
  $("#namesInput").addEventListener("input", updateNameCount);
  $("#uploadBtn").onclick = () => $("#fileInput").click();
  $("#fileInput").onchange = (e) => { const f = e.target.files[0]; if (f) doUpload(f); e.target.value = ""; };
  $("#ingestBannerClose").onclick = () => $("#ingestBanner").classList.add("hidden");

  $("#draft5Btn").onclick = draft5;
  if ($("#draftAllBtn")) $("#draftAllBtn").onclick = draftAllInQueue;
  $("#clearQueueBtn").onclick = clearQueue;
  $("#clearDraftsBtn").onclick = clearDrafts;

  // Find New Targets (Sourcing)
  if ($("#findTargetsBtn")) $("#findTargetsBtn").onclick = openSourcingPanel;
  if ($("#closeSourcingPanelBtn")) $("#closeSourcingPanelBtn").onclick = closeSourcingPanel;
  if ($("#runSourcingBtn")) $("#runSourcingBtn").onclick = runSourcing;
  if ($("#sourcingStopBtn")) $("#sourcingStopBtn").onclick = stopSourcingNow;
  if ($("#managePresetsBtn")) $("#managePresetsBtn").onclick = openPresetsManager;
  if ($("#presetsCloseBtn")) $("#presetsCloseBtn").onclick = () => $("#sourcingPresetsModal").classList.add("hidden");
  if ($("#newPresetBtn")) $("#newPresetBtn").onclick = () => openPresetEditor(null);
  if ($("#presetCancelBtn")) $("#presetCancelBtn").onclick = () => {
    // Clearing this is what makes the NEXT action correct. Left set, a later save
    // issues PUT against the preset that was being edited here, overwriting it even
    // when the user intended to create something new.
    editingPresetId = null;
    $("#presetEditor").classList.add("hidden");
  };
  if ($("#presetSaveBtn")) $("#presetSaveBtn").onclick = savePreset;
  if ($("#sourcingPromptSelect")) $("#sourcingPromptSelect").onchange = updateSourcingPanelMandateHint;
  setupBulkExportHandlers();

  $("#settingsBtn").onclick = openSettings;
  $("#settingsCancel").onclick = () => $("#settingsModal").classList.add("hidden");
  $("#settingsSave").onclick = saveSettings;
  $("#setProvider").onchange = () => { const stub = $("#setProvider").value === "stub"; $("#setKeyField").style.display = stub ? "none" : ""; };
  $("#setAttachInput").onchange = (e) => { const f = e.target.files[0]; if (f) doUploadAttachment(f); e.target.value = ""; };

  $("#archiveBtn").onclick = openArchive;
  $("#archiveCloseBtn").onclick = () => $("#archiveModal").classList.add("hidden");
  $("#clearArchiveBtn").onclick = clearArchive;

  // primary tab strip: click + roving-arrow keyboard nav
  initTabStripScroll();
  const tabs = $$(".topbar-tab");
  tabs.forEach((t, i) => {
    t.onclick = () => showView(t.dataset.view);
    t.onkeydown = (e) => {
      if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        e.preventDefault();
        const dir = e.key === "ArrowRight" ? 1 : -1;
        const next = tabs[(i + dir + tabs.length) % tabs.length];
        next.focus(); showView(next.dataset.view);
      } else if (e.key === "Enter" || e.key === " ") {
        e.preventDefault(); showView(t.dataset.view);
      }
    };
  });
  $("#fuClearBtn").onclick = clearFollowups;
  $("#exportBtn").onclick = doExport;
  $("#costMeter").onclick = openCostPopover;

  if ($("#listSelect")) $("#listSelect").onchange = (e) => switchList(e.target.value);
  if ($("#createListBtn")) $("#createListBtn").onclick = createNamedList;

  if ($("#saveProfileTabBtn")) $("#saveProfileTabBtn").onclick = saveProfileTab;
  if ($("#exportResumeBtn")) $("#exportResumeBtn").onclick = doExportResume;
  if ($("#importResumeBtn")) $("#importResumeBtn").onclick = () => $("#importResumeInput").click();
  if ($("#importResumeInput")) $("#importResumeInput").onchange = (e) => { const f = e.target.files[0]; if (f) doImportResume(f); e.target.value = ""; };
  if ($("#addProofExpBtn")) $("#addProofExpBtn").onclick = () => {
    const exps = (state.profile || {}).experiences || {};
    const newKey = "exp_" + Date.now().toString(36);
    exps[newKey] = { name: "New Experience", title: "Role", when: "2026 - present", anchor: "", facts: [], bridges: ["builds"], xyz: { action: "", metric: "", method: "" } };
    if (!state.profile) state.profile = {};
    state.profile.experiences = exps;
    renderProfileProofList(exps);
  };

  // profile switcher
  if ($("#profileSelect")) {
    $("#profileSelect").onchange = async () => {
      const pid = $("#profileSelect").value;
      try {
        await api(`/api/profiles/${encodeURIComponent(pid)}/activate`, { method: "POST" });
        state.activeProfileId = pid;
        await refreshProfileTab();
        toast(`Active profile changed to ${pid}`);
      } catch (e) {
        toast(e.message, true);
      }
    };
  }

  if ($("#newProfileBtn")) {
    $("#newProfileBtn").onclick = async () => {
      const name = prompt("Enter new profile name (e.g. Personal / Firm):");
      if (!name || !name.trim()) return;
      const pid = name.trim().toLowerCase().replace(/[^a-z0-9_-]/g, "_").slice(0, 30) || "profile_" + Date.now();
      try {
        await api("/api/profiles", { method: "POST", body: { id: pid, name: name.trim() } });
        await api(`/api/profiles/${encodeURIComponent(pid)}/activate`, { method: "POST" });
        await fetchProfiles();
        await refreshProfileTab();
        toast(`Created and activated profile '${name.trim()}'`);
      } catch (e) {
        toast("Failed to create profile: " + e.message, true);
      }
    };
  }

  // pipeline filters
  if ($("#pipeListFilter")) $("#pipeListFilter").onchange = refreshPipeline;
  $("#pipeVoiceFilter").onchange = renderPipeline;
  $("#pipeStaleOnly").onchange = renderPipeline;

  // performance voice-set toggle
  $("#perfKindOutreach").onclick = () => setPerfKind("outreach");
  $("#perfKindFollowup").onclick = () => setPerfKind("followup");

  // triage filter + sweep
  $$("#triageView .triage-filter .vk-seg").forEach(b => {
    b.onclick = () => setTriageBucket(b.dataset.bucket);
  });
  $("#triageSweepBtn").onclick = () => runSweep($("#triageSweepBtn"));

  // suppression manager
  $("#openSuppBtn").onclick = openSuppressions;
  $("#suppCloseBtn").onclick = () => $("#suppModal").classList.add("hidden");
  $("#suppAddBtn").onclick = addSuppression;
  $("#suppInput").addEventListener("keydown", e => { if (e.key === "Enter") addSuppression(); });
  $("#suppClearBtn").onclick = async () => {
    const ok = await dialog({ title: "Clear do-not-contact?", message: "Remove every entry. Bounces will re-add addresses as they occur.", options: [{ label: "Cancel", value: false }, { label: "Clear all", value: true, danger: true }] });
    if (!ok) return;
    await api("/api/suppressions/clear", { method: "POST" }); await refreshSuppressions(); toast("Cleared");
  };

  // imap test connection
  $("#imapTestBtn").onclick = () => testImap($("#imapTestBtn"));

  // keyboard shortcuts + cheat-sheet
  $("#shortcutCloseBtn").onclick = () => $("#shortcutModal").classList.add("hidden");
  document.addEventListener("keydown", handleShortcut);

  // empty-state action buttons → wire to existing handlers
  if ($("#emptyPasteBtn")) $("#emptyPasteBtn").onclick = () => { const t = $("#namesInput"); if (t) t.focus(); };
  if ($("#emptyUploadBtn")) $("#emptyUploadBtn").onclick = () => $("#fileInput").click();
  if ($("#emptySourceBtn")) $("#emptySourceBtn").onclick = openSourcingPanel;
  if ($("#emptyDraft5Btn")) $("#emptyDraft5Btn").onclick = draft5;
  if ($("#emptyTriageSweepBtn")) $("#emptyTriageSweepBtn").onclick = () => runSweep($("#emptyTriageSweepBtn"));

  $("#guideBtn").onclick = () => $("#guideModal").classList.remove("hidden");
  $("#guideCloseBtn").onclick = () => $("#guideModal").classList.add("hidden");

  $("#startProvider").onchange = syncStartupKeyField;
  $("#startBtn").onclick = doStart;

  // voices manager + editor
  $("#voicesBtn").onclick = openVoicesManager;
  $("#voicesCloseBtn").onclick = () => $("#voicesModal").classList.add("hidden");
  $("#newVoiceBtn").onclick = () => openVoiceEditor(null);
  $("#veCancel").onclick = () => $("#voiceEditorModal").classList.add("hidden");
  $("#veSave").onclick = saveVoice;
  $("#veLearnNow").onclick = async () => {
    const vid = state.editingVoiceId; if (!vid) return;
    try { const r = await api(`/api/voices/${encodeURIComponent(vid)}/learn`, { method: "POST" });
      toast(r.proposal ? "Suggestion ready" : (r.message || "Nothing to suggest yet")); loadLearningPanel(vid); }
    catch (e) { toast(e.message, true); }
  };
  $("#veOptimize").onclick = async () => {
    const vid = state.editingVoiceId; if (!vid) return;
    try { await api(`/api/voices/${encodeURIComponent(vid)}/optimize`, { method: "POST" });
      toast("Challenger created for A/B"); loadLearningPanel(vid); }
    catch (e) { toast(e.message, true); }
  };
  $("#veArbitrate").onclick = async () => {
    const vid = state.editingVoiceId; if (!vid) return;
    try { const r = await api(`/api/voices/arbitrate`, { method: "POST" });
      const d = (r.decisions || []).find(x => x.champion === vid);
      toast(d ? `A/B: ${d.decision}` : "No clear result yet"); await fetchVoices(); openVoiceEditor(vid); }
    catch (e) { toast(e.message, true); }
  };
  $("#veAddBlock").onclick = addBlock;
  $("#veAddExample").onclick = () => { addTextRow("#veExamples", "", "Example email", true); updateLivePreview(); };
  $("#veAddFact").onclick = () => { addTextRow("#veCustomFacts", "", "A true claim I can make", true); updateLivePreview(); };
  $("#veAddVar").onclick = () => addVarRow("", "");
  $("#veStartFrom").onchange = (e) => {
    const src = voiceById(e.target.value);
    if (src) { const keepName = veGet("veName"); fillFromVoice(src); veSet("veName", keepName || ""); updateLivePreview(); }
  };
  ["veName", "veSubject", "veLenMin", "veLenMax", "veMentionSci", "veAllowDashes", "veStyleNotes",
   "veIdentityNote", "veSit_no_role_small", "veSit_role_small", "veSit_role_large"].forEach(id => {
    const el = $("#" + id);
    if (el) el.addEventListener(el.type === "checkbox" ? "change" : "input", updateLivePreview);
  });
  document.addEventListener("focusin", (e) => {
    const t = e.target;
    if (t && t.classList && t.classList.contains("ve-tok-target")) veLastFocus = t;
  });
}

/* ================= SOURCING ("Find new targets") ================= */
/* ================= SOURCING PRESETS MANAGER ================= */
let sourcingPrompts = [];
let availableSourcingSources = [];
let editingPresetId = null;

async function loadSourcingPrompts() {
  try {
    const r = await api("/api/sourcing_prompts");
    sourcingPrompts = r.prompts || [];
    const sel = $("#sourcingPromptSelect");
    if (!sel) return;
    const curVal = sel.value;
    sel.innerHTML = `<option value="">Default (Hot Startups & Fresh Funding)</option>` +
      sourcingPrompts.map(p => `<option value="${esc(p.id)}"${p.id === curVal ? " selected" : ""}>${esc(p.display_name)}</option>`).join("");
    updateSourcingPanelMandateHint();
    refreshSourcingListSelect();
  } catch (e) {
    console.error("Failed to load sourcing prompts", e);
  }
}

function updateSourcingPanelMandateHint() {
  const sel = $("#sourcingPromptSelect");
  const hint = $("#sourcingMandateHint");
  if (!sel || !hint) return;
  const p = sourcingPrompts.find(x => x.id === sel.value);
  if (p && p.criteria_text) {
    hint.textContent = `Mandate: "${p.criteria_text.length > 90 ? p.criteria_text.slice(0, 90) + "…" : p.criteria_text}"`;
    hint.classList.remove("hidden");
  } else {
    hint.classList.add("hidden");
  }
}

async function fetchSourcingSources() {
  if (availableSourcingSources.length > 0) return availableSourcingSources;
  try {
    const r = await api("/api/sourcing/sources");
    availableSourcingSources = r.sources || [];
  } catch (e) {
    availableSourcingSources = [
      { id: "grounded_search", label: "Grounded Web Search" },
      { id: "techeu_funding_feed", label: "Tech.eu Funding Feed" },
      { id: "franceinvest_directory", label: "France Invest Directory" }
    ];
  }
  return availableSourcingSources;
}

function openPresetsManager() {
  $("#sourcingPresetsModal").classList.remove("hidden");
  $("#presetEditor").classList.add("hidden");
  renderPresetsList();
}

async function renderPresetsList() {
  await loadSourcingPrompts();
  const listEl = $("#presetList");
  if (!listEl) return;
  if (!sourcingPrompts.length) {
    listEl.innerHTML = `<div class="col-empty"><span>No custom presets defined.</span></div>`;
    return;
  }

  listEl.innerHTML = sourcingPrompts.map(p => {
    const runTime = p.last_run_at ? `last run ${timeAgo(p.last_run_at)}` : "never run";
    const seen = `${p.total_candidates_seen || 0} candidates seen`;
    const isSeeded = Boolean(p.seeded_from);
    const tagSeeded = isSeeded ? `<span class="tag" style="background:var(--panel-2); color:var(--ink-soft);">seeded</span>` : "";
    const srcs = (p.sources || []).map(s => `<span class="tag">${esc(s)}</span>`).join(" ");
    const canDelete = sourcingPrompts.length > 1;

    return `
      <div class="preset-card" style="background:var(--panel); border:1px solid var(--line); border-radius:var(--r); padding:12px 14px;">
        <div style="display:flex; justify-content:space-between; align-items:flex-start;">
          <div>
            <div style="font-weight:600; font-size:14px; color:var(--ink); display:flex; align-items:center; gap:8px;">
              ${esc(p.display_name)} ${tagSeeded}
            </div>
            <div style="font-size:12.5px; color:var(--ink-soft); margin-top:3px;">
              ${esc(p.criteria_text || "No specific mandate text")}
            </div>
            <div style="display:flex; gap:12px; font-size:11.5px; color:var(--ink-faint); margin-top:6px; align-items:center;">
              <div>${srcs}</div>
              <div>·</div>
              <div>${runTime}</div>
              <div>·</div>
              <div>${seen}</div>
            </div>
          </div>
          <div style="display:flex; gap:6px;">
            <button class="btn ghost small edit-preset-btn" data-id="${esc(p.id)}">Edit</button>
            <button class="btn ghost small dup-preset-btn" data-id="${esc(p.id)}">Duplicate</button>
            ${isSeeded ? `<button class="btn ghost small reset-preset-btn" data-id="${esc(p.id)}">Reset to seed</button>` : ""}
            <button class="btn ghost small del-preset-btn" data-id="${esc(p.id)}"${canDelete ? "" : " disabled title=\"Cannot delete the only preset\""}>Delete</button>
          </div>
        </div>
      </div>
    `;
  }).join("");

  $$(".edit-preset-btn", listEl).forEach(b => b.onclick = () => openPresetEditor(b.dataset.id));
  $$(".dup-preset-btn", listEl).forEach(b => b.onclick = () => duplicatePreset(b.dataset.id));
  $$(".reset-preset-btn", listEl).forEach(b => b.onclick = () => resetPreset(b.dataset.id));
  $$(".del-preset-btn", listEl).forEach(b => b.onclick = () => deletePreset(b.dataset.id));
}

async function openPresetEditor(promptId) {
  editingPresetId = promptId;
  // The lookup below reads sourcingPrompts, which is filled by a separate call. If it
  // is empty, find() returns undefined and every field is filled with "", so an
  // existing preset opens as a blank form and saving it wipes the preset.
  if (promptId && (!Array.isArray(sourcingPrompts) || sourcingPrompts.length === 0)) {
    try { await loadSourcingPrompts(); } catch (e) { /* fall through to a blank form */ }
  }
  const sourcesList = await fetchSourcingSources();
  const editor = $("#presetEditor");
  editor.classList.remove("hidden");
  $("#presetEditorTitle").textContent = promptId ? "Edit preset" : "New preset";

  const p = promptId ? sourcingPrompts.find(x => x.id === promptId) : null;
  $("#presetNameInput").value = p ? p.display_name : "";
  $("#presetCriteriaInput").value = p ? (p.criteria_text || "") : "";
  $("#presetRecencyInput").value = p ? (p.recency_days || 120) : 120;
  if ($("#presetTargetNInput")) $("#presetTargetNInput").value = p ? (p.target_n || 0) : 0;
  if ($("#presetMaxCandidatesInput")) $("#presetMaxCandidatesInput").value = p ? (p.max_candidates || 40) : 40;
  $("#presetExcludeInput").value = p ? (p.exclude_notes || "") : "";

  const activeSrcs = p ? (p.sources || ["grounded_search"]) : ["grounded_search"];
  const chkWrap = $("#presetSourcesCheckboxes");
  chkWrap.innerHTML = sourcesList.map(s => `
    <label style="font-size:13px; display:inline-flex; align-items:center; gap:4px;">
      <input type="checkbox" class="preset-src-chk" value="${esc(s.id)}"${activeSrcs.includes(s.id) ? " checked" : ""} />
      ${esc(s.label)}
    </label>
  `).join("");

  // Attach live query preview listeners
  ["presetNameInput", "presetCriteriaInput", "presetRecencyInput", "presetExcludeInput"].forEach(id => {
    const el = $("#" + id);
    if (el) el.oninput = updatePresetQueryPreview;
  });
  $$(".preset-src-chk", chkWrap).forEach(chk => chk.onchange = updatePresetQueryPreview);

  updatePresetQueryPreview();
}

async function updatePresetQueryPreview() {
  const pdef = {
    id: editingPresetId || "preview",
    display_name: $("#presetNameInput").value.trim() || "Preview",
    criteria_text: $("#presetCriteriaInput").value.trim(),
    recency_days: parseInt($("#presetRecencyInput").value, 10) || 120,
    exclude_notes: $("#presetExcludeInput").value.trim(),
    sources: $$(".preset-src-chk:checked").map(c => c.value),
  };
  try {
    const r = await api("/api/sourcing/preview_query", { method: "POST", body: pdef });
    $("#presetQueryPreview").textContent = r.query || "";
  } catch (e) {
    $("#presetQueryPreview").textContent = "(Unable to build query preview)";
  }
}

async function savePreset() {
  const name = $("#presetNameInput").value.trim();
  const criteria = $("#presetCriteriaInput").value.trim();
  if (!name) { toast("Preset display name is required", true); return; }

  const pdef = {
    id: editingPresetId || name.toLowerCase().replace(/[^a-z0-9_-]/g, "_").slice(0, 40) || "custom_preset",
    display_name: name,
    criteria_text: criteria,
    sources: $$(".preset-src-chk:checked").map(c => c.value),
    recency_days: parseInt($("#presetRecencyInput").value, 10) || 120,
    target_n: $("#presetTargetNInput") ? (parseInt($("#presetTargetNInput").value, 10) || 0) : 0,
    max_candidates: $("#presetMaxCandidatesInput") ? (parseInt($("#presetMaxCandidatesInput").value, 10) || 40) : 40,
    exclude_notes: $("#presetExcludeInput").value.trim(),
  };
  let body = pdef;
  if (editingPresetId) {
    const existing = sourcingPrompts.find(x => x.id === editingPresetId);
    if (existing) {
      // The PUT endpoint replaces the whole object with no merge, so any field the
      // form does not carry resets to its Pydantic default: created_at, last_run_at,
      // total_candidates_seen, the revenue band, exclusion_policy and the rest.
      //
      // Spread order matters and is easy to get backwards. Applying left to right
      // into a NEW object means pdef wins on every key it defines and existing
      // supplies the remainder. An invalid Object.assign merge would do the
      // opposite and silently discard the user's edits.
      body = { ...existing, ...pdef };
    }
  }

  try {
    if (editingPresetId) {
      await api(`/api/sourcing_prompts/${encodeURIComponent(editingPresetId)}`, { method: "PUT", body: body });
    } else {
      await api("/api/sourcing_prompts", { method: "POST", body: body });
    }
    toast("Preset saved");
    // Cleared on the way out, so the next open decides fresh whether this is an edit
    // or a create. Left set, the next save overwrites whatever was edited here.
    editingPresetId = null;
    $("#presetEditor").classList.add("hidden");
    renderPresetsList();
  } catch (e) { toast(e.message, true); }
}

async function duplicatePreset(promptId) {
  try {
    const r = await api(`/api/sourcing_prompts/${encodeURIComponent(promptId)}/duplicate`, { method: "POST" });
    toast(`Duplicated as "${r.prompt.display_name}"`);
    renderPresetsList();
  } catch (e) { toast(e.message, true); }
}

async function resetPreset(promptId) {
  const ok = await dialog({
    title: "Reset preset to seed?",
    message: "This will overwrite your changes to this preset with the original seeded default values.",
    options: [{ label: "Cancel", value: false }, { label: "Reset to seed", value: true, danger: true }]
  });
  if (!ok) return;
  try {
    await api(`/api/sourcing_prompts/${encodeURIComponent(promptId)}/reset`, { method: "POST" });
    toast("Preset reset to seed defaults");
    renderPresetsList();
  } catch (e) { toast(e.message, true); }
}

async function deletePreset(promptId) {
  if (sourcingPrompts.length <= 1) {
    toast("Cannot delete the only preset", true);
    return;
  }
  const sel = $("#sourcingPromptSelect");
  const isSelected = sel && sel.value === promptId;
  const ok = await dialog({
    title: "Delete preset?",
    message: isSelected ? "This preset is currently selected for sourcing runs. Deleting it will reset selection to default." : "Delete this sourcing preset permanently.",
    options: [{ label: "Cancel", value: false }, { label: "Delete", value: true, danger: true }]
  });
  if (!ok) return;
  try {
    await api(`/api/sourcing_prompts/${encodeURIComponent(promptId)}`, { method: "DELETE" });
    toast("Preset deleted");
    if (isSelected && sel) sel.value = "";
    renderPresetsList();
  } catch (e) { toast(e.message, true); }
}

async function openSourcingPanel() {
  const panel = $("#sourcingPanel");
  if (panel) panel.classList.remove("hidden");
  await loadSourcingPrompts();
  await refreshSourcingListSelect();
  // Always write the banner, never leave it. A null last_run otherwise leaves
  // "Sourcing run in progress..." on screen forever after a restart.
  try {
    const res = await api("/api/source/research/last");
    const last = res.last_run || res.job || res;
    const el = $("#sourcingStatusText");
    if (el) {
      if (last && last.status === "running") {
        el.textContent = "Sourcing run in progress...";
        // trackSourcingJob only exists once live polling has been added; guard for it so
        // this works either way.
        if (last.job_id && typeof trackSourcingJob === "function") trackSourcingJob(last.job_id);
      } else if (last && (last.counts || last.added_slugs)) {
        el.textContent = `Last run: ${last.counts?.queued || last.added_slugs?.length || 0} companies added`;
        renderSourcingReport(last);
      } else {
        el.textContent = "";
      }
    }
  } catch (e) {
    const el = $("#sourcingStatusText");
    if (el) el.textContent = "";
  }
}

function closeSourcingPanel() {
  const panel = $("#sourcingPanel");
  if (panel) panel.classList.add("hidden");
}

async function runSourcing() {
  const promptSelect = $("#sourcingPromptSelect");
  const promptId = (promptSelect && promptSelect.value) || null;
  const listSelect = $("#sourcingListSelect");
  const listId = (listSelect && listSelect.value) || undefined;
  const statusEl = $("#sourcingStatusText");
  const reportEl = $("#sourcingReport");
  if (statusEl) statusEl.textContent = "🚀 Sourcing run in progress...";
  if (reportEl) reportEl.innerHTML = "";

  try {
    const r = await api("/api/source/research", {
      method: "POST",
      body: { sourcing_prompt_id: promptId, list_id: listId }
    });
    const job = r.job || r;
    if (job && job.job_id) {
      trackSourcingJob(job.job_id);
    } else {
      if (statusEl) statusEl.textContent = `Completed sourcing run in ${job.stage || 'Completed'}.`;
      renderSourcingReport(job);
    }
  } catch (e) {
    if (statusEl) statusEl.textContent = "Sourcing run failed: " + e.message;
    toast(e.message, true);
  }
}

let parsedBulkRows = [];

function setupBulkExportHandlers() {
  const chooseBtn = $("#chooseBulkFileBtn");
  const fileInput = $("#bulkExportFileInput");
  const nameDisplay = $("#bulkFileNameDisplay");
  const runBtn = $("#runScreenedImportBtn");

  if (chooseBtn && fileInput) {
    chooseBtn.onclick = () => fileInput.click();
    fileInput.onchange = async (evt) => {
      const file = evt.target.files && evt.target.files[0];
      if (!file) return;
      if (nameDisplay) {
        nameDisplay.textContent = `Selected: ${file.name} (${(file.size / 1024).toFixed(1)} KB)`;
        nameDisplay.classList.remove("hidden");
      }
      try {
        const text = await file.text();
        if (file.name.endsWith(".json")) {
          const data = JSON.parse(text);
          parsedBulkRows = Array.isArray(data) ? data : (data.results || data.companies || data.items || []);
        } else {
          const lines = text.split(/\r?\n/).filter(l => l.trim());
          if (lines.length > 1) {
            const headers = lines[0].split(",").map(h => h.trim().replace(/^["']|["']$/g, ""));
            parsedBulkRows = lines.slice(1).map(line => {
              const vals = line.split(",").map(v => v.trim().replace(/^["']|["']$/g, ""));
              const row = {};
              headers.forEach((h, idx) => { row[h] = vals[idx] || ""; });
              return row;
            });
          } else {
            parsedBulkRows = [];
          }
        }
        if (runBtn) runBtn.disabled = parsedBulkRows.length === 0;
        toast(`Loaded ${parsedBulkRows.length} targets from ${file.name}`);
      } catch (e) {
        toast(`Failed to parse file: ${e.message}`, true);
        if (runBtn) runBtn.disabled = true;
      }
    };
  }

  if (runBtn) {
    runBtn.onclick = async () => {
      if (!parsedBulkRows.length) return;
      runBtn.disabled = true;
      runBtn.textContent = "Screening & Importing...";

      const revMin = parseInt(($("#gateRevMin") || {}).value, 10);
      const revMax = parseInt(($("#gateRevMax") || {}).value, 10);
      const reqKwRaw = (($("#gateReqKeyword") || {}).value || "").trim();
      const reqKw = {};
      if (reqKwRaw.includes(":")) {
        const [f, k] = reqKwRaw.split(":", 2);
        reqKw[f.trim()] = k.trim();
      }
      const rejEvents = (($("#gateRejectEvents") || {}).value || "")
        .split(",").map(s => s.trim()).filter(Boolean);

      const gates = {};
      if (!isNaN(revMin)) gates.revenue_band_min = revMin;
      if (!isNaN(revMax)) gates.revenue_band_max = revMax;
      if (Object.keys(reqKw).length) gates.require_keyword_in_field = reqKw;
      if (rejEvents.length) gates.reject_last_event_types = rejEvents;

      try {
        const res = await api("/api/source/import_screened", {
          method: "POST",
          body: {
            rows: parsedBulkRows,
            gates: gates,
            list_id: state.activeListId || "default"
          }
        });
        toast(`Imported: ${res.added} added, ${res.skipped_duplicates ? res.skipped_duplicates.length : 0} duplicate, ${res.screened_out || 0} screened out`);
        const q = await api(`/api/queue?list_id=${encodeURIComponent(state.activeListId || "default")}`);
        state.queue = q.queue;
        renderQueue();
      } catch (e) {
        toast(`Import failed: ${e.message}`, true);
      } finally {
        runBtn.disabled = false;
        runBtn.textContent = "Import & Screen Targets";
      }
    };
  }
}

function renderSourcingReport(job) {
  const reportEl = $("#sourcingReport");
  if (!reportEl || !job) return;

  const counts = job.counts || {};
  const notes = [...(job.notes || [])];
  const candidates = job.candidates || [];
  const addedSlugs = job.added_slugs || [];
  const presetObj = sourcingPrompts.find(x => x.id === job.sourcing_prompt_id);
  const presetName = presetObj ? presetObj.display_name : (job.sourcing_prompt_id || "Default");

  const unresWeb = counts.unresolved_website || 0;
  if (job.stopped_because === "target_met") {
    notes.unshift(`${counts.accepted || 0} of ${job.target_n || 'target'} companies accepted — target met (${counts.checked || 0} examined)`);
  } else if (job.stopped_because === "budget_exhausted") {
    notes.unshift(`${counts.accepted || 0} of ${job.target_n || 'target'} companies accepted — attempt budget exhausted (${counts.checked || 0} examined)`);
  }
  if (unresWeb > 0) {
    notes.push(`${counts.queued || 0} queued, ${unresWeb} with no confirmed website yet`);
  }

  // A run with no live provider returns offline samples. Say so plainly, or a missing
  // API key is indistinguishable from an exhausted market.
  if (counts.no_provider) {
    notes.unshift("No live provider configured. These are offline sample companies, not real results. Set a provider and API key in Settings.");
  } else if ((counts.ungrounded || 0) > 0) {
    notes.unshift(`${counts.ungrounded} companies were produced without a web search and are not verified findings.`);
  }

  // errors previously went nowhere: the ingest path writes failures into this list and
  // nothing displayed them, so a failed ingest looked like a clean run.
  if ((job.errors || []).length) {
    notes.unshift(`Run reported ${job.errors.length} error(s): ${job.errors[0]}`);
  }
  if (job.added_list_id) {
    notes.push(`Companies added to list: ${job.added_list_id}`);
  }
  if ((counts.checked || 0) > 0 && (counts.queued || 0) === 0) {
    notes.unshift("Nothing reached the queue. Check the destination list and any errors above.");
  }

  let html = `
    <div style="background: #ffffff; padding: 12px; border-radius: 6px; border: 1px solid #e2e8f0; font-size: 13px;">
      <div class="sr-preset" style="font-weight:600; font-size:12px; color:var(--ink-soft); margin-bottom:6px;">Preset: ${esc(presetName)}</div>
      <div style="font-size: 12px; color: var(--ink-soft); margin-bottom: 6px;">
        harvested ${counts.harvested || 0}, already seen ${counts.already_seen || 0}, excluded ${counts.excluded || 0}, new ${counts.new || 0}, ungrounded ${counts.ungrounded || 0}, attempts ${counts.harvest_attempts || 0}
      </div>
      <div style="display: flex; gap: 16px; font-weight: 500; color: #1e293b; margin-bottom: 8px;">
        <span>Checked: ${counts.checked || 0}</span>
        <span style="color: #16a34a;">Queued: ${counts.queued || 0}</span>
      </div>
  `;

  if (counts.queued_for_review) {
    notes.push(`${counts.queued_for_review} flagged by screening and queued for review`);
  }

  if (notes.length) {
    html += `<div style="font-size: 12px; color: #475569; margin-bottom: 8px;">ℹ️ ${notes.map(esc).join('<br/>')}</div>`;
  }

  if (addedSlugs.length) {
    html += `
      <div style="margin-top: 8px;">
        <button class="btn ghost small" id="undoSourcingBtn" style="color: #dc2626;">Undo — remove all ${addedSlugs.length} queued targets</button>
      </div>
    `;
  }

  html += `</div>`;
  reportEl.innerHTML = html;

  if ($("#undoSourcingBtn")) {
    $("#undoSourcingBtn").onclick = async () => {
      try {
        const u = await api(`/api/source/research/${job.job_id}/undo`, { method: "POST" });
        toast(`Removed ${u.undo.removed} targets from queue`);
        const q = await api(`/api/queue?list_id=${encodeURIComponent(state.activeListId || "default")}`);
        state.queue = q.queue;
        renderQueue();
        reportEl.innerHTML = "";
      } catch (e) {
        toast(e.message, true);
      }
    };
  }
}

/* ================= CANDIDATE PROFILE MODAL (Phase 4) ================= */

async function openProfileModal() {
  state.profileLoaded = false;
  const saveBtn = $("#profileForm") ? $("#profileForm").querySelector("button[type='submit']") : null;
  if (saveBtn) saveBtn.disabled = true;
  showModal("profileModal");
  try {
    const p = await api("/api/profile");
    state.profile = p;
    $("#profName").value = p.name || "";
    $("#profEmail").value = p.email || "";
    $("#profPhone").value = p.phone || "";
    $("#profLinkedin").value = p.linkedin || "";
    $("#profTargetRoles").value = (p.target_roles || []).join(", ");
    $("#profTargetFirms").value = (p.target_firm_types || []).join(", ");
    $("#profTargetLocations").value = (p.target_locations || []).join(", ");
    $("#profOneLine").value = p.one_line || "";
    $("#profSpine").value = p.spine || "";
    const exps = p.experiences || {};
    const standingKey = p.standing_key || "anchor_co";
    const standingExp = exps[standingKey] || {};
    if ($("#profStandingExp")) $("#profStandingExp").value = standingExp.anchor || "";
    state.profileLoaded = true;
    if (saveBtn) saveBtn.disabled = false;
  } catch (e) {
    state.profileLoaded = false;
    if (saveBtn) saveBtn.disabled = true;
    toast("Failed to load profile: " + e.message, true);
  }
}

function closeProfileModal() {
  const backdrop = $("#profileModal");
  if (backdrop) backdrop.classList.add("hidden");
}

async function saveProfile() {
  if (!state.profileLoaded) {
    toast("Cannot save: Profile is not loaded", true);
    return;
  }
  const cur = state.profile || {};
  const splitComma = (val) => (val || "").split(",").map(s => s.trim()).filter(Boolean);
  const updated = {
    ...cur,
    name: $("#profName").value.trim(),
    email: $("#profEmail").value.trim(),
    phone: $("#profPhone").value.trim(),
    linkedin: $("#profLinkedin").value.trim(),
    target_roles: splitComma($("#profTargetRoles").value),
    target_firm_types: splitComma($("#profTargetFirms").value),
    target_locations: splitComma($("#profTargetLocations").value),
    one_line: $("#profOneLine").value.trim(),
    spine: $("#profSpine").value.trim(),
  };
  const standingKey = updated.standing_key || "anchor_co";
  if (updated.experiences && updated.experiences[standingKey]) {
    if ($("#profStandingExp")) updated.experiences[standingKey].anchor = $("#profStandingExp").value.trim();
  }
  try {
    const res = await api("/api/profile", { method: "POST", body: updated });
    state.profile = res.profile;
    toast("Profile saved successfully");
    closeProfileModal();
  } catch (e) {
    toast("Failed to save profile: " + e.message, true);
  }
}

async function resetProfile() {
  const ok = await dialog({
    title: "Reset Profile?",
    message: "This will revert your profile template to default values. Continue?",
    options: [{ label: "Reset", value: "reset", danger: true }, { label: "Cancel", value: false, primary: true }],
  });
  if (ok === "reset") {
    try {
      const res = await api("/api/profile/reset", { method: "POST" });
      state.profile = res.profile;
      toast("Profile reset to defaults");
      closeProfileModal();
    } catch (e) {
      toast(e.message, true);
    }
  }
}

async function fetchProfiles() {
  const sel = $("#profileSelect");
  if (!sel) return;
  try {
    const res = await api("/api/profiles");
    state.profiles = res.profiles || [];
    state.activeProfileId = res.active || "default";
    sel.innerHTML = state.profiles.map(p => 
      `<option value="${esc(p.id)}">${esc(p.name || p.id)}</option>`
    ).join("");
    sel.value = state.activeProfileId;
  } catch (e) {
    /* fallback */
  }
}

async function refreshProfileTab() {
  await fetchProfiles();
  try {
    const p = await api("/api/profile");
    state.profile = p;
    state.profileLoaded = true;
    if ($("#profTabName")) $("#profTabName").value = p.name || "";
    if ($("#profTabEmail")) $("#profTabEmail").value = p.email || "";
    if ($("#profTabPhone")) $("#profTabPhone").value = p.phone || "";
    if ($("#profTabLinkedin")) $("#profTabLinkedin").value = p.linkedin || "";
    if ($("#profTabOneLine")) $("#profTabOneLine").value = p.one_line || "";
    if ($("#profTabSpine")) $("#profTabSpine").value = p.spine || "";
    renderProfileProofList(p.experiences || {});
  } catch (e) {
    state.profileLoaded = false;
    toast("Failed to load profile tab: " + e.message, true);
  }
}

function renderProfileProofList(experiences) {
  const container = $("#profProofList");
  if (!container) return;
  const entries = Object.entries(experiences);
  if (!entries.length) {
    container.innerHTML = '<div class="col-empty"><span>No experiences in proof library. Click "+ Add Experience" to create one.</span></div>';
    return;
  }
  container.innerHTML = entries.map(([key, exp]) => {
    const facts = (exp.facts || []).join("\n");
    const bridges = exp.bridges || [];
    const xyz = exp.xyz || { action: "", metric: "", method: "" };
    const hasXyz = Boolean(xyz.action || xyz.metric || xyz.method);

    const bridgeTagsHtml = bridges.map(b => `<span class="tag neutral">${esc(b)}<button class="tag-remove-btn" data-key="${esc(key)}" data-tag="${esc(b)}" aria-label="Remove tag">&times;</button></span>`).join("");

    return `<div class="proof-exp-card sheet" data-key="${esc(key)}">
      <div class="proof-exp-card-head">
        <div class="identity-grid-3col" style="flex:1;">
          <div class="field" style="margin-bottom:0;">
            <label for="exp_name_${esc(key)}">Company / Entity</label>
            <input type="text" id="exp_name_${esc(key)}" class="exp-name" value="${esc(exp.name || key)}" placeholder="e.g. Acme Capital" />
          </div>
          <div class="field" style="margin-bottom:0;">
            <label for="exp_title_${esc(key)}">Title / Role</label>
            <input type="text" id="exp_title_${esc(key)}" class="exp-title" value="${esc(exp.title || '')}" placeholder="e.g. Investment Associate" />
          </div>
          <div class="field" style="margin-bottom:0;">
            <label for="exp_when_${esc(key)}">Dates / Timeline</label>
            <input type="text" id="exp_when_${esc(key)}" class="exp-when" value="${esc(exp.when || '')}" placeholder="e.g. 2026 - present" />
          </div>
        </div>
        <button class="qrow-remove-btn remove-exp-btn" data-key="${esc(key)}" aria-label="Remove experience" title="Remove">&times;</button>
      </div>

      <div class="field" style="margin-top:12px;">
        <label for="exp_anchor_${esc(key)}">Headline / Anchor Claim</label>
        <div class="desc">How this experience reads in outreach email correspondence</div>
        <input type="text" id="exp_anchor_${esc(key)}" class="exp-anchor" value="${esc(exp.anchor || '')}" placeholder="e.g. Led deal execution and financial modeling for B2B SaaS growth investments" />
      </div>

      <details class="xyz-details"${hasXyz ? " open" : ""}>
        <summary>Deconstruct XYZ formula (Action, Metric, Method)</summary>
        <div class="identity-grid-3col" style="margin-top:10px;">
          <div class="field" style="margin-bottom:0;">
            <label for="exp_xyz_action_${esc(key)}">XYZ Action</label>
            <div class="desc">e.g. Accomplished X</div>
            <input type="text" id="exp_xyz_action_${esc(key)}" class="exp-xyz-action" value="${esc(xyz.action || '')}" placeholder="e.g. Led deal sourcing & due diligence" />
          </div>
          <div class="field" style="margin-bottom:0;">
            <label for="exp_xyz_metric_${esc(key)}">XYZ Metric</label>
            <div class="desc">e.g. Measured by Y</div>
            <input type="text" id="exp_xyz_metric_${esc(key)}" class="exp-xyz-metric" value="${esc(xyz.metric || '')}" placeholder="e.g. 5 deals closed, €40M deployed" />
          </div>
          <div class="field" style="margin-bottom:0;">
            <label for="exp_xyz_method_${esc(key)}">XYZ Method</label>
            <div class="desc">e.g. By doing Z</div>
            <input type="text" id="exp_xyz_method_${esc(key)}" class="exp-xyz-method" value="${esc(xyz.method || '')}" placeholder="e.g. building automated screening models" />
          </div>
        </div>
      </details>

      <div class="field">
        <label for="exp_facts_${esc(key)}">Proof Points / Highlights</label>
        <div class="desc">One proof point per line</div>
        <textarea id="exp_facts_${esc(key)}" class="exp-facts" rows="2" placeholder="e.g. Built automated pipeline tracking&#10;Evaluated 120+ growth stage candidates">${esc(facts)}</textarea>
      </div>

      <div class="field">
        <label>Bridge Tags</label>
        <div class="desc">Topic tags linking this experience to target companies</div>
        <div class="chip-row">
          ${bridgeTagsHtml}
          <div class="tag-add-box">
            <input type="text" class="tag-add-input" placeholder="+ add tag" />
            <button class="btn ghost small tag-add-btn" data-key="${esc(key)}" type="button">Add</button>
          </div>
        </div>
        <input type="hidden" class="exp-bridges" value="${esc(bridges.join(", "))}" />
      </div>
    </div>`;
  }).join("");

  container.querySelectorAll(".remove-exp-btn").forEach(btn => {
    btn.onclick = () => {
      const k = btn.dataset.key;
      delete experiences[k];
      renderProfileProofList(experiences);
    };
  });

  container.querySelectorAll(".tag-remove-btn").forEach(btn => {
    btn.onclick = () => {
      const k = btn.dataset.key;
      const tagToRemove = btn.dataset.tag;
      if (experiences[k] && experiences[k].bridges) {
        experiences[k].bridges = experiences[k].bridges.filter(t => t !== tagToRemove);
        renderProfileProofList(experiences);
      }
    };
  });

  container.querySelectorAll(".tag-add-btn").forEach(btn => {
    btn.onclick = () => {
      const k = btn.dataset.key;
      const card = btn.closest(".proof-exp-card");
      const inp = card.querySelector(".tag-add-input");
      const newTag = (inp.value || "").trim().toLowerCase().replace(/[^a-z0-9_-]/g, "");
      if (newTag && experiences[k]) {
        if (!experiences[k].bridges) experiences[k].bridges = [];
        if (!experiences[k].bridges.includes(newTag)) {
          experiences[k].bridges.push(newTag);
        }
        renderProfileProofList(experiences);
      }
    };
  });
}

async function saveProfileTab() {
  const cur = state.profile || {};
  const experiences = {};
  $$("#profProofList .proof-exp-card").forEach(card => {
    const key = card.dataset.key || ("exp_" + Math.random().toString(36).slice(2, 7));
    const name = card.querySelector(".exp-name").value.trim() || key;
    const title = card.querySelector(".exp-title").value.trim() || "Role";
    const when = card.querySelector(".exp-when").value.trim() || "2026 - present";
    const anchor = card.querySelector(".exp-anchor").value.trim() || `${name} experience`;
    const facts = card.querySelector(".exp-facts").value.split("\n").map(s => s.trim()).filter(Boolean);
    const bridgesVal = card.querySelector(".exp-bridges").value;
    const bridges = bridgesVal.split(",").map(s => s.trim()).filter(Boolean);
    const xyz = {
      action: card.querySelector(".exp-xyz-action").value.trim(),
      metric: card.querySelector(".exp-xyz-metric").value.trim(),
      method: card.querySelector(".exp-xyz-method").value.trim(),
    };
    experiences[key] = {
      name, title, when, tense: when.toLowerCase().includes("present") ? "present" : "past",
      anchor, facts: facts.length ? facts : [anchor], bridges: bridges.length ? bridges : ["builds"], xyz
    };
  });

  const updated = {
    ...cur,
    name: ($("#profTabName") ? $("#profTabName").value : "").trim(),
    email: ($("#profTabEmail") ? $("#profTabEmail").value : "").trim(),
    phone: ($("#profTabPhone") ? $("#profTabPhone").value : "").trim(),
    linkedin: ($("#profTabLinkedin") ? $("#profTabLinkedin").value : "").trim(),
    one_line: ($("#profTabOneLine") ? $("#profTabOneLine").value : "").trim(),
    spine: ($("#profTabSpine") ? $("#profTabSpine").value : "").trim(),
    experiences: Object.keys(experiences).length ? experiences : (cur.experiences || {})
  };

  try {
    const res = await api("/api/profile", { method: "POST", body: updated });
    state.profile = res.profile;
    toast("Profile saved successfully");
    refreshProfileTab();
  } catch (e) {
    toast("Failed to save profile: " + e.message, true);
  }
}

async function doExportResume() {
  try {
    const data = await api("/api/profile/export_resume");
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "resume.json";
    a.click();
    URL.revokeObjectURL(url);
    toast("Exported resume.json");
  } catch (e) {
    toast("Failed to export resume: " + e.message, true);
  }
}

async function doImportResume(file) {
  try {
    const text = await file.text();
    const resume = JSON.parse(text);
    const res = await api("/api/profile/import_resume", { method: "POST", body: resume });
    state.profile = res.profile;
    toast("Imported resume.json successfully");
    refreshProfileTab();
  } catch (e) {
    toast("Failed to import resume.json: " + e.message, true);
  }
}

async function boot() {
  if ($("#profileBtn")) $("#profileBtn").onclick = openProfileModal;
  if ($("#closeProfileModal")) $("#closeProfileModal").onclick = closeProfileModal;
  if ($("#profileForm")) $("#profileForm").onsubmit = async (e) => { e.preventDefault(); await saveProfile(); };
  if ($("#saveProfileTabBtn")) $("#saveProfileTabBtn").onclick = async () => { await saveProfile(); };
  if ($("#saveProfileModalBtn")) $("#saveProfileModalBtn").onclick = async () => { await saveProfile(); };
  if ($("#resetProfileBtn")) $("#resetProfileBtn").onclick = async () => { await resetProfile(); };
  wire();
  try {
    await refreshStatus();
    await fetchVoices();
    await fetchLists();
    await fetchMeta();
    await refreshAttachmentsPanel();
    await api(`/api/queue?list_id=${encodeURIComponent(state.activeListId || "default")}`)
      .then(r => { state.queue = r.queue; renderQueue(); });
    await refreshDrafts();
    await updateFollowupsBadge();
    await refreshCost();
    try { const t = await api("/api/triage"); state.triageData = t; updateTriageBadge(); } catch (e) {}
    showView("workspace");
    if (needsKey(state.status)) openStartup();
  } catch (e) {
    toast("Could not reach the local server: " + e.message, true);
  }
}
document.addEventListener("DOMContentLoaded", boot);
