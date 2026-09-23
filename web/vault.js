"use strict";
/* Browser side of app/vault.py — same blob and keys.json formats (see that file for the design).
   Passkey unlock uses the WebAuthn PRF extension: the authenticator derives a stable secret from a
   per-wrap salt, which (via HKDF) becomes the key that wraps the data key. */
(function () {
  const enc = new TextEncoder(), dec = new TextDecoder();
  const b64 = (buf) => {                       // chunked: spreading a large array overflows the call stack
    const u = new Uint8Array(buf);
    let s = "";
    for (let i = 0; i < u.length; i += 0x8000) s += String.fromCharCode.apply(null, u.subarray(i, i + 0x8000));
    return btoa(s);
  };
  const unb64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
  const rand = (n) => crypto.getRandomValues(new Uint8Array(n));

  async function gzip(bytes) {
    return new Uint8Array(await new Response(new Blob([bytes]).stream().pipeThrough(new CompressionStream("gzip"))).arrayBuffer());
  }
  async function gunzip(bytes) {
    return new Uint8Array(await new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"))).arrayBuffer());
  }
  const aes = (raw, usage) => crypto.subtle.importKey("raw", raw, "AES-GCM", false, usage);

  async function encryptBlob(dataKey, name, obj) {
    const iv = rand(12);
    const plain = await gzip(enc.encode(JSON.stringify(obj)));
    const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv, additionalData: enc.encode(`jobboard:${name}`) },
      await aes(dataKey, ["encrypt"]), plain);
    return JSON.stringify({ v: 1, iv: b64(iv), ct: b64(ct) });
  }

  async function decryptBlob(dataKey, name, text) {
    const d = typeof text === "string" ? JSON.parse(text) : text;
    const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: unb64(d.iv), additionalData: enc.encode(`jobboard:${name}`) },
      await aes(dataKey, ["decrypt"]), unb64(d.ct));
    return JSON.parse(dec.decode(await gunzip(new Uint8Array(plain))));
  }

  // -- passphrase wraps -------------------------------------------------------------------------
  // Case and spacing don't matter when typing it back (same as app/vault.py normalize_passphrase).
  const normalize = (p) => p.toLowerCase().split(/\s+/).filter(Boolean).join(" ");

  async function kekFromPassphrase(passphrase, salt, iterations) {
    const base = await crypto.subtle.importKey("raw", enc.encode(normalize(passphrase)), "PBKDF2", false, ["deriveBits"]);
    return new Uint8Array(await crypto.subtle.deriveBits({ name: "PBKDF2", hash: "SHA-256", salt, iterations }, base, 256));
  }

  async function wrapPassphrase(dataKey, passphrase, iterations = 600000) {
    const salt = rand(16), iv = rand(12);
    const kek = await kekFromPassphrase(passphrase, salt, iterations);
    const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv, additionalData: enc.encode("jobboard:wrap:passphrase") },
      await aes(kek, ["encrypt"]), dataKey);
    return { kind: "passphrase", kdf: "PBKDF2-SHA256", iterations, salt: b64(salt), iv: b64(iv), ct: b64(ct) };
  }

  async function unlockPassphrase(keys, passphrase) {
    for (const w of keys.wraps.filter((w) => w.kind === "passphrase")) {
      try {
        const kek = await kekFromPassphrase(passphrase, unb64(w.salt), w.iterations);
        return new Uint8Array(await crypto.subtle.decrypt({ name: "AES-GCM", iv: unb64(w.iv), additionalData: enc.encode("jobboard:wrap:passphrase") },
          await aes(kek, ["decrypt"]), unb64(w.ct)));
      } catch (_) { /* wrong passphrase for this wrap */ }
    }
    return null;
  }

  // -- passkey wraps (WebAuthn PRF) ----------------------------------------------------------------
  const b64url = (buf) => b64(buf).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  const unb64url = (s) => unb64(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4));

  async function kekFromPrf(prfOutput) {
    const base = await crypto.subtle.importKey("raw", prfOutput, "HKDF", false, ["deriveBits"]);
    return new Uint8Array(await crypto.subtle.deriveBits(
      { name: "HKDF", hash: "SHA-256", salt: new Uint8Array(0), info: enc.encode("jobboard:wrap:passkey") }, base, 256));
  }

  async function prfFor(credentialIds, salt) {
    const cred = await navigator.credentials.get({ publicKey: {
      challenge: rand(32), userVerification: "required", timeout: 60000,
      allowCredentials: credentialIds.map((id) => ({ type: "public-key", id: unb64url(id) })),
      extensions: { prf: { evalByCredential: Object.fromEntries(credentialIds.map((id) => [id, { first: salt(id) }])) } },
    } });
    const out = cred.getClientExtensionResults()?.prf?.results?.first;
    if (!out) throw new Error("This browser or passkey doesn't support the PRF extension — use your passphrase here.");
    return { id: b64url(cred.rawId), prf: new Uint8Array(out) };
  }

  async function addPasskey(dataKey, label) {
    if (!window.PublicKeyCredential) throw new Error("Passkeys aren't available in this browser.");
    const prfSalt = rand(32);
    const cred = await navigator.credentials.create({ publicKey: {
      rp: { name: "Job Board" }, challenge: rand(32), timeout: 60000,
      user: { id: rand(16), name: `jobboard-${label || "device"}`, displayName: `Job Board (${label || "this device"})` },
      pubKeyCredParams: [{ type: "public-key", alg: -7 }, { type: "public-key", alg: -257 }],
      authenticatorSelection: { residentKey: "required", userVerification: "required" },
      extensions: { prf: { eval: { first: prfSalt } } },
    } });
    const ext = cred.getClientExtensionResults()?.prf;
    if (ext && ext.enabled === false) throw new Error("This passkey doesn't support PRF, so it can't unlock encrypted data.");
    const id = b64url(cred.rawId);
    // Some browsers only return PRF output on get(), not create(): ask once more if needed.
    let prf = ext?.results?.first ? new Uint8Array(ext.results.first) : (await prfFor([id], () => prfSalt)).prf;
    const kek = await kekFromPrf(prf), iv = rand(12);
    const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv, additionalData: enc.encode("jobboard:wrap:passkey") },
      await aes(kek, ["encrypt"]), dataKey);
    return { kind: "passkey", label: label || "passkey", credential_id: id, prf_salt: b64(prfSalt), iv: b64(iv), ct: b64(ct) };
  }

  async function unlockPasskey(keys) {
    const wraps = keys.wraps.filter((w) => w.kind === "passkey");
    if (!wraps.length) return null;
    const byId = Object.fromEntries(wraps.map((w) => [w.credential_id, w]));
    const { id, prf } = await prfFor(Object.keys(byId), (cid) => unb64(byId[cid].prf_salt));
    const w = byId[id];
    const kek = await kekFromPrf(prf);
    return new Uint8Array(await crypto.subtle.decrypt({ name: "AES-GCM", iv: unb64(w.iv), additionalData: enc.encode("jobboard:wrap:passkey") },
      await aes(kek, ["decrypt"]), unb64(w.ct)));
  }

  window.Vault = { encryptBlob, decryptBlob, wrapPassphrase, unlockPassphrase, addPasskey, unlockPasskey,
                   newDataKey: () => rand(32), b64, unb64,
                   passkeysSupported: () => !!window.PublicKeyCredential };
})();
