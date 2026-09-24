"use strict";
/* Visual resume editor. Your resume is a list of sections; each section type has its LaTeX predefined here,
   so you edit text on a page that looks like the PDF and the LaTeX is written for you. LaTeX stays one click
   away (Advanced) for anyone who wants it.

     Resume.parse(tex)  → model   reads this template (and the common "Jake's resume" macros) back into sections
     Resume.toTex(model) → tex    the full document: preamble from the style choices + every section
     Resume.editor(el, model, {onChange})                               the page you edit

   Text inside a model is a tiny HTML subset — <b>, <i>, <code>, <a href> — that maps 1:1 to
   \textbf, \textit, \texttt and \href. */
(function () {
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const uid = () => Math.random().toString(36).slice(2, 9);
  const stripTags = (h) => String(h || "").replace(/<[^>]*>/g, "").replace(/&nbsp;/g, " ");

  /* ---------------------------------------------------------------- LaTeX → HTML (inline text) */
  function readGroup(s, i) {                           // s[i] === "{" → [inside, index after "}"]
    let depth = 0;
    for (let j = i; j < s.length; j++) {
      const c = s[j];
      if (c === "\\") { j++; continue; }
      if (c === "{") depth++;
      else if (c === "}" && --depth === 0) return [s.slice(i + 1, j), j + 1];
    }
    return [s.slice(i + 1), s.length];
  }
  const skipWs = (s, i) => { while (i < s.length && /\s/.test(s[i])) i++; return i; };
  const noComments = (s) => String(s || "").replace(/(^|[^\\])%.*$/gm, "$1");
  const SYM = { "&": "&amp;", "%": "%", $: "$", "#": "#", _: "_", "{": "{", "}": "}", ",": " ", " ": " ", ";": " ", "!": "", "/": "", "-": "", "\\": " ", "'": "'", '"': "" };
  const SWITCH = new Set(["small", "footnotesize", "scriptsize", "tiny", "large", "Large", "LARGE", "huge", "Huge", "normalsize",
    "centering", "noindent", "hfill", "quad", "qquad", "newline", "linebreak", "par", "bfseries", "itshape", "scshape", "mdseries",
    "upshape", "rmfamily", "sffamily", "ttfamily", "vfill", "medskip", "smallskip", "bigskip", "item", "raggedright", "normalfont"]);

  function tex2html(src) {
    const s = noComments(src);
    let out = "", i = 0;
    const peek = () => s[skipWs(s, i)];
    const arg = () => { i = skipWs(s, i); if (s[i] !== "{") return ""; const [g, n] = readGroup(s, i); i = n; return g; };
    const opt = () => { const k = skipWs(s, i); if (s[k] === "[") { const e = s.indexOf("]", k); i = e < 0 ? s.length : e + 1; } };
    while (i < s.length) {
      const c = s[i];
      if (c === "\\") {
        const m = /^\\([A-Za-z]+)\*?|^\\(.)/.exec(s.slice(i));
        if (!m) { i++; continue; }
        i += m[0].length;
        if (m[2] !== undefined) { out += m[2] in SYM ? SYM[m[2]] : esc(m[2]); if (m[2] === "\\") opt(); continue; }
        const n = m[1];
        if (n === "textbf" || (n === "bf" && peek() === "{")) out += `<b>${tex2html(arg())}</b>`;
        else if (["textit", "emph", "textsl"].includes(n) || (n === "it" && peek() === "{")) out += `<i>${tex2html(arg())}</i>`;
        else if (n === "texttt") out += `<code>${tex2html(arg())}</code>`;
        else if (n === "href") { const u = arg().replace(/\\([#%&_~])/g, "$1"); out += `<a href="${esc(u)}">${tex2html(arg())}</a>`; }
        else if (n === "url") { const u = arg(); out += `<a href="${esc(u)}">${esc(u)}</a>`; }
        else if (n === "textbullet") out += "•";
        else if (n === "cdot" || n === "textperiodcentered") out += "·";
        else if (n === "LaTeX" || n === "TeX") out += n;
        else if (n === "textasciitilde") { if (peek() === "{") arg(); out += "~"; }
        else if (n === "textbackslash") { if (peek() === "{") arg(); out += "\\"; }
        else if (n === "textasciicircum") { if (peek() === "{") arg(); out += "^"; }
        else if (["vspace", "hspace", "color", "setlength", "addtolength"].includes(n)) { opt(); arg(); if (n.endsWith("length")) arg(); }
        else if (n === "textcolor") { arg(); out += tex2html(arg()); }
        else if (SWITCH.has(n)) { /* size/shape switches: the template decides these */ }
        else if (peek() === "{") out += tex2html(arg());          // \underline, \textsc, \mbox, unknown: keep the text
      } else if (c === "{") { const [g, n] = readGroup(s, i); i = n; out += tex2html(g); }
      else if (c === "}") i++;
      else if (c === "$") {
        const e = s.indexOf("$", i + 1), math = s.slice(i + 1, e < 0 ? s.length : e);
        i = e < 0 ? s.length : e + 1;
        out += /\\cdot|\\bullet/.test(math) ? "·" : /\\sim/.test(math) ? "~" : /\\times/.test(math) ? "×" : esc(math.replace(/[\\{}^_]/g, ""));
      }
      else if (c === "~") { out += "&nbsp;"; i++; }
      else if (s.startsWith("---", i)) { out += "—"; i += 3; }
      else if (s.startsWith("--", i)) { out += "–"; i += 2; }
      else if (s.startsWith("``", i) || s.startsWith("''", i)) { out += "\""; i += 2; }
      else { out += esc(c); i++; }
    }
    return out.replace(/\s+/g, " ");
  }
  const unwrap = (h) => { const m = /^\s*<i>([\s\S]*)<\/i>\s*$/.exec(h || ""); return m && !/<i>/.test(m[1]) ? m[1].trim() : (h || "").trim(); };

  /* ---------------------------------------------------------------- HTML → LaTeX */
  const TEXESC = { "\\": "\\textbackslash{}", "{": "\\{", "}": "\\}", "&": "\\&", "%": "\\%", $: "\\$", "#": "\\#", _: "\\_",
    "~": "\\textasciitilde{}", "^": "\\textasciicircum{}", "·": "$\\cdot$", "—": "---", "–": "--", "\u00a0": "~", "•": "\\textbullet{}",
    "“": "``", "”": "''" };
  const texText = (t) => t.replace(/[\\{}&%$#_~^·—–\u00a0•“”]/g, (c) => TEXESC[c]);
  const urlTex = (u) => String(u || "").trim().replace(/[#%\\]/g, (c) => "\\" + c);
  function html2tex(html) {
    const root = document.createElement("div");
    root.innerHTML = html || "";
    const walk = (n) => {
      if (n.nodeType === 3) return texText(n.nodeValue);
      if (n.nodeType !== 1) return "";
      const inner = [...n.childNodes].map(walk).join("");
      if (!inner.trim() && n.tagName !== "BR") return inner;
      switch (n.tagName) {
        case "B": case "STRONG": return `\\textbf{${inner}}`;
        case "I": case "EM": return `\\textit{${inner}}`;
        case "CODE": return `\\texttt{${inner}}`;
        case "A": return `\\href{${urlTex(n.getAttribute("href"))}}{${inner}}`;
        case "BR": return " ";
        default: return inner;
      }
    };
    return walk(root).replace(/\s+/g, " ").trim();
  }

  /* what the editor keeps from a contenteditable field: text + b/i/code/a */
  function clean(el) {
    const walk = (n) => {
      if (n.nodeType === 3) return esc(n.nodeValue);
      if (n.nodeType !== 1) return "";
      const inner = [...n.childNodes].map(walk).join("");
      const t = n.tagName, st = n.getAttribute("style") || "";
      if (t === "B" || t === "STRONG" || /font-weight:\s*(bold|[6-9]00)/.test(st)) return inner ? `<b>${inner}</b>` : "";
      if (t === "I" || t === "EM" || /font-style:\s*italic/.test(st)) return inner ? `<i>${inner}</i>` : "";
      if (t === "CODE") return inner ? `<code>${inner}</code>` : "";
      if (t === "A") return `<a href="${esc(n.getAttribute("href") || "")}">${inner}</a>`;
      if (t === "BR" || t === "DIV" || t === "P") return " " + inner;
      return inner;
    };
    return [...el.childNodes].map(walk).join("").replace(/\s+/g, " ").trim();
  }

  /* ---------------------------------------------------------------- parse a .tex into sections */
  function parseStyle(pre) {
    const st = { font: "cm", size: 10, margins: "compact", accent: "0000EE", headings: "smallcaps" };
    const size = /\\documentclass\[[^\]]*?(\d{2})pt/.exec(pre);
    if (size) st.size = +size[1];
    st.font = /mathpazo|palatino/i.test(pre) ? "palatino" : /mathptmx|\{times\}/.test(pre) ? "times" : /helvet/.test(pre) ? "helvetica"
      : /charter/i.test(pre) ? "charter" : /lmodern/.test(pre) ? "latin-modern" : "cm";
    const g = /\\usepackage\[([^\]]*)\]\{geometry\}/.exec(pre);
    if (g) {
      const num = (k) => +((new RegExp(`${k}=([\\d.]+)`).exec(g[1]) || [])[1] || 0);
      st.margins = num("left") >= 0.9 ? "roomy" : num("left") >= 0.7 || num("top") >= 0.3 ? "normal" : "compact";
    }
    const acc = /\\definecolor\{linkblue\}\{(RGB|HTML)\}\{([^}]*)\}/.exec(pre);
    if (acc) st.accent = acc[1] === "HTML" ? acc[2].toUpperCase() : acc[2].split(",").map((x) => (+x).toString(16).padStart(2, "0")).join("").toUpperCase();
    const tf = /\\titleformat\{\\section\}\{([^\n]*)\}\{\}/.exec(pre);
    if (tf) st.headings = /linkblue/.test(tf[1]) ? "accent" : /scshape/.test(tf[1]) ? "smallcaps" : "bold";
    return st;
  }

  const CONTACT_KIND = { MapMarker: "location", MapMarkerAlt: "location", MapPin: "location", Phone: "phone", PhoneAlt: "phone",
    Mobile: "phone", Envelope: "email", At: "email", Github: "github", GithubSquare: "github", Linkedin: "linkedin", LinkedinIn: "linkedin",
    Globe: "website", Link: "website", Home: "website", Twitter: "twitter", XTwitter: "twitter" };
  function parseHeader(head) {
    const h = { name: "", headline: "", contacts: [] };
    const nm = /\\(?:LARGE|Large|huge|Huge|large)\b[^{}]*?\\textbf\{([^}]*)\}/.exec(head) || /\\textbf\{([^}]*)\}/.exec(head);
    if (nm) h.name = tex2html(nm[1]).trim();
    let rest = nm ? head.slice(nm.index + nm[0].length) : head;
    const iconAt = rest.search(/\\fa[A-Z]/);
    const hl = /\\textbf\{((?:[^{}]|\{[^{}]*\})*)\}/.exec(iconAt >= 0 ? rest.slice(0, iconAt) : rest);
    if (hl) h.headline = tex2html(hl[1]).trim();
    if (iconAt >= 0) {
      const block = rest.slice(iconAt).split(/\\end\{center\}/)[0];
      for (const part of block.split(/\\quad\s*\|\s*\\quad|\s\|\s|\$\|\$/)) {
        const icon = (/\\fa([A-Za-z]+)\*?/.exec(part) || [])[1] || "";
        const href = /\\href\{([^}]*)\}\{((?:[^{}]|\{[^{}]*\})*)\}/.exec(part);
        const text = tex2html(href ? href[2] : part.replace(/\\fa[A-Za-z]+\*?/, "")).replace(/^[\s}]+|[\s{]+$/g, "").trim();
        let url = href ? href[1].replace(/\\([#%&_])/g, "$1") : "";
        const kind = CONTACT_KIND[icon] || (/^mailto:/.test(url) ? "email" : /^tel:/.test(url) ? "phone" : "website");
        if (kind === "email" && url === `mailto:${stripTags(text)}`) url = "";      // derived again when writing
        if (text || url) h.contacts.push({ kind, text, url });
      }
    }
    return h;
  }

  function rawItems(s) {                              // \item … pieces (raw LaTeX), from the first list in s
    if (/\\resumeItem\b/.test(s)) {
      const out = [], re = /\\resumeItem\s*\{/g;
      let m;
      while ((m = re.exec(s))) out.push(readGroup(s, m.index + m[0].length - 1)[0]);
      return out;
    }
    const m = /\\begin\{(\w+)\}(?:\[[^\]]*\])?([\s\S]*)\\end\{\1\}/.exec(s);
    const body = m ? m[2] : s;
    return body.split(/\\item\b(?:\[[^\]]*\])?/).slice(1).map((x) => x.replace(/\\(begin|end)\{[^}]*\}/g, "")).filter((x) => x.trim());
  }
  function tableRows(s) {
    const m = /\\begin\{(tabularx?|tabular\*)\}(?:\{[^}]*\})?\{((?:[^{}]|\{[^{}]*\})*)\}([\s\S]*?)\\end\{\1\}/.exec(s);
    if (!m) return [];
    return m[3].split(/\\\\(?:\[[^\]]*\])?/).map((r) => r.replace(/\\(hline|toprule|midrule|bottomrule)\b/g, "").trim())
      .filter(Boolean).map((r) => r.split(/(?<!\\)&/).map((c) => c.trim()));
  }
  function parseEntries(s) {
    const out = [], re = /\\(entryheader|resumeSubheading|resumeProjectHeading)\b/g, idx = [];
    let m;
    while ((m = re.exec(s))) idx.push([m.index, m[1], re.lastIndex]);
    idx.forEach(([, kind, argStart], k) => {
      let i = argStart;
      const args = [], n = kind === "resumeSubheading" ? 4 : kind === "resumeProjectHeading" ? 2 : 3;
      for (let a = 0; a < n; a++) { i = skipWs(s, i); if (s[i] !== "{") break; const [g, nx] = readGroup(s, i); args.push(g); i = nx; }
      const rest = s.slice(i, k + 1 < idx.length ? idx[k + 1][0] : s.length);
      let [t, r, sub, subR] = kind === "entryheader" ? [args[0], args[1], ...(args[2] || "").split(/\\hfill/)] : [args[0], args[1], args[2], args[3]];
      const e = { id: uid(), title: "", url: "", desc: "", right: unwrap(tex2html(r)), sub: unwrap(tex2html(sub)), subRight: unwrap(tex2html(subR)),
                  bullets: rawItems(rest).map((b) => tex2html(b).trim()) };
      const href = /^\s*(?:\\textbf\{\s*)?\\href\{([^}]*)\}\{((?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*)\}\}?/.exec(t || "");
      if (href) { e.url = href[1].replace(/\\([#%&_])/g, "$1"); e.title = tex2html(href[2]).trim(); t = t.slice(href[0].length); }
      const parts = tex2html(t || "").trim().split(/\s+[-–—|]\s+|^[-–—|]\s+/).filter((x) => x.trim());
      if (!href) e.title = (parts.shift() || "").trim();
      e.desc = parts.join(" - ").trim();
      out.push(e);
    });
    return out;
  }
  function parseSection(title, s) {
    const id = uid();
    if (/\\(entryheader|resumeSubheading|resumeProjectHeading)\b/.test(s)) return { id, type: "entries", title, entries: parseEntries(s) };
    const rows = /\\begin\{tabular/.test(s) ? tableRows(s) : [];
    if (rows.length && rows.every((r) => r.length === 2))
      return { id, type: "skills", title, rows: rows.map(([l, it]) => ({ label: stripTags(tex2html(l)).trim(), items: tex2html(it).trim() })) };
    if (rows.length) {
      const n = Math.max(...rows.map((r) => r.length));
      const cell = (r, k) => tex2html(r[k] || "").trim();
      return { id, type: "table", title, header: Array.from({ length: n }, (_, k) => stripTags(cell(rows[0], k))),
               rows: rows.slice(1).map((r) => Array.from({ length: n }, (_, k) => cell(r, k))) };
    }
    if (/\\item\b|\\resumeItem\b/.test(s))
      return { id, type: "list", title, items: rawItems(s).map((x) => { const [a, b] = x.split(/\\hfill/); return { text: tex2html(a).trim(), right: unwrap(tex2html(b || "")) }; }) };
    return { id, type: "text", title, text: tex2html(s.replace(/\\vspace\*?\{[^}]*\}/g, "")).trim() };
  }
  function parse(tex) {
    const [pre, rest = ""] = String(tex || "").split("\\begin{document}");
    const body = noComments(rest.split("\\end{document}")[0]);
    const starts = [];
    const re = /\\section\*?\s*\{/g;
    let m;
    while ((m = re.exec(body))) starts.push(m.index);
    const model = { v: 1, style: parseStyle(pre), header: parseHeader(starts.length ? body.slice(0, starts[0]) : body), sections: [] };
    starts.forEach((st, k) => {
      const chunk = body.slice(st, starts[k + 1] ?? body.length);
      const [title, after] = readGroup(chunk, chunk.indexOf("{"));
      model.sections.push(parseSection(stripTags(tex2html(title)).trim(), chunk.slice(after)));
    });
    return model;
  }

  /* ---------------------------------------------------------------- sections → LaTeX */
  const FONTS = { cm: "", "latin-modern": "\\usepackage{lmodern}", palatino: "\\usepackage{mathpazo}", times: "\\usepackage{mathptmx}",
    helvetica: "\\usepackage[scaled]{helvet}\n\\renewcommand{\\familydefault}{\\sfdefault}", charter: "\\usepackage{charter}" };
  const MARGINS = { compact: "top=0.1in,bottom=0in,left=0.65in,right=0.65in", normal: "top=0.4in,bottom=0.4in,left=0.75in,right=0.75in",
    roomy: "top=0.6in,bottom=0.6in,left=1in,right=1in" };
  const HEADS = { smallcaps: "\\large\\bfseries\\scshape\\color{darkgray}", bold: "\\large\\bfseries\\color{darkgray}",
    accent: "\\large\\bfseries\\scshape\\color{linkblue}" };
  const ICONS = { location: "\\faMapMarker*", phone: "\\faPhone*", email: "\\faEnvelope", github: "\\faGithub", linkedin: "\\faLinkedin",
    website: "\\faGlobe", twitter: "\\faTwitter" };
  const hasText = (h) => stripTags(h).trim() !== "";

  function contactTex(c) {
    const t = html2tex(c.text || c.url);
    const url = c.url || (c.kind === "email" ? `mailto:${stripTags(c.text).trim()}` : c.kind === "phone" ? `tel:${stripTags(c.text).replace(/[^\d+]/g, "")}` : "");
    return url ? `${ICONS[c.kind] || ICONS.website}\n    \\href{${urlTex(url)}}{\\, ${t}}` : `${ICONS[c.kind] || ICONS.website}\\, ${t}`;
  }
  function sectionTex(s, k) {
    const out = [`% ---------- ${stripTags(s.title) || "Summary"} ----------`, `\\section*{${html2tex(s.title || "")}}`];
    if (!hasText(s.title) && k === 0) out.unshift("\\vspace{-20pt}", "");       // a title-less first section: just a rule under the header
    if (s.type === "text") out.push(html2tex(s.text));
    else if (s.type === "skills") out.push("{\\renewcommand{\\arraystretch}{1.2} \\small", "\\begin{tabularx}{\\textwidth}{@{}l X@{}}",
      ...s.rows.filter((r) => hasText(r.label) || hasText(r.items)).map((r) => `    \\textbf{${html2tex(r.label)}} & ${html2tex(r.items)} \\\\`), "\\end{tabularx}}");
    else if (s.type === "entries") s.entries.forEach((e, j) => {
      if (j) out.push("", "\\vspace{5pt}");
      const title = e.url ? `\\href{${urlTex(e.url)}}{${html2tex(e.title)}}` : html2tex(e.title);
      const sub = html2tex(e.sub) + (hasText(e.subRight) ? ` \\hfill ${html2tex(e.subRight)}` : "");
      out.push(`\\entryheader{${title}${hasText(e.desc) ? ` - ${html2tex(e.desc)}` : ""}}{${html2tex(e.right)}}{${sub}}`);
      const bl = (e.bullets || []).filter(hasText);
      if (bl.length) out.push("\\vspace{-10pt}", "\\begin{tightitemize}", ...bl.map((b) => `    \\item ${html2tex(b)}`), "\\end{tightitemize}");
    });
    else if (s.type === "list") {
      const items = s.items.filter((i) => hasText(i.text));
      if (items.length) out.push("{\\small", "\\begin{tightitemize}", ...items.map((i) => `    \\item ${html2tex(i.text)}${hasText(i.right) ? ` \\hfill ${html2tex(i.right)}` : ""}`), "\\end{tightitemize}}");
    } else if (s.type === "table") {
      const n = Math.max(1, s.header.length);
      out.push("{\\small \\renewcommand{\\arraystretch}{1.2}", `\\begin{tabularx}{\\textwidth}{@{}${Array(n).fill("X").join(" ")}@{}}`,
        `    ${s.header.map((c) => `\\textbf{${html2tex(c)}}`).join(" & ")} \\\\ \\hline`,
        ...s.rows.map((r) => `    ${Array.from({ length: n }, (_, i) => html2tex(r[i] || "")).join(" & ")} \\\\`), "\\end{tabularx}}");
    }
    return out;
  }
  function toTex(m) {
    const st = { ...DEFAULT_STYLE, ...(m.style || {}) }, h = m.header || {};
    const L = [`\\documentclass[${st.size}pt,letterpaper]{article}`, "", `\\usepackage[${MARGINS[st.margins] || MARGINS.compact}]{geometry}`,
      "\\usepackage{titlesec}", "\\usepackage{enumitem}", "\\usepackage{hyperref}", "\\usepackage[T1]{fontenc}"];
    if (FONTS[st.font]) L.push(FONTS[st.font]);
    L.push("\\usepackage{xcolor}", "\\usepackage{tabularx}", "\\usepackage{fontawesome5}", "\\usepackage{graphicx}", "",
      "\\definecolor{darkgray}{RGB}{40,40,40}", `\\definecolor{linkblue}{HTML}{${String(st.accent).replace("#", "").toUpperCase()}}`, "",
      "\\pagestyle{empty}", "\\raggedbottom", "\\setlength{\\parindent}{0pt}", "",
      "\\hypersetup{", "    colorlinks=true,", "    urlcolor=linkblue,", "    linkcolor=linkblue", "}", "",
      "% Section formatting", `\\titleformat{\\section}{${HEADS[st.headings] || HEADS.smallcaps}}{}{0em}{}[\\titlerule]`,
      "\\titlespacing{\\section}{0pt}{5pt}{5pt}", "",
      "% Custom list environment", "\\newlist{tightitemize}{itemize}{2}",
      "\\setlist[tightitemize]{leftmargin=1.1em, itemsep=0pt, topsep=0pt, parsep=0pt, label=\\textbullet}", "",
      "\\newcommand{\\entryheader}[3]{%", "    \\textbf{#1} \\hfill \\textit{#2}\\\\[0pt]", "    \\textit{\\small #3}\\\\[1pt]", "}", "",
      "% Written by the Job Board resume editor — edit it there, or by hand here.", "",
      "\\begin{document}", "", "% ---------- Header ----------", "\\begin{center}",
      `    {\\LARGE \\textbf{${html2tex(h.name)}}}\\\\[2pt]`);
    if (hasText(h.headline)) L.push(`    {\\small \\textbf{${html2tex(h.headline)}}}\\\\[2pt]`);
    const cs = (h.contacts || []).filter((c) => hasText(c.text) || c.url).map(contactTex);
    if (cs.length) L.push("    {\\small", "    " + cs.join(" \\quad|\\quad\n    "), "    }");
    L.push("\\end{center}", "");
    (m.sections || []).forEach((s, k) => L.push(...sectionTex(s, k), ""));
    L.push("\\end{document}", "");
    return L.join("\n");
  }

  /* ---------------------------------------------------------------- blank pieces, section catalogue */
  const DEFAULT_STYLE = { font: "cm", size: 10, margins: "compact", accent: "0000EE", headings: "smallcaps" };
  const entry = () => ({ id: uid(), title: "", url: "", desc: "", right: "", sub: "", subRight: "", bullets: [""] });
  const CATALOG = [
    ["Experience", "entries", "Jobs, internships, freelance work"],
    ["Projects", "entries", "Things you built, with links"],
    ["Education", "entries", "Degrees, schools, dates"],
    ["Skills", "skills", "A table: category → skills"],
    ["Summary", "text", "A short paragraph about you"],
    ["Certifications", "list", "One line each, with a date"],
    ["Awards", "list", "One line each, with a date"],
    ["Publications", "list", "One line each"],
    ["Custom text", "text", "Any paragraph"],
    ["Custom list", "list", "Bullets"],
    ["Custom table", "table", "Rows and columns"],
  ];
  function newSection(title, type) {
    const s = { id: uid(), type, title: /^Custom/.test(title) ? "" : title };
    if (type === "entries") s.entries = [entry()];
    if (type === "skills") s.rows = [{ label: "", items: "" }];
    if (type === "text") s.text = "";
    if (type === "list") s.items = [{ text: "", right: "" }];
    if (type === "table") { s.header = ["", ""]; s.rows = [["", ""]]; }
    return s;
  }
  const blank = () => ({ v: 1, style: { ...DEFAULT_STYLE }, header: { name: "", headline: "", contacts: [
    { kind: "location", text: "", url: "" }, { kind: "email", text: "", url: "" }, { kind: "phone", text: "", url: "" },
    { kind: "github", text: "GitHub", url: "" }, { kind: "linkedin", text: "LinkedIn", url: "" }] },
    sections: [newSection("Summary", "text"), newSection("Skills", "skills"), newSection("Experience", "entries"), newSection("Projects", "entries"), newSection("Education", "entries")] });

  /* ---------------------------------------------------------------- the editor (a page that looks like the PDF) */
  const CICON = { location: "📍", phone: "☎", email: "✉", github: "GH", linkedin: "in", website: "🌐", twitter: "𝕏" };
  function editor(root, model, { onChange }) {
    let m = model;
    const undo = [];
    const get = (path) => path.split(".").reduce((o, k) => (o == null ? o : o[k]), m);
    const set = (path, v) => { const ks = path.split("."), last = ks.pop(); ks.reduce((o, k) => o[k], m)[last] = v; onChange(m); };
    const F = (path, cls, ph, { tag = "span", multi = false } = {}) =>
      `<${tag} class="rz-f ${cls}" contenteditable="true" data-path="${path}" data-ph="${esc(ph)}"${multi ? " data-multi" : ""}>${get(path) || ""}</${tag}>`;
    const btn = (act, label, title, data = "") => `<button type="button" class="rz-b" data-act="${act}" ${data} title="${esc(title)}" aria-label="${esc(title)}">${label}</button>`;

    function entryHTML(P, e, i, j) {
      const d = `data-i="${i}" data-j="${j}"`;
      return `<div class="rz-entry">
        <div class="rz-tools">${btn("eup", "↑", "Move up", d)}${btn("edown", "↓", "Move down", d)}${btn("edel", "×", "Delete", d)}</div>
        <div class="rz-l1"><span class="rz-l1l">${F(`${P}.title`, `rz-etitle${e.url ? " rz-linked" : ""}`, "Title")}${btn("elink", "🔗", e.url ? `Link: ${e.url}` : "Add a link", `${d} data-on="${e.url ? 1 : 0}"`)}${F(`${P}.desc`, "rz-edesc rz-opt", "short description (optional)")}</span>${F(`${P}.right`, "rz-right rz-opt", "dates or place")}</div>
        <div class="rz-l2">${F(`${P}.sub`, "rz-esub rz-opt", "role or tech used (optional)")}${F(`${P}.subRight`, "rz-right rz-small rz-opt", "dates")}</div>
        <ul class="rz-ul">${(e.bullets || []).map((b, k) => `<li>${F(`${P}.bullets.${k}`, "rz-bullet", "What you did, and what came of it", { multi: true })}</li>`).join("")}</ul>
        ${btn("badd", "+ bullet", "Add a bullet", d)}
      </div>`;
    }
    function sectionHTML(s, i) {
      const P = `sections.${i}`, d = `data-i="${i}"`;
      let body = "";
      if (s.type === "text") body = F(`${P}.text`, "rz-text", "Write a few sentences…", { tag: "div", multi: true });
      else if (s.type === "skills") body = `<div class="rz-skills">${s.rows.map((r, j) => `<div class="rz-srow">${F(`${P}.rows.${j}.label`, "rz-slabel", "Category")}${F(`${P}.rows.${j}.items`, "rz-sitems", "skill, skill, skill", { multi: true })}${btn("rdel", "×", "Remove row", `${d} data-j="${j}"`)}</div>`).join("")}</div>${btn("radd", "+ row", "Add a row", d)}`;
      else if (s.type === "entries") body = s.entries.map((e, j) => entryHTML(`${P}.entries.${j}`, e, i, j)).join("") + btn("eadd", `+ ${/educ/i.test(s.title) ? "school" : /proj/i.test(s.title) ? "project" : "entry"}`, "Add an entry", d);
      else if (s.type === "list") body = `<ul class="rz-ul rz-small">${s.items.map((it, j) => `<li><span class="rz-li">${F(`${P}.items.${j}.text`, "", "Item", { multi: true })}${F(`${P}.items.${j}.right`, "rz-right rz-opt", "date")}</span>${btn("ldel", "×", "Remove", `${d} data-j="${j}"`)}</li>`).join("")}</ul>${btn("ladd", "+ item", "Add an item", d)}`;
      else if (s.type === "table") body = `<table class="rz-table rz-small"><thead><tr>${s.header.map((_, k) => `<th>${F(`${P}.header.${k}`, "", "Column")}</th>`).join("")}<th class="rz-tc">${btn("tcadd", "+", "Add a column", d)}${s.header.length > 1 ? btn("tcdel", "−", "Remove the last column", d) : ""}</th></tr></thead>
        <tbody>${s.rows.map((r, j) => `<tr>${s.header.map((_, k) => `<td>${F(`${P}.rows.${j}.${k}`, "", "")}</td>`).join("")}<td class="rz-tc">${btn("trdel", "×", "Remove row", `${d} data-j="${j}"`)}</td></tr>`).join("")}</tbody></table>${btn("tradd", "+ row", "Add a row", d)}`;
      return `<section class="rz-sec">
        <div class="rz-tools rz-stools">${btn("sup", "↑", "Move section up", d)}${btn("sdown", "↓", "Move section down", d)}${btn("sdel", "×", "Delete section", d)}</div>
        <h2 class="rz-h">${F(`${P}.title`, i === 0 ? "rz-opt" : "", i === 0 ? "(no title — just a line)" : "Section title")}</h2>${body}</section>`;
    }
    function render(focusPath) {
      const st = { ...DEFAULT_STYLE, ...m.style };
      root.innerHTML = `
        <div class="rz-paper rz-font-${st.font} rz-size-${st.size} rz-m-${st.margins} rz-h-${st.headings}" style="--rz-accent:#${String(st.accent).replace("#", "")}">
          <header class="rz-head">
            ${F("header.name", "rz-name", "Your name")}
            ${F("header.headline", "rz-headline", "Headline — e.g. Full-stack & AI engineer")}
            <div class="rz-contacts">${(m.header.contacts || []).map((c, i) => `<span class="rz-contact"><button type="button" class="rz-ico" data-act="ckind" data-i="${i}" title="Change type (${c.kind})">${CICON[c.kind] || "🔗"}</button>${F(`header.contacts.${i}.text`, "rz-ctext", c.kind)}${
              ["github", "linkedin", "website", "twitter"].includes(c.kind) ? btn("clink", "🔗", c.url ? `Link: ${c.url}` : "Add the link", `data-i="${i}" data-on="${c.url ? 1 : 0}"`) : ""}${btn("cdel", "×", "Remove", `data-i="${i}"`)}</span>`).join('<span class="rz-sep">|</span>')}
              ${btn("cadd", "+", "Add a contact")}</div>
          </header>
          ${m.sections.map(sectionHTML).join("")}
          <div class="rz-addsec">${btn("secmenu", "+ Add section", "Add a section")}
            <div class="rz-menu" hidden>${CATALOG.map(([t, type, hint], k) => `<button type="button" data-act="secadd" data-k="${k}"><b>${t}</b><span>${hint}</span></button>`).join("")}</div></div>
        </div>`;
      fit();
      if (focusPath) {
        const el = root.querySelector(`[data-path="${focusPath}"]`);
        if (el) { el.focus(); const r = document.createRange(); r.selectNodeContents(el); r.collapse(false); const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r); }
      }
    }
    function fit() {                                   // the page is 8.5in wide; shrink it to fit the screen
      const p = root.querySelector(".rz-paper");
      if (!p) return;
      const w = root.clientWidth - 8;
      p.style.zoom = w < 816 ? (w / 816).toFixed(3) : "";
    }
    const snap = () => { undo.push(JSON.stringify(m)); if (undo.length > 50) undo.shift(); };
    const change = (fn, focus) => { snap(); fn(); onChange(m); render(focus); };
    const swap = (arr, a, b) => { if (b < 0 || b >= arr.length) return; [arr[a], arr[b]] = [arr[b], arr[a]]; };

    root.addEventListener("input", (e) => { const el = e.target.closest("[data-path]"); if (el) set(el.dataset.path, clean(el)); });
    root.addEventListener("paste", (e) => {                 // paste as plain text: formatting comes from the template
      if (!e.target.closest("[data-path]")) return;
      e.preventDefault();
      document.execCommand("insertText", false, (e.clipboardData.getData("text/plain") || "").replace(/\s*\n\s*/g, " "));
    });
    root.addEventListener("keydown", (e) => {
      const el = e.target.closest("[data-path]");
      if (!el) return;
      const path = el.dataset.path, mod = e.metaKey || e.ctrlKey;
      if (mod && e.key.toLowerCase() === "k") {           // link the selected text
        e.preventDefault();
        const url = prompt("Link address (https://…)");
        if (url) document.execCommand("createLink", false, url.trim());
        return;
      }
      if (mod && e.key.toLowerCase() === "e") { e.preventDefault(); wrapCode(); return; }
      const bm = /^(sections\.\d+\.entries\.\d+\.bullets)\.(\d+)$/.exec(path);
      if (e.key === "Enter") {
        e.preventDefault();
        if (bm) { const arr = get(bm[1]), k = +bm[2]; change(() => arr.splice(k + 1, 0, ""), `${bm[1]}.${k + 1}`); }
        else el.blur();
      } else if (e.key === "Backspace" && bm && !el.textContent) {
        e.preventDefault();
        const arr = get(bm[1]), k = +bm[2];
        if (arr.length > 1) change(() => arr.splice(k, 1), `${bm[1]}.${Math.max(0, k - 1)}`);
      }
    });
    function wrapCode() {
      const sel = getSelection();
      if (!sel.rangeCount || sel.isCollapsed) return;
      const r = sel.getRangeAt(0), code = document.createElement("code");
      code.append(r.extractContents()); r.insertNode(code);
      const el = code.closest("[data-path]"); if (el) set(el.dataset.path, clean(el));
    }
    const KINDS = ["location", "phone", "email", "github", "linkedin", "website", "twitter"];
    root.addEventListener("click", (e) => {
      const b = e.target.closest("[data-act]");
      if (!b) { if (!e.target.closest(".rz-addsec")) root.querySelector(".rz-menu")?.setAttribute("hidden", ""); return; }
      const i = +b.dataset.i, j = +b.dataset.j, S = m.sections[i];
      const act = b.dataset.act;
      if (act === "secmenu") return root.querySelector(".rz-menu").toggleAttribute("hidden");
      const ops = {
        secadd: () => { const [t, type] = CATALOG[+b.dataset.k]; m.sections.push(newSection(t, type)); return `sections.${m.sections.length - 1}.title`; },
        sup: () => swap(m.sections, i, i - 1), sdown: () => swap(m.sections, i, i + 1),
        sdel: () => { m.sections.splice(i, 1); window.toast?.("Section deleted", { label: "Undo", run: api.undo }); },
        eadd: () => { S.entries.push(entry()); return `sections.${i}.entries.${S.entries.length - 1}.title`; },
        eup: () => swap(S.entries, j, j - 1), edown: () => swap(S.entries, j, j + 1),
        edel: () => { S.entries.splice(j, 1); window.toast?.("Entry deleted", { label: "Undo", run: api.undo }); },
        badd: () => { S.entries[j].bullets.push(""); return `sections.${i}.entries.${j}.bullets.${S.entries[j].bullets.length - 1}`; },
        elink: () => { const u = prompt("Link for this title (leave empty to remove)", S.entries[j].url || ""); if (u === null) return false; S.entries[j].url = u.trim(); },
        radd: () => { S.rows.push({ label: "", items: "" }); return `sections.${i}.rows.${S.rows.length - 1}.label`; },
        rdel: () => { S.rows.splice(j, 1); },
        ladd: () => { S.items.push({ text: "", right: "" }); return `sections.${i}.items.${S.items.length - 1}.text`; },
        ldel: () => { S.items.splice(j, 1); },
        tradd: () => { S.rows.push(S.header.map(() => "")); },
        trdel: () => { S.rows.splice(j, 1); },
        tcadd: () => { S.header.push(""); S.rows.forEach((r) => r.push("")); },
        tcdel: () => { S.header.pop(); S.rows.forEach((r) => r.pop()); },
        cadd: () => { m.header.contacts.push({ kind: "website", text: "", url: "" }); return `header.contacts.${m.header.contacts.length - 1}.text`; },
        cdel: () => { m.header.contacts.splice(i, 1); },
        ckind: () => { const c = m.header.contacts[i]; c.kind = KINDS[(KINDS.indexOf(c.kind) + 1) % KINDS.length]; },
        clink: () => { const c = m.header.contacts[i]; const u = prompt("Link (https://…)", c.url || ""); if (u === null) return false; c.url = u.trim(); },
      };
      if (!ops[act]) return;
      snap();
      const focus = ops[act]();
      if (focus === false) { undo.pop(); return; }
      onChange(m);
      render(typeof focus === "string" ? focus : null);
    });
    window.addEventListener("resize", fit);
    render();
    const api = {
      get model() { return m; },
      setModel(next) { m = next; render(); },
      setStyle(k, v) { snap(); m.style = { ...DEFAULT_STYLE, ...m.style, [k]: v }; onChange(m); render(); },
      undo() { const prev = undo.pop(); if (!prev) return false; m = JSON.parse(prev); onChange(m); render(); return true; },
      fit,
      destroy() { window.removeEventListener("resize", fit); },
    };
    return api;
  }

  window.Resume = { parse, toTex, editor, blank, DEFAULT_STYLE, html2tex, tex2html,
    FONTS: [["cm", "Computer Modern (LaTeX classic)"], ["latin-modern", "Latin Modern"], ["palatino", "Palatino"], ["times", "Times"], ["charter", "Charter"], ["helvetica", "Helvetica (sans)"]] };
})();
