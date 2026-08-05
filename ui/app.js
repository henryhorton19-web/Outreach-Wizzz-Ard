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
function shortUrl(u) { try { const x = new URL(u); return x.hostname.replace(/^www\./, "") + (x.pathname.length > 1 ? x.pathname : ""); } catch { return u; } }
function wordCount(s) { return (s || "").trim() ? (s.trim().match(/\S+/g) || []).length : 0; }

/* ================= STATUS + STARTUP ================= */
async function refreshStatus() {
  state.status = await api("/api/status");
  const prov = state.status.provider;
  $("#providerPill").textContent = "provider: " + (PROVIDER_LABEL[prov] || prov);
  return state.status;
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
  try {
    const r = await api("/api/ingest", { method: "POST", body: { text } });
    state.queue = r.queue;
    $("#namesInput").value = ""; updateNameCount();
    showIngestBanner(r);
    renderQueue();
    toast(`${r.added} added to queue`);
  } catch (e) { toast(e.message, true); }
}
async function doUpload(file) {
  const form = new FormData(); form.append("file", file);
  try {
    const r = await api("/api/ingest_file", { method: "POST", form });
    state.queue = r.queue; showIngestBanner(r); renderQueue();
    toast(`${r.added} added from ${file.name}`);
  } catch (e) { toast(e.message, true); }
}

/* ================= QUEUE ================= */
function renderQueue() {
  const list = $("#queueList");
  list.innerHTML = "";
  $("#queueCount").textContent = state.queue.length ? state.queue.length : "";
  $("#queueEmpty").classList.toggle("hidden", state.queue.length > 0);
  state.queue.forEach(rec => {
    const el = document.createElement("div");
    el.className = "qrow";
    const m = rec.meta || {};
    const chips = [];
    if (m.employees_band) chips.push(m.employees_band);
    if (m.funding_heat || m.signal_basis || m.discovery_label) chips.push(m.funding_heat || m.signal_basis || m.discovery_label);
    if (m.hq_city || m.hq_country) chips.push([m.hq_city, m.hq_country].filter(Boolean).join(", "));

    const chipHtml = chips.map(c => `<span class="qrow-chip" title="${esc(c)}">${esc(c)}</span>`).join("");

    el.innerHTML = `
      <div class="qrow-info">
        <div class="qrow-name">
          ${esc(rec.name)}
          ${rec.crm_id || rec.ref ? `<span class="qrow-ref">${esc(rec.crm_id || rec.ref)}</span>` : ""}
        </div>
        ${chips.length ? `<div class="qrow-chips">${chipHtml}</div>` : ""}
      </div>
      <div class="qrow-act">
        <button class="btn primary small" data-act="draft">Draft &rarr;</button>
        <button class="icon-btn small" data-act="remove" title="Remove">&times;</button>
      </div>`;
    el.querySelector('[data-act="draft"]').onclick = () => draftFromQueue(rec.slug);
    el.querySelector('[data-act="remove"]').onclick = () => removeFromQueue(rec.slug);
    list.appendChild(el);
  });
}
async function draftFromQueue(slug) {
  if (state.status && needsKey(state.status)) { openStartup(); return; }
  try {
    const r = await api(`/api/queue/${slug}/draft`, { method: "POST" });
    state.queue = r.queue; renderQueue();
    ingestCompany(r.company);
    renderDrafts();
    // now actually run the pipeline for this row
    await runDraft(slug);
  } catch (e) { toast(e.message, true); }
}
async function removeFromQueue(slug) {
  try { const r = await api(`/api/queue/${slug}`, { method: "DELETE" }); state.queue = r.queue; renderQueue(); }
  catch (e) { toast(e.message, true); }
}
async function clearQueue() {
  if (!state.queue.length) return;
  const ok = await dialog({ title: "Clear queue?", message: "Remove all queued targets? Drafts are unaffected.", options: [{ label: "Cancel", value: false }, { label: "Clear", value: true, danger: true }] });
  if (!ok) return;
  await api("/api/queue/clear", { method: "POST" });
  state.queue = []; renderQueue(); toast("Queue cleared");
}
async function draft5() {
  if (state.status && needsKey(state.status)) { openStartup(); return; }
  const slugs = state.queue.slice(0, 5).map(r => r.slug);
  if (!slugs.length) { toast("Queue is empty", true); return; }
  for (const slug of slugs) {
    try {
      const r = await api(`/api/queue/${slug}/draft`, { method: "POST" });
      state.queue = r.queue; ingestCompany(r.company);
    } catch (e) { toast(e.message, true); }
  }
  renderQueue(); renderDrafts();
  await Promise.all(slugs.map(runDraft));
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
  const results = $("#results");
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

    const pill = row.querySelector(".pill");
    pill.textContent = cs.status_pill || stateLabel(cs);
    pill.className = "pill" + (cs.state === "error" || cs.disqualified ? " pill-err" : cs.state === "drafted" ? " pill-ok" : "");

    // actions
    const act = row.querySelector(".cell-act");
    act.innerHTML = "";
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
      const retry = document.createElement("button");
      retry.className = "btn ghost small"; retry.textContent = "Retry";
      retry.onclick = (e) => { e.stopPropagation(); runDraft(cs.slug); };
      act.appendChild(retry);
    }
    const del = document.createElement("button");
    del.className = "icon-btn small"; del.title = "Delete"; del.innerHTML = "&times;";
    del.onclick = (e) => { e.stopPropagation(); deleteDraft(cs.slug); };
    act.appendChild(del);

    // drawer toggle
    row.querySelector(".row-main").onclick = () => toggleDrawer(cs.slug);

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
    wrap.innerHTML = `<div class="research"><div class="research-fail">${esc(cs.error || "Draft failed.")}</div>
      <button class="btn ghost small" id="retryBtn">Retry this target</button></div>`;
    wrap.querySelector("#retryBtn").onclick = () => runDraft(cs.slug);
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

  // voice picker (redraft) — all voices plus Auto
  const voiceOpts = `<option value="__auto__">Auto (by situation)</option>` +
    allVoices.map(v => `<option value="${v.id}"${v.id === cs.voice ? " selected" : ""}>${esc(v.display_name)}</option>`).join("");

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
            <div class="c-to">To: ${esc((cs.contact || {}).email || "")}</div>
          </div>
          <div class="letter-body">
            <textarea class="emailEdit" spellcheck="true">${esc(cs.final_email || cs.machine_email || "")}</textarea>
          </div>
        </div>
        <div class="letter-actions">
          <button class="btn ghost small saveEdit">Save edit</button>
          <button class="btn ghost small resetEdit">Restore original</button>
          <button class="btn ghost small compareBtn">Compare with original</button>
          <button class="btn ghost small insertSnippet">Insert snippet</button>
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
      const updated = await api(`/api/companies/${cs.slug}/email`, { method: "PUT", body: { subject: subjectInput.value, email: emailEdit.value } });
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
  wrap.querySelector(".insertSnippet").onclick = (e) => {
    e.stopPropagation();
    openSnippetPopover(e.currentTarget, emailEdit);
  };
  wrap.querySelector(".voiceSel").onchange = async (e) => {
    const val = e.target.value;
    const voice = val === "__auto__" ? null : val;
    if (voice === cs.voice) return;
    try {
      const r = await api(`/api/companies/${cs.slug}/redraft`, { method: "POST", body: { voice, reuse_cache: true } });
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
  const dqNote = cs && cs.disqualified ? "\n\nThis target is marked disqualified (work mode or language). Approve anyway?" : "";
  const eff = getEffectiveAttachments(cs);
  const attachNote = eff.length ? `\n\nThe email will include ${eff.join(", ")}.` : "";
  // send-window advisory (Phase 6c): a non-blocking hint, never a block
  let windowNote = "";
  try { const w = await api("/api/send_window"); if (w.advise) windowNote = `\n\n${w.message}`; } catch (e) {}
  const ok = await dialog({
    title: "Approve and stage?",
    message: `Stage the email for ${cs ? cs.name : "this target"} as a .eml file and write it to your tracker. Nothing sends until you open it and press send.${dqNote}${attachNote}${windowNote}`,
    options: [{ label: "Cancel", value: false }, { label: "Approve", value: true, primary: true }],
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
const VIEWS = ["workspace", "followups", "pipeline", "performance", "triage"];

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
  // tab strip a11y + active state
  $$(".topbar-tab").forEach(t => {
    const on = t.dataset.view === name;
    t.classList.toggle("is-active", on);
    t.setAttribute("aria-selected", on ? "true" : "false");
  });
  if (name === "followups") refreshFollowups();
  else if (name === "pipeline") refreshPipeline();
  else if (name === "performance") refreshPerformance();
  else if (name === "triage") refreshTriage();
}

// keep the old name working as a thin wrapper (called from follow-up row handlers)
function showFollowupsView(on) { showView(on ? "followups" : "workspace"); }

async function refreshFollowups() {
  try {
    const r = await api("/api/followups");
    state.followups = r.followups || [];
  } catch (e) { state.followups = []; }
  renderFollowups();
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
    meter.textContent = `${fmtCost(c.cost)} · ${drafts} draft${drafts === 1 ? "" : "s"}`;
    meter.title = `in ${c.in || 0} · out ${c.out || 0} · cached ${c.cached || 0} tokens`;
    meter.classList.toggle("hidden", !(c.cost || drafts));
    state._cost = c;
  } catch (e) { /* cost is optional; never block */ }
}

async function openCostPopover() {
  const c = state._cost || await api("/api/cost");
  const byModel = c.by_model || {};
  const lines = Object.keys(byModel).map(m =>
    `${m}: ${fmtCost(byModel[m].cost)} (${byModel[m].in}/${byModel[m].out} tok)`).join("\n") || "No usage yet.";
  const ok = await dialog({
    title: "Session cost",
    message: `${fmtCost(c.cost)} across ${c.drafts || 0} drafts.\n\n${lines}`,
    options: [{ label: "Reset session", value: "reset", danger: true }, { label: "Close", value: false, primary: true }],
  });
  if (ok === "reset") { await api("/api/cost/reset", { method: "POST" }); await refreshCost(); toast("Session cost reset"); }
}

/* ================= PIPELINE BOARD (Phase 2) ================= */

async function refreshPipeline() {
  try { state.pipeline = await api("/api/pipeline"); }
  catch (e) { state.pipeline = null; toast(e.message, true); return; }
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
  let data;
  try { data = await api(`/api/voice_stats?kind=${state.perfKind}`); }
  catch (e) { toast(e.message, true); return; }
  const rows = data.voices || [];
  $("#perfEmpty").classList.toggle("hidden", rows.length > 0);
  const best = data.best;
  $("#perfSummary").innerHTML = best
    ? `Best (min n=${data.min_n}): <b>${esc(best.display_name)}</b> ${pctCI(best)}`
    : `<span class="nodata">Not enough data yet — every voice is below the ${data.min_n}-send minimum.</span>`;
  const wrap = $("#perfTableWrap");
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
  try { state.triageData = await api("/api/triage"); }
  catch (e) { toast(e.message, true); return; }
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

/* ================= SNIPPET INSERT (Phase 4b) ================= */

async function loadSnippets() {
  try { state.snippets = (await api("/api/snippets")).snippets || []; }
  catch (e) { state.snippets = []; }
}

function openSnippetPopover(anchorBtn, textarea) {
  closeSnippetPopover();
  const pop = document.createElement("div");
  pop.className = "snippet-popover";
  pop.id = "snippetPopover";
  const search = document.createElement("input");
  search.type = "text"; search.placeholder = "Search snippets…";
  search.style.cssText = "width:100%; margin-bottom:6px; padding:6px 8px; border:1px solid var(--line-strong); border-radius:6px;";
  pop.appendChild(search);
  const list = document.createElement("div");
  pop.appendChild(list);
  const render = (filter) => {
    const items = (state.snippets || []).filter(s => !filter || (s.name + s.text).toLowerCase().includes(filter.toLowerCase()));
    list.innerHTML = "";
    if (!items.length) { list.innerHTML = `<div class="snippet-item"><span class="sn-prev">No snippets. Add them in the Voices editor.</span></div>`; return; }
    items.forEach((s, i) => {
      const it = document.createElement("div");
      it.className = "snippet-item" + (i === 0 ? " sel" : "");
      it.innerHTML = `<div class="sn-name">${esc(s.name)}</div><div class="sn-prev">${esc((s.text || "").slice(0, 80))}</div>`;
      it.onclick = () => { insertAtCursor(textarea, s.text || ""); closeSnippetPopover(); };
      list.appendChild(it);
    });
  };
  render("");
  search.oninput = () => render(search.value);
  search.onkeydown = (e) => {
    const sel = list.querySelector(".snippet-item.sel");
    if (e.key === "Enter" && sel) { e.preventDefault(); sel.click(); }
    else if (e.key === "Escape") { e.preventDefault(); closeSnippetPopover(); }
  };
  const r = anchorBtn.getBoundingClientRect();
  pop.style.left = `${Math.max(8, r.left)}px`;
  pop.style.top = `${r.bottom + window.scrollY + 4}px`;
  document.body.appendChild(pop);
  search.focus();
  setTimeout(() => document.addEventListener("click", closeSnippetOnOutside, true), 0);
}

function closeSnippetOnOutside(e) {
  const pop = $("#snippetPopover");
  if (pop && !pop.contains(e.target)) closeSnippetPopover();
}
function closeSnippetPopover() {
  const pop = $("#snippetPopover");
  if (pop) pop.remove();
  document.removeEventListener("click", closeSnippetOnOutside, true);
}
function insertAtCursor(textarea, text) {
  if (!textarea) return;
  const s = textarea.selectionStart || 0, e = textarea.selectionEnd || 0;
  textarea.value = textarea.value.slice(0, s) + text + textarea.value.slice(e);
  textarea.selectionStart = textarea.selectionEnd = s + text.length;
  textarea.dispatchEvent(new Event("input"));
}

/* ================= VOICES (block-schema editor) ================= */
let META = { experiences: [], tokens: [], fact_scopes: ["recent","target_proofs","situation_read","candidate_evidence","candidate_spine","custom_facts"],
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
    sessionSel.innerHTML = `<option value=""${sessionVoice ? "" : " selected"}>Auto (match by situation)</option>` + opt(sessionVoice);
    defaultSel.innerHTML = opt(state.defaultVoice);
    sessionSel.onchange = async () => {
      try {
        await api("/api/session", { method: "POST", body: { voice: sessionSel.value || null } });
        await refreshStatus();
        toast(sessionSel.value ? `This session: ${voiceLabel(sessionSel.value)}` : "This session: Auto");
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
  candidate_evidence:"my selected evidence", candidate_spine:"my spine", custom_facts:"custom facts" };
const LEN_LABEL = { one_line:"one line", short:"short", medium:"medium", body:"body (full length)" };

function starterBlocks() {
  return [
    { id:"greeting", label:"Greeting", mode:"fixed", text:"Hi {contact_first},", guidance:"", fact_scope:[], length:"short", optional:false },
    { id:"body", label:"Body", mode:"ai", text:"", guidance:"Tie one piece of my evidence to what they need. Lead with wanting to build inside a company rather than evaluate it from outside.", fact_scope:["target_proofs","candidate_evidence","candidate_spine","situation_read"], length:"body", optional:false },
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
  $$("#veVars > div").forEach(r => { const k=(r.querySelector(".var-k").value||"").trim(); const v=r.querySelector(".var-v").value; if (k) o[k]=v; });
  return o;
}

/* ---- token palette ---- */
function renderTokenPalette() {
  const pal = $("#veTokenPalette");
  const chip = (t, kind, title) => `<button class="token-chip ${kind}" data-tok="${esc(t)}" title="${esc(title||"")}">{${esc(t)}}</button>`;
  const research = (META.tokens||[]).filter(t=>t.kind==="research").map(t=>chip(t.token,"research")).join("");
  const exps = (META.tokens||[]).filter(t=>t.kind==="experience").map(t=>chip(t.token,"experience",t.anchor)).join("");
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
  $("#veVars").innerHTML = ""; Object.entries(v.variables || {}).forEach(([k,val]) => addVarRow(k, val));
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
  $("#clearQueueBtn").onclick = clearQueue;
  $("#clearDraftsBtn").onclick = clearDrafts;

  // Find New Targets (Sourcing)
  if ($("#findTargetsBtn")) $("#findTargetsBtn").onclick = openSourcingPanel;
  if ($("#closeSourcingPanelBtn")) $("#closeSourcingPanelBtn").onclick = closeSourcingPanel;
  if ($("#runSourcingBtn")) $("#runSourcingBtn").onclick = runSourcing;

  $("#settingsBtn").onclick = openSettings;
  $("#settingsCancel").onclick = () => $("#settingsModal").classList.add("hidden");
  $("#settingsSave").onclick = saveSettings;
  $("#setProvider").onchange = () => { const stub = $("#setProvider").value === "stub"; $("#setKeyField").style.display = stub ? "none" : ""; };
  $("#setAttachInput").onchange = (e) => { const f = e.target.files[0]; if (f) doUploadAttachment(f); e.target.value = ""; };

  $("#archiveBtn").onclick = openArchive;
  $("#archiveCloseBtn").onclick = () => $("#archiveModal").classList.add("hidden");
  $("#clearArchiveBtn").onclick = clearArchive;

  // primary tab strip: click + roving-arrow keyboard nav
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

  // pipeline filters
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
let sourcingPrompts = [];

async function loadSourcingPrompts() {
  try {
    const r = await api("/api/sourcing_prompts");
    sourcingPrompts = r.prompts || [];
    const sel = $("#sourcingPromptSelect");
    if (!sel) return;
    sel.innerHTML = `<option value="">Default (Hot Startups & Fresh Funding)</option>` +
      sourcingPrompts.map(p => `<option value="${esc(p.id)}">${esc(p.display_name)}</option>`).join("");
  } catch (e) {
    console.error("Failed to load sourcing prompts", e);
  }
}

function openSourcingPanel() {
  const panel = $("#sourcingPanel");
  if (panel) panel.classList.remove("hidden");
  loadSourcingPrompts();
}

function closeSourcingPanel() {
  const panel = $("#sourcingPanel");
  if (panel) panel.classList.add("hidden");
}

async function runSourcing() {
  const promptSelect = $("#sourcingPromptSelect");
  const promptId = (promptSelect && promptSelect.value) || null;
  const statusEl = $("#sourcingStatusText");
  const reportEl = $("#sourcingReport");
  if (statusEl) statusEl.textContent = "🚀 Sourcing run in progress...";
  if (reportEl) reportEl.innerHTML = "";

  try {
    const r = await api("/api/source/research", {
      method: "POST",
      body: { sourcing_prompt_id: promptId }
    });
    const job = r.job;
    if (statusEl) statusEl.textContent = `Completed sourcing run in ${job.stage || 'Completed'}.`;
    renderSourcingReport(job);

    const q = await api("/api/queue");
    state.queue = q.queue;
    renderQueue();
  } catch (e) {
    if (statusEl) statusEl.textContent = "Sourcing run failed: " + e.message;
    toast(e.message, true);
  }
}

function renderSourcingReport(job) {
  const reportEl = $("#sourcingReport");
  if (!reportEl || !job) return;

  const counts = job.counts || {};
  const notes = job.notes || [];
  const candidates = job.candidates || [];
  const addedSlugs = job.added_slugs || [];

  let html = `
    <div style="background: #ffffff; padding: 12px; border-radius: 6px; border: 1px solid #e2e8f0; font-size: 13px;">
      <div style="display: flex; gap: 16px; font-weight: 500; color: #1e293b; margin-bottom: 8px;">
        <span>Checked: ${counts.checked || 0}</span>
        <span style="color: #16a34a;">Queued: ${counts.queued || 0}</span>
        <span style="color: #d97706;">Held for review: ${counts.held || 0}</span>
        <span style="color: #64748b;">Filtered: ${counts.rejected || 0}</span>
      </div>
  `;

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

  const heldCandidates = candidates.filter(c => c.verdict === "needs_review" || c.tier === "Tier 2");
  if (heldCandidates.length) {
    html += `
      <div style="margin-top: 12px; padding-top: 8px; border-top: 1px solid #f1f5f9;">
        <div style="font-weight: 500; font-size: 12px; color: #334155; margin-bottom: 4px;">Held for review (${heldCandidates.length}):</div>
        ${heldCandidates.map(c => `
          <div style="font-size: 12px; margin-top: 4px; display: flex; justify-content: space-between; align-items: center;">
            <span><strong>${esc(c.name)}</strong> — ${esc(c.reject_reason || c.fit?.why_fit || 'Review required')}</span>
            <button class="btn ghost small add-held-btn" data-slug="${esc(c.canon_slug)}">Add to queue</button>
          </div>
        `).join('')}
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
        const q = await api("/api/queue");
        state.queue = q.queue;
        renderQueue();
        reportEl.innerHTML = "";
      } catch (e) {
        toast(e.message, true);
      }
    };
  }

  $$(".add-held-btn").forEach(btn => {
    btn.onclick = async () => {
      const slug = btn.dataset.slug;
      try {
        await api(`/api/source/research/${job.job_id}/add`, {
          method: "POST",
          body: { slugs: [slug] }
        });
        toast("Added to queue");
        btn.disabled = true;
        btn.textContent = "Added";
        const q = await api("/api/queue");
        state.queue = q.queue;
        renderQueue();
      } catch (e) {
        toast(e.message, true);
      }
    };
  });
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
    toast("Cannot save: Candidate profile is not loaded", true);
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
    title: "Reset Candidate Profile?",
    message: "This will revert your candidate profile template to default values. Continue?",
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

async function boot() {
  if ($("#profileBtn")) $("#profileBtn").onclick = openProfileModal;
  if ($("#closeProfileModal")) $("#closeProfileModal").onclick = closeProfileModal;
  if ($("#profileForm")) $("#profileForm").onsubmit = async (e) => { e.preventDefault(); await saveProfile(); };
  if ($("#resetProfileBtn")) $("#resetProfileBtn").onclick = async () => { await resetProfile(); };
  wire();
  try {
    await refreshStatus();
    await fetchVoices();
    await fetchMeta();
    await refreshAttachmentsPanel();
    await api("/api/queue").then(r => { state.queue = r.queue; renderQueue(); });
    await refreshDrafts();
    await updateFollowupsBadge();
    await refreshCost();
    await loadSnippets();
    try { const t = await api("/api/triage"); state.triageData = t; updateTriageBadge(); } catch (e) {}
    showView("workspace");
    if (needsKey(state.status)) openStartup();
  } catch (e) {
    toast("Could not reach the local server: " + e.message, true);
  }
}
document.addEventListener("DOMContentLoaded", boot);
