"use strict";

/* ------------------------------------------------------------------ helpers */
const $ = (sel, el = document) => el.querySelector(sel);
const view = $("#view");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (p) => (p == null ? "" : `${Math.round(p * 100)}%`);

let STATIC_MODE = false;   // GitHub Pages: no server; web/static.js answers /api calls from your decrypted data

async function api(path, opts = {}) {
  if (STATIC_MODE) return Static.api(path, opts);
  const init = { ...opts, headers: { ...(opts.headers || {}) } };
  if (opts.json !== undefined) {
    init.method = init.method || "POST";
    init.body = JSON.stringify(opts.json);
    init.headers["Content-Type"] = "application/json";
  }
  const r = await fetch(path, init);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (_) { /* not json */ }
    throw new Error(msg);
  }
  return r.headers.get("content-type")?.includes("json") ? r.json() : r.text();
}

let toastTimer;
function toast(msg, action) {
  const t = $("#toast");
  t.innerHTML = `<span>${esc(msg)}</span>`;
  if (action) {
    const b = document.createElement("button");
    b.textContent = action.label;
    b.onclick = () => { t.classList.remove("show"); action.run(); };
    t.append(b);
  }
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), action ? 5000 : 2600);
}

function ago(iso) {
  if (!iso) return "";
  const d = Math.round((Date.now() - new Date(iso).getTime()) / 864e5);
  if (d <= 0) return "today";
  if (d === 1) return "yesterday";
  if (d < 30) return `${d}d ago`;
  return `${Math.round(d / 30)}mo ago`;
}

const ICON = {
  x: '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg>',
  check: '<svg viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
  up: '<svg viewBox="0 0 24 24"><path d="M12 19V5M6 11l6-6 6 6"/></svg>',
  undo: '<svg viewBox="0 0 24 24"><path d="M9 14L4 9l5-5"/><path d="M4 9h10a6 6 0 010 12h-3"/></svg>',
  ext: '<svg viewBox="0 0 24 24"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 01-1 1H5a1 1 0 01-1-1V7a1 1 0 011-1h5"/></svg>',
  trash: '<svg viewBox="0 0 24 24"><path d="M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12"/></svg>',
};

/* ------------------------------------------------------------------ status / nav */
let status = null;
const E = () => status?.jev?.engine || "Jev";  // name of the decision engine (Jev, Kev-4B, ...)
async function refreshStatus() {
  try { status = await api("/api/status"); } catch (_) { return; }
  const pill = $("#jev-pill");
  pill.className = "jev-pill " + (status.jev.available ? "on" : "off");
  const es = status.jev.engine_state;
  pill.textContent = !status.jev.available ? "No engine configured"
    : `${status.jev.engine} ● ${es === "ready" ? "loaded" : es === "loading" ? "loading…" : es === "stopping" ? "unloading…" : es === "error" ? "failed to start" : status.jev.local ? "idle (loads when needed)" : "online"}`;
  pill.title = status.jev.local ? "Runs on this Mac. Loaded only during runs and profile builds; unloads after 5 idle minutes." : "Hosted engine";
  document.querySelectorAll(".seg button[data-mode] b").forEach((b) => (b.textContent = status.counts[b.parentElement.dataset.mode] || 0));
  const n = status.counts.new || 0;
  const b = $("#badge-new");
  b.hidden = !n; b.textContent = n > 99 ? "99+" : n;
  const apps = await api("/api/applications").catch(() => []);
  const a = $("#badge-apps");
  a.hidden = !apps.length; a.textContent = apps.length;
}

const routes = { profile: renderProfile, applications: renderApplications, tracker: renderTracker };
function route() {
  const name = (location.hash.replace("#/", "") || "applications").split("?")[0];
  const fn = routes[name] || renderApplications;
  document.querySelectorAll(".tabs a").forEach((a) => a.classList.toggle("active", a.dataset.tab === name));
  cleanupDeck();
  fn();
  view.focus({ preventScroll: true });
}
window.addEventListener("hashchange", route);

/* ================================================================== APPLICATIONS (swipe deck) */
let deck = { mode: "new", jobs: [], history: [] };
let keyHandler = null;

function cleanupDeck() {
  if (keyHandler) document.removeEventListener("keydown", keyHandler);
  keyHandler = null;
}

async function renderApplications() {
  view.innerHTML = `<div class="empty"><p class="muted">Loading jobs…</p></div>`;
  const [jobs, counts] = await Promise.all([
    api(`/api/jobs?status=${deck.mode}`),
    api("/api/status").then((s) => s.counts),
  ]);
  deck.jobs = jobs;
  const tab = (key, label) =>
    `<button data-mode="${key}" class="${deck.mode === key ? "active" : ""}">${label}<b>${counts[key] || 0}</b></button>`;
  view.innerHTML = `
    <div class="deck-head">
      <div class="seg" role="tablist">${tab("new", "To review")}${tab("later", "Later")}${tab("rejected", "Passed")}${tab("applied", "Applied")}</div>
      <span class="faint" style="font-size:13px">Ranked by match score</span>
    </div>
    <div id="deck-area"></div>`;
  view.querySelectorAll(".seg button").forEach((b) => (b.onclick = () => { deck.mode = b.dataset.mode; renderApplications(); }));
  if (deck.mode === "new" || deck.mode === "later") drawDeck();
  else drawList();
}

function drawList() {
  const area = $("#deck-area");
  if (!deck.jobs.length) {
    area.innerHTML = `<div class="empty"><h2>Nothing here</h2><p>Jobs you ${deck.mode === "applied" ? "apply to" : "pass on"} show up here.</p></div>`;
    return;
  }
  area.innerHTML = `<div class="list">${deck.jobs.map((j) => `
    <div class="card-box row-job">
      ${ring(j.card)}
      <div class="grow"><div class="t">${esc(j.title)}</div><div class="muted" style="font-size:13px">${esc(j.company)} · ${esc(j.location || "")}</div></div>
      <a class="btn small" href="${esc(j.url)}" target="_blank" rel="noopener">Open ${ICON.ext.replace("<svg", '<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"')}</a>
      <button class="btn small" data-undo="${j.id}">Back to review</button>
    </div>`).join("")}</div>`;
  area.querySelectorAll("[data-undo]").forEach((b) => (b.onclick = async () => {
    await api(`/api/jobs/${b.dataset.undo}/decision`, { json: { action: "undo" } });
    toast("Moved back to review");
    renderApplications(); refreshStatus();
  }));
}

function ring(card) {
  const m = card?.match ?? 0;
  return `<div class="ring ${card?.tier || "good"}" style="--p:${m}"><span>${m}</span></div>`;
}

function cardHTML(j) {
  const c = j.card || {};
  const logo = j.logo ? `<img src="${esc(j.logo)}" alt="" onerror="this.remove()">` : "";
  const initial = esc((j.company || "?").trim()[0]?.toUpperCase());
  const tierLabel = { strong: "Strong match", good: "Good match", stretch: "Stretch" }[c.tier] || "Match";
  const conf = c.confidence == null ? "" : ` · confidence ${pct(c.confidence)}`;
  return `
    <div class="stamp apply">APPLY</div><div class="stamp pass">PASS</div><div class="stamp later">LATER</div>
    <div class="jc-scroll">
      <div class="jc-top">
        <div class="avatar">${logo || initial}</div>
        <div><div class="jc-company">${esc(j.company)}</div>
        <div class="jc-meta">${esc((j.location || "Location n/a").slice(0, 80))}${j.posted_at ? " · " + ago(j.posted_at) : ""}</div></div>
      </div>
      <h2 class="jc-title">${esc(j.title)}</h2>
      <div class="jc-match">
        ${ring(c)}
        <div><div class="verdict">${tierLabel}${c.unsure ? ' <span class="chip warn" title="The engine spread its probability across options — double-check this one">Unsure</span>' : ""}</div>
        <div class="sub">${c.scored_by === "jev" ? `Scored by ${esc(E())}` : "Heuristic score"}${conf}</div></div>
      </div>
      <div class="chips">${(c.chips || []).map((x, i) => `<span class="chip ${i === 0 ? (c.location_ok ? "good" : "warn") : ""}">${esc(x)}</span>`).join("")}${j.salary ? `<span class="chip info">${esc(j.salary)}</span>` : ""}</div>
      ${c.lead_project ? `<div class="jc-block lead">💡 <span>Lead your application with <b>${esc(c.lead_project)}</b></span></div>` : ""}
      <div class="jc-block"><h4>Why it was picked</h4><ul class="reasons">${(c.reasons || []).map((r) => `<li>${esc(r)}</li>`).join("")}</ul></div>
      ${c.skills_matched?.length ? `<div class="jc-block"><h4>Your matching skills</h4><div class="chips">${c.skills_matched.map((s) => `<span class="chip good">${esc(s)}</span>`).join("")}</div></div>` : ""}
      ${c.skills_gap?.length ? `<div class="jc-block"><h4>Gaps they ask for</h4><div class="chips">${c.skills_gap.map((s) => `<span class="chip bad">${esc(s)}</span>`).join("")}</div></div>` : ""}
      <div class="jc-block"><details class="desc"><summary>Full description</summary><pre>${esc(j.description || "No description provided.")}</pre></details></div>
    </div>
    <div class="jc-foot">
      <span class="src">via ${esc(j.source)}</span>
      <a class="btn small primary" href="${esc(j.url)}" target="_blank" rel="noopener">Open posting ${ICON.ext.replace("<svg", '<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.2"')}</a>
    </div>`;
}

function drawDeck() {
  const area = $("#deck-area");
  if (!deck.jobs.length) {
    const hasProfile = status?.has_profile;
    area.innerHTML = `<div class="empty">
      <h2>${deck.mode === "later" ? "Nothing saved for later" : "You're all caught up"}</h2>
      <p>${deck.mode === "later" ? "Swipe up on a card to save it here." : hasProfile ? "Run the pipeline to fetch and score fresh jobs." : "Start by building your profile from your resume."}</p>
      ${deck.mode === "new" ? `<a class="btn primary" href="#/profile">${hasProfile ? "Run pipeline" : "Build profile"}</a>` : ""}
    </div>`;
    return;
  }
  area.innerHTML = `
    <div class="deck-wrap"><div class="deck" id="deck"></div></div>
    <div class="actions">
      <button class="act small" id="a-undo" title="Undo (Z)" aria-label="Undo">${ICON.undo}</button>
      <button class="act pass" id="a-pass" title="Pass (←)" aria-label="Pass">${ICON.x}</button>
      <button class="act later" id="a-later" title="Later (↑)" aria-label="Save for later">${ICON.up}</button>
      <button class="act apply" id="a-apply" title="Apply (→)" aria-label="Apply">${ICON.check}</button>
    </div>
    <p class="hint"><kbd>←</kbd> pass · <kbd>↑</kbd> later · <kbd>→</kbd> apply (opens the posting &amp; adds it to Tracker) · <kbd>Z</kbd> undo</p>`;
  stackCards();
  $("#a-pass").onclick = () => fling("reject");
  $("#a-apply").onclick = () => fling("apply");
  $("#a-later").onclick = () => fling("later");
  $("#a-undo").onclick = undo;
  $("#a-later").hidden = deck.mode === "later";
  keyHandler = (e) => {
    if (e.target.matches("input, textarea, select")) return;
    if (e.key === "ArrowLeft") fling("reject");
    else if (e.key === "ArrowRight") fling("apply");
    else if (e.key === "ArrowUp" && deck.mode !== "later") { e.preventDefault(); fling("later"); }
    else if (e.key.toLowerCase() === "z") undo();
  };
  document.addEventListener("keydown", keyHandler);
}

function stackCards() {
  const el = $("#deck");
  if (!el) return;
  el.innerHTML = "";
  if (!deck.jobs.length) { drawDeck(); return; }
  deck.jobs.slice(0, 3).reverse().forEach((j, idx, arr) => {
    const depth = arr.length - 1 - idx;
    const c = document.createElement("article");
    c.className = "jobcard" + (depth === 1 ? " behind" : depth === 2 ? " behind2" : "");
    c.innerHTML = cardHTML(j);
    c.dataset.id = j.id;
    el.append(c);
    if (depth === 0) attachDrag(c);
  });
}

function attachDrag(card) {
  let sx = 0, sy = 0, dx = 0, dy = 0, dragging = false, pid = null;
  const stamps = { apply: $(".stamp.apply", card), pass: $(".stamp.pass", card), later: $(".stamp.later", card) };
  card.addEventListener("pointerdown", (e) => {
    if (e.target.closest("a, button, summary, pre, .jc-block details[open]")) return;
    dragging = true; pid = e.pointerId; sx = e.clientX; sy = e.clientY; dx = dy = 0;
    card.setPointerCapture(pid);
    card.classList.add("dragging");
  });
  card.addEventListener("pointermove", (e) => {
    if (!dragging || e.pointerId !== pid) return;
    dx = e.clientX - sx; dy = e.clientY - sy;
    card.style.transform = `translate(${dx}px, ${dy}px) rotate(${dx / 18}deg)`;
    stamps.apply.style.opacity = Math.max(0, Math.min(1, dx / 110));
    stamps.pass.style.opacity = Math.max(0, Math.min(1, -dx / 110));
    stamps.later.style.opacity = deck.mode === "later" ? 0 : Math.max(0, Math.min(1, (-dy - Math.abs(dx)) / 110));
  });
  const end = () => {
    if (!dragging) return;
    dragging = false;
    card.classList.remove("dragging");
    const w = card.offsetWidth;
    if (dx > w * 0.33) return fling("apply");
    if (dx < -w * 0.33) return fling("reject");
    if (-dy > 140 && Math.abs(dx) < 120 && deck.mode !== "later") return fling("later");
    card.style.transform = "";
    Object.values(stamps).forEach((s) => (s.style.opacity = 0));
  };
  card.addEventListener("pointerup", end);
  card.addEventListener("pointercancel", end);
}

let flinging = false;
async function fling(action) {
  if (flinging || !deck.jobs.length) return;
  const card = $("#deck .jobcard:last-child");
  const job = deck.jobs[0];
  if (!card || !job) return;
  flinging = true;
  if (action === "apply") window.open(job.url, "_blank", "noopener"); // in the user gesture, so not blocked
  const out = { apply: "translate(140%, 40px) rotate(24deg)", reject: "translate(-140%, 40px) rotate(-24deg)", later: "translate(0, -130%)" }[action];
  const stamp = { apply: ".stamp.apply", reject: ".stamp.pass", later: ".stamp.later" }[action];
  $(stamp, card).style.opacity = 1;
  card.style.transform = out;
  card.style.opacity = 0;
  try {
    await api(`/api/jobs/${job.id}/decision`, { json: { action } });
  } catch (e) {
    toast(`Couldn't save: ${e.message}`);
    card.style.transform = ""; card.style.opacity = 1; flinging = false;
    return;
  }
  deck.history.push({ job, action });
  setTimeout(() => {
    deck.jobs.shift();
    stackCards();
    flinging = false;
  }, 260);
  const msg = { apply: "Opened posting · added to Tracker", reject: "Passed", later: "Saved for later" }[action];
  toast(msg, { label: "Undo", run: undo });
  refreshStatus();
}

async function undo() {
  const last = deck.history.pop();
  if (!last) return toast("Nothing to undo");
  await api(`/api/jobs/${last.job.id}/decision`, { json: { action: deck.mode === "later" ? "later" : "undo" } });
  if (last.action === "apply") {
    const apps = await api("/api/applications");
    const row = apps.find((a) => a.job_id === last.job.id);
    if (row) await api(`/api/applications/${row.id}`, { method: "DELETE" });
  }
  deck.jobs.unshift(last.job);
  stackCards();
  refreshStatus();
}

/* ================================================================== PROFILE */
let pollTimer = null;

async function renderProfile() {
  const [profile, settings, st] = await Promise.all([api("/api/profile"), api("/api/settings"), api("/api/status")]);
  status = st;
  const noKey = !st.jev.available ? `<div class="warnbox"><b>No decision engine is configured.</b> Start the local Kev engine with <code>./run.sh</code> (see <code>.env</code>), or add a TypeSafe/OpenRouter key. Until then a basic keyword heuristic is used.</div>` : "";

  if (!profile) {
    view.innerHTML = `${noKey}<div class="card-box empty">
      <h2>Build your profile</h2>
      <p>${E()} reads your resume and decides which keywords to keep, your field and level, and which job titles to search for.</p>
      <div class="row" style="display:flex;gap:10px;justify-content:center;flex-wrap:wrap">
        <button class="btn primary" id="p-build">Use Devesh_Patel_Resume.tex</button>
        <label class="btn">Upload resume…<input type="file" id="p-upload" accept=".pdf,.tex,.txt,.md" hidden></label>
      </div></div>`;
    $("#p-build").onclick = () => buildProfile();
    $("#p-upload").onchange = (e) => buildProfile(e.target.files[0]);
    return;
  }

  const fieldLabel = (k) => k.replace(/_/g, " ").replace(/\b\w/g, (m) => m.toUpperCase());
  const kept = profile.keywords.filter((k) => k.keep).length;
  view.innerHTML = `${noKey}
  <div class="grid-2">
    <div class="stack">
      <section class="card-box me">
        <img class="photo" src="/api/avatar" alt="" onerror="this.style.visibility='hidden'">
        <h1>${esc(profile.name)}</h1>
        <div class="headline">${esc(profile.headline)}</div>
        <div class="row"${STATIC_MODE ? ' hidden' : ''}>
          <button class="btn small" id="p-rebuild">${profile.scored_by === "jev" ? `Re-run ${E()} on resume` : `Build with ${E()}`}</button>
          <label class="btn small">Upload new resume<input type="file" id="p-upload" accept=".pdf,.tex,.txt,.md" hidden></label>
        </div>
        <dl class="kv">
          <dt>Resume</dt><dd>${esc(profile.resume_file)}</dd>
          <dt>Location</dt><dd>${esc(profile.location)}</dd>
          <dt>Email</dt><dd>${esc(profile.email)}</dd>
          <dt>Profile by</dt><dd>${profile.scored_by === "jev" ? `<span class="chip good">${esc(E())}</span>` : '<span class="chip warn">Heuristic</span>'}</dd>
        </dl>
      </section>
      <section class="card-box">
        <h3 class="section-title">Projects to point to</h3>
        <div class="proj">${Object.entries(profile.projects || {}).map(([n, d]) => `<div><b>${esc(n)}</b><span class="muted">${esc(d || "")}</span></div>`).join("")}</div>
      </section>
    </div>

    <div class="stack">
      <section class="card-box">
        <h3 class="section-title">Pipeline</h3>
        <div class="checks" id="s-sources">${["remotive", "himalayas", "jobicy", "weworkremotely", "arbeitnow", "hackernews", "greenhouse", "ashby", "lever"].map((s) =>
          `<label><input type="checkbox" value="${s}" ${settings.sources.includes(s) ? "checked" : ""}> ${s}</label>`).join("")}</div>
        <div class="range-row"><label for="s-min">Min match</label><input type="range" id="s-min" min="0" max="95" step="5" value="${settings.min_match}"><b id="s-min-v">${settings.min_match}</b></div>
        <div class="range-row"><label for="s-age">Max age (days)</label><input type="range" id="s-age" min="7" max="120" step="1" value="${settings.max_age_days}"><b id="s-age-v">${settings.max_age_days}</b></div>
        <div class="range-row"><label for="s-deep">Deep evals / run</label><input type="range" id="s-deep" min="25" max="1000" step="25" value="${settings.max_deep}"><b id="s-deep-v">${settings.max_deep}</b></div>
        <div class="range-row"><label for="s-daily">Daily run at</label>
          <div style="display:flex;gap:10px;align-items:center"><input class="input" type="time" id="s-daily" value="${esc(settings.daily_at || "")}" style="width:auto">
          <label class="checks"><label><input type="checkbox" id="s-daily-on" ${settings.daily_at ? "checked" : ""}> on</label></label></div><span></span></div>
        <p class="faint" style="font-size:12.5px;margin:0 0 10px">${STATIC_MODE
          ? "Runs on GitHub Actions at this time (your timezone) every day — your devices can be off. Only new postings are processed."
          : "Runs automatically while the app is open; if the Mac was off or the app closed at that time, it catches up when you next start it. Only new postings are processed."}</p>
        <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:6px">
          <button class="btn primary" id="run">Run pipeline</button>
          <label class="checks"><label><input type="checkbox" id="reeval"> Re-evaluate jobs already on the board</label></label>
        </div>
        <div id="run-status"></div>
      </section>

      <section class="card-box" id="kaggle-box"></section>

      <section class="card-box">
        <h3 class="section-title">${E()}'s read of you</h3>
        <div class="field-row"><label>Level</label>
          <select class="input" id="p-level">${Object.entries(profile.level_labels).map(([k, v]) => `<option value="${k}" ${k === profile.level ? "selected" : ""}>${fieldLabel(k)} — ${esc(v.split(";")[0])}</option>`).join("")}</select></div>
        <div class="field-row"><label>Country</label>
          <select class="input" id="p-country">${profile.countries.concat(profile.countries.includes(profile.country) ? [] : [profile.country]).filter(Boolean).map((c) => `<option ${c === profile.country ? "selected" : ""}>${esc(c)}</option>`).join("")}</select></div>
        <div class="field-row"><label>Goal</label><input class="input" id="p-goal" value="${esc(profile.goal)}"></div>
        <h3 class="section-title" style="margin-top:18px">Field ${profile.level_confidence != null ? `<span class="faint" style="text-transform:none;letter-spacing:0">· level confidence ${pct(profile.level_confidence)}</span>` : ""}</h3>
        <div class="bars">${profile.fields.map((f) => `<div class="bar"><span>${fieldLabel(f.name)}</span><div class="track"><div class="fill" style="width:${(f.p ?? 0.5) * 100}%"></div></div><span class="v">${f.p == null ? "—" : pct(f.p)}</span></div>`).join("")}</div>
      </section>

      <section class="card-box">
        <h3 class="section-title">Search terms <span class="faint" style="text-transform:none;letter-spacing:0">· ${E()} picked the highlighted titles; click to toggle</span></h3>
        <div class="chips" id="p-terms">${profile.search_terms.map((t, i) => `<button class="chip toggle-chip ${t.keep ? "on core" : ""}" data-i="${i}">${esc(t.name)} ${t.p != null ? `<small>${pct(t.p)}</small>` : ""}</button>`).join("")}</div>
        <div class="add-row"><input class="input" id="p-term-new" placeholder="Add a search term, e.g. “Python Backend Developer”"><button class="btn" id="p-term-add">Add</button></div>
      </section>

      <section class="card-box">
        <h3 class="section-title">Keywords · ${kept} kept of ${profile.keywords.length}</h3>
        <div class="chips" id="p-kws">${profile.keywords.map((k, i) => `<button class="chip toggle-chip ${k.keep ? "on" : ""} ${k.core ? "core" : ""}" data-i="${i}" title="${k.used == null ? "" : `used in projects/work: ${pct(k.used)} · commonly required: ${pct(k.market)}`}">${esc(k.name)}</button>`).join("")}</div>
        <p class="legend">Blue = core (${E()} sees it used in your work) · grey = kept · struck-through = dropped. Kept keywords are what jobs get matched against.</p>
      </section>
    </div>
  </div>`;

  $("#p-rebuild").onclick = () => buildProfile();
  $("#p-upload").onchange = (e) => buildProfile(e.target.files[0]);
  const save = async (patch) => { await api("/api/profile", { method: "PATCH", json: patch }); toast("Profile saved"); };
  $("#p-level").onchange = (e) => save({ level: e.target.value });
  $("#p-country").onchange = (e) => save({ country: e.target.value });
  $("#p-goal").onchange = (e) => save({ goal: e.target.value });
  $("#p-terms").onclick = (e) => {
    const b = e.target.closest("button"); if (!b) return;
    const t = profile.search_terms[+b.dataset.i]; t.keep = !t.keep;
    b.classList.toggle("on", t.keep); b.classList.toggle("core", t.keep);
    save({ search_terms: profile.search_terms });
  };
  $("#p-term-add").onclick = () => {
    const v = $("#p-term-new").value.trim(); if (!v) return;
    profile.search_terms.unshift({ name: v, p: null, keep: true });
    save({ search_terms: profile.search_terms }).then(renderProfile);
  };
  $("#p-kws").onclick = (e) => {
    const b = e.target.closest("button"); if (!b) return;
    const k = profile.keywords[+b.dataset.i]; k.keep = !k.keep;
    b.classList.toggle("on", k.keep);
    save({ keywords: profile.keywords });
  };

  const bindRange = (id, key) => {
    const el = $(`#${id}`);
    el.oninput = () => ($(`#${id}-v`).textContent = el.value);
    el.onchange = async () => {
      const r = await api("/api/settings", { method: "PATCH", json: { [key]: +el.value } });
      toast(r.rescored_on_board != null ? `Re-scored — ${r.rescored_on_board} jobs now on the board` : "Saved");
      refreshStatus();
    };
  };
  bindRange("s-min", "min_match"); bindRange("s-age", "max_age_days"); bindRange("s-deep", "max_deep");
  const saveDaily = async () => {
    const on = $("#s-daily-on").checked, at = $("#s-daily").value || "09:00";
    await api("/api/settings", { method: "PATCH", json: { daily_at: on ? at : "" } });
    toast(on ? `Daily run set for ${at}` : "Daily run turned off");
  };
  $("#s-daily").onchange = () => { $("#s-daily-on").checked = true; saveDaily(); };
  $("#s-daily-on").onchange = saveDaily;
  $("#s-sources").onchange = () => {
    const sources = [...view.querySelectorAll("#s-sources input:checked")].map((i) => i.value);
    api("/api/settings", { method: "PATCH", json: { sources } }).then(() => toast("Sources saved"));
  };
  $("#run").onclick = async () => {
    try { await api("/api/pipeline/run", { json: { reevaluate: $("#reeval").checked } }); }
    catch (e) { return toast(e.message); }
    pollRun();
  };
  drawRunStatus(st.pipeline, st.last_run);
  if (st.pipeline.running) pollRun();
  renderKaggle(settings);
  if (STATIC_MODE) renderSync(); else renderGithub();
}

/* ------------------------------------------------------------------ Mac app: publish to GitHub */
async function renderGithub() {
  let box = $("#github-box");
  if (!box) {
    box = Object.assign(document.createElement("section"), { className: "card-box", id: "github-box" });
    $("#kaggle-box").after(box);
  }
  const g = await api("/api/github");
  if (g.enabled) {
    box.innerHTML = `
      <h3 class="section-title">GitHub</h3>
      <p style="margin:0 0 6px">Synced with <b>${esc(g.owner)}/${esc(g.repo)}</b> · web app: <a href="${esc(g.site)}" target="_blank" rel="noopener">${esc(g.site)}</a></p>
      ${g.pages_manual ? `<p class="warnbox">One step left for the web app: on GitHub open <a href="https://github.com/${esc(g.owner)}/${esc(g.repo)}/settings/pages" target="_blank" rel="noopener">Settings → Pages</a> and set <b>Source: GitHub Actions</b>, then re-run the <i>Pages</i> workflow from the Actions tab. (Your token can't do this itself — it has no Pages permission.) Daily runs already work without it.</p>` : ""}
      <p class="faint" style="font-size:13px;margin:0 0 12px">Daily runs happen on GitHub Actions (Kev on Kaggle). This Mac syncs your data every 5 minutes and a few seconds after each change.
        Last sync: ${esc((g.last_sync || "never").replace("T", " "))} UTC${g.last_error ? ` · <span style="color:var(--bad)">${esc(g.last_error)}</span>` : ""}</p>
      <div style="display:flex;gap:10px;flex-wrap:wrap"><button class="btn" id="g-sync">Sync now</button>
        <button class="btn" id="g-update" title="Re-upload the app code and workflows, refresh secrets, and start a run">Update app on GitHub</button>
        <button class="btn" id="g-off">Disconnect</button></div>`;
    $("#g-update").onclick = async (e) => {
      e.target.disabled = true; e.target.textContent = "Updating…";
      try { await api("/api/github/publish", { json: { repo: `${g.owner}/${g.repo}` } }); toast("Updated — the new code is on GitHub and a run has started"); }
      catch (err) { toast(err.message); }
      renderGithub();
    };
    $("#g-sync").onclick = async () => { const r = await api("/api/github/sync", { method: "POST" }); toast(r.ok ? "Synced" : `Sync failed: ${r.detail}`); renderGithub(); };
    $("#g-off").onclick = async () => { await api("/api/github/disconnect", { method: "POST" }); toast("GitHub sync off — runs happen on this Mac again"); renderGithub(); };
    return;
  }
  box.innerHTML = `
    <h3 class="section-title">Publish to GitHub</h3>
    <p class="faint" style="font-size:13px;margin-top:0">Puts the web app on GitHub Pages and moves daily runs to GitHub Actions (Kev on Kaggle), so they happen even when this Mac is off.
      Your data is encrypted with your passphrase before it leaves this Mac — the repo can be public.</p>
    <form id="g-form" class="stack" autocomplete="off" style="gap:10px">
      <div class="field-row"><label>Repository</label><input class="input" name="repo" value="${g.owner ? esc(g.owner + "/" + g.repo) : ""}" placeholder="your-username/job-board (create it empty first)" required></div>
      <div class="field-row"><label>Token</label><input class="input" name="token" type="password" autocomplete="off"
        placeholder="${g.token_hint ? `saved ${esc(g.token_hint)} — leave blank to reuse` : "fine-grained token, this repo only"}" ${g.token_hint ? "" : "required"}></div>
      <p class="faint" style="font-size:12.5px;margin:0">Create one at <a href="https://github.com/settings/personal-access-tokens/new" target="_blank" rel="noopener">github.com → Settings → Fine-grained tokens</a>
        with <b>Only select repositories</b> → your repo, and <b>Read and write</b> on: Contents, Actions, Secrets, Workflows, Pages.</p>
      ${g.has_key ? `<p class="faint" style="font-size:13px;margin:0">✓ This Mac already holds the encryption key for <b>${esc(g.owner)}/${esc(g.repo)}</b> from your earlier attempt — no passphrase needed. Just press Publish.</p>` : ""}
      <div class="field-row" ${g.has_key ? "hidden" : ""}><label>Passphrase</label>
        <div>
          <div class="phrase" id="g-phrase" aria-live="polite">—</div>
          <div id="g-tools" style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px">
            <button type="button" class="btn small" id="g-new">Generate another</button>
            <button type="button" class="btn small" id="g-copy">Copy</button>
            <button type="button" class="btn small" id="g-dl">Download as .txt</button></div>
          <button type="button" class="btn small" id="g-restart" hidden style="margin-top:8px">Start over with a new passphrase</button>
        </div></div>
      <p class="faint" id="g-help" style="font-size:12.5px;margin:0" ${g.has_key ? "hidden" : ""}>This passphrase is your password for the web app on every device. It's randomly generated
        (6 words ≈ 77 bits) because the encrypted data sits in a public repo. <b>Nobody can recover it for you</b> — save it in a password manager or write it down.</p>
      <label class="checks" id="g-saved-row" ${g.has_key ? "hidden" : ""}><label><input type="checkbox" id="g-saved"> I've saved this passphrase somewhere safe</label></label>
      <div class="field-row" id="g-confirm-row" hidden><label>Type it back</label>
        <input class="input" name="again" autocomplete="off" spellcheck="false" autocapitalize="off" placeholder="from where you saved it — it's hidden now"></div>
      <details id="g-own-box" class="faint" style="font-size:13px" ${g.has_key ? "hidden" : ""}><summary>Use a passphrase I already have</summary>
        <p style="margin:6px 0">For connecting this Mac to a repo you already published, or reusing a phrase you saved earlier.</p>
        <input class="input" name="own" type="password" autocomplete="off" placeholder="your saved passphrase"></details>
      <div><button class="btn primary" id="g-go" disabled>Publish</button> <span class="faint" id="g-msg" aria-live="polite"></span></div>
      ${g.publish_error ? `<p style="color:var(--bad);font-size:13px;margin:0">Last attempt failed: ${esc(g.publish_error)}</p>` : ""}
    </form>`;
  const f = $("#g-form");
  let phrase = "", confirmed = false;
  const norm = (p) => p.toLowerCase().split(/\s+/).filter(Boolean).join(" ");
  const own = () => norm(f.own.value);
  const check = () => { $("#g-go").disabled = !(g.has_key || own() || confirmed); };
  const show = (mode) => {                       // "shown" → "hidden" (retype it) → "confirmed"
    $("#g-phrase").textContent = mode === "shown" ? phrase
      : mode === "hidden" ? "•••••• hidden — type it back from where you saved it"
      : "✓ Confirmed and saved on this Mac until publishing finishes";
    $("#g-phrase").classList.toggle("phrase-hidden", mode !== "shown");
    $("#g-tools").hidden = mode !== "shown";
    $("#g-restart").hidden = mode !== "confirmed";
    $("#g-help").hidden = mode === "confirmed";
    $("#g-saved-row").hidden = mode === "confirmed";
    $("#g-confirm-row").hidden = mode !== "hidden";
    if (mode === "hidden") { f.again.value = ""; f.again.focus(); }
    check();
  };
  const load = async (fresh) => {
    const r = await api(`/api/github/passphrase${fresh ? "?new=1" : ""}`);
    confirmed = !!r.confirmed;
    phrase = r.passphrase || "";
    $("#g-saved").checked = false;
    show(confirmed ? "confirmed" : "shown");
  };
  $("#g-new").onclick = () => load(true);
  $("#g-restart").onclick = () => load(true);
  $("#g-copy").onclick = async () => { try { await navigator.clipboard.writeText(phrase); toast("Copied — paste it into your password manager"); } catch (_) { toast("Couldn't copy — select the words and copy them"); } };
  $("#g-dl").onclick = () => {
    const txt = `Job Board passphrase (${new Date().toISOString().slice(0, 10)})\n\n${phrase}\n\nRepository: ${f.repo.value || "(your repo)"}\nUse it to unlock the web app. Keep this file private.\n`;
    Object.assign(document.createElement("a"), { download: "job-board-passphrase.txt",
      href: URL.createObjectURL(new Blob([txt], { type: "text/plain" })) }).click();
  };
  $("#g-saved").onchange = () => show($("#g-saved").checked ? "hidden" : "shown");
  f.again.oninput = async () => {
    if (norm(f.again.value) !== norm(phrase)) return;
    try {
      await api("/api/github/passphrase/confirm", { json: { passphrase: f.again.value } });
      confirmed = true;
      phrase = "";                                 // no longer kept in the page
      show("confirmed");
      toast("Passphrase confirmed and saved on this Mac");
    } catch (e) { toast(e.message); }
  };
  f.own.oninput = check;
  if (!g.has_key) load(false); else check();
  f.onsubmit = async (e) => {
    e.preventDefault();
    if (!g.has_key && !own() && !confirmed) return toast("Confirm the passphrase first");
    $("#g-go").disabled = true;
    $("#g-msg").textContent = "Publishing… (uploads the app, creates the encrypted data branch, saves secrets)";
    try {
      const r = await api("/api/github/publish", { json: { repo: f.repo.value, token: f.token.value, passphrase: g.has_key ? "" : own() } });   // blank → the Mac uses the confirmed one
      toast(`Published — web app at ${r.site} (Pages takes a minute or two)`);
    } catch (err) { toast(err.message); }
    renderGithub();
  };
}

/* ------------------------------------------------------------------ web app: sync & security */
function renderSync() {
  const box = document.createElement("section");
  box.className = "card-box";
  const st = Static.state();
  box.innerHTML = `
    <h3 class="section-title">Sync &amp; security</h3>
    <p class="faint" style="font-size:13px;margin-top:0">This device keeps an encrypted copy; changes sync to <b>${esc(st.repo.owner)}/${esc(st.repo.repo)}</b>
      (branch <code>data</code>)${st.token ? "" : " once a token is available"}.${st.dirty ? " <b>Unsynced changes pending.</b>" : ""}
      The GitHub token is stored in your encrypted data, so every device you unlock picks it up automatically — you only need this field to replace it.</p>
    <div class="field-row"><label>GitHub token</label>
      <div class="add-row" style="margin:0"><input class="input" type="password" id="s-token" placeholder="${st.token ? "•••• saved (type to replace)" : "fine-grained token for this repo"}" autocomplete="off">
      <button class="btn" id="s-token-save">Save</button></div></div>
    <p class="faint" style="font-size:12.5px">Token permissions (this repository only): Contents, Actions, Secrets — read &amp; write.</p>
    <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:8px">
      ${st.passkeys ? `<button class="btn" id="s-passkey">Add a passkey on this device</button>` : ""}
      <button class="btn" id="s-backup">Download encrypted backup</button>
      <label class="btn">Restore from backup<input type="file" id="s-restore" accept="application/json" hidden></label>
      <button class="btn" id="s-lock">Lock</button>
    </div>`;
  $("#kaggle-box").after(box);
  $("#s-token-save").onclick = async () => { await Static.setToken($("#s-token").value.trim()); toast("Token saved on this device (encrypted)"); renderProfile(); };
  $("#s-passkey")?.addEventListener("click", async () => {
    try { const n = await Static.addPasskey(navigator.platform || "device"); toast(`Passkey added (${n} total)`); }
    catch (e) { toast(e.message); }
  });
  $("#s-backup").onclick = () => Static.backup();
  $("#s-restore").onchange = async (e) => { try { await Static.restore(e.target.files[0]); toast("Backup merged"); renderProfile(); } catch (err) { toast(`Restore failed: ${err.message}`); } };
  $("#s-lock").onclick = () => Static.lock();
}

/* ------------------------------------------------------------------ Kaggle accounts */
async function renderKaggle(settings) {
  const box = $("#kaggle-box");
  if (!box) return;
  let accts = await api("/api/kaggle/accounts");
  const rows = accts.map((a) => ({ ...a, key: "" }));
  while (rows.length < 3) rows.push({ username: "", key: "", enabled: true, key_hint: "", _new: true });
  const mode = settings.engine_mode || "local";
  const fmt = (iso) => iso ? new Date(iso).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" }) : "";
  const stateOf = (a) => {
    if (a._new || !a.key_hint) return `<span class="faint">not saved</span>`;
    if (a.gpu_blocked_until) return `<span class="chip warn" title="${esc(a.quota_message || "")}">GPU used up · back ${esc(fmt(a.gpu_blocked_until))}</span>`;
    if (a.verified === false) return `<span class="chip bad" title="${esc(a.last_error || "")}">key rejected</span>`;
    return `${a.verified ? '<span class="chip good">verified</span>' : '<span class="chip">unverified</span>'} <span class="faint">${a.gpu_hours_this_week} h GPU this week</span>`;
  };
  box.innerHTML = `
    <h3 class="section-title">Where decisions run</h3>
    ${STATIC_MODE ? `<p class="faint" style="font-size:13px">Scheduled runs happen on GitHub Actions, with Kev on Kaggle (GPU T4 ×2, then Kaggle CPU). Keys you add here are saved as repository secrets and can't be read back.</p>` : ""}
    <div class="checks" style="margin-bottom:12px${STATIC_MODE ? ";display:none" : ""}">
      <label><input type="radio" name="engine-mode" value="local" ${mode === "local" ? "checked" : ""}> This Mac (Kev-4B, ~18 s/job)</label>
      <label><input type="radio" name="engine-mode" value="kaggle" ${mode === "kaggle" ? "checked" : ""}> Kaggle — GPU T4 ×2, then Kaggle CPU, then this Mac</label>
    </div>
    <div class="table-wrap"><table class="tracker kaggle-table">
      <thead><tr><th>Kaggle username</th><th>API key</th><th>Use</th><th>Status</th><th></th></tr></thead>
      <tbody>${rows.map((a, i) => `
        <tr data-i="${i}">
          <td><input class="cell" data-k="username" value="${esc(a.username)}" placeholder="username" autocomplete="off" ${a._new ? "" : "readonly"}></td>
          <td><input class="cell" data-k="key" type="password" value="" placeholder="${a.key_hint ? esc(a.key_hint) + " (saved — type to replace)" : "KGAT_… token or kaggle.json key"}" autocomplete="off"></td>
          <td><input type="checkbox" data-k="enabled" ${a.enabled !== false ? "checked" : ""}></td>
          <td class="k-state">${stateOf(a)}</td>
          <td style="white-space:nowrap">${a.key_hint ? `<button class="btn small" data-verify="${esc(a.username)}">Verify</button>` : ""}
            ${a._new ? "" : `<button class="icon-btn" data-remove="${i}" title="Remove account" aria-label="Remove account">${ICON.trash}</button>`}</td>
        </tr>`).join("")}</tbody></table></div>
    <div class="add-foot"><span class="faint">Get a token at Kaggle → Settings → API → <i>Generate New Token</i> (starts with <code>KGAT_</code>); an older <code>kaggle.json</code> key works too. They stay in this app's local database; the browser only sees the last 4 characters. GPU hours are tracked from this app's runs only — time used in other notebooks on the same account isn't visible.</span>
      <button class="btn" id="k-add">+ Row</button><button class="btn primary" id="k-save">Save accounts</button></div>`;

  box.querySelectorAll('input[name="engine-mode"]').forEach((r) => (r.onchange = async () => {
    await api("/api/settings", { method: "PATCH", json: { engine_mode: r.value } });
    settings.engine_mode = r.value;
    toast(r.value === "kaggle" ? "Runs will use Kaggle first" : "Runs will use this Mac");
  }));
  const collect = () => [...box.querySelectorAll("tbody tr")].map((tr) => ({
    username: tr.querySelector('[data-k="username"]').value.trim(),
    key: tr.querySelector('[data-k="key"]').value.trim(),
    enabled: tr.querySelector('[data-k="enabled"]').checked,
  })).filter((a) => a.username);
  $("#k-save").onclick = async () => {
    try { await api("/api/kaggle/accounts", { method: "PUT", json: collect() }); toast("Accounts saved"); }
    catch (e) { return toast(e.message); }
    renderKaggle(settings);
  };
  $("#k-add").onclick = () => { accts = collect(); rows.push({ username: "", key: "", enabled: true, _new: true }); renderKaggleRowsOnly(); };
  const renderKaggleRowsOnly = () => {  // add a blank row without losing typed values
    const tb = box.querySelector("tbody");
    const tr = document.createElement("tr");
    tr.innerHTML = `<td><input class="cell" data-k="username" placeholder="username" autocomplete="off"></td>
      <td><input class="cell" data-k="key" type="password" placeholder="KGAT_… token or kaggle.json key" autocomplete="off"></td>
      <td><input type="checkbox" data-k="enabled" checked></td><td class="k-state"><span class="faint">not saved</span></td><td></td>`;
    tb.append(tr);
  };
  box.querySelectorAll("[data-remove]").forEach((b) => (b.onclick = async () => {
    const i = +b.dataset.remove;
    const keep = rows.filter((_, j) => j !== i && !rows[j]._new).map((a) => ({ username: a.username, key: "", enabled: a.enabled }));
    await api("/api/kaggle/accounts", { method: "PUT", json: keep });
    toast("Account removed");
    renderKaggle(settings);
  }));
  box.querySelectorAll("[data-verify]").forEach((b) => (b.onclick = async () => {
    b.disabled = true; b.textContent = "Checking…";
    try {
      const r = await api(`/api/kaggle/accounts/${encodeURIComponent(b.dataset.verify)}/verify`, { method: "POST" });
      toast(r.ok ? `${b.dataset.verify}: key works` : `${b.dataset.verify}: Kaggle rejected the key`);
    } catch (e) { toast(e.message); }
    renderKaggle(settings);
  }));
}

async function buildProfile(file) {
  view.innerHTML = `<div class="card-box empty"><h2>Asking ${esc(E())} about your resume…</h2><p>Picking keywords, field, level and search terms.${status?.jev?.local ? " A local engine takes a few minutes." : ""}</p></div>`;
  try {
    const q = file ? `?filename=${encodeURIComponent(file.name)}` : "";
    await api(`/api/profile/build${q}`, { method: "POST", body: file || undefined });
    toast("Profile built");
  } catch (e) { toast(`Profile build failed: ${e.message}`); }
  renderProfile(); refreshStatus();
}

function drawRunStatus(p, last) {
  const el = $("#run-status");
  if (!el) return;
  const running = p.running;
  $("#run").disabled = running;
  $("#run").textContent = running ? "Running…" : "Run pipeline";
  const s = last?.stats || {};
  const w = p.total ? Math.round((100 * p.done) / p.total) : running ? 5 : 0;
  el.innerHTML = `
    ${running || p.log.length ? `<div class="progress"><div style="width:${running ? w : 100}%"></div></div>
      <div class="faint" style="font-size:13px">${esc(p.stage)}${p.total ? ` · ${p.done}/${p.total}` : ""}${p.error ? ` · <span style="color:var(--bad)">${esc(p.error)}</span>` : ""}</div>` : ""}
    ${!running && last ? `<div class="stats">
      <div class="stat"><b>${s.fetched ?? "—"}</b><span>fetched</span></div>
      <div class="stat"><b>${s.fresh ?? "—"}</b><span>new &amp; recent</span></div>
      <div class="stat"><b>${s.triaged_in ?? "—"}</b><span>passed triage</span></div>
      <div class="stat"><b>${s.evaluated ?? "—"}</b><span>deep-evaluated</span></div>
      <div class="stat"><b>${s.loaded ?? "—"}</b><span>loaded on board</span></div>
      <div class="stat"><b>${(s.jev?.input_tokens ?? 0).toLocaleString()}</b><span>tokens ${status?.jev?.local ? "(local, free)" : `(~$${(((s.jev?.input_tokens ?? 0) * 0.042) / 1e6).toFixed(3)})`}</span></div>
    </div><div class="faint" style="font-size:12px;margin-top:8px">Last run ${esc((last.finished || "").replace("T", " "))}</div>` : ""}
    ${p.log.length ? `<div class="log" id="run-log">${esc(p.log.join("\n"))}</div>` : ""}`;
  const log = $("#run-log");
  if (log) log.scrollTop = log.scrollHeight;
}

function pollRun() {
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    if (!location.hash.startsWith("#/profile")) return clearInterval(pollTimer);
    const st = await api("/api/status");
    drawRunStatus(st.pipeline, st.last_run);
    if (!st.pipeline.running) {
      clearInterval(pollTimer);
      refreshStatus();
      if (!st.pipeline.error) toast(`Done — ${st.last_run?.stats?.loaded ?? 0} new jobs on the board`, { label: "Review", run: () => (location.hash = "#/applications") });
    }
  }, 1000);
}

/* ================================================================== TRACKER */
const STATUSES = ["Saved", "Applied", "Assessment", "Interviewing", "Offer", "Rejected", "Ghosted", "Withdrawn"];
let tracker = { rows: [], q: "", status: "" };

async function renderTracker() {
  tracker.rows = await api("/api/applications");
  view.innerHTML = `
    <div class="tr-head">
      <h1>Tracker</h1>
      <input class="input" id="t-q" placeholder="Search company, role, notes…" value="${esc(tracker.q)}">
      <select class="input" id="t-status" style="width:auto"><option value="">All statuses</option>${STATUSES.map((s) => `<option ${s === tracker.status ? "selected" : ""}>${s}</option>`).join("")}</select>
      <a class="btn" id="t-csv" href="/api/applications.csv">Export CSV</a>
      <button class="btn primary" id="t-add">+ Add application</button>
    </div>
    <form class="card-box add-form" id="t-form" hidden autocomplete="off">
      <div class="add-grid">
        <label class="span2">Job link <input class="input" type="url" name="url" placeholder="Paste the link you applied on — details fill in automatically" required></label>
        <label>Company <input class="input" name="company" required></label>
        <label>Role <input class="input" name="role" required></label>
        <label>Location <input class="input" name="location"></label>
        <label>Status <select class="input" name="status">${STATUSES.map((s) => `<option ${s === "Applied" ? "selected" : ""}>${s}</option>`).join("")}</select></label>
        <label>Applied on <input class="input" type="date" name="applied_on"></label>
        <label>Notes <input class="input" name="notes"></label>
      </div>
      <div class="add-foot"><span class="faint" id="t-hint" aria-live="polite"></span>
        <button type="button" class="btn" id="t-cancel">Cancel</button><button class="btn primary">Save application</button></div>
    </form>
    <div class="summary" id="t-summary"></div>
    <div id="t-table"></div>`;
  $("#t-q").oninput = (e) => { tracker.q = e.target.value; drawTable(); };
  if (STATIC_MODE) $("#t-csv").onclick = (e) => {
    e.preventDefault();
    const a = Object.assign(document.createElement("a"), { download: "applications.csv",
      href: URL.createObjectURL(new Blob([Static.csv()], { type: "text/csv" })) });
    a.click();
  };
  $("#t-status").onchange = (e) => { tracker.status = e.target.value; drawTable(); };
  const form = $("#t-form");
  $("#t-add").onclick = () => {
    form.reset();
    form.applied_on.value = new Date().toISOString().slice(0, 10);
    $("#t-hint").textContent = "";
    form.hidden = false;
    form.url.focus();
  };
  $("#t-cancel").onclick = () => (form.hidden = true);
  let lookupTimer, lookedUp = "";
  const lookup = async () => {
    const url = form.url.value.trim();
    if (!/^https?:\/\//.test(url) || url === lookedUp) return;
    lookedUp = url;
    $("#t-hint").textContent = "Looking up the posting…";
    try {
      const d = await api(`/api/applications/lookup?url=${encodeURIComponent(url)}`);
      for (const k of ["company", "role", "location"]) if (d[k] && !form[k].value) form[k].value = d[k];
      form.dataset.source = d.source || "";
      form.dataset.match = d.match ?? "";
      $("#t-hint").textContent = { board: "✓ Matched a job from your board", page: "Filled from the page — check it",
        url: "Company filled from the link; add the role" }[d.from] || "";
    } catch (_) { $("#t-hint").textContent = "Couldn't read that page — fill in the details"; }
  };
  form.url.addEventListener("input", () => { clearTimeout(lookupTimer); lookupTimer = setTimeout(lookup, 500); });
  form.url.addEventListener("change", lookup);
  form.onsubmit = async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(form));
    data.source = form.dataset.source || "manual";
    if (form.dataset.match) data.match = +form.dataset.match;
    const row = await api("/api/applications", { json: data });
    tracker.rows.unshift(row);
    tracker.q = ""; tracker.status = "";
    form.hidden = true;
    drawTable(); refreshStatus();
    toast(row.linked ? "Added · linked to the job on your board" : "Application added");
  };
  drawTable();
}

function drawTable() {
  const today = new Date().toISOString().slice(0, 10);
  const counts = {};
  tracker.rows.forEach((r) => (counts[r.status || "Applied"] = (counts[r.status || "Applied"] || 0) + 1));
  $("#t-summary").innerHTML = STATUSES.filter((s) => counts[s]).map((s) => `<span class="chip st-${s.toLowerCase()}">${s} · ${counts[s]}</span>`).join("");

  const q = tracker.q.toLowerCase();
  const rows = tracker.rows.filter((r) =>
    (!tracker.status || r.status === tracker.status) &&
    (!q || [r.company, r.role, r.notes, r.next_step, r.location].some((v) => (v || "").toLowerCase().includes(q))));
  const wrap = $("#t-table");
  if (!tracker.rows.length) {
    wrap.innerHTML = `<div class="card-box empty"><h2>No applications yet</h2><p>Swipe right on a job, or add one you applied to elsewhere.</p></div>`;
    return;
  }
  const txt = (r, k, ph = "", w = "") => `<input class="cell" data-k="${k}" value="${esc(r[k])}" placeholder="${ph}" ${w ? `style="min-width:${w}"` : ""}>`;
  wrap.innerHTML = `<div class="table-wrap"><table class="tracker">
    <thead><tr><th>Company</th><th>Role</th><th>Link</th><th>Status</th><th>Applied</th><th>Next step</th><th>Follow-up</th><th>Notes</th><th title="Match score">Match</th><th></th></tr></thead>
    <tbody>${rows.map((r) => `
      <tr data-id="${r.id}">
        <td>${txt(r, "company", "Company", "130px")}</td>
        <td>${txt(r, "role", "Role", "190px")}</td>
        <td><div class="link-cell"><input class="cell" type="url" data-k="url" value="${esc(r.url || "")}" placeholder="Paste link" title="${esc(r.url || "")}">${r.url ? `<a class="link-out" href="${esc(r.url)}" target="_blank" rel="noopener" title="Open posting" aria-label="Open posting">${ICON.ext}</a>` : ""}${r.job_id ? `<span class="chip good" title="Linked to a job from your board">board</span>` : ""}</div></td>
        <td><select class="status-sel st-${(r.status || "applied").toLowerCase()}" data-k="status">${STATUSES.map((s) => `<option ${s === r.status ? "selected" : ""}>${s}</option>`).join("")}</select></td>
        <td><input class="cell" type="date" data-k="applied_on" value="${esc(r.applied_on || "")}"></td>
        <td>${txt(r, "next_step", "e.g. OA due", "130px")}</td>
        <td class="${r.follow_up && r.follow_up < today && !["Offer", "Rejected", "Withdrawn"].includes(r.status) ? "overdue" : ""}"><input class="cell" type="date" data-k="follow_up" value="${esc(r.follow_up || "")}"></td>
        <td><textarea class="cell" data-k="notes" rows="1" placeholder="Notes" style="min-width:180px">${esc(r.notes || "")}</textarea></td>
        <td class="match-num">${r.match != null ? Math.round(r.match) : "—"}</td>
        <td><button class="icon-btn" data-del="${r.id}" title="Delete row" aria-label="Delete row">${ICON.trash}</button></td>
      </tr>`).join("")}</tbody></table></div>
    ${rows.length ? "" : `<p class="faint" style="text-align:center">No rows match your filter.</p>`}
    <p class="faint" style="font-size:12.5px;margin-top:10px">Every cell is editable and saves automatically. Overdue follow-ups show in red.</p>`;

  wrap.querySelectorAll("tr[data-id]").forEach((tr) => {
    const id = tr.dataset.id;
    tr.addEventListener("change", async (e) => {
      const k = e.target.dataset.k; if (!k) return;
      const updated = await api(`/api/applications/${id}`, { method: "PATCH", json: { [k]: e.target.value } });
      const i = tracker.rows.findIndex((r) => String(r.id) === id);
      tracker.rows[i] = updated;
      if (k === "status" || k === "follow_up" || k === "url") drawTable();
      toast(k === "url" && updated.linked ? "Linked to the job on your board" : "Saved");
    });
  });
  wrap.querySelectorAll("[data-del]").forEach((b) => (b.onclick = async () => {
    const id = b.dataset.del;
    const row = tracker.rows.find((r) => String(r.id) === id);
    await api(`/api/applications/${id}`, { method: "DELETE" });
    tracker.rows = tracker.rows.filter((r) => String(r.id) !== id);
    drawTable(); refreshStatus();
    toast("Row deleted", { label: "Undo", run: async () => {
      const again = await api("/api/applications", { json: row });
      tracker.rows.unshift(again); drawTable(); refreshStatus();
    } });
  }));
}

/* ------------------------------------------------------------------ boot */
(async () => {
  let server = false;
  try { server = (await fetch("api/status", { cache: "no-store" })).ok; } catch (_) { /* no server */ }
  if (server) return refreshStatus().then(route);
  STATIC_MODE = true;
  Static.boot(() => refreshStatus().then(route));
})();
