"use strict";
/* Setup: your own Job Board in a few minutes, from one GitHub token.

   1. makes your copy of this repository (GitHub "template" → your account, public; your data stays encrypted)
   2. generates a passphrase that only you keep, and encrypts a fresh, empty board with it (branch `data`)
   3. saves the secrets the daily search needs (repository secrets, sealed in this browser)
   4. turns on GitHub Pages and publishes your site at https://<you>.github.io/<name>/

   Nothing is sent anywhere except GitHub's API (and, if you add them, your own keys go straight into your
   repository's secrets). The token is kept inside your encrypted data so your devices can sync. */
(function () {
  const $ = (s) => document.querySelector(s);
  const view = $("#view");
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const params = new URLSearchParams(location.search);
  const API = params.get("gh_api") || "https://api.github.com";           // gh_api: local test stand-in
  const TEMPLATE = {                                                      // the repository this page is served from
    owner: params.get("template_owner") || (location.hostname.endsWith(".github.io") ? location.hostname.split(".")[0] : "deveshpat"),
    repo: params.get("template_repo") || location.pathname.split("/").filter(Boolean)[0] || "job-board",
  };
  const TOKEN_URL = "https://github.com/settings/tokens/new?scopes=repo,workflow&description=Job%20Board";
  const S = { token: "", login: "", name: "job-board", phrase: "", confirmed: false, kaggle: { username: "", key: "" }, jev: "" };
  const nowIso = () => new Date().toISOString().slice(0, 19);

  // -- GitHub ---------------------------------------------------------------------------------------------------
  async function gh(method, path, body, ok = [200, 201, 204]) {
    const r = await fetch(`${API}${path}`, { method, headers: { Accept: "application/vnd.github+json", Authorization: `Bearer ${S.token}`,
      "X-GitHub-Api-Version": "2022-11-28", ...(body ? { "Content-Type": "application/json" } : {}) }, body: body ? JSON.stringify(body) : undefined });
    if (!ok.includes(r.status)) {
      let msg = r.statusText;
      try { msg = (await r.json()).message || msg; } catch (_) { /* */ }
      const e = new Error(`GitHub ${r.status}: ${msg}`); e.status = r.status; throw e;
    }
    return r.status === 204 ? null : r.json().catch(() => null);
  }
  const repoPath = (p = "") => `/repos/${S.login}/${S.name}${p}`;
  const b64 = (text) => Vault.b64(new TextEncoder().encode(text));
  async function setSecret(name, value) {
    const pk = await gh("GET", repoPath("/actions/secrets/public-key"));
    const sodium = (await import("https://cdn.jsdelivr.net/npm/libsodium-wrappers@0.7.15/+esm")).default;
    await sodium.ready;
    const sealed = sodium.crypto_box_seal(sodium.from_string(value), sodium.from_base64(pk.key, sodium.base64_variants.ORIGINAL));
    await gh("PUT", repoPath(`/actions/secrets/${name}`), { encrypted_value: sodium.to_base64(sealed, sodium.base64_variants.ORIGINAL), key_id: pk.key_id });
  }
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // -- passphrase (same EFF list and 6 words as the Mac app) ------------------------------------------------------
  let WORDS = null;
  async function newPhrase() {
    WORDS ||= (await (await fetch("static/wordlist.txt")).text()).split(/\s+/).filter(Boolean);
    const pick = new Uint32Array(6);
    crypto.getRandomValues(pick);
    return [...pick].map((n) => WORDS[n % WORDS.length]).join(" ");       // 2^32 % 7776 bias is negligible
  }
  const norm = (p) => p.toLowerCase().split(/\s+/).filter(Boolean).join(" ");

  // -- steps -----------------------------------------------------------------------------------------------------
  const steps = ["Welcome", "GitHub", "Your copy", "Passphrase", "AI screening", "Build"];
  function frame(i, body) {
    view.innerHTML = `
      <div class="setup">
        <ol class="su-steps">${steps.map((s, k) => `<li class="${k < i ? "done" : k === i ? "on" : ""}">${s}</li>`).join("")}</ol>
        <section class="card-box su-card">${body}</section>
      </div>`;
  }

  function welcome() {
    frame(0, `
      <h1>Your own AI job board</h1>
      <p class="su-lead">It finds jobs that fit your resume every day, gives you a quick gut check on each one, and keeps track of what you apply to.</p>
      <ul class="su-list">
        <li><b>Free.</b> It runs on GitHub's free plan — no subscription, no server.</li>
        <li><b>Private.</b> Your resume and applications are encrypted with a passphrase only you know. Not even GitHub can read them.</li>
        <li><b>Yours.</b> It's your own copy, at <code>https://&lt;your-name&gt;.github.io/job-board</code>.</li>
      </ul>
      <p class="faint small">Takes about 5 minutes. You'll need a free GitHub account.</p>
      <div class="su-actions"><button class="btn primary" id="go">Start</button></div>`);
    $("#go").onclick = github;
  }

  function github() {
    frame(1, `
      <h2>Connect GitHub</h2>
      <ol class="su-how">
        <li>No GitHub account? <a href="https://github.com/signup" target="_blank" rel="noopener">Create one</a> (free), then come back.</li>
        <li><a class="btn small" href="${TOKEN_URL}" target="_blank" rel="noopener">Create a key for Job Board</a> — GitHub opens with everything ticked.
          Scroll down, press <b>Generate token</b>, and copy it.</li>
        <li>Paste it here:</li>
      </ol>
      <input class="input" id="tok" type="password" placeholder="ghp_…" autocomplete="off" spellcheck="false">
      <p class="faint small">The key lets this page create your copy and lets your board save your data. It stays in this browser and,
        encrypted, in your own repository — never anywhere else.</p>
      <div class="su-actions"><button class="btn" id="back">Back</button><button class="btn primary" id="go">Continue</button></div>
      <p class="su-msg" id="msg" aria-live="polite"></p>`);
    $("#back").onclick = welcome;
    $("#tok").value = S.token;
    $("#go").onclick = async () => {
      S.token = $("#tok").value.trim();
      if (!S.token) return ($("#msg").textContent = "Paste the key first.");
      $("#msg").textContent = "Checking…";
      try {
        const r = await fetch(`${API}/user`, { headers: { Authorization: `Bearer ${S.token}`, Accept: "application/vnd.github+json" } });
        if (!r.ok) throw new Error(r.status === 401 ? "GitHub didn't accept that key — copy it again?" : `GitHub ${r.status}`);
        const scopes = r.headers.get("x-oauth-scopes");
        if (scopes !== null && !(/\brepo\b/.test(scopes) && /\bworkflow\b/.test(scopes)))
          throw new Error("That key is missing the “repo” and “workflow” boxes — use the Create a key link, which ticks them for you.");
        S.login = (await r.json()).login;
        yourCopy();
      } catch (e) { $("#msg").textContent = e.message; }
    };
  }

  function yourCopy() {
    frame(2, `
      <h2>Name your copy</h2>
      <p>Signed in as <b>${esc(S.login)}</b>. Your board will live at:</p>
      <p class="su-url">https://${esc(S.login.toLowerCase())}.github.io/<input class="input inline-input" id="name" value="${esc(S.name)}" spellcheck="false"></p>
      <p class="faint small">It's a public repository (GitHub's free Pages need that), but everything personal in it is encrypted.</p>
      <div class="su-actions"><button class="btn" id="back">Back</button><button class="btn primary" id="go">Continue</button></div>
      <p class="su-msg" id="msg" aria-live="polite"></p>`);
    $("#back").onclick = github;
    $("#go").onclick = async () => {
      S.name = $("#name").value.trim().replace(/[^\w.-]+/g, "-") || "job-board";
      $("#msg").textContent = "Checking the name…";
      try {
        const r = await fetch(`${API}/repos/${S.login}/${S.name}`, { headers: { Authorization: `Bearer ${S.token}` } });
        if (r.ok) {
          const repo = await r.json();
          if (repo.template_repository?.full_name?.toLowerCase() !== `${TEMPLATE.owner}/${TEMPLATE.repo}`.toLowerCase())
            return ($("#msg").textContent = `You already have a repository called “${S.name}” — pick another name.`);
          const keys = await fetch(`${API}/repos/${S.login}/${S.name}/contents/keys.json?ref=data`, { headers: { Authorization: `Bearer ${S.token}` } });
          if (keys.ok) return ($("#msg").innerHTML = `You already have a board there — <a href="https://${esc(S.login.toLowerCase())}.github.io/${esc(S.name)}/">open it</a>, or pick another name.`);
          S.existing = true;                                            // made earlier but setup didn't finish: reuse it
        } else S.existing = false;
        passphrase();
      } catch (e) { $("#msg").textContent = e.message; }
    };
  }

  async function passphrase() {
    S.phrase = S.phrase || await newPhrase();
    frame(3, `
      <h2>Your passphrase</h2>
      <p>This is the password for your board, on every device. Six random words — strong, and easy to type.</p>
      <div class="phrase" id="ph">${esc(S.phrase)}</div>
      <div class="su-tools" id="tools">
        <button class="btn small" id="copy">Copy</button><button class="btn small" id="dl">Download as .txt</button>
        <button class="btn small" id="again">Different words</button></div>
      <p class="warnbox small"><b>Save it now</b> — in a password manager, or written down. Nobody can recover it for you, not even us.</p>
      <label class="checks"><label><input type="checkbox" id="saved"> I've saved it somewhere safe</label></label>
      <div id="confirm" hidden><p>Now type it from where you saved it:</p>
        <input class="input" id="retype" autocomplete="off" spellcheck="false" autocapitalize="off" placeholder="six words"></div>
      <div class="su-actions"><button class="btn" id="back">Back</button><button class="btn primary" id="go" disabled>Continue</button></div>
      <p class="su-msg" id="msg" aria-live="polite"></p>`);
    $("#back").onclick = yourCopy;
    $("#again").onclick = async () => { S.phrase = await newPhrase(); passphrase(); };
    $("#copy").onclick = async () => { try { await navigator.clipboard.writeText(S.phrase); $("#msg").textContent = "Copied."; } catch (_) { $("#msg").textContent = "Select the words and copy them."; } };
    $("#dl").onclick = () => Object.assign(document.createElement("a"), { download: "job-board-passphrase.txt",
      href: URL.createObjectURL(new Blob([`Job Board passphrase\n\n${S.phrase}\n\nhttps://${S.login.toLowerCase()}.github.io/${S.name}/\n`], { type: "text/plain" })) }).click();
    $("#saved").onchange = (e) => {
      $("#confirm").hidden = !e.target.checked;
      $("#ph").textContent = e.target.checked ? "•••••• hidden — type it back below" : S.phrase;
      $("#ph").classList.toggle("phrase-hidden", e.target.checked);
      $("#tools").hidden = e.target.checked;
      if (e.target.checked) $("#retype").focus();
    };
    $("#retype").oninput = (e) => {
      S.confirmed = norm(e.target.value) === norm(S.phrase);
      $("#go").disabled = !S.confirmed;
      $("#msg").textContent = S.confirmed ? "✓ That's it." : "";
    };
    $("#go").onclick = screening;
  }

  function screening() {
    frame(4, `
      <h2>AI screening</h2>
      <p>Your board reads each job and judges how well it fits you. Connect one of these so it can — or skip and add one later in Settings.</p>
      <details class="su-opt" open><summary><b>Free:</b> a Kaggle account (recommended)</summary>
        <ol class="su-how">
          <li><a href="https://www.kaggle.com/account/login?phase=startRegisterTab" target="_blank" rel="noopener">Create a free Kaggle account</a> and verify your phone number (Kaggle needs this for free GPUs).</li>
          <li>Open <a href="https://www.kaggle.com/settings" target="_blank" rel="noopener">Kaggle → Settings</a> → <b>API</b> → <b>Generate New Token</b>, and copy it.</li>
        </ol>
        <div class="su-row"><input class="input" id="ku" placeholder="Kaggle username" autocomplete="off" value="${esc(S.kaggle.username)}">
          <input class="input" id="kk" type="password" placeholder="KGAT_… token" autocomplete="off"></div></details>
      <details class="su-opt"><summary><b>Faster:</b> a TypeSafe API key (paid by you)</summary>
        <p class="small">Screens a whole day's jobs in about a minute. Get a key at <a href="https://typesafe.ai" target="_blank" rel="noopener">typesafe.ai</a>; typically cents a day.</p>
        <input class="input" id="jk" type="password" placeholder="TypeSafe API key" autocomplete="off"></details>
      <p class="faint small">Keys go straight into your repository's secrets — they can't be read back, even by you.</p>
      <div class="su-actions"><button class="btn" id="back">Back</button><button class="btn" id="skip">Skip for now</button><button class="btn primary" id="go">Build my board</button></div>`);
    $("#back").onclick = passphrase;
    $("#skip").onclick = () => { S.kaggle = { username: "", key: "" }; S.jev = ""; build(); };
    $("#go").onclick = () => {
      S.kaggle = { username: $("#ku").value.trim(), key: $("#kk").value.trim() };
      S.jev = $("#jk").value.trim();
      build();
    };
  }

  // -- the build -------------------------------------------------------------------------------------------------
  async function build() {
    const TASKS = [
      ["copy", "Make your copy on GitHub"], ["data", "Encrypt your (empty) board"], ["secrets", "Save the secrets"],
      ["pages", "Turn on your website"], ["publish", "Publish your website"]];
    frame(5, `
      <h2>Building your board</h2>
      <ul class="su-tasks">${TASKS.map(([k, t]) => `<li id="t-${k}"><span class="st"></span>${t}<small></small></li>`).join("")}</ul>
      <p class="su-msg" id="msg" aria-live="polite"></p><div id="done"></div>`);
    const mark = (k, state, note = "") => { const li = $(`#t-${k}`); li.className = state; li.querySelector("small").textContent = note; };
    const site = `https://${S.login.toLowerCase()}.github.io/${S.name}/`;
    let step = "copy";
    try {
      // 1. the copy
      mark("copy", "busy");
      if (!S.existing) {
        try {
          await gh("POST", `/repos/${TEMPLATE.owner}/${TEMPLATE.repo}/generate`, { owner: S.login, name: S.name,
            description: "My AI job board (data encrypted)", private: false, include_all_branches: false });
        } catch (e) {
          if (e.status === 404) throw new Error(`Couldn't copy ${TEMPLATE.owner}/${TEMPLATE.repo} — it isn't set up as a template yet.`);
          if (e.status !== 422) throw e;                                // 422: already exists — carry on with it
        }
      }
      for (let i = 0; i < 40; i++) {                                    // GitHub fills a new copy in a few seconds
        const r = await fetch(`${API}${repoPath("/branches/main")}`, { headers: { Authorization: `Bearer ${S.token}` } });
        if (r.ok) break;
        await sleep(2000);
      }
      mark("copy", "ok", `github.com/${S.login}/${S.name}`);

      // 2. encrypted data on an orphan `data` branch
      step = "data"; mark("data", "busy");
      const key = Vault.newDataKey();
      const keys = { v: 1, wraps: [await Vault.wrapPassphrase(key, norm(S.phrase))] };
      const t = nowIso();
      const kaggle = S.kaggle.username && S.kaggle.key ? [{ username: S.kaggle.username, enabled: true, slot: 1, key_set: true }] : [];
      const user = { schema: 1, exported_at: t, meta: { settings: t, kaggle: t },
        profile: null, settings: { daily_at: "09:00", tz_offset_min: -new Date().getTimezoneOffset() },
        decisions: {}, applications: [], deleted_apps: {}, kaggle, resume: null, photo: null };
      const files = {
        "keys.json": JSON.stringify(keys, null, 1),
        "README.md": "# Encrypted job-board data\n\nEverything except keys.json is AES-256-GCM ciphertext; keys.json holds the data key wrapped by your passphrase/passkeys.\n",
        "user.enc": await Vault.encryptBlob(key, "user", user),
        "token.enc": await Vault.encryptBlob(key, "token", { token: S.token, saved_at: t }),
      };
      const tree = [];
      for (const [path, text] of Object.entries(files)) {
        const blob = await gh("POST", repoPath("/git/blobs"), { content: b64(text), encoding: "base64" });
        tree.push({ path, mode: "100644", type: "blob", sha: blob.sha });
      }
      const tr = await gh("POST", repoPath("/git/trees"), { tree });
      const commit = await gh("POST", repoPath("/git/commits"), { message: "Encrypted job-board data", tree: tr.sha, parents: [] });
      try { await gh("POST", repoPath("/git/refs"), { ref: "refs/heads/data", sha: commit.sha }); }
      catch (e) { if (e.status !== 422) throw e; await gh("PATCH", repoPath("/git/refs/heads/data"), { sha: commit.sha, force: true }); }
      mark("data", "ok", "only your passphrase opens it");

      // 3. secrets
      step = "secrets"; mark("secrets", "busy");
      await setSecret("JOBBOARD_DATA_KEY", Vault.b64(key));
      if (kaggle.length) await setSecret("KAGGLE_KEY_1", S.kaggle.key);
      if (S.jev) await setSecret("TYPESAFE_API_KEY", S.jev);
      mark("secrets", "ok", [kaggle.length && "Kaggle", S.jev && "TypeSafe"].filter(Boolean).join(" + ") || "no AI key yet — add one in Settings");

      // 4. Pages on, 5. first deploy
      step = "pages"; mark("pages", "busy");
      let manual = false;
      try { await gh("POST", repoPath("/pages"), { build_type: "workflow" }, [201, 409]); }
      catch (e) { if (e.status === 403 || e.status === 404) manual = true; else throw e; }
      if (manual) {
        mark("pages", "warn", "one click needed");
        $("#msg").innerHTML = `Open <a href="https://github.com/${esc(S.login)}/${esc(S.name)}/settings/pages" target="_blank" rel="noopener">your repository's Pages settings</a>,
          set <b>Source: GitHub Actions</b>, then press Continue.<br><button class="btn small" id="cont">Continue</button>`;
        await new Promise((ok) => ($("#cont").onclick = ok));
        $("#msg").textContent = "";
      }
      mark("pages", "ok");
      step = "publish"; mark("publish", "busy", "about a minute");
      for (let i = 0; i < 8; i++) {                                     // a brand-new copy registers its workflows after a moment
        try { await gh("POST", repoPath("/actions/workflows/pages.yml/dispatches"), { ref: "main" }); break; }
        catch (e) { if (i === 7) throw e; await sleep(4000); }
      }
      for (let i = 0; i < 90; i++) {
        await sleep(4000);
        const runs = await gh("GET", repoPath("/actions/workflows/pages.yml/runs?per_page=1"));
        const run = runs.workflow_runs?.[0];
        if (run?.status === "completed") {
          if (run.conclusion !== "success") throw new Error(`the website build failed — see github.com/${S.login}/${S.name}/actions`);
          break;
        }
      }
      mark("publish", "ok", site);
      $("#done").innerHTML = `
        <div class="su-done">
          <h2>Your board is ready 🎉</h2>
          <p>Open it, unlock with your passphrase, and add your resume on the <b>Profile</b> page — upload a PDF or LaTeX file, or build one there.
            Your first job search runs automatically after that, then every morning at 9.</p>
          <p><a class="btn primary" href="${site}">Open my job board</a></p>
          <p class="faint small">Bookmark it: ${esc(site)}</p>
        </div>`;
    } catch (e) {
      mark(step, "err");
      $("#msg").innerHTML = `${esc(e.message)}<br><button class="btn small" id="retry">Try again</button>`;
      $("#retry").onclick = () => { S.existing = true; build(); };
    }
  }

  welcome();
})();
