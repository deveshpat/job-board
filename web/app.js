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
  // The pill only appears while something is happening (or something is wrong) — details live in Settings.
  const pill = $("#jev-pill");
  const es = status.jev.engine_state, busy = status.pipeline?.running || status.profile_job?.running;
  const text = !status.jev.available ? "Setup needed"
    : busy ? (status.pipeline?.running ? "Searching…" : "Reading your resume…")
    : es === "loading" ? "Warming up…" : es === "error" ? "Needs attention" : "";
  pill.hidden = !text;
  pill.textContent = text;
  pill.className = "jev-pill " + (!status.jev.available || es === "error" ? "off" : "on");
  pill.title = "";
  document.querySelectorAll(".seg button[data-mode] b").forEach((b) => (b.textContent = status.counts[b.parentElement.dataset.mode] || 0));
  const n = status.counts.new || 0;
  const b = $("#badge-new");
  b.hidden = !n; b.textContent = n > 99 ? "99+" : n;
  const apps = await api("/api/applications").catch(() => []);
  const a = $("#badge-apps");
  a.hidden = !apps.length; a.textContent = apps.length;
}

const routes = { profile: renderProfile, applications: renderApplications, tracker: renderTracker, settings: renderSettings };
function route() {
  const name = (location.hash.replace("#/", "") || "applications").split("?")[0];
  const fn = routes[name] || renderApplications;
  document.querySelectorAll("[data-tab]").forEach((a) => a.classList.toggle("active", a.dataset.tab === name));
  clearTimeout(profileTimer);
  view.classList.toggle("wide", name === "tracker");
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
  if (!deck.skills) deck.skills = ((await api("/api/profile").catch(() => null))?.keywords || []).filter((k) => k.keep).map((k) => k.name);
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

/* ------------------------------------------------------------------ job description: structure + highlights
   Postings run long and differ a lot. This splits the text into sections, emphasises requirements, folds away
   company/benefits/legal boilerplate, highlights what Kev's take depends on (your skills, missing skills,
   years asked, location/visa lines, pay), and quotes those lines under "In their words" so the take can be
   checked at a glance. Pure text work — same on the Mac and on Pages. */
const JD = {
  key: /(requirement|qualification|what (you('|’)ll|you will|we('|’)d like you to) (need|bring|have)|what we('|’)re looking for|we('|’)re looking for|you have|you bring|you should|who you are|about you|ideal candidate|must.have|minimum|basic qual|skills|experience)/i,
  nice: /(nice.to.have|bonus|preferred|pluses|extra credit|good to have)/i,
  boil: /^(about (us|the company|the team at|[A-Z][\w&.-]*$)|who we are|(our )?benefits|perks|what we offer|why (join|work)|life at|our (values|culture|mission)|equal (opportunit|employment)|eeo|diversity|privacy|accommodation|how to apply|the fine print|disclaimer|compensation (and|&) benefits)/i,
  boilPara: /(equal opportunit|regardless of (race|gender|age|background|sex)|reasonable accommodation|privacy (notice|policy)|e-verify|we are committed to (building|providing|creating) a diverse|consideration for employment)/i,
  headWords: /(responsibilit|what you('|’)ll (do|work on)|what you will do|the role|role overview|about the role|your impact|day.to.day|in this role|job description|overview|summary|the team|location|compensation|salary|pay range|tech stack|our stack|interview)/i,
  locStrong: /(\bmust (be|reside|live|work)\b[^.]{0,50}\b(in|based|located|within)\b|\bbased in\b|\blocated in\b|\bresid(e|ence|ent|ing)\b|\bauthori[sz]ed to work\b|\bwork(ing)? authori[sz]ation\b|\bvisas?\b|\bsponsor(ship)?\b|\btime ?zones?\b[^.]{0,40}\b(overlap|within|hours|gmt|utc|est|pst|cet|ist|[+±]\s?\d)|\b(overlap|within)\b[^.]{0,30}\btime ?zones?\b|\b(us|u\.s\.|eu|uk|emea|apac|latam|north america)[- ](only|based)\b|\bwithin the (us|u\.s\.|eu|uk|united states)\b|\brelocat(e|ion)\b|\bon-?site\b|\bin[- ]office\b|\bhybrid\b)/i,
  notLoc: /\b(benefits?|insurance|401\(?k|perks?|pto|parental|investors?|funding|raised|stipend|allowance|equipment|communication)\b/i,
  locWeak: /\b(remote|anywhere|worldwide|distributed)\b/i,
  exp: /\b\d{1,2}\s*\+?\s*(?:(?:-|–|to)\s*\d{1,2}\s*\+?\s*)?(?:years?|yrs?)\b[^.;,\n]{0,45}/i,
  pay: /(?:[$€£₹]\s?\d[\d,.]*\s?[kKmM]?(?:\s?(?:-|–|to)\s?[$€£₹]?\s?\d[\d,.]*\s?[kKmM]?)?(?:\s?(?:USD|EUR|GBP|INR|CAD|AUD))?|\b\d[\d,.]*\s?(?:LPA|lakhs?\b|k\s?(?:USD|EUR|GBP)\b))/,
};
const reEsc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

function skillTerms(names) {           // "PEFT/LoRA" → PEFT, LoRA; "quantization (4-bit)" → quantization
  const out = new Set();
  for (const n of names || []) for (const part of String(n).replace(/\(.*?\)/g, "").split("/")) {
    const t = part.trim();
    if (t.length >= 2) out.add(t);
  }
  return [...out].sort((a, b) => b.length - a.length);    // longest first: "Next.js" before "Next"
}

function jdMarker(have, gaps) {
  const H = skillTerms(have), G = skillTerms(gaps).filter((g) => !H.some((h) => h.toLowerCase() === g.toLowerCase()));
  const kind = new Map([...H.map((t) => [t.toLowerCase(), ["have", t]]), ...G.map((t) => [t.toLowerCase(), ["gap", t]])]);
  const words = [...H, ...G].map((t) => `(?<![\\w+#.])${reEsc(esc(t))}(?![\\w+#])`).join("|");
  const re = new RegExp(`(${JD.pay.source})|(${JD.exp.source})${words ? `|(${words})` : ""}`, "gi");
  return (plain) => esc(plain).replace(re, (m, pay, exp, word) => {
    if (pay) return `<mark class="hl-pay">${m}</mark>`;
    if (exp) return `<mark class="hl-exp">${m}</mark>`;
    const [k, canon] = kind.get(m.toLowerCase().replace(/&amp;/g, "&")) || [];
    if (!k || (canon.length <= 3 && m !== esc(canon))) return m;      // "Go", "SQL": exact case only
    return `<mark class="hl-${k}">${m}</mark>`;
  });
}

const sentences = (s) => s.split(/(?<=[.!?])\s+(?=[A-Z0-9("“])/);

function jdDigest(text, have, gaps, listed = "") {
  const lines = String(text || "").replace(/\r/g, "").split("\n").map((l) => l.trim());
  const isHead = (l) => l.length <= 70 && !/[.!?,;]$/.test(l) && !/https?:|@/.test(l) && /[A-Za-z]/.test(l) &&
    (/:$/.test(l) || (l === l.toUpperCase() && l.length > 3) ||
     (l.split(/\s+/).length <= 7 && /^[A-Z]/.test(l) && (JD.key.test(l) || JD.nice.test(l) || JD.boil.test(l) || JD.headWords.test(l))));
  const secs = [{ head: "", kind: "", blocks: [] }], boil = [];
  for (const l of lines) {
    if (!l) continue;
    const li = /^([-•*·▪●◦–]|\d{1,2}[.)])\s+(.*)$/.exec(l);
    if (!li && isHead(l)) {
      const h = l.replace(/:$/, "");
      secs.push({ head: h, kind: JD.boil.test(h) ? "boil" : JD.nice.test(h) ? "nice" : JD.key.test(h) ? "key" : "", blocks: [] });
    } else secs.at(-1).blocks.push(li ? { t: "li", s: li[2] } : { t: "p", s: l });
  }
  const mark = jdMarker(have, gaps);
  const facts = {};
  const render = (blocks) => {
    let html = "", list = false;
    for (const b of blocks) {
      const inner = sentences(b.s).map((x) => {
        const locish = !JD.notLoc.test(x);
        if (!facts.loc && locish && JD.locStrong.test(x)) facts.loc = x;
        if (!facts.locWeak && locish && JD.locWeak.test(x)) facts.locWeak = x;
        if (!facts.exp && JD.exp.test(x)) facts.exp = x;
        if (!facts.pay && JD.pay.test(x)) facts.pay = x;
        const m = mark(x);
        return locish && JD.locStrong.test(x) ? `<span class="hl-loc">${m}</span>` : m;
      }).join(" ");
      if (b.t === "li" && !list) { html += "<ul>"; list = true; }
      if (b.t !== "li" && list) { html += "</ul>"; list = false; }
      html += b.t === "li" ? `<li>${inner}</li>` : `<p>${inner}</p>`;
    }
    return html + (list ? "</ul>" : "");
  };
  let body = "";
  for (const s of secs) {
    const blocks = s.kind === "boil" ? [] : s.blocks.filter((b) => !(b.t === "p" && JD.boilPara.test(b.s)));
    if (s.kind === "boil") boil.push(s);
    else if (blocks.length !== s.blocks.length) boil.push({ head: "", blocks: s.blocks.filter((b) => !blocks.includes(b)) });
    if (!blocks.length && !(s.head && s.kind !== "boil")) continue;
    if (s.kind === "boil") continue;
    body += `<section class="jd-sec ${s.kind}">${s.head ? `<h5>${esc(s.head)}${s.kind === "key" ? ' <span class="tag">what they need</span>' : s.kind === "nice" ? ' <span class="tag soft">nice to have</span>' : ""}</h5>` : ""}${render(blocks)}</section>`;
  }
  if (boil.length) body += `<details class="jd-boil"><summary>About the company, benefits &amp; legal</summary>${
    boil.map((s) => `${s.head ? `<h5>${esc(s.head)}</h5>` : ""}${render(s.blocks)}`).join("")}</details>`;
  const clip = (s) => (s.length > 220 ? s.slice(0, 217).replace(/\s+\S*$/, "") + "…" : s);
  const where = facts.loc ? mark(clip(facts.loc))                    // their own words beat the board's field
    : listed ? `<span class="faint">Listed as</span> ${esc(clip(listed))}` : facts.locWeak ? mark(clip(facts.locWeak)) : "";
  const quotes = [["Location", where, true], ["Experience", facts.exp], ["Pay", facts.pay]]
    .filter(([, s]) => s).map(([k, s, done]) => ({ k, html: done ? s : mark(clip(s)) }));
  const words = String(text || "").split(/\s+/).length;
  return { html: body, quotes, minutes: Math.max(1, Math.round(words / 230)) };
}

// Jev is a "System One" model — fast, intuitive judgment — so its reasoning is a gut check. Open/closed is remembered.
const gutOpen = () => { try { return localStorage.getItem("gut-open") === "1"; } catch (_) { return false; } };
document.addEventListener("toggle", (e) => {
  if (!e.target.matches?.("details.gut")) return;
  try { localStorage.setItem("gut-open", e.target.open ? "1" : "0"); } catch (_) { /* storage off */ }
}, true);

function cardHTML(j) {
  const c = j.card || {};
  const logo = j.logo ? `<img src="${esc(j.logo)}" alt="" onerror="this.remove()">` : "";
  const initial = esc((j.company || "?").trim()[0]?.toUpperCase());
  const tierLabel = { strong: "Strong match", good: "Good match", stretch: "Stretch" }[c.tier] || "Match";
  const conf = c.confidence == null ? "" : ` · confidence ${pct(c.confidence)}`;
  const tk = c.take;
  const jdq = jdDigest(j.description, [...new Set([...(deck.skills || []), ...(c.skills_matched || [])])], c.skills_gap,
    [...new Map([j.location, j.location_restrictions].filter(Boolean).map((x) => [x.trim().toLowerCase(), x.trim()])).values()].join(" · "));
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
        <div><div class="verdict">${esc(tk?.verdict || tierLabel)}${c.unsure ? ' <span class="chip warn" title="The read on this one was split — double-check it">Unsure</span>' : ""}</div>
        <div class="sub">${tk ? `${tierLabel}${tk.summary.includes("—") ? " · " + esc(tk.summary.split("—")[1].replace(/\.$/, "").trim()) : ""}` : c.scored_by === "jev" ? `Scored${conf}` : "Keyword score (no engine)"}</div></div>
      </div>
      <div class="chips">${(c.chips || []).map((x, i) => `<span class="chip ${i === 0 ? (c.location_ok ? "good" : "warn") : ""}">${esc(x)}</span>`).join("")}${j.salary ? `<span class="chip info">${esc(j.salary)}</span>` : ""}</div>
      ${tk ? `<details class="gut"${gutOpen() ? " open" : ""}>
        <summary><span class="gut-ico" aria-hidden="true">⚡</span><span class="gut-t">Gut check</span>
          <span class="gut-n">${tk.pros.length ? `<b class="p">✓ ${tk.pros.length}</b>` : ""}${tk.cons.length ? `<b class="c">! ${tk.cons.length}</b>` : ""}</span></summary>
        <ul class="take">${tk.pros.map((x) => `<li class="pro">${esc(x)}</li>`).join("")}${tk.cons.map((x) => `<li class="con">${esc(x)}</li>`).join("")}</ul>
        ${c.lead_project ? `<div class="jc-block lead">💡 <span>Lead your application with <b>${esc(c.lead_project)}</b></span></div>` : ""}
        ${jdq.quotes.length ? `<div class="jc-block evidence"><h4>In their words</h4>${jdq.quotes.map((q) => `<div class="ev"><span>${q.k}</span><p>${q.html}</p></div>`).join("")}</div>` : ""}
      </details>` : c.lead_project ? `<div class="jc-block lead">💡 <span>Lead your application with <b>${esc(c.lead_project)}</b></span></div>` : ""}
      ${tk ? "" : `<div class="jc-block"><h4>Why it was picked</h4><ul class="reasons">${(c.reasons || []).map((r) => `<li>${esc(r)}</li>`).join("")}</ul></div>
      ${c.skills_matched?.length ? `<div class="jc-block"><h4>Your matching skills</h4><div class="chips">${c.skills_matched.map((s) => `<span class="chip good">${esc(s)}</span>`).join("")}</div></div>` : ""}
      ${c.skills_gap?.length ? `<div class="jc-block"><h4>Gaps they ask for</h4><div class="chips">${c.skills_gap.map((s) => `<span class="chip bad">${esc(s)}</span>`).join("")}</div></div>` : ""}`}
      <div class="jc-block"><details class="desc"><summary>Full description · ${jdq.minutes} min read</summary>
        <div class="jd-legend"><mark class="hl-have">your skills</mark><mark class="hl-gap">missing</mark><mark class="hl-exp">experience</mark><span class="hl-loc">location / visa</span><mark class="hl-pay">pay</mark></div>
        <div class="jd">${jdq.html || `<p class="faint">${esc(j.description || "No description provided.")}</p>`}</div></details></div>
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
      <p>${deck.mode === "later" ? "Swipe up on a card to save it here."
        : hasProfile ? "We look for new jobs every day. Want some now?" : "Start with your resume — it's what every job is matched against."}</p>
      ${deck.mode === "new" ? (hasProfile ? `<button class="btn primary" id="e-run">Find jobs now</button>` : `<a class="btn primary" href="#/profile">Add your resume</a>`) : ""}
    </div>`;
    $("#e-run")?.addEventListener("click", () => startRun());
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
    if (e.target.closest("a, button, summary, pre, .jd, .evidence, .jc-block details[open]")) return;
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
const LEVEL_NAMES = { internship: "Internship", entry: "Entry level", mid: "Mid level", senior: "Senior", staff_plus: "Staff+ / manager" };
const FIELD_NAMES = { ai_ml_engineering: "AI/ML engineering", applied_ai_products: "Applied AI", data_science: "Data science",
  data_engineering: "Data engineering", backend: "Backend", full_stack: "Full stack", frontend: "Frontend",
  devops_platform: "DevOps / platform", research: "Research", other_engineering: "Other engineering", non_engineering: "Non-engineering" };
let profileTimer = null;

async function renderProfile() {
  clearTimeout(profileTimer);
  const profile = await api("/api/profile");
  if (!profile) return renderOnboarding();
  const res = profile.resume;
  const job = profile.job || {};
  const initials = esc((profile.name || "?").split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase());
  const roles = profile.search_terms || [], skills = profile.keywords || [];
  const keptRoles = roles.filter((t) => t.keep), moreRoles = roles.filter((t) => !t.keep);
  const keptSkills = skills.filter((k) => k.keep), leftSkills = skills.filter((k) => !k.keep);
  const reads = (profile.fields || []).slice(0, 2).map((f) => FIELD_NAMES[f.name] || f.name).join(" · ");
  const input = (f, ph = "", cls = "") => `<input class="plain ${cls}" data-f="${f}" value="${esc(profile[f] || "")}" placeholder="${ph}" aria-label="${ph || f}">`;

  view.innerHTML = `
  <div class="profile">
    <div class="stack">
      <section class="card-box me2">
        <label class="photo-edit" title="Change photo">
          ${profile.photo_url ? `<img src="${esc(profile.photo_url)}" alt="">` : `<span class="ph">${initials}</span>`}
          <input type="file" accept="image/*" id="p-photo" hidden><span class="cam">Change</span>
        </label>
        <div class="me2-main">
          ${input("name", "Your name", "h1")}
          <textarea class="plain sub" data-f="headline" rows="1" data-autosize placeholder="Headline, e.g. Full-stack & AI engineer" aria-label="Headline">${esc(profile.headline || "")}</textarea>
          <div class="me2-meta">
            <label><span aria-hidden="true">📍</span>${input("location", "Location")}</label>
            <label><span aria-hidden="true">✉️</span>${input("email", "Email")}</label>
          </div>
        </div>
      </section>

      <section class="card-box">
        <h3 class="section-title">What you're looking for</h3>
        <textarea class="input" data-f="goal" rows="2" data-autosize placeholder="e.g. An AI/ML or full-stack role where shipping fast matters">${esc(profile.goal || "")}</textarea>
        <div class="pref-row">
          <label>Your level<select class="input" data-f="level">${Object.entries(LEVEL_NAMES).map(([k, v]) =>
            `<option value="${k}" ${k === profile.level ? "selected" : ""}>${v}</option>`).join("")}</select></label>
          <label>Based in<select class="input" data-f="country">${[...new Set([...(profile.countries || []), profile.country].filter(Boolean))].map((c) =>
            `<option ${c === profile.country ? "selected" : ""}>${esc(c)}</option>`).join("")}</select></label>
          <label class="span2">Languages you work in<input class="input" data-f="languages" placeholder="English, Hindi"
            value="${esc((profile.languages || (profile.country === "India" ? ["English", "Hindi"] : ["English"])).join(", "))}"></label>
        </div>
        ${reads ? `<p class="faint small">We read you as <b>${esc(reads)}</b>. Every job is matched against this, your skills and your resume.</p>` : ""}
      </section>

      <section class="card-box o-late">
        <h3 class="section-title">Roles we search for</h3>
        <div class="chips" id="p-roles">${keptRoles.map((t) => chipX(t.name, roles.indexOf(t), "role")).join("") || '<span class="faint small">None yet — add one below.</span>'}</div>
        <div class="add-row"><input class="input" id="p-role-new" placeholder="Add a role, e.g. Python Backend Developer"><button class="btn" id="p-role-add">Add</button></div>
        ${moreRoles.length ? `<details class="more"><summary>More roles that fit you (${moreRoles.length})</summary>
          <div class="chips">${moreRoles.map((t) => chipPlus(t.name, roles.indexOf(t), "role")).join("")}</div></details>` : ""}
      </section>

      <section class="card-box o-late">
        <h3 class="section-title">Skills jobs are matched on</h3>
        <div class="chips" id="p-skills">${keptSkills.map((k) => chipX(k.name, skills.indexOf(k), "skill", k.core)).join("")}</div>
        <div class="add-row"><input class="input" id="p-skill-new" placeholder="Add a skill"><button class="btn" id="p-skill-add">Add</button></div>
        ${leftSkills.length ? `<details class="more"><summary>Left out (${leftSkills.length})</summary>
          <div class="chips">${leftSkills.map((k) => chipPlus(k.name, skills.indexOf(k), "skill")).join("")}</div></details>` : ""}
        <p class="faint small" style="margin-bottom:0">Highlighted = skills you've actually used in your work and projects.</p>
      </section>

      ${Object.keys(profile.projects || {}).length ? `<section class="card-box o-late">
        <h3 class="section-title">Projects to lead your applications with</h3>
        <div class="proj">${Object.entries(profile.projects).map(([n, d]) => `<div><b>${esc(n)}</b><span class="muted">${esc(d || "")}</span></div>`).join("")}</div>
      </section>` : ""}
    </div>

    <section class="card-box resume-card">
      <div class="rc-head">
        <div><h3 class="section-title" style="margin:0">Resume</h3>
          <div class="faint small">${res ? `${esc(res.filename || "resume")} · updated ${esc(ago(res.updated_at) || "")}` : "No resume yet"}</div></div>
        <div class="rc-actions">
          ${res?.kind === "tex" ? `<button class="btn small" id="r-edit">Edit</button>` : ""}
          <label class="btn small">Upload<input type="file" id="r-upload" accept=".tex,.pdf" hidden></label>
          ${res ? `<button class="btn small" id="r-dl">Download</button>` : ""}
        </div>
      </div>
      <div id="r-status">${resumeStatus(profile, res, job)}</div>
      <div class="pdf-view" id="r-view">${res ? '<p class="faint small">Loading…</p>' : '<p class="faint small">Upload your resume as LaTeX (.tex — editable here) or PDF.</p>'}</div>
    </section>
  </div>`;

  // -- your details: saved as you leave each field (and kept when Kev re-reads the resume)
  view.querySelectorAll("[data-f]").forEach((el) => (el.onchange = async () => {
    const f = el.dataset.f;
    const v = f === "languages" ? el.value.split(/[,;]/).map((x) => x.trim()).filter(Boolean).map((x) => x[0].toUpperCase() + x.slice(1)) : el.value.trim();
    await api("/api/profile", { method: "PATCH", json: { [f]: v } });
    toast(f === "languages" ? "Saved — jobs in other languages stay off your deck" : "Saved");
    if (f === "languages") refreshStatus();
  }));
  $("#p-photo").onchange = (e) => e.target.files[0] && setPhoto(e.target.files[0]);
  view.querySelectorAll("[data-autosize]").forEach((t) => {
    const fit = () => { t.style.height = "auto"; t.style.height = t.scrollHeight + 2 + "px"; };
    t.addEventListener("input", fit); requestAnimationFrame(fit);
    t.addEventListener("keydown", (e) => { if (e.key === "Enter" && t.classList.contains("plain")) { e.preventDefault(); t.blur(); } });
  });

  // -- roles & skills: × removes, + brings back, Add adds your own
  const saveList = async (key, list) => { await api("/api/profile", { method: "PATCH", json: { [key]: list } }); renderProfile(); };
  const onChips = (e) => {
    const b = e.target.closest("[data-kind]"); if (!b) return;
    const [key, list] = b.dataset.kind === "role" ? ["search_terms", roles] : ["keywords", skills];
    const it = list[+b.dataset.i];
    it.keep = b.dataset.act === "add"; it.user_set = true;
    saveList(key, list);
  };
  view.querySelectorAll(".chips").forEach((c) => (c.onclick = onChips));
  const adder = (inputId, btnId, key, list, extra) => {
    const add = () => {
      const v = $(inputId).value.trim(); if (!v) return;
      const hit = list.find((x) => x.name.toLowerCase() === v.toLowerCase());
      if (hit) Object.assign(hit, { keep: true, user_set: true });
      else list.unshift({ name: v, p: null, keep: true, added: true, user_set: true, ...extra });
      saveList(key, list);
    };
    $(btnId).onclick = add;
    $(inputId).onkeydown = (e) => { if (e.key === "Enter") add(); };
  };
  adder("#p-role-new", "#p-role-add", "search_terms", roles, {});
  adder("#p-skill-new", "#p-skill-add", "keywords", skills, { used: null, market: null, core: false });

  // -- resume
  $("#r-upload").onchange = (e) => e.target.files[0] && uploadResume(e.target.files[0]);
  $("#r-edit")?.addEventListener("click", openEditor);
  $("#r-dl")?.addEventListener("click", downloadResume);
  $("#r-kev")?.addEventListener("click", async () => { await api("/api/profile/build", { method: "POST" }); renderProfile(); });
  if (res) showResumePdf($("#r-view"));
  if (job.running || res?.building || (res && !res.pdf_current && !res.error && STATIC_MODE))
    profileTimer = setTimeout(() => location.hash.startsWith("#/profile") && renderProfile(), job.running ? 3000 : 10000);
}

const chipX = (name, i, kind, core) =>
  `<span class="chip pill ${core ? "core" : ""}">${esc(name)}<button data-kind="${kind}" data-i="${i}" data-act="remove" aria-label="Remove ${esc(name)}" title="Remove">×</button></span>`;
const chipPlus = (name, i, kind) =>
  `<button class="chip pill ghost" data-kind="${kind}" data-i="${i}" data-act="add" title="Add">+ ${esc(name)}</button>`;

function resumeStatus(profile, res, job) {
  if (!res) return "";
  const line = (cls, html) => `<div class="rstat ${cls}">${html}</div>`;
  if (job.running) return line("busy", `<span class="spin"></span>${esc(job.stage || "Updating your profile")}… (a minute or two)`);
  if (job.error) return line("bad", `Couldn't update your profile: ${esc(job.error)}`);
  if (res.error && res.error.rev === res.rev)
    return line("bad", `LaTeX error: ${esc(res.error.message)}. The PDF below is the previous version — <a href="#" onclick="openEditor();return false">fix it in the editor</a>.`);
  if (!res.pdf_current && res.kind === "tex")
    return line("busy", `<span class="spin"></span>${STATIC_MODE ? "Building the PDF on GitHub (about a minute)…" : "Building the PDF…"}`);
  if (profile.kev_behind)
    return STATIC_MODE ? ""
      : line("", `Your profile isn't updated from this version yet. <button class="btn small" id="r-kev">Update now</button>`);
  return "";
}

async function renderOnboarding() {
  const st = status || (await api("/api/status"));
  const busy = st.profile_job?.running;
  view.innerHTML = `<div class="card-box empty onboard">
    <h2>${busy ? "Reading your resume…" : "Start with your resume"}</h2>
    <p>${busy ? "Picking your skills, level and the roles to search for. This takes a minute or two."
      : `Upload it as LaTeX (.tex — you can edit and rebuild it here) or PDF — or build one here. We read it and pick your skills, level and the roles to search for.`}</p>
    ${busy ? '<div class="spin big"></div>' : `<label class="btn primary">Upload resume<input type="file" id="r-upload" accept=".tex,.pdf" hidden></label>`}
  </div>`;
  $("#r-upload")?.addEventListener("change", (e) => e.target.files[0] && uploadResume(e.target.files[0]));
  if (busy) profileTimer = setTimeout(() => refreshStatus().then(() => location.hash.startsWith("#/profile") && renderProfile()), 3000);
}

async function uploadResume(file) {
  toast("Uploading…");
  try {
    await api(`/api/resume/upload?filename=${encodeURIComponent(file.name)}`, { method: "POST", body: file });
    toast(file.name.endsWith(".pdf") ? "Resume saved" : "Resume saved — building the PDF");
  } catch (e) { return toast(e.message); }
  await refreshStatus();
  renderProfile();
}

async function setPhoto(file) {
  const img = await new Promise((ok, bad) => { const i = new Image(); i.onload = () => ok(i); i.onerror = bad; i.src = URL.createObjectURL(file); });
  const side = Math.min(img.width, img.height), size = Math.min(480, side);
  const c = Object.assign(document.createElement("canvas"), { width: size, height: size });
  c.getContext("2d").drawImage(img, (img.width - side) / 2, (img.height - side) / 2, side, side, 0, 0, size, size);
  try { await api("/api/photo", { method: "PUT", json: { data_url: c.toDataURL("image/jpeg", 0.86) } }); toast("Photo updated"); }
  catch (e) { toast(e.message); }
  renderProfile();
}

/* ------------------------------------------------------------------ resume PDF: render pages with pdf.js */
const PDFJS = "https://cdn.jsdelivr.net/npm/pdfjs-dist@4.10.38/build/";
let pdfjsLib = null;
async function pdfBytes() {
  if (STATIC_MODE) return (await Static.resumePdf())?.bytes || null;
  const r = await fetch("api/resume.pdf", { cache: "no-store" });
  return r.ok ? new Uint8Array(await r.arrayBuffer()) : null;
}
async function showResumePdf(el) {
  const bytes = await pdfBytes().catch(() => null);
  if (!bytes) { el.innerHTML = `<p class="faint small">No PDF yet.</p>`; return; }
  await renderPdf(el, bytes);
}
async function renderPdf(el, bytes) {
  try {
    pdfjsLib ||= await import(PDFJS + "pdf.min.mjs").then((m) => { m.GlobalWorkerOptions.workerSrc = PDFJS + "pdf.worker.min.mjs"; return m; });
    const doc = await pdfjsLib.getDocument({ data: bytes.slice() }).promise;
    const frag = document.createDocumentFragment(), width = el.clientWidth || 600, dpr = Math.min(window.devicePixelRatio || 1, 2);
    for (let i = 1; i <= doc.numPages; i++) {
      const page = await doc.getPage(i);
      const vp = page.getViewport({ scale: (width / page.getViewport({ scale: 1 }).width) * dpr });
      const c = Object.assign(document.createElement("canvas"), { width: vp.width, height: vp.height, className: "pdf-page" });
      await page.render({ canvasContext: c.getContext("2d"), viewport: vp }).promise;
      frag.append(c);
    }
    el.replaceChildren(frag);
  } catch (e) {                                           // offline / no pdf.js: the browser's own viewer
    console.warn(e);
    el.innerHTML = `<iframe class="pdf-frame" title="Resume" src="${URL.createObjectURL(new Blob([bytes], { type: "application/pdf" }))}"></iframe>`;
  }
}
async function downloadResume() {
  const bytes = await pdfBytes();
  if (!bytes) return toast("No PDF yet");
  const p = await api("/api/profile");
  const name = (p?.resume?.filename || "resume").replace(/\.(tex|pdf)$/i, "") + ".pdf";
  Object.assign(document.createElement("a"), { download: name, href: URL.createObjectURL(new Blob([bytes], { type: "application/pdf" })) }).click();
}

/* ------------------------------------------------------------------ resume editor: visual first, LaTeX for those who want it */
function starterModel(p) {                      // no LaTeX yet (a PDF upload, or nothing): start from the profile
  const m = Resume.blank();
  if (!p) return m;
  m.header.name = esc(p.name || "");
  m.header.headline = esc(p.headline || "");
  const c = Object.fromEntries(m.header.contacts.map((x) => [x.kind, x]));
  c.location.text = esc(p.location || "");
  c.email.text = esc(p.email || "");
  m.sections[0].text = esc(p.summary || p.goal || "");
  const skills = (p.keywords || []).filter((k) => k.keep).map((k) => k.name);
  if (skills.length) m.sections[1].rows = [{ label: "Skills", items: esc(skills.join(", ")) }];
  const projects = Object.entries(p.projects || {});
  if (projects.length) m.sections[3].entries = projects.map(([n, d]) => ({ id: Math.random().toString(36).slice(2, 9), title: esc(n), url: "",
    desc: esc(d || ""), right: "", sub: "", subRight: "", bullets: [""] }));
  return m;
}

async function openEditor(opts = {}) {
  let r = null;
  try { r = await api("/api/resume"); } catch (_) { /* no resume yet */ }
  const fromScratch = !r || r.kind !== "tex";
  const profile = fromScratch ? await api("/api/profile").catch(() => null) : null;
  let model = fromScratch ? starterModel(profile) : r.model && r.model_rev === r.rev ? r.model : null;
  if (!model && !fromScratch) { try { model = Resume.parse(r.tex); } catch (e) { console.warn(e); } }
  const filename = fromScratch ? `${(profile?.name || "resume").replace(/\s+/g, "_")}_Resume.tex` : r.filename || "resume.tex";
  let savedTex = fromScratch ? "" : r.tex;
  let mode = opts.mode || (() => { try { return localStorage.getItem("resume-mode") || "visual"; } catch (_) { return "visual"; } })();
  if (!model) mode = "latex";
  const draftKey = "resume-draft";
  let draft = null;
  try { draft = JSON.parse(localStorage.getItem(draftKey) || "null"); } catch (_) { /* storage off */ }
  const restored = draft && draft.base === (r?.rev || "new") && (draft.tex !== savedTex);
  if (restored) { model = draft.model || model; if (!draft.model) mode = "latex"; }

  const st = () => ({ ...Resume.DEFAULT_STYLE, ...(model?.style || {}) });
  const opt = (list, cur) => list.map(([v, l]) => `<option value="${v}" ${String(v) === String(cur) ? "selected" : ""}>${l}</option>`).join("");
  const box = document.createElement("div");
  box.className = "editor";
  box.setAttribute("role", "dialog");
  box.innerHTML = `
    <div class="ed-bar">
      <b class="ed-name">${esc(filename)}</b>
      <div class="seg ed-mode"><button data-mode="visual">Visual</button><button data-mode="latex">LaTeX</button></div>
      <div class="ed-style">
        <select class="input" data-style="font" title="Font">${opt(Resume.FONTS, st().font)}</select>
        <select class="input" data-style="size" title="Text size">${opt([[10, "10 pt"], [11, "11 pt"], [12, "12 pt"]], st().size)}</select>
        <select class="input" data-style="margins" title="Margins">${opt([["compact", "Tight margins"], ["normal", "Normal margins"], ["roomy", "Wide margins"]], st().margins)}</select>
        <select class="input" data-style="headings" title="Section headings">${opt([["smallcaps", "Small caps"], ["bold", "Bold"], ["accent", "Coloured"]], st().headings)}</select>
        <label class="ed-color" title="Link & accent colour"><input type="color" data-style="accent" value="#${st().accent}"></label>
      </div>
      <span class="faint small ed-msg" id="ed-msg" aria-live="polite">${restored ? 'Restored your unsaved edits · <a href="#" id="ed-discard">discard</a>' : ""}</span>
      ${STATIC_MODE ? "" : `<button class="btn small" id="ed-pdf" title="Show the compiled PDF next to the editor">PDF</button>`}
      <button class="btn small primary" id="ed-save" title="⌘S">${STATIC_MODE ? "Save & build" : "Save"}</button>
      <button class="btn small" id="ed-close">Close</button>
    </div>
    <div class="ed-body" data-mode="${mode}">
      <div class="ed-visual"><p class="ed-tip faint small">Click any text to edit it · <kbd>Enter</kbd> adds a bullet · <kbd>⌘B</kbd> bold · <kbd>⌘I</kbd> italic · <kbd>⌘K</kbd> link · <kbd>⌘E</kbd> code · hover a section or entry to move or delete it</p><div id="ed-canvas"></div></div>
      <textarea id="ed-src" spellcheck="false" autocapitalize="off" autocomplete="off"></textarea>
      <div class="ed-prev"><div id="ed-err" class="rstat bad" hidden></div><div class="pdf-view" id="ed-view"></div></div>
    </div>`;
  document.body.append(box);
  document.body.classList.add("modal-open");
  const $b = (s) => box.querySelector(s);
  const src = $b("#ed-src"), body = $b(".ed-body"), msg = (h) => ($b("#ed-msg").innerHTML = h);
  let showPdf = !STATIC_MODE && window.innerWidth >= 1250;
  const currentTex = () => (mode === "visual" ? Resume.toTex(model) : src.value);
  const dirty = () => currentTex() !== savedTex;

  const saveDraft = () => { try { localStorage.setItem(draftKey, JSON.stringify({ base: r?.rev || "new", tex: currentTex(), model: mode === "visual" ? model : null })); } catch (_) { /* */ } };
  let previewTimer = null, previewing = false;
  const onEdit = () => {
    saveDraft();
    msg(dirty() ? "Unsaved changes" : "");
    clearTimeout(previewTimer);
    if (showPdf) previewTimer = setTimeout(preview, 1200);
  };
  const vis = Resume.editor($b("#ed-canvas"), model || Resume.blank(), { onChange: (m) => { model = m; onEdit(); } });

  const showError = (message, log) => {
    const e = $b("#ed-err");
    e.hidden = false;
    e.innerHTML = `LaTeX error: ${esc(message)}${log ? `<details><summary>Log</summary><pre>${esc(log)}</pre></details>` : ""}`;
    const m = /line (\d+)/.exec(message);
    if (m && mode === "latex") {
      const lines = src.value.split("\n"), n = Math.min(+m[1], lines.length) - 1;
      const start = lines.slice(0, n).reduce((a, l) => a + l.length + 1, 0);
      src.setSelectionRange(start, start + (lines[n] || "").length);
    }
  };
  async function preview() {
    if (STATIC_MODE || previewing) return;
    previewing = true;
    try {
      const resp = await fetch("api/resume/preview", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ tex: currentTex() }) });
      if (resp.status === 422) { const e = await resp.json(); showError(e.detail, e.log); }
      else if (resp.ok) { $b("#ed-err").hidden = true; await renderPdf($b("#ed-view"), new Uint8Array(await resp.arrayBuffer())); }
    } catch (e) { msg(esc(e.message)); }
    previewing = false;
  }
  const layout = () => {
    body.dataset.mode = mode;
    body.classList.toggle("with-pdf", showPdf || mode === "latex");
    box.querySelectorAll(".ed-mode button").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
    $b(".ed-style").hidden = mode !== "visual";
    $b("#ed-pdf")?.classList.toggle("on", showPdf);
    requestAnimationFrame(() => vis?.fit());
    try { localStorage.setItem("resume-mode", mode); } catch (_) { /* */ }
  };
  const setMode = (next) => {
    if (next === mode) return;
    if (next === "latex") src.value = Resume.toTex(model);
    else {
      try { model = Resume.parse(src.value); vis.setModel(model); msg("Rebuilt the visual view from your LaTeX. Custom commands it can't show are replaced when you save from here."); }
      catch (e) { return msg(`Couldn't read that LaTeX into sections: ${esc(e.message)}`); }
    }
    mode = next; layout();
    if (showPdf || mode === "latex") preview();
  };
  const save = async () => {
    const btn = $b("#ed-save");
    btn.disabled = true; msg(STATIC_MODE ? "Saving…" : "Saving and compiling…");
    const tex = currentTex();
    try {
      const out = await api("/api/resume", { method: "PUT", json: { tex, filename, model: mode === "visual" ? model : null } });
      savedTex = tex;
      r = { ...(r || {}), ...out, kind: "tex", rev: out.rev };
      try { localStorage.removeItem(draftKey); } catch (_) { /* */ }
      if (out.error && out.error.rev === out.rev) { showError(out.error.message); msg("Saved — but LaTeX failed, so the PDF is the previous version"); }
      else if (STATIC_MODE) msg("Saved · building the PDF on GitHub (about a minute)");
      else { $b("#ed-err").hidden = true; msg("Saved · updating your profile from it"); if (showPdf || mode === "latex") showResumePdf($b("#ed-view")); }
    } catch (e) { msg(esc(e.message)); }
    btn.disabled = false;
  };
  const close = () => {
    if (dirty() && !confirm("Close without saving? Your edits stay as a draft in this browser.")) return;
    vis.destroy(); box.remove(); document.body.classList.remove("modal-open"); document.removeEventListener("keydown", keys);
    renderProfile();
  };
  const keys = (e) => {
    const mod = e.metaKey || e.ctrlKey;
    if (mod && e.key === "s") { e.preventDefault(); save(); }
    else if (mod && e.key === "Enter") { e.preventDefault(); preview(); }
    else if (mod && e.key === "z" && !e.shiftKey && mode === "visual" && !e.target.closest("[contenteditable]")) { e.preventDefault(); vis.undo(); }
    else if (e.key === "Escape" && !e.target.closest("[contenteditable]")) close();
  };
  document.addEventListener("keydown", keys);
  src.value = mode === "latex" ? (restored ? draft.tex : savedTex || Resume.toTex(model)) : "";
  src.addEventListener("keydown", (e) => { if (e.key === "Tab") { e.preventDefault(); src.setRangeText("  ", src.selectionStart, src.selectionEnd, "end"); } });
  src.addEventListener("input", onEdit);
  box.querySelectorAll(".ed-mode button").forEach((b) => (b.onclick = () => setMode(b.dataset.mode)));
  box.querySelectorAll("[data-style]").forEach((el) => (el.onchange = () =>
    vis.setStyle(el.dataset.style, el.dataset.style === "size" ? +el.value : el.dataset.style === "accent" ? el.value.slice(1).toUpperCase() : el.value)));
  $b("#ed-save").onclick = save;
  $b("#ed-close").onclick = close;
  $b("#ed-pdf")?.addEventListener("click", () => { showPdf = !showPdf; layout(); if (showPdf) preview(); });
  $b("#ed-discard")?.addEventListener("click", (e) => {
    e.preventDefault();
    try { localStorage.removeItem(draftKey); } catch (_) { /* */ }
    box.remove(); document.body.classList.remove("modal-open"); document.removeEventListener("keydown", keys); vis.destroy();
    openEditor({ mode });
  });
  window.toast = toast;                          // the visual editor offers Undo after deleting a section
  layout();
  if (showPdf || mode === "latex") { savedTex && !restored ? showResumePdf($b("#ed-view")) : preview(); }
  if (fromScratch) msg(r?.kind === "pdf" ? "Your resume is a PDF, so this starts from your profile — fill it in and save to switch to an editable resume."
    : "Starting from your profile — fill in the rest and save.");
}

/* ================================================================== SETTINGS */
const pickyLabel = (v) => (v <= 30 ? "Show me more" : v <= 45 ? "Balanced" : v <= 60 ? "Picky" : "Only strong matches");

async function renderSettings() {
  const [settings, st] = await Promise.all([api("/api/settings"), api("/api/status")]);
  status = st;
  const SOURCES = ["remotive", "himalayas", "jobicy", "weworkremotely", "arbeitnow", "hackernews", "greenhouse", "ashby", "lever"];
  const noEngine = !st.jev.available ? `<div class="warnbox"><b>No decision engine is configured.</b> Start the local Kev engine with <code>./run.sh</code> (see <code>.env</code>). Until then a basic keyword heuristic is used.</div>` : "";
  view.innerHTML = `
  <div class="settings">
    <h1>Settings</h1>
    ${noEngine}
    <section class="card-box">
      <h3 class="section-title">Job search</h3>
      <div class="set-row">
        <div><b>How picky</b><div class="faint small">Jobs scoring below this stay off your deck.</div></div>
        <div class="picky"><input type="range" id="s-min" min="20" max="80" step="5" value="${settings.min_match}" aria-label="Minimum match">
          <span id="s-min-v"><b>${settings.min_match}</b> · ${pickyLabel(settings.min_match)}</span></div>
      </div>
      <div class="set-row">
        <div><b>Daily search</b><div class="faint small">${STATIC_MODE ? "Runs on GitHub at this time in your timezone — your devices can be off."
          : st.github?.enabled ? "Runs on GitHub at this time — the Mac can be off." : "Runs while the app is open, and catches up when you next start it."}</div></div>
        <div class="inline"><label class="switch"><input type="checkbox" id="s-daily-on" ${settings.daily_at ? "checked" : ""}><span></span></label>
          <input class="input" type="time" id="s-daily" value="${esc(settings.daily_at || "09:00")}"></div>
      </div>
      <div class="set-row">
        <div><b>Search now</b><div class="faint small" id="last-run"></div></div>
        <button class="btn primary" id="run">Find jobs now</button>
      </div>
      <div id="run-status"></div>
    </section>

    <section class="card-box" id="kaggle-box"></section>

    <details class="card-box adv">
      <summary>Advanced</summary>
      <p class="faint small">These tune themselves; change them only if you have a reason.</p>
      <h4>Job sources</h4>
      <div class="checks" id="s-sources">${SOURCES.map((s) => `<label><input type="checkbox" value="${s}" ${settings.sources.includes(s) ? "checked" : ""}> ${s}</label>`).join("")}</div>
      <div class="range-row"><label for="s-age">Ignore postings older than (days)</label><input type="range" id="s-age" min="7" max="120" step="1" value="${settings.max_age_days}"><b id="s-age-v">${settings.max_age_days}</b></div>
      <div class="range-row"><label for="s-deep">Most jobs to read in depth per run</label><input type="range" id="s-deep" min="25" max="1000" step="25" value="${settings.max_deep}"><b id="s-deep-v">${settings.max_deep}</b></div>
      <label class="checks" style="margin-top:8px"><label><input type="checkbox" id="reeval"> Next manual run: re-read jobs already on the board</label></label>
    </details>
  </div>`;

  const minEl = $("#s-min");
  minEl.oninput = () => ($("#s-min-v").innerHTML = `<b>${minEl.value}</b> · ${pickyLabel(+minEl.value)}`);
  minEl.onchange = async () => {
    const r = await api("/api/settings", { method: "PATCH", json: { min_match: +minEl.value } });
    toast(r.rescored_on_board != null ? `${r.rescored_on_board} jobs on your deck now` : "Saved");
    refreshStatus();
  };
  const bindRange = (id, key) => {
    const el = $(`#${id}`);
    el.oninput = () => ($(`#${id}-v`).textContent = el.value);
    el.onchange = () => api("/api/settings", { method: "PATCH", json: { [key]: +el.value } }).then(() => toast("Saved"));
  };
  bindRange("s-age", "max_age_days"); bindRange("s-deep", "max_deep");
  const saveDaily = async () => {
    const on = $("#s-daily-on").checked, at = $("#s-daily").value || "09:00";
    await api("/api/settings", { method: "PATCH", json: { daily_at: on ? at : "" } });
    toast(on ? `Daily search at ${at}` : "Daily search off");
  };
  $("#s-daily").onchange = () => { $("#s-daily-on").checked = true; saveDaily(); };
  $("#s-daily-on").onchange = saveDaily;
  $("#s-sources").onchange = () => {
    const sources = [...view.querySelectorAll("#s-sources input:checked")].map((i) => i.value);
    api("/api/settings", { method: "PATCH", json: { sources } }).then(() => toast("Sources saved"));
  };
  $("#run").onclick = () => startRun($("#reeval").checked);
  drawRunStatus(st.pipeline, st.last_run);
  if (st.pipeline.running) pollRun();
  renderKaggle(settings);
  if (STATIC_MODE) renderSync(); else renderGithub();
}

async function startRun(reevaluate = false) {
  try {
    const r = await api("/api/pipeline/run", { json: { reevaluate } });
    toast(r.stage?.startsWith("Started on GitHub") || STATIC_MODE ? "Search started on GitHub — new jobs show up when it finishes" : "Searching…");
  } catch (e) { return toast(e.message); }
  if (location.hash.startsWith("#/settings")) pollRun();
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
      <p class="faint" style="font-size:13px;margin:0 0 12px">Daily searches run on GitHub, so this Mac can be off. It syncs your data every 5 minutes and a few seconds after each change.
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
    <p class="faint" style="font-size:13px;margin-top:0">Puts the web app on GitHub Pages and moves daily searches to GitHub, so they happen even when this Mac is off.
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
  $("#s-token-save").onclick = async () => { await Static.setToken($("#s-token").value.trim()); toast("Token saved on this device (encrypted)"); renderSettings(); };
  $("#s-passkey")?.addEventListener("click", async () => {
    try { const n = await Static.addPasskey(navigator.platform || "device"); toast(`Passkey added (${n} total)`); }
    catch (e) { toast(e.message); }
  });
  $("#s-backup").onclick = () => Static.backup();
  $("#s-restore").onchange = async (e) => { try { await Static.restore(e.target.files[0]); toast("Backup merged"); renderSettings(); } catch (err) { toast(`Restore failed: ${err.message}`); } };
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
  const hosted = !!status?.jev?.available && !status.jev.local && status.jev.engine !== "Kev-4B" || STATIC_MODE;
  const fmt = (iso) => iso ? new Date(iso).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" }) : "";
  const stateOf = (a) => {
    if (a._new || !a.key_hint) return `<span class="faint">not saved</span>`;
    if (a.gpu_blocked_until) return `<span class="chip warn" title="${esc(a.quota_message || "")}">GPU used up · back ${esc(fmt(a.gpu_blocked_until))}</span>`;
    if (a.verified === false) return `<span class="chip bad" title="${esc(a.last_error || "")}">key rejected</span>`;
    return `${a.verified ? '<span class="chip good">verified</span>' : '<span class="chip">unverified</span>'} <span class="faint">${a.gpu_hours_this_week} h GPU this week</span>`;
  };
  box.innerHTML = `
    <h3 class="section-title">${hosted ? "Backup compute" : "Where searches run"}</h3>
    <p class="faint small" style="margin-top:0">${hosted
      ? "Searches normally finish in seconds. If the main service is ever unavailable, they carry on using free GPUs on these Kaggle accounts, so a daily search is never skipped."
      : "Searches use free GPUs on these Kaggle accounts (GPU first, then CPU)."}${STATIC_MODE ? " Keys you add here are saved as repository secrets and can't be read back." : ""}</p>
    <div class="checks" style="margin-bottom:12px${STATIC_MODE || hosted ? ";display:none" : ""}">
      <label><input type="radio" name="engine-mode" value="local" ${mode === "local" ? "checked" : ""}> This Mac</label>
      <label><input type="radio" name="engine-mode" value="kaggle" ${mode === "kaggle" ? "checked" : ""}> Kaggle GPUs, then this Mac</label>
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
    <div class="add-foot"><span class="faint">Get a token at Kaggle → Settings → API → <i>Generate New Token</i> (starts with <code>KGAT_</code>); an older <code>kaggle.json</code> key works too. ${STATIC_MODE ? "They go straight into your repository's secrets and can't be read back." : "They stay in this app's local database; the browser only sees the last 4 characters."} GPU hours are tracked from this app's runs only — time used in other notebooks on the same account isn't visible.</span>
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

function drawRunStatus(p, last) {
  const el = $("#run-status");
  if (!el) return;
  const running = p.running;
  $("#run").disabled = running;
  $("#run").textContent = running ? "Searching…" : "Find jobs now";
  const s = last?.stats || {};
  const when = last?.finished ? new Date(last.finished + (last.finished.endsWith("Z") ? "" : "Z")).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" }) : "";
  $("#last-run").textContent = last ? `Last search ${when}: ${s.fresh ?? 0} new postings, ${s.evaluated ?? 0} read in depth, ${s.loaded ?? 0} added to your deck.` : "No searches yet.";
  const w = p.total ? Math.round((100 * p.done) / p.total) : running ? 5 : 0;
  el.innerHTML = `
    ${running ? `<div class="progress"><div style="width:${w}%"></div></div>
      <div class="faint small">${esc(p.stage)}${p.total ? ` · ${p.done}/${p.total}` : ""}</div>` : ""}
    ${p.error ? `<div class="rstat bad">${esc(p.error)}</div>` : ""}
    ${p.log.length ? `<details class="more"${running ? " open" : ""}><summary>Run log</summary><div class="log" id="run-log">${esc(p.log.join("\n"))}</div></details>` : ""}`;
  const log = $("#run-log");
  if (log) log.scrollTop = log.scrollHeight;
}

function pollRun() {
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    if (!location.hash.startsWith("#/settings")) return clearInterval(pollTimer);
    const st = await api("/api/status");
    drawRunStatus(st.pipeline, st.last_run);
    if (!st.pipeline.running) {
      clearInterval(pollTimer);
      refreshStatus();
      if (!st.pipeline.error && st.last_run) toast(`Done — ${st.last_run?.stats?.loaded ?? 0} new jobs on your deck`, { label: "Review", run: () => (location.hash = "#/applications") });
    }
  }, STATIC_MODE ? 15000 : 1000);
}

/* ================================================================== TRACKER */
const STATUSES = ["Saved", "Applied", "Assessment", "Interviewing", "Offer", "Rejected", "Ghosted", "Withdrawn"];
let tracker = { rows: [], q: "", status: "", sort: (() => {
  try { return JSON.parse(localStorage.getItem("tracker-sort")) || { key: "applied_on", dir: 1 }; } catch (_) { return { key: "applied_on", dir: 1 }; }
})() };
// Columns, in reading order. dir 1 = ascending (oldest application first by default).
const TCOLS = [
  { sorts: [["match", "Match"]], cls: "c-match" },
  { sorts: [["role", "Role"], ["company", "Company"]], cls: "c-role" },
  { sorts: [["status", "Status"]], cls: "c-status" },
  { sorts: [["applied_on", "Applied"], ["follow_up", "Follow-up"]], cls: "c-dates" },
  { sorts: [], label: "Next step · Notes", cls: "c-notes" },
];
function sortRows(rows) {
  const { key, dir } = tracker.sort;
  const val = (r) => key === "status" ? STATUSES.indexOf(r.status || "Applied")
    : key === "match" ? (r.match ?? -1) : key === "applied_on" ? (r.applied_on || r.created_at || "")
    : String(r[key] || "").toLowerCase() || "\uffff";                     // blanks last
  return [...rows].sort((a, b) => { const x = val(a), y = val(b); return (x < y ? -1 : x > y ? 1 : 0) * dir; });
}

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
    requestAnimationFrame(() => document.querySelector(`tr[data-id="${row.id}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" }));
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
  const rows = sortRows(tracker.rows.filter((r) =>
    (!tracker.status || r.status === tracker.status) &&
    (!q || [r.company, r.role, r.notes, r.next_step, r.location].some((v) => (v || "").toLowerCase().includes(q)))));
  const wrap = $("#t-table");
  if (!tracker.rows.length) {
    wrap.innerHTML = `<div class="card-box empty"><h2>No applications yet</h2><p>Swipe right on a job, or add one you applied to elsewhere.</p></div>`;
    return;
  }
  const { key: sk, dir } = tracker.sort;
  const arrow = (k) => (k === sk ? (dir > 0 ? " ▲" : " ▼") : "");
  const cell = (r, k, ph = "", type = "text") => `<input class="cell" ${type !== "text" ? `type="${type}"` : ""} data-k="${k}" value="${esc(r[k] || "")}" placeholder="${ph}" aria-label="${ph || k}">`;
  wrap.innerHTML = `
    <div class="t-sort-m"><label>Sort by <select class="input" id="t-sort">${[["applied_on", 1, "Applied — oldest first"], ["applied_on", -1, "Applied — newest first"],
      ["match", -1, "Match — best first"], ["follow_up", 1, "Follow-up — soonest"], ["status", 1, "Status"], ["company", 1, "Company"]]
      .map(([k, d, l]) => `<option value="${k}:${d}" ${k === sk && d === dir ? "selected" : ""}>${l}</option>`).join("")}</select></label></div>
    <div class="table-wrap"><table class="apps">
    <colgroup>${TCOLS.map((c) => `<col class="${c.cls}">`).join("")}<col class="c-del"></colgroup>
    <thead><tr>${TCOLS.map((c) => `<th>${c.sorts.length ? c.sorts.map(([k, l]) => `<button class="th-sort ${k === sk ? "on" : ""}" data-sort="${k}">${l}${arrow(k)}</button>`).join('<span class="th-sep">·</span>')
      : `<span class="th-sort">${c.label}</span>`}</th>`).join("")}<th></th></tr></thead>
    <tbody>${rows.map((r) => `
      <tr data-id="${r.id}">
        <td class="c-match"><span class="m-num ${r.match == null ? "none" : r.match >= 75 ? "strong" : r.match >= 60 ? "good" : "stretch"}" title="${r.job_id ? "Match score from your board" : "Added by hand"}">${r.match != null ? Math.round(r.match) : "—"}</span></td>
        <td class="c-role">
          <div class="rc-line"><input class="cell strong" data-k="role" value="${esc(r.role || "")}" placeholder="Role" aria-label="Role">
            ${r.url ? `<a class="link-out" href="${esc(r.url)}" target="_blank" rel="noopener" title="Open posting" aria-label="Open posting">${ICON.ext}</a>` : ""}
            <button class="icon-btn link-edit" data-link="${r.id}" title="${r.url ? "Change link" : "Add the posting link"}" aria-label="Edit link">${r.url ? "✎" : "+ link"}</button></div>
          ${cell(r, "company", "Company")}
        </td>
        <td class="c-status"><select class="status-sel st-${(r.status || "applied").toLowerCase()}" data-k="status">${STATUSES.map((s) => `<option ${s === r.status ? "selected" : ""}>${s}</option>`).join("")}</select></td>
        <td class="c-dates">
          <label class="d-row"><span>Applied</span>${cell(r, "applied_on", "", "date")}</label>
          <label class="d-row ${r.follow_up && r.follow_up < today && !["Offer", "Rejected", "Withdrawn"].includes(r.status) ? "overdue" : ""}"><span>Follow-up</span>${cell(r, "follow_up", "", "date")}</label>
        </td>
        <td class="c-notes">${cell(r, "next_step", "Next step, e.g. OA due Fri")}
          <textarea class="cell" data-k="notes" rows="1" placeholder="Notes" data-autosize>${esc(r.notes || "")}</textarea></td>
        <td class="c-del"><button class="icon-btn" data-del="${r.id}" title="Delete row" aria-label="Delete row">${ICON.trash}</button></td>
      </tr>`).join("")}</tbody></table></div>
    ${rows.length ? "" : `<p class="faint" style="text-align:center">No rows match your filter.</p>`}
    <p class="faint small" style="margin-top:10px">Every cell saves as you edit. Click a column title to sort. Overdue follow-ups show in red.</p>`;

  const setSort = (key, d) => {
    tracker.sort = { key, dir: d };
    try { localStorage.setItem("tracker-sort", JSON.stringify(tracker.sort)); } catch (_) { /* storage off */ }
    drawTable();
  };
  wrap.querySelectorAll("[data-sort]").forEach((b) => (b.onclick = () => {
    const k = b.dataset.sort;
    setSort(k, k === sk ? -dir : k === "match" ? -1 : 1);
  }));
  $("#t-sort").onchange = (e) => { const [k, d] = e.target.value.split(":"); setSort(k, +d); };
  wrap.querySelectorAll("[data-autosize]").forEach((t) => {
    const fit = () => { t.style.height = "auto"; t.style.height = t.scrollHeight + 2 + "px"; };
    t.addEventListener("input", fit); requestAnimationFrame(fit);
  });
  const save = async (id, patch) => {
    const updated = await api(`/api/applications/${id}`, { method: "PATCH", json: patch });
    const i = tracker.rows.findIndex((r) => String(r.id) === String(id));
    tracker.rows[i] = updated;
    return updated;
  };
  wrap.querySelectorAll("tr[data-id]").forEach((tr) => {
    const id = tr.dataset.id;
    tr.addEventListener("change", async (e) => {
      const k = e.target.dataset.k; if (!k) return;
      await save(id, { [k]: e.target.value });
      if (k === "status" || k === "follow_up") drawTable();
      toast("Saved");
    });
  });
  wrap.querySelectorAll("[data-link]").forEach((b) => (b.onclick = async () => {
    const row = tracker.rows.find((r) => String(r.id) === b.dataset.link);
    const url = prompt("Link to the posting you applied on", row.url || "");
    if (url === null) return;
    const updated = await save(row.id, { url: url.trim() });
    drawTable();
    toast(updated.linked ? "Linked to the job on your board" : "Link saved");
  }));
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
