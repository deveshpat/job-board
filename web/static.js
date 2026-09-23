"use strict";
/* Static mode (GitHub Pages): no server. The UI in app.js keeps calling api("/api/..."), and this file
   answers those calls from your decrypted data:

     keys.json   (public)   your data key, wrapped by passphrase / passkeys          ← web/vault.js
     user.enc    (yours)    profile, settings, swipes, tracker — written by your devices
     board.enc   (Action)   the jobs the scheduled run found — read-only here

   Everything is cached in IndexedDB (encrypted) so the app opens offline; changes to user.enc are pushed
   to the repo's `data` branch with the GitHub API, merging with other devices record by record. */
(function () {
  const S = { key: null, user: null, board: null, userSha: null, cfg: null, token: null,
              dirty: false, pushTimer: null, runsCache: null, runsAt: 0 };
  const nowIso = () => new Date().toISOString().slice(0, 19);
  const today = () => new Date().toISOString().slice(0, 10);

  // -- IndexedDB (one key/value store) ---------------------------------------------------------------
  const idb = (() => {
    let dbp;
    const open = () => (dbp = dbp || new Promise((ok, bad) => {
      const r = indexedDB.open("jobboard", 1);
      r.onupgradeneeded = () => r.result.createObjectStore("kv");
      r.onsuccess = () => ok(r.result);
      r.onerror = () => bad(r.error);
    }));
    const tx = async (mode, fn) => {
      const db = await open();
      return new Promise((ok, bad) => {
        const t = db.transaction("kv", mode), st = t.objectStore("kv"), req = fn(st);
        t.oncomplete = () => ok(req?.result);
        t.onerror = () => bad(t.error);
      });
    };
    return { get: (k) => tx("readonly", (s) => s.get(k)), set: (k, v) => tx("readwrite", (s) => s.put(v, k)),
             del: (k) => tx("readwrite", (s) => s.delete(k)) };
  })();

  // -- GitHub REST -------------------------------------------------------------------------------------
  const GH = {
    url: (p) => `${S.cfg.api || "https://api.github.com"}/repos/${S.cfg.owner}/${S.cfg.repo}${p}`,
    headers(extra = {}) {
      const h = { Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", ...extra };
      if (S.token) h.Authorization = `Bearer ${S.token}`;
      return h;
    },
    async file(path) {                        // → {sha, text} | null
      const r = await fetch(GH.url(`/contents/${path}?ref=data&t=${Date.now()}`), { headers: GH.headers(), cache: "no-store" });
      if (r.status === 404) return null;
      if (!r.ok) throw new Error(`GitHub ${r.status} reading ${path}`);
      const meta = await r.json();
      if (meta.content && meta.encoding === "base64")
        return { sha: meta.sha, text: new TextDecoder().decode(Uint8Array.from(atob(meta.content.replace(/\n/g, "")), (c) => c.charCodeAt(0))) };
      const raw = await fetch(GH.url(`/contents/${path}?ref=data`), { headers: GH.headers({ Accept: "application/vnd.github.raw" }), cache: "no-store" });
      return { sha: meta.sha, text: await raw.text() };
    },
    async put(path, text, sha, message) {
      const b64 = Vault.b64(new TextEncoder().encode(text));
      const r = await fetch(GH.url(`/contents/${path}`), { method: "PUT", headers: GH.headers(),
        body: JSON.stringify({ message, content: b64, sha: sha || undefined, branch: "data" }) });
      if (r.status === 409 || r.status === 422) { const e = new Error("conflict"); e.conflict = true; throw e; }
      if (!r.ok) throw new Error(`GitHub ${r.status} saving ${path}: ${(await r.text()).slice(0, 200)}`);
      return (await r.json()).content.sha;
    },
    async dispatch() {
      const r = await fetch(GH.url("/actions/workflows/daily.yml/dispatches"), { method: "POST", headers: GH.headers(),
        body: JSON.stringify({ ref: "main", inputs: { force: true } }) });
      if (!r.ok) throw new Error(`Couldn't start the run (GitHub ${r.status}) — does your token have Actions: write?`);
    },
    async latestRun() {
      const r = await fetch(GH.url("/actions/workflows/daily.yml/runs?per_page=5"), { headers: GH.headers() });
      if (!r.ok) return null;
      const runs = (await r.json()).workflow_runs || [];
      return runs.find((x) => x.status !== "completed") || runs[0] || null;
    },
    async setSecret(name, value) {
      const pk = await fetch(GH.url("/actions/secrets/public-key"), { headers: GH.headers() });
      if (!pk.ok) throw new Error(`Couldn't read the repo's secret key (GitHub ${pk.status}) — token needs Secrets: write`);
      const { key, key_id } = await pk.json();
      const sodium = (await import("https://cdn.jsdelivr.net/npm/libsodium-wrappers@0.7.15/+esm")).default;
      await sodium.ready;
      const sealed = sodium.crypto_box_seal(sodium.from_string(value), sodium.from_base64(key, sodium.base64_variants.ORIGINAL));
      const r = await fetch(GH.url(`/actions/secrets/${name}`), { method: "PUT", headers: GH.headers(),
        body: JSON.stringify({ encrypted_value: sodium.to_base64(sealed, sodium.base64_variants.ORIGINAL), key_id }) });
      if (!r.ok) throw new Error(`GitHub ${r.status} saving secret ${name}`);
    },
    async deleteSecret(name) {
      await fetch(GH.url(`/actions/secrets/${name}`), { method: "DELETE", headers: GH.headers() });
    },
  };
  const MAX_SLOTS = 5;                      // .github/workflows/daily.yml passes KAGGLE_KEY_1 … KAGGLE_KEY_5

  // -- user-state merge (same rules as app/state.py merge_user) ----------------------------------------
  function mergeUser(a, b) {
    if (!a) return b;
    if (!b) return a;
    const out = { schema: 1, exported_at: nowIso(), meta: {} };
    for (const part of ["profile", "settings", "kaggle"]) {
      const src = (a.meta?.[part] || "") > (b.meta?.[part] || "") ? a : b;   // tie → remote (b) wins, like app/state.py
      out[part] = src[part];
      out.meta[part] = src.meta?.[part] || "";
    }
    out.decisions = { ...(a.decisions || {}) };
    for (const [j, d] of Object.entries(b.decisions || {}))
      if (!out.decisions[j] || (d.at || "") > (out.decisions[j].at || "")) out.decisions[j] = d;
    const dead = { ...(a.deleted_apps || {}) };
    for (const [u, at] of Object.entries(b.deleted_apps || {})) dead[u] = at > (dead[u] || "") ? at : dead[u];
    const apps = {};
    for (const r of [...(a.applications || []), ...(b.applications || [])])
      if (!apps[r.uid] || (r.updated_at || "") > (apps[r.uid].updated_at || "")) apps[r.uid] = r;
    out.applications = Object.values(apps).filter((r) => (dead[r.uid] || "") < (r.updated_at || ""));
    out.deleted_apps = dead;
    return out;
  }

  // -- load / save -----------------------------------------------------------------------------------
  async function loadRemote() {
    const [u, b] = await Promise.all([GH.file("user.enc"), GH.file("board.enc")]);
    if (b) { S.board = await Vault.decryptBlob(S.key, "board", b.text); await idb.set("board.enc", b.text); }
    if (u) {
      const remote = await Vault.decryptBlob(S.key, "user", u.text);
      S.userSha = u.sha;
      S.user = S.dirty ? mergeUser(S.user, remote) : remote;
      await idb.set("user.enc", u.text);
      await idb.set("user.sha", u.sha);
    }
  }

  async function saveLocal() {
    await idb.set("user.local", await Vault.encryptBlob(S.key, "user", S.user));
    await idb.set("user.dirty", S.dirty);
  }

  function changed(part) {
    if (part) S.user.meta = { ...(S.user.meta || {}), [part]: nowIso() };
    S.dirty = true;
    saveLocal();
    clearTimeout(S.pushTimer);
    S.pushTimer = setTimeout(push, 2500);
  }

  async function push() {
    if (!S.dirty || !S.token) return;
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        const text = await Vault.encryptBlob(S.key, "user", S.user);
        S.userSha = await GH.put("user.enc", text, S.userSha, "update from web app");
        S.dirty = false;
        await idb.set("user.sha", S.userSha);
        await saveLocal();
        return;
      } catch (e) {
        if (!e.conflict) { console.warn(e); return; }          // offline etc.: retried on the next change
        const remote = await GH.file("user.enc");               // another device saved first: merge, retry
        S.user = mergeUser(S.user, await Vault.decryptBlob(S.key, "user", remote.text));
        S.userSha = remote.sha;
      }
    }
  }
  window.addEventListener("online", push);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") push(); });

  // -- helpers mirroring app/urls.py ------------------------------------------------------------------
  function normUrl(u) {
    try {
      const x = new URL(String(u).trim().toLowerCase());
      const q = [...x.searchParams].filter(([k]) => !/^(utm_.*|ref|source|src|gh_src|lever-source|lever-origin|fbclid|gclid)$/.test(k)).sort();
      return x.host.replace(/^www\./, "") + x.pathname.replace(/\/+$/, "") + (q.length ? "?" + new URLSearchParams(q) : "");
    } catch (_) { return String(u || "").toLowerCase(); }
  }
  const ATS = [[/(?:job-)?boards(?:-api)?\.greenhouse\.io\/([^/]+)/, "greenhouse"], [/jobs\.(?:eu\.)?lever\.co\/([^/]+)/, "lever"],
    [/jobs\.ashbyhq\.com\/([^/]+)/, "ashby"], [/apply\.workable\.com\/([^/]+)/, "workable"], [/jobs\.smartrecruiters\.com\/([^/]+)/, "smartrecruiters"],
    [/jobs\.polymer\.co\/([^/]+)/, "polymer"], [/([^./]+)\.zohorecruit\.(?:com|in)/, "zoho"], [/([^./]+)\.bamboohr\.com/, "bamboohr"],
    [/([^./]+)\.wd\d+\.myworkdayjobs\.com/, "workday"], [/([^./]+)\.breezy\.hr/, "breezy"], [/([^./]+)\.eightfold\.ai/, "eightfold"]];
  function fromUrl(u) {
    try {
      const x = new URL(u), hp = x.host.toLowerCase() + x.pathname;
      for (const [re, source] of ATS) {
        const m = hp.match(re);
        if (m) return { company: m[1].replace(/[-_]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()), source };
      }
      return { company: null, source: x.host.replace(/^(www|jobs|careers|apply)\./, "") };
    } catch (_) { return { company: null, source: "manual" }; }
  }

  // -- the /api emulation ------------------------------------------------------------------------------
  const APP_FIELDS = ["company", "role", "url", "location", "source", "applied_on", "status", "next_step", "follow_up", "notes", "match"];
  const statusOf = (j) => S.user.decisions?.[j.id]?.status || j.status;
  const jobsView = () => (S.board?.jobs || []).map((j) => ({ ...j, status: statusOf(j) }));
  const app = (row) => ({ ...row, id: row.uid });
  const settings = () => ({ ...(S.board?.defaults || { min_match: 40, max_age_days: 45, max_deep: 300, daily_at: "09:00" }),
                            ...(S.user.settings || {}), engine_mode: "kaggle" });
  function counts() {
    const c = {};
    for (const j of jobsView()) c[j.status] = (c[j.status] || 0) + 1;
    return c;
  }
  const httpError = (msg) => { const e = new Error(msg); throw e; };

  async function runStatus() {
    if (!S.token) return null;
    if (Date.now() - S.runsAt > 15000) { S.runsCache = await GH.latestRun().catch(() => null); S.runsAt = Date.now(); }
    return S.runsCache;
  }

  async function api(path, opts = {}) {
    const method = (opts.method || (opts.json !== undefined ? "POST" : "GET")).toUpperCase();
    const body = opts.json;
    const [p, qs] = path.split("?");
    const q = new URLSearchParams(qs || "");
    let m;

    if (p === "/api/status") {
      const run = await runStatus();
      const running = !!run && run.status !== "completed";
      if (S.wasRunning && !running) await loadRemote().catch(() => {});   // a run just finished: fetch its board
      S.wasRunning = running;
      return {
        jev: { available: true, engine: "Kev-4B on Kaggle", local: false, engine_state: null, model: "kev-4b" },
        counts: counts(), has_profile: !!S.user.profile, static: true, token: !!S.token,
        pipeline: { running, stage: running ? `GitHub Actions: ${run.status.replace("_", " ")}` : "idle", done: 0, total: 0,
                    log: S.board?.last_log || [], error: null },
        last_run: S.board?.last_run ? { ...S.board.last_run, stats: typeof S.board.last_run.stats === "string" ? JSON.parse(S.board.last_run.stats) : S.board.last_run.stats } : null,
      };
    }
    if (p === "/api/profile" && method === "GET")
      return S.user.profile ? { ...S.user.profile, ...(S.board?.labels || {}) } : null;
    if (p === "/api/profile" && method === "PATCH") {
      S.user.profile = { ...S.user.profile, ...body };
      changed("profile");
      return { ...S.user.profile, ...(S.board?.labels || {}) };
    }
    if (p === "/api/profile/build") httpError("Rebuilding the profile needs Kev — use the Mac app for that (it syncs here).");
    if (p === "/api/settings" && method === "GET") return settings();
    if (p === "/api/settings" && method === "PATCH") {
      S.user.settings = { ...(S.user.settings || {}), ...body, tz_offset_min: -new Date().getTimezoneOffset() };
      delete S.user.settings.engine_mode;
      changed("settings");
      return { ...settings(), rescored_on_board: null };
    }
    if (p === "/api/pipeline/run") {
      if (!S.token) httpError("Add your GitHub token (Profile → Sync) to start runs from here.");
      await GH.dispatch();
      S.runsAt = 0;
      return { running: true, stage: "GitHub Actions: queued", done: 0, total: 0, log: [], error: null };
    }
    if (p === "/api/pipeline/status") return (await api("/api/status")).pipeline;
    if (p === "/api/jobs") {
      const want = (q.get("status") || "new").split(",");
      return jobsView().filter((j) => want.includes(j.status)).sort((a, b) => (b.match ?? -1) - (a.match ?? -1));
    }
    if ((m = p.match(/^\/api\/jobs\/([^/]+)\/decision$/))) {
      const id = decodeURIComponent(m[1]);
      const status = { apply: "applied", reject: "rejected", later: "later", undo: "new" }[body.action];
      S.user.decisions = { ...(S.user.decisions || {}), [id]: { status, at: nowIso() } };
      let application = null;
      if (body.action === "apply") {
        const j = (S.board?.jobs || []).find((x) => x.id === id);
        const existing = S.user.applications.find((a) => a.job_id === id);
        application = existing || { uid: crypto.randomUUID().replace(/-/g, ""), job_id: id, company: j?.company, role: j?.title,
          url: j?.url, location: j?.location, source: j?.source, status: "Applied", applied_on: today(), match: j?.match,
          next_step: null, follow_up: null, notes: null, created_at: nowIso(), updated_at: nowIso() };
        if (!existing) S.user.applications.push(application);
        application = app(application);
      }
      changed();
      return { status, application };
    }
    if (p === "/api/applications" && method === "GET")
      return S.user.applications.map(app).sort((a, b) => (b.applied_on || b.created_at || "").localeCompare(a.applied_on || a.created_at || ""));
    if (p === "/api/applications" && method === "POST") {
      const row = { uid: body.uid || crypto.randomUUID().replace(/-/g, ""), job_id: body.job_id || null, created_at: nowIso(), updated_at: nowIso() };
      for (const k of APP_FIELDS) row[k] = body[k] ?? null;
      row.status ||= "Applied";
      row.applied_on ||= today();
      const job = row.url && (S.board?.jobs || []).find((j) => normUrl(j.url) === normUrl(row.url));
      if (job) { row.job_id = job.id; row.match ??= job.match; S.user.decisions[job.id] = { status: "applied", at: nowIso() }; }
      S.user.applications.push(row);
      changed();
      return { ...app(row), linked: job ? { title: job.title, company: job.company } : null };
    }
    if (p === "/api/applications/lookup") {
      const url = q.get("url");
      const job = (S.board?.jobs || []).find((j) => normUrl(j.url) === normUrl(url));
      if (job) return { company: job.company, role: job.title, location: job.location, source: job.source, match: job.match, from: "board" };
      return { ...fromUrl(url), role: null, location: null, match: null, from: "url" };
    }
    if ((m = p.match(/^\/api\/applications\/([^/]+)$/))) {
      const uid = decodeURIComponent(m[1]);
      const row = S.user.applications.find((a) => a.uid === uid);
      if (!row) httpError("Not found");
      if (method === "DELETE") {
        S.user.applications = S.user.applications.filter((a) => a.uid !== uid);
        S.user.deleted_apps = { ...(S.user.deleted_apps || {}), [uid]: nowIso() };
        changed();
        return { ok: true };
      }
      for (const k of APP_FIELDS) if (k in body) row[k] = body[k];
      row.updated_at = nowIso();
      let linked = null;
      if ("url" in body) {
        const job = row.url && (S.board?.jobs || []).find((j) => normUrl(j.url) === normUrl(row.url));
        row.job_id = job ? job.id : null;
        if (job) { S.user.decisions[job.id] = { status: "applied", at: nowIso() }; linked = { title: job.title, company: job.company }; }
      }
      changed();
      return { ...app(row), linked };
    }
    if (p === "/api/kaggle/accounts" && method === "GET") {
      const usage = Object.fromEntries((S.board?.kaggle || []).map((k) => [k.username.toLowerCase(), k]));
      return (S.user.kaggle || []).map((k) => ({ ...(usage[k.username.toLowerCase()] || { gpu_hours_this_week: 0 }),
        username: k.username, enabled: k.enabled !== false, key_hint: k.key_set ? "•••• in GitHub secrets" : "", verified: null }));
    }
    if (p === "/api/kaggle/accounts" && method === "PUT") {
      const prevList = S.user.kaggle || [];
      const next = [];
      const used = new Set(body.map((a) => prevList.find((k) => k.username === a.username)?.slot).filter(Boolean));
      for (const a of body) {
        const prev = prevList.find((k) => k.username === a.username) || {};
        let slot = prev.slot;
        if (!slot) {
          slot = [1, 2, 3, 4, 5].find((n) => !used.has(n));
          if (!slot) httpError(`At most ${MAX_SLOTS} Kaggle accounts can be used with GitHub runs.`);
          used.add(slot);
        }
        if (a.key) {
          if (!S.token) httpError("Add your GitHub token first — keys are saved as repository secrets.");
          await GH.setSecret(`KAGGLE_KEY_${slot}`, a.key);
        }
        next.push({ username: a.username, enabled: a.enabled !== false, slot, key_set: !!(a.key || prev.key_set) });
      }
      for (const k of prevList)
        if (!next.find((n) => n.username === k.username) && k.slot && S.token) await GH.deleteSecret(`KAGGLE_KEY_${k.slot}`);
      S.user.kaggle = next;
      changed("kaggle");
      return api("/api/kaggle/accounts");
    }
    if ((m = p.match(/^\/api\/kaggle\/accounts\/([^/]+)\/verify$/)))
      return { ok: null, detail: "Keys are checked by the next scheduled run on GitHub.", accounts: await api("/api/kaggle/accounts") };
    if (p === "/api/applications.csv") return csv();
    httpError(`Not available in the web app: ${method} ${p}`);
  }

  function csv() {
    const cols = ["company", "role", "status", "applied_on", "next_step", "follow_up", "location", "source", "url", "notes", "match"];
    const cell = (v) => (v == null ? "" : /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v));
    return [cols.join(","), ...S.user.applications.map((r) => cols.map((c) => cell(r[c])).join(","))].join("\n") + "\n";
  }

  // -- unlock & setup screen ------------------------------------------------------------------------------
  function repoFromLocation() {
    const owner = location.hostname.endsWith(".github.io") ? location.hostname.split(".")[0] : "";
    const repo = location.pathname.split("/").filter(Boolean)[0] || "";
    return { owner, repo };
  }

  async function unlocked(key, remember) {
    S.key = key;
    if (remember) await idb.set("remembered_key", Vault.b64(key));
    const tok = await idb.get("token.enc");
    try { if (tok) S.token = (await Vault.decryptBlob(key, "token", tok)).token; } catch (_) { S.token = null; }
    const local = await idb.get("user.local");
    S.dirty = !!(await idb.get("user.dirty"));
    S.userSha = await idb.get("user.sha");
    try {
      S.user = local ? await Vault.decryptBlob(key, "user", local) : null;
      const cachedBoard = await idb.get("board.enc");
      if (cachedBoard) S.board = await Vault.decryptBlob(key, "board", cachedBoard);
    } catch (_) {                              // cache from an older key (data reset): start from the repo copy
      S.user = null; S.board = null; S.dirty = false; S.userSha = null;
      for (const k of ["user.local", "user.dirty", "user.sha", "board.enc", "token.enc"]) await idb.del(k);
    }
    try { await loadRemote(); } catch (e) { console.warn("offline — using this device's copy", e); }
    if (!S.user) throw new Error("No data yet — publish from the Mac app first.");
    S.user.decisions ||= {}; S.user.applications ||= []; S.user.deleted_apps ||= {};
    navigator.storage?.persist?.();          // ask the browser not to evict this site's storage
    push();
  }

  async function boot(start) {
    S.cfg = (await idb.get("config")) || repoFromLocation();
    const dev = new URLSearchParams(location.search);           // testing against a local GitHub stand-in
    if (dev.get("gh_api")) { S.cfg = { owner: dev.get("owner"), repo: dev.get("repo"), api: dev.get("gh_api") }; await idb.set("config", S.cfg); }
    const remembered = await idb.get("remembered_key");
    let keysText = null;
    try { keysText = S.cfg.owner && (await GH.file("keys.json"))?.text; } catch (_) { /* offline */ }
    keysText = keysText || (await idb.get("keys.json"));
    if (keysText) await idb.set("keys.json", keysText);
    if (remembered && keysText) {
      try { await unlocked(Vault.unb64(remembered), true); return start(); } catch (e) { console.warn(e); }
    }
    renderUnlock(keysText ? JSON.parse(keysText) : null, start);
  }

  function renderUnlock(keys, start) {
    const hasPasskey = keys?.wraps?.some((w) => w.kind === "passkey");
    document.querySelector(".tabs").style.visibility = "hidden";
    const view = document.getElementById("view");
    view.innerHTML = `
      <div class="card-box unlock">
        <h1>Job Board</h1>
        ${keys ? `<p class="muted">Your data is encrypted. Unlock it on this device.</p>
          <form id="u-form" class="stack" autocomplete="off">
            <input class="input" type="password" id="u-pass" placeholder="Passphrase" autocomplete="current-password">
            <label class="checks"><label><input type="checkbox" id="u-remember"> Keep unlocked on this device</label></label>
            <button class="btn primary">Unlock</button>
            ${hasPasskey ? `<button type="button" class="btn" id="u-passkey">Unlock with passkey</button>` : ""}
          </form>`
        : `<p class="muted">No encrypted data found for <b>${S.cfg.owner || "?"}/${S.cfg.repo || "?"}</b> yet.</p>
           <p class="muted">Set it up once from the Mac app: Profile → <i>Publish to GitHub</i>. Using someone else's copy? Make your own with "Use this template" on GitHub.</p>`}
        <p class="faint" id="u-msg" aria-live="polite"></p>
        <details class="faint"><summary>Repository</summary>
          <div class="add-row"><input class="input" id="u-repo" value="${S.cfg.owner}/${S.cfg.repo}"><button class="btn" id="u-repo-save" type="button">Use</button></div></details>
      </div>`;
    const msg = (t) => (document.getElementById("u-msg").textContent = t);
    document.getElementById("u-repo-save").onclick = async () => {
      const [owner, repo] = document.getElementById("u-repo").value.trim().split("/");
      await idb.set("config", { owner, repo });
      await idb.del("keys.json");
      location.reload();
    };
    if (!keys) return;
    const done = async (key, remember) => {
      if (!key) return msg("That passphrase didn't unlock anything.");
      msg("Unlocked — loading your data…");
      try { await unlocked(key, remember); } catch (e) { return msg(e.message); }
      document.querySelector(".tabs").style.visibility = "";
      start();
    };
    document.getElementById("u-form").onsubmit = async (e) => {
      e.preventDefault();
      msg("Checking…");
      done(await Vault.unlockPassphrase(keys, document.getElementById("u-pass").value), document.getElementById("u-remember").checked);
    };
    document.getElementById("u-passkey")?.addEventListener("click", async () => {
      try { done(await Vault.unlockPasskey(keys), document.getElementById("u-remember").checked); }
      catch (e) { msg(e.message); }
    });
  }

  // -- things only the web app does (Profile → Sync & security card) ---------------------------------------
  async function setToken(token) {
    S.token = token || null;
    if (token) await idb.set("token.enc", await Vault.encryptBlob(S.key, "token", { token }));
    else await idb.del("token.enc");
    S.runsAt = 0;
    await push();
  }

  async function addPasskey(label) {
    if (!S.token) throw new Error("Add your GitHub token first — the passkey is saved to keys.json in the repo.");
    const f = await GH.file("keys.json");
    const keys = JSON.parse(f.text);
    keys.wraps.push(await Vault.addPasskey(S.key, label));
    await GH.put("keys.json", JSON.stringify(keys, null, 1), f.sha, `add passkey (${label})`);
    await idb.set("keys.json", JSON.stringify(keys));
    return keys.wraps.filter((w) => w.kind === "passkey").length;
  }

  async function lock() {
    await idb.del("remembered_key");
    location.reload();
  }

  async function backup() {
    const blob = new Blob([JSON.stringify({ kind: "jobboard-backup", saved: nowIso(),
      "keys.json": await idb.get("keys.json"), "user.enc": await Vault.encryptBlob(S.key, "user", S.user),
      "board.enc": await idb.get("board.enc") })], { type: "application/json" });
    const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: `jobboard-backup-${today()}.json` });
    a.click();
  }

  async function restore(file) {
    const b = JSON.parse(await file.text());
    const user = await Vault.decryptBlob(S.key, "user", b["user.enc"]);   // must be this data key
    S.user = mergeUser(S.user, user);
    changed();
  }

  window.Static = { boot, api, setToken, addPasskey, lock, backup, restore, csv,
                    state: () => ({ token: !!S.token, dirty: S.dirty, repo: S.cfg, passkeys: Vault.passkeysSupported() }) };
})();
