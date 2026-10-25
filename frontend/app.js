/* SiteForge customer app — no build step, no third-party code (CSP: script-src 'self'). */
(function () {
  "use strict";

  // ------------------------------------------------------------------ utilities
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const app = $("#app");
  const S = { me: null, csrf: null, projects: [], pid: null, tab: "guide", st: null, builds: [], buildSel: null,
              device: "desktop", editing: null, poll: null, authMode: "signin",
              adv: (() => { try { return localStorage.getItem("sf-adv") === "1"; } catch (e) { return false; } })() };

  function h(v) {
    return String(v == null ? "" : v).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function md(text) {
    const lines = h(text).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").split("\n");
    let out = "", inList = false;
    for (const l of lines) {
      if (l.startsWith("- ")) { if (!inList) { out += "<ul>"; inList = true; } out += "<li>" + l.slice(2) + "</li>"; }
      else { if (inList) { out += "</ul>"; inList = false; } if (l.trim()) out += "<p>" + l + "</p>"; }
    }
    return out + (inList ? "</ul>" : "");
  }
  function toast(msg, bad) {
    const t = $("#toast"); t.textContent = msg; t.className = "show" + (bad ? " bad" : "");
    clearTimeout(toast._t); toast._t = setTimeout(() => (t.className = ""), 3800);
  }
  function fmtDate(s) { try { return new Date(s.endsWith("Z") || s.includes("+") ? s : s + "Z").toLocaleString(); } catch (e) { return s; } }
  function uid() { return (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random()).replace(/-/g, ""); }

  async function api(method, path, body, opts = {}) {
    const headers = { Accept: "application/json" };
    if (method !== "GET") headers["X-CSRF-Token"] = S.csrf || "";
    let payload;
    if (body instanceof FormData) payload = body;
    else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
    const r = await fetch(path, { method, headers, body: payload, credentials: "same-origin" });
    if (opts.raw) return r;
    let data = null;
    try { data = await r.json(); } catch (e) { /* empty */ }
    if (!r.ok) {
      const msg = (data && (typeof data.detail === "string" ? data.detail : "Please check your input.")) || "Something went wrong.";
      if (r.status === 401 && !opts.quiet401 && !path.startsWith("/api/auth/")) { S.me = null; route(); }
      const err = new Error(msg); err.status = r.status; throw err;
    }
    return data;
  }

  // ------------------------------------------------------------------ dialogs
  function dialog(html, onMount) {
    const d = $("#dialog"); d.className = ""; d.innerHTML = `<div class="dlg">${html}</div>`;
    d.showModal(); if (onMount) onMount(d);
    return d;
  }
  function confirmPassword(title, body, actionLabel) {
    return new Promise(resolve => {
      const d = dialog(`<h2>${h(title)}</h2><div>${body}</div>
        <form id="pwf"><div class="field"><label for="pw">Confirm your password</label>
        <input class="input" id="pw" type="password" autocomplete="current-password" required></div>
        <p class="err" id="pwerr"></p>
        <div class="row"><span class="spacer"></span><button type="button" class="btn" id="pwc">Cancel</button>
        <button class="btn btn-primary" type="submit">${h(actionLabel)}</button></div></form>`);
      $("#pw", d).focus();
      $("#pwc", d).onclick = () => { d.close(); resolve(null); };
      $("#pwf", d).onsubmit = e => { e.preventDefault(); const v = $("#pw", d).value; d.close(); resolve(v); };
    });
  }

  // ------------------------------------------------------------------ routing
  window.addEventListener("hashchange", route);
  async function route() {
    clearInterval(S.poll);
    if (!S.me) {
      try { const me = await api("GET", "/api/auth/me", undefined, { quiet401: true }); if (!me.user) return renderAuth(); S.me = me; S.csrf = me.csrf_token; }
      catch (e) { return renderAuth(); }
    }
    const m = location.hash.match(/^#\/p\/([a-f0-9]+)(?:\/(\w+))?/);
    if (m) { S.pid = m[1]; S.tab = m[2] || "guide"; return loadProject(); }
    S.pid = null; return renderProjects();
  }

  // ------------------------------------------------------------------ auth
  function renderAuth() {
    const signup = S.authMode === "signup";
    app.innerHTML = `<div class="auth">
      <section class="auth-hero"><div class="wordmark"><i></i>SiteForge</div>
        <h1>Tell us about your business. We'll handle the website.</h1>
        <p>Plan, build, test and launch a secure site in one guided conversation — no web jargon needed.</p>
        <ol><li>Answer a few plain-language questions</li><li>Review your plan before anything is built</li>
        <li>Preview, test and approve</li><li>Publish with one-click rollback</li></ol></section>
      <section class="auth-card">
        <div class="tabs-inline" role="tablist">
          <button role="tab" aria-selected="${!signup}" data-mode="signin">Sign in</button>
          <button role="tab" aria-selected="${signup}" data-mode="signup">Create account</button></div>
        <h2>${signup ? "Create your account" : "Welcome back"}</h2>
        <form id="authf" novalidate>
          ${signup ? `<div class="field"><label for="nm">Your name</label><input class="input" id="nm" autocomplete="name"></div>
          <div class="field"><label for="org">Business or workspace name <span class="muted">(optional)</span></label><input class="input" id="org" autocomplete="organization"></div>` : ""}
          <div class="field"><label for="em">Email</label><input class="input" id="em" type="email" autocomplete="email" required></div>
          <div class="field"><label for="pw">Password</label><input class="input" id="pw" type="password" autocomplete="${signup ? "new-password" : "current-password"}" required minlength="10">
          ${signup ? `<span class="small muted">At least 10 characters. A short phrase works well.</span>` : ""}</div>
          <p class="err" id="aerr" role="alert"></p>
          <button class="btn btn-primary" type="submit">${signup ? "Create account" : "Sign in"}</button>
        </form></section></div>`;
    $$("[data-mode]").forEach(b => (b.onclick = () => { S.authMode = b.dataset.mode; renderAuth(); }));
    $("#authf").onsubmit = async e => {
      e.preventDefault();
      const body = { email: $("#em").value, password: $("#pw").value };
      if (signup) { body.name = $("#nm").value; body.organization = $("#org").value; }
      try {
        const r = await api("POST", signup ? "/api/auth/register" : "/api/auth/login", body);
        S.csrf = r.csrf_token; S.me = null; await route();
      } catch (err) { $("#aerr").textContent = err.status === 422 && signup ? "Please use a valid email and a password of at least 10 characters." : err.message; }
    };
  }

  function topbar(extra = "") {
    return `<header class="topbar"><a class="wordmark" href="#/"><i></i>SiteForge</a>${extra}<span class="spacer"></span>
      <span class="small muted">${h(S.me.user.email)}</span><button class="btn btn-sm btn-quiet" id="logout">Sign out</button></header>`;
  }
  function bindTop() {
    $("#logout").onclick = async () => { await api("POST", "/api/auth/logout"); S.me = null; location.hash = "#/"; route(); };
  }

  // ------------------------------------------------------------------ projects
  async function renderProjects() {
    S.projects = await api("GET", "/api/projects");
    app.innerHTML = topbar() + `<main class="wrap">
      <div class="row"><div><h1>Your websites</h1><p class="muted">Each project goes from first question to live site, with your approval at every step.</p></div></div>
      <form id="newp" class="card row"><label class="sr" for="pname">Project name</label>
        <input class="input" id="pname" placeholder="Name your project, e.g. Sharma Plumbing website" maxlength="120" required>
        <button class="btn btn-primary">Start a new website</button></form>
      ${S.projects.length ? `<div class="proj-grid">${S.projects.map(p => `<button class="proj-card" data-id="${p.id}">
          <span class="row"><strong>${h(p.name)}</strong><span class="spacer"></span>${stateBadge(p.lifecycle_state, p.lifecycle_label)}</span>
          <span class="small muted">${p.current_release_id ? "Live at /sites/" + h(p.slug) + "/" : "Not published yet"} · ${h(p.role)}</span></button>`).join("")}</div>`
        : `<div class="empty"><h2>No websites yet</h2><p>Give your project a name above to get started. It takes about ten minutes.</p></div>`}
    </main>`;
    bindTop();
    $("#newp").onsubmit = async e => {
      e.preventDefault();
      try { const p = await api("POST", "/api/projects", { name: $("#pname").value }); location.hash = `#/p/${p.id}/guide`; }
      catch (err) { toast(err.message, true); }
    };
    $$(".proj-card").forEach(b => (b.onclick = () => (location.hash = `#/p/${b.dataset.id}/guide`)));
  }

  function stateBadge(state, label) {
    const cls = { live: "ok", monitoring: "ok", approved_for_release: "info", preview_ready: "info", brief_ready: "amber",
                  scope_approved: "info", failed: "bad", changes_requested: "warn", building: "info", deploying: "info" }[state] || "";
    return `<span class="badge ${cls}">${h(label || state)}</span>`;
  }

  // ------------------------------------------------------------------ project shell
  async function loadProject() {
    try { S.st = await api("GET", `/api/projects/${S.pid}`); }
    catch (e) { toast(e.message, true); location.hash = "#/"; return; }
    renderShell();
  }
  const can = p => S.st && S.st.permissions.includes(p);

  const TABS = [["guide", "Guide"], ["plan", "Plan"], ["build", "Build & preview"], ["launch", "Launch"],
                ["leads", "Enquiries"], ["insights", "Insights"], ["settings", "Settings"]];

  function renderShell() {
    const st = S.st, p = st.project, pr = st.progress;
    const attention = { plan: p.lifecycle_state === "brief_ready", build: ["scope_approved", "preview_ready"].includes(p.lifecycle_state),
                        launch: p.lifecycle_state === "approved_for_release" };
    app.innerHTML = topbar(`<span class="muted">/</span><span class="proj">${h(p.name)}</span>${stateBadge(p.lifecycle_state, p.lifecycle_label)}`) +
      `<div class="layout"><aside class="rail" aria-label="Progress"><h2>Your progress</h2>
        <div class="meter" role="progressbar" aria-valuenow="${pr.percent}" aria-valuemin="0" aria-valuemax="100" aria-label="Questions answered"><span id="meterbar"></span></div>
        <ol class="steps">${pr.steps.map(s => `<li class="${s.status}">${h(s.label)}</li>`).join("")}</ol>
        <p class="small muted">${pr.answered} of ${pr.total} questions settled · saved automatically</p>
        <label class="adv"><input type="checkbox" id="advtog" ${S.adv ? "checked" : ""}> Show technical details</label>
      </aside><main class="main">
        <nav class="nav" role="tablist" aria-label="Project sections">${TABS.map(([k, l]) =>
          `<button role="tab" aria-selected="${S.tab === k}" data-tab="${k}">${l}${attention[k] ? '<span class="dot" aria-label="needs attention"></span>' : ""}</button>`).join("")}</nav>
        <section id="panel"></section></main></div>`;
    $("#meterbar").style.width = pr.percent + "%";
    bindTop();
    $("#advtog").onchange = e => { S.adv = e.target.checked; try { localStorage.setItem("sf-adv", S.adv ? "1" : "0"); } catch (x) { /* ignore */ } renderTab(); };
    $$("[data-tab]").forEach(b => (b.onclick = () => (location.hash = `#/p/${S.pid}/${b.dataset.tab}`)));
    renderTab();
  }

  function renderTab() {
    const fn = { guide: tabGuide, plan: tabPlan, build: tabBuild, launch: tabLaunch, leads: tabLeads, insights: tabInsights, settings: tabSettings }[S.tab] || tabGuide;
    fn($("#panel"));
  }

  // ------------------------------------------------------------------ Guide tab
  function tabGuide(el) {
    const st = S.st;
    const q = S.editing || st.question;
    el.innerHTML = `<div class="guide"><div class="chat"><div class="log" id="log" aria-live="polite">
        ${st.transcript.map(m => `<div class="msg ${m.role}">${md(m.text)}</div>`).join("")}</div>
        <div class="composer" id="composer"></div></div>
      <aside class="facts" aria-label="What I know so far"><h2>What I know so far</h2>
        <p class="small muted">Everything here is editable. Badges show where each answer came from.</p>
        ${st.facts.length ? st.facts.map(factRow).join("") : `<p class="muted small">Nothing yet — start by telling me about your business.</p>`}
      </aside></div>`;
    const log = $("#log"); log.scrollTop = log.scrollHeight;
    renderComposer($("#composer"), q);
    $$(".fact .edit").forEach(b => (b.onclick = async () => {
      try { S.editing = await api("GET", `/api/projects/${S.pid}/questions/${b.dataset.key}`); S.editing.isEdit = true; tabGuide(el); $("#composer").scrollIntoView({ block: "nearest" }); }
      catch (e) { toast(e.message, true); }
    }));
  }

  function factRow(f) {
    const cls = { user_provided: "ok", recommended: "info", extracted: "amber", inferred: "amber", skipped: "" }[f.source] || "";
    return `<div class="fact"><div class="k"><span>${h(f.label)}</span>${can("onboarding.write") ? `<button class="edit" data-key="${h(f.field_key)}">Edit</button>` : ""}</div>
      <div class="v">${h(f.display)}</div><div><span class="badge ${cls}">${h(f.source === "skipped" || f.source === "user_provided" ? f.source_label : f.confirmed ? (f.source === "recommended" ? "Recommended · accepted" : "Confirmed by you") : f.source_label)}</span></div></div>`;
  }

  function renderComposer(el, q) {
    const st = S.st;
    if (!can("onboarding.write")) { el.innerHTML = `<p class="muted">You have view-only access to this project.</p>`; return; }
    if (!q) {
      const s = st.project.lifecycle_state;
      const next = s === "brief_ready" ? ["plan", "Review your plan"] : ["scope_approved", "preview_ready", "changes_requested"].includes(s) ? ["build", "Go to build & preview"]
        : s === "approved_for_release" ? ["launch", "Go to launch"] : ["live", "monitoring"].includes(s) ? ["insights", "See how your site is doing"] : null;
      el.innerHTML = `<div class="done-card"><p class="q-prompt">All set for now.</p><p class="muted">Need to change something? Use “Edit” next to any answer — your live site won't change until you approve a new version.</p>
        ${next ? `<button class="btn btn-primary" id="gonext">${next[1]}</button>` : ""}</div>`;
      if (next) $("#gonext").onclick = () => (location.hash = `#/p/${S.pid}/${next[0]}`);
      return;
    }
    const recVal = q.recommendation ? q.recommendation.value : null;
    const isRec = v => recVal != null && (Array.isArray(recVal) ? recVal.includes(v) : recVal === v);
    let input = "";
    const cur = q.current;
    if (q.kind === "palette") {
      input = `<div id="palpick" class="picker"><p class="small muted"><span class="spin"></span> Gathering colour ideas…</p></div>`;
    } else if (q.kind === "template") {
      input = `<div id="tplpick" class="picker"><p class="small muted"><span class="spin"></span> Preparing designs…</p></div>`;
    } else if (q.kind === "choice") {
      input = `<div class="options" role="group" aria-label="${h(q.prompt)}">${q.options.map(o => `<button type="button" class="opt ${o.available ? "" : "na"}" data-val="${h(o.value)}" aria-pressed="${cur === o.value}" ${o.available ? "" : "disabled"}>
        ${q.field_key === "palette" ? `<span class="swatch" data-sw="${h(o.description)}"><b></b><b></b></span>` : ""}
        <span class="opt-label">${h(o.label)}</span>${o.description && q.field_key !== "palette" ? `<span class="d">${h(o.description)}</span>` : ""}
        ${isRec(o.value) ? `<span class="rec">Recommended</span>` : ""}</button>`).join("")}</div>`;
    } else if (q.kind === "multi") {
      const sel = new Set(Array.isArray(cur) ? cur : (recVal || []));
      input = `<div class="options" role="group" aria-label="${h(q.prompt)}">${q.options.map(o => `<label class="opt ${sel.has(o.value) ? "checked" : ""}">
        <input type="checkbox" value="${h(o.value)}" ${sel.has(o.value) ? "checked" : ""}><span class="opt-label">${h(o.label)}</span>
        ${o.description ? `<span class="d">${h(o.description)}</span>` : ""}${isRec(o.value) ? `<span class="rec">Recommended</span>` : ""}</label>`).join("")}</div>
        <button class="btn btn-primary" id="multi-go">Continue</button>`;
    } else if (q.kind === "upload") {
      input = `<div class="answer-row"><label class="sr" for="file">Logo file</label><input class="input" id="file" type="file" accept="image/png,image/jpeg,image/webp"><button class="btn btn-primary" id="up-go">Upload</button></div>`;
    } else {
      const val = cur == null ? "" : Array.isArray(cur) ? cur.join(", ") : cur;
      const type = { email: "email", phone: "tel" }[q.kind] || "text";
      input = `<form class="answer-row" id="txt"><label class="sr" for="ans">${h(q.prompt)}</label>
        ${q.kind === "longtext" ? `<textarea class="input" id="ans" placeholder="${h(q.placeholder)}" maxlength="2000">${h(val)}</textarea>`
          : `<input class="input" id="ans" type="${type}" placeholder="${h(q.placeholder)}" value="${h(val)}" maxlength="${q.kind === "list" ? 1000 : 200}" autocomplete="${type === "email" ? "email" : type === "tel" ? "tel" : "off"}">`}
        <button class="btn btn-primary" type="submit">${q.isEdit ? "Save" : "Send"}</button></form>`;
    }
    el.innerHTML = `${q.isEdit ? `<p class="small"><span class="badge info">Editing an earlier answer</span> <button class="btn btn-sm btn-quiet" id="canceledit">Cancel</button></p>` : ""}
      <div class="q-prompt" id="qp">${h(q.prompt)}</div><div class="q-why">${h(q.why)}</div>
      ${S.adv ? `<div class="q-tech">→ ${h(q.technical)}</div>` : ""}
      ${q.suggestion && !q.isEdit ? `<div class="suggest"><span class="small muted">My guess from what you said</span><strong>${h(q.suggestion.label)}</strong>
        <span class="small">${h(q.suggestion.reason || "")}</span><div class="helpers"><button class="btn btn-sm btn-primary" id="confirm">Yes, that's right</button>
        <span class="small muted">or change it below</span></div></div>` : ""}
      ${input}<p class="err" id="qerr" role="alert"></p>
      <div class="helpers">${q.recommendation ? `<button class="btn btn-sm" id="rec" title="${h(q.recommendation.reason)}">Recommend for me</button>
        <button class="btn btn-sm btn-quiet" id="unsure">I'm not sure</button>` : ""}
        ${q.allow_skip ? `<button class="btn btn-sm btn-quiet" id="skip">Skip for now</button>` : ""}
        <a class="btn btn-sm btn-quiet" href="mailto:support@example.com?subject=Help%20with%20my%20website">Talk to a person</a></div>`;
    $$("[data-sw]", el).forEach(sw => { const c = sw.dataset.sw.split(" / "); const b = $$("b", sw); b[0].style.background = c[0]; b[1].style.background = c[1]; });
    const send = (action, value) => submit(q.field_key, action, value);
    $$(".opt[data-val]", el).forEach(b => (b.onclick = () => send("answer", b.dataset.val)));
    $$(".opt input", el).forEach(i => (i.onchange = () => i.closest(".opt").classList.toggle("checked", i.checked)));
    if ($("#multi-go", el)) $("#multi-go", el).onclick = () => send("answer", $$(".opt input:checked", el).map(i => i.value));
    if ($("#txt", el)) {
      $("#txt", el).onsubmit = e => { e.preventDefault(); send("answer", $("#ans", el).value); };
      const ta = $("textarea#ans", el);
      if (ta) ta.onkeydown = e => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); send("answer", ta.value); } };
      setTimeout(() => $("#ans", el) && $("#ans", el).focus({ preventScroll: true }), 30);
    }
    if ($("#up-go", el)) $("#up-go", el).onclick = async () => {
      const f = $("#file", el).files[0]; if (!f) { $("#qerr").textContent = "Choose a file first."; return; }
      const fd = new FormData(); fd.append("file", f);
      try { const r = await api("POST", `/api/projects/${S.pid}/uploads`, fd); send("answer", r.reference); }
      catch (e) { $("#qerr").textContent = e.message; }
    };
    if ($("#confirm", el)) $("#confirm", el).onclick = () => send("confirm");
    if ($("#rec", el)) $("#rec", el).onclick = () => send("recommend");
    if ($("#unsure", el)) $("#unsure", el).onclick = () => send("not_sure");
    if ($("#skip", el)) $("#skip", el).onclick = () => send("skip");
    if ($("#canceledit", el)) $("#canceledit", el).onclick = () => { S.editing = null; tabGuide($("#panel")); };
    if ($("#palpick", el)) palettePicker($("#palpick", el), v => send("answer", v), q.current);
    if ($("#tplpick", el)) templatePicker($("#tplpick", el), v => send("answer", v));
  }

  async function submit(field_key, action, value) {
    const btns = $$("#composer button"); btns.forEach(b => (b.disabled = true));
    try {
      const r = await api("POST", `/api/projects/${S.pid}/answers`, { field_key, action, value, expected_version: S.st.project.state_version });
      S.editing = null;
      const perms = S.st.permissions, role = S.st.role;
      S.st = Object.assign(r, { permissions: perms, role });
      renderShell();
    } catch (e) {
      btns.forEach(b => (b.disabled = false));
      if (e.status === 409) { toast(e.message); return loadProject(); }
      const err = $("#qerr"); if (err) err.textContent = e.message; else toast(e.message, true);
    }
  }

  // ------------------------------------------------------------------ colour & template pickers
  const SRC = { logo: "Your logo", website: "Site you like", industry: "Industry", harmony: "Colour theory", api: "Colour API",
                preset: "Preset", thecolorapi: "The Color API", colormind: "Colormind", custom: "Custom" };
  function lum(hex) { const c = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255).map(v => v <= .03928 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4); return .2126 * c[0] + .7152 * c[1] + .0722 * c[2]; }
  function ratio(a, b) { const x = lum(a), y = lum(b); return (Math.max(x, y) + .05) / (Math.min(x, y) + .05); }
  function palCard(p) {
    return `<button type="button" class="pal" data-val="${h(p.value)}" data-p="${h(p.primary)}" data-a="${h(p.accent)}" data-on="${h(p.on_accent || "#111111")}" title="${h(p.why)}">
      <span class="pal-mock"><span class="pal-bar"></span><span class="pal-h">Aa</span><span class="pal-btn">Book</span><span class="pal-chip"></span></span>
      <span class="pal-name">${h(p.name)}</span><span class="pal-meta"><span class="badge">${h(SRC[p.source] || p.source)}</span> <span class="small muted">${p.contrast ? `${p.contrast}:1` : ""}</span></span></button>`;
  }
  function paintPals(root) {
    $$(".pal", root).forEach(b => {
      $(".pal-bar", b).style.background = b.dataset.p; $(".pal-h", b).style.color = b.dataset.p;
      const btn = $(".pal-btn", b); btn.style.background = b.dataset.p; btn.style.color = "#fff";
      const ch = $(".pal-chip", b); ch.style.background = b.dataset.a; ch.style.color = b.dataset.on;
    });
  }
  async function palettePicker(el, onPick, current, seed) {
    let data;
    try { data = await api("GET", `/api/projects/${S.pid}/palettes${seed ? `?seed=${encodeURIComponent(seed)}` : ""}`); }
    catch (e) { el.innerHTML = `<p class="err">${h(e.message)}</p>`; return; }
    const cur = current && current.startsWith("custom:") ? current.split(":").slice(1) : ["#1f3a5f", "#f08a24"];
    el.innerHTML = `${data.groups.map(g => `<div class="pal-group"><p class="pal-title">${h(g.title)}</p><div class="pal-grid">${g.palettes.map(palCard).join("")}</div></div>`).join("")}
      <div class="pal-group"><p class="pal-title">Your exact brand colours</p>
        <div class="custom-row"><label>Main <input type="color" id="cp1" value="${h(cur[0])}"></label><input class="input hex" id="cp1t" value="${h(cur[0])}" aria-label="Main colour hex">
        <label>Highlight <input type="color" id="cp2" value="${h(cur[1])}"></label><input class="input hex" id="cp2t" value="${h(cur[1])}" aria-label="Highlight colour hex">
        <span class="pal-live" id="cplive">Button</span><span class="small" id="cpratio"></span>
        <button type="button" class="btn btn-sm btn-primary" id="cpuse">Use these</button>
        <button type="button" class="btn btn-sm" id="cpseed" title="Ask colour theory and the colour APIs for ideas around your main colour">Ideas around this colour</button></div></div>
      <div class="pal-group"><p class="pal-title">Copy the colours of a website you like</p>
        <form class="custom-row" id="webf"><input class="input" id="weburl" placeholder="e.g. www.a-site-you-like.com" aria-label="Website address"><button class="btn btn-sm">Get its colours</button></form>
        <div id="webout"></div></div>
      <p class="small muted">Colour APIs: ${data.api_status.map(h).join(" · ")}. Every option is adjusted so text stays readable (4.5:1 or better).</p>`;
    paintPals(el);
    const bind = root => $$(".pal", root).forEach(b => (b.onclick = () => onPick(b.dataset.val)));
    bind(el);
    const sync = () => {
      const a = $("#cp1t", el).value.trim(), b = $("#cp2t", el).value.trim();
      const ok = /^#[0-9a-f]{6}$/i.test(a) && /^#[0-9a-f]{6}$/i.test(b);
      const live = $("#cplive", el);
      if (ok) { live.style.background = a; live.style.color = "#fff"; live.style.borderColor = b; const r = ratio("#ffffff", a);
        $("#cpratio", el).textContent = r >= 4.5 ? `✓ readable (${r.toFixed(1)}:1)` : `will be darkened slightly for readability (${r.toFixed(1)}:1)`; }
      $("#cpuse", el).disabled = !ok;
    };
    $("#cp1", el).oninput = e => { $("#cp1t", el).value = e.target.value; sync(); };
    $("#cp2", el).oninput = e => { $("#cp2t", el).value = e.target.value; sync(); };
    $("#cp1t", el).oninput = e => { if (/^#[0-9a-f]{6}$/i.test(e.target.value)) $("#cp1", el).value = e.target.value; sync(); };
    $("#cp2t", el).oninput = e => { if (/^#[0-9a-f]{6}$/i.test(e.target.value)) $("#cp2", el).value = e.target.value; sync(); };
    sync();
    $("#cpuse", el).onclick = () => onPick(`custom:${$("#cp1t", el).value.toLowerCase()}:${$("#cp2t", el).value.toLowerCase()}`);
    $("#cpseed", el).onclick = () => { const sd = $("#cp1t", el).value; el.innerHTML = `<p class="small muted"><span class="spin"></span> Asking colour theory and the colour APIs…</p>`; palettePicker(el, onPick, current, sd); };
    $("#webf", el).onsubmit = async e => {
      e.preventDefault(); const out = $("#webout", el); out.innerHTML = `<p class="small"><span class="spin"></span> Reading that site's colours…</p>`;
      try {
        const r = await api("POST", `/api/projects/${S.pid}/palettes/from-website`, { url: $("#weburl", el).value });
        out.innerHTML = r.palettes.length ? `<div class="pal-grid">${r.palettes.map(palCard).join("")}</div><p class="small muted">Fonts found: ${r.fonts_found.map(h).join(", ") || "none declared"}${r.template ? ` · closest design: ${h(r.template)}` : ""}</p>` : `<p class="small">I couldn't find clear brand colours on that site.</p>`;
        paintPals(out); bind(out);
      } catch (x) { out.innerHTML = `<p class="err">${h(x.message)}</p>`; }
    };
  }
  const loadedFonts = new Set();
  function loadFont(f) {
    const fam = "sf-" + f.file.replace(/\W/g, "");
    if (!loadedFonts.has(fam) && window.FontFace) { loadedFonts.add(fam); const ff = new FontFace(fam, `url(/api/fonts/${f.file})`); ff.load().then(x => document.fonts.add(x)).catch(() => {}); }
    return fam;
  }
  async function templatePicker(el, onPick) {
    let data;
    try { data = await api("GET", `/api/projects/${S.pid}/templates`); } catch (e) { el.innerHTML = `<p class="err">${h(e.message)}</p>`; return; }
    const sorted = [...data.templates].sort((a, b) => (b.recommended - a.recommended) || (b.fits_style - a.fits_style));
    el.innerHTML = `<div class="tpl-grid">${sorted.map(t => `<button type="button" class="tpl" data-val="${h(t.id)}" data-hf="${h(loadFont(t.fonts.heading))}" data-bf="${h(loadFont(t.fonts.body))}">
      <span class="tpl-mock" data-t='${h(JSON.stringify({p: t.theme.primary, a: t.theme.accent, bg: t.theme.bg, d: t.theme.dark, r: t.theme.radius, b: t.theme.button, w: t.theme.heading_weight, on: t.theme.on_accent, tx: t.theme.text}))}'>
        <span class="tm-nav"><span class="tm-logo"></span><span class="tm-links"></span><span class="tm-cta"></span></span>
        <span class="tm-h">${h(data.headline || data.business)}</span><span class="tm-p">Tell us what you need and we'll get back to you.</span>
        <span class="tm-btns"><span class="tm-b1">Get in touch</span><span class="tm-b2">Services</span></span>
        <span class="tm-cards"><i></i><i></i><i></i></span></span>
      <span class="tpl-name">${h(t.name)} ${t.recommended ? '<span class="badge amber">Recommended</span>' : ""}${t.current ? ' <span class="badge ok">Current</span>' : ""}</span>
      <span class="small muted">${h(t.theme.font_heading.replace(/-/g, " "))} + ${h(t.theme.font_body.replace(/-/g, " "))} · ${h(t.mood)}</span></button>`).join("")}</div>`;
    $$(".tpl", el).forEach(b => {
      const t = JSON.parse($(".tpl-mock", b).dataset.t), m = $(".tpl-mock", b);
      m.style.background = t.bg; m.style.borderRadius = Math.min(t.r, 14) + "px";
      const hd = $(".tm-h", b); hd.style.fontFamily = `"${b.dataset.hf}", Georgia, serif`; hd.style.fontWeight = t.w; hd.style.color = t.tx;
      $(".tm-p", b).style.fontFamily = `"${b.dataset.bf}", system-ui, sans-serif`;
      const rad = t.b === "pill" ? "999px" : t.b === "square" ? "0" : "6px";
      [$(".tm-b1", b), $(".tm-cta", b)].forEach(x => { x.style.background = t.p; x.style.color = "#fff"; x.style.borderRadius = rad; });
      const b2 = $(".tm-b2", b); b2.style.border = `1.5px solid ${t.p}`; b2.style.color = t.p; b2.style.borderRadius = rad;
      $(".tm-logo", b).style.background = t.p; $$(".tm-cards i", b).forEach(i => { i.style.borderRadius = Math.min(t.r, 12) + "px"; i.style.borderTop = `3px solid ${t.a}`; });
      b.onclick = () => onPick(b.dataset.val);
    });
  }

  // ------------------------------------------------------------------ Plan tab
  function tabPlan(el) {
    const bp = S.st.blueprint;
    if (!bp) { el.innerHTML = `<div class="empty"><h2>Your plan isn't ready yet</h2><p>Finish the questions in the Guide and I'll put together a plan for you to review.</p><button class="btn btn-primary" id="tog">Continue in the guide</button></div>`; $("#tog").onclick = () => (location.hash = `#/p/${S.pid}/guide`); return; }
    const c = bp.content, b = c.business, s = c.scope, d = c.data.lead_form, t = c.design.tokens;
    const pending = S.st.project.lifecycle_state === "brief_ready" && bp.status === "draft";
    const statusBadge = bp.status === "approved" ? `<span class="badge ok">Approved</span>` : bp.status === "superseded" ? `<span class="badge">Out of date — answers changed</span>` : `<span class="badge amber">Waiting for your approval</span>`;
    el.innerHTML = `
      <div class="card hero-card"><div class="card-head"><div><h1>Your website plan</h1>
        <p class="muted">Version ${bp.version} · ${statusBadge}</p></div></div>
        <p>A <strong>${h(c.design.template.replace("sf-", "").replace("-v1", ""))}</strong> website for <strong>${h(b.business_name || "your business")}</strong>${b.service_area ? ` in ${h(b.service_area)}` : ""},
        built so visitors <strong>${h(c.design.cta_label.toLowerCase())}</strong>. ${s.in_scope.length} things are included now; ${s.deferred.length + s.out_of_scope.length} are saved for later.</p>
        ${c.blocking.length ? `<div class="callout bad"><strong>Needs your attention before approval:</strong><ul>${c.blocking.map(r => `<li>${h(r.title)} — ${h(r.mitigation)}</li>`).join("")}</ul></div>` : ""}
      </div>
      <div class="grid2">
        <div class="card"><h2>What we'll build now</h2>${s.in_scope.map(r => `<div class="req"><strong>${h(r.description)}</strong>
          ${S.adv ? `<small class="mono">${h(r.key)} · ${h(r.priority)} · risk ${h(r.risk_level)}</small>` : ""}
          ${r.acceptance_criteria.length ? `<small>${r.acceptance_criteria.map(h).join(" · ")}</small>` : ""}</div>`).join("")}</div>
        <div>
          <div class="card"><h2>Saved for later</h2>${s.deferred.length + s.out_of_scope.length ? `<ul class="list later">${[...s.deferred, ...s.out_of_scope].map(r => `<li><span>${h(r.description)} <span class="badge">${r.status === "out_of_scope" ? "Not in this product yet" : "Later release"}</span></span></li>`).join("")}</ul>` : `<p class="muted">Nothing — everything you asked for is in this release.</p>`}
            <p class="small muted">We never buy domains, change DNS, spend money or send marketing emails on your behalf without a separate, explicit request.</p></div>
          <div class="card"><h2>Look & feel</h2><p><strong>${h(c.design.template)}</strong>${c.design.fonts ? ` · ${c.design.fonts.map(x => h(x.replace(/-/g, " "))).join(" + ")}` : ""}${c.design.headline ? `<br><span class="muted">Headline:</span> “${h(c.design.headline)}”` : ""}</p><div class="tokens">${[["Main colour", t.primary], ["Accent", t.accent], ["Background", t.bg], ["Text", t.text]].map(([n, v]) => `<span class="tok"><b data-c="${h(v)}"></b>${h(n)}</span>`).join("")}</div>
            <p class="small" >Pages: ${c.design.nav.map(n => h(n.label)).join(", ")}${c.design.extra_pages.includes("privacy") ? ", Privacy notice" : ""}</p></div>
        </div>
      </div>
      <div class="grid2">
        <div class="card"><h2>Visitor data & where it goes</h2>${d ? `
          <p>The enquiry form asks for <strong>${d.fields.map(f => h(f.label.toLowerCase()) + (f.required ? "" : " (optional)")).join(", ")}</strong>.</p>
          <ul class="list">${c.data.data_flows.map(f => `<li><span><strong>${h(f.from)} → ${h(f.to)}</strong><br><span class="small muted">${h(Array.isArray(f.data) ? f.data.join(", ") : f.data)} · ${h(f.protection)}</span></span></li>`).join("")}</ul>
          <p class="small muted">Kept for ${d.retention_days} days, visible only to owners and admins. A privacy notice page is included.</p>` : `<p>No form — this site won't store visitor details.</p>`}</div>
        <div class="card"><h2>Security & privacy</h2><ul class="list">${c.security.required_controls.map(x => `<li>${h(x)}</li>`).join("")}</ul></div>
      </div>
      <div class="grid2">
        <div class="card"><h2>How it's hosted</h2><ul class="list">${c.platform.plain_language.map(x => `<li>${h(x)}</li>`).join("")}</ul>
          ${S.adv ? `<details class="box"><summary>Technical stack</summary><table>${Object.entries(c.platform.stack).map(([k, v]) => `<tr><th>${h(k)}</th><td>${h(v)}</td></tr>`).join("")}</table></details>` : ""}
          <details class="box"><summary>Compare options</summary><div class="table-wrap"><table><tr><th>Option</th><th>Ease</th><th>Cost</th><th>Lock-in</th></tr>${c.platform.tradeoffs.map(o => `<tr><td>${h(o.option)}</td><td>${h(o.ease)}</td><td>${h(o.cost)}</td><td>${h(o.lock_in)}</td></tr>`).join("")}</table></div></details></div>
        <div class="card"><h2>Please double-check</h2>${c.assumptions.length ? `<p class="small muted">These came from my guesses or recommendations. Approving the plan confirms them.</p><ul class="list warn">${c.assumptions.map(a => `<li><span><strong>${h(a.label)}:</strong> ${h(a.value)}</span></li>`).join("")}</ul>` : `<p class="muted">Everything in this plan came directly from you.</p>`}
          ${c.risks.filter(r => !r.blocking && r.severity !== "info").length ? `<h3>Notes</h3><ul class="list warn">${c.risks.filter(r => !r.blocking && r.severity !== "info").map(r => `<li><span>${h(r.title)}. <span class="muted">${h(r.mitigation)}</span></span></li>`).join("")}</ul>` : ""}</div>
      </div>
      ${c.design.home_sections ? `<div class="card"><h2>Your home page, top to bottom</h2><ol class="review">${c.design.home_sections.map(x => `<li><strong>${h(x.type.replace("_", " "))}</strong> <span class="badge">${h(x.variant.replace("_", " "))}</span>${x.why ? ` <span class="small muted">— ${h(x.why)}</span>` : ""}</li>`).join("")}</ol></div>` : ""}
      <div class="card"><h2>How the team thought about it</h2><p class="small muted">Each specialist read what the others had concluded before deciding. Nothing here is published unless it came from you.</p><div id="trail"><span class="spin"></span></div></div>
      <div class="card"><h2>How we'll measure success</h2><p>Cookie-free counts of ${c.measurement.events.map(h).join(", ")}. Main measure: <strong>${h(c.measurement.primary_metric)}</strong>${c.measurement.target ? `, aiming for about ${h(c.measurement.target)} a month` : ""}. We'll review after ${c.measurement.review_after_days} days.</p>
        ${S.adv ? `<details class="box"><summary>Agent summaries & plan hash</summary><ul>${Object.entries(c.agent_summaries).map(([k, v]) => `<li><span class="mono">${h(k)}</span> — ${h(v)}</li>`).join("")}</ul><p class="mono">sha256 ${h(bp.content_hash)}</p></details>` : ""}</div>
      ${pending ? `<div class="sticky-actions"><button class="btn" id="changes">Change answers</button>
        ${can("blueprint.approve") ? `<button class="btn btn-primary" id="approve" ${c.blocking.length ? "disabled" : ""}>Approve plan & start building</button>` : `<span class="muted">An owner, admin or reviewer needs to approve this plan.</span>`}</div>` : ""}`;
    $$("[data-c]", el).forEach(b => (b.style.background = b.dataset.c));
    loadTrail();
    if ($("#changes")) $("#changes").onclick = () => (location.hash = `#/p/${S.pid}/guide`);
    if ($("#approve")) $("#approve").onclick = async () => {
      $("#approve").disabled = true;
      try {
        await api("POST", `/api/projects/${S.pid}/blueprint/decision`, { content_hash: bp.content_hash, decision: "approved" });
        toast("Plan approved. Building your preview…");
        if (can("build.create")) await api("POST", `/api/projects/${S.pid}/builds`);
        location.hash = `#/p/${S.pid}/build`;
      } catch (e) { toast(e.message, true); $("#approve").disabled = false; if (e.status === 409) loadProject(); }
    };
  }

  const AGENT_NAMES = { brand_strategist: "Strategist", information_architect: "Architect", copywriter: "Copywriter",
                        art_director: "Art director", visual_critic: "Visual critic", editor: "Editor" };
  const SKIP_KEYS = new Set(["copy.library", "ia.considered"]);
  function fmtVal(v) {
    if (v == null) return "";
    if (typeof v === "string" || typeof v === "number") return h(v);
    if (Array.isArray(v)) return v.slice(0, 6).map(x => typeof x === "object" ? (x.text != null ? `${h(x.text)}${x.score != null ? ` <span class="muted">(${x.score})</span>` : ""}` : h(JSON.stringify(x))) : h(x)).join(" · ");
    if (v.name) return h(v.name) + (v.fonts ? " — " + v.fonts.map(h).join(" + ") : "");
    if (v.fix) return `${h(v.issue)} → ${h(v.fix)}`;
    if (v.request) return `“${h(v.request)}” → ${(v.applied || []).map(h).join(", ")}`;
    return Object.entries(v).slice(0, 5).map(([k, x]) => `${h(k)}: ${h(typeof x === "object" ? JSON.stringify(x) : x)}`).join(" · ");
  }
  async function loadTrail() {
    const box = $("#trail"); if (!box) return;
    try {
      const t = await api("GET", `/api/projects/${S.pid}/knowledge`);
      const rows = t.filter(e => e.current && AGENT_NAMES[e.agent] && !SKIP_KEYS.has(e.key));
      box.innerHTML = rows.length ? `<ul class="trail">${rows.map(e => `<li><span class="who">${h(AGENT_NAMES[e.agent])}<br><span class="badge">${h(e.kind)}</span></span>
        <span><span class="val">${fmtVal(e.content)}</span>${e.rationale ? `<br><span class="why">${h(e.rationale)}</span>` : ""}${S.adv && e.based_on.length ? `<br><span class="mono">based on: ${e.based_on.map(h).join(", ")}</span>` : ""}</span></li>`).join("")}</ul>` : `<p class="muted small">No notes yet.</p>`;
    } catch (x) { box.textContent = ""; }
  }

  // ------------------------------------------------------------------ Build tab
  async function tabBuild(el) {
    el.innerHTML = `<p class="muted"><span class="spin"></span> Loading builds…</p>`;
    S.builds = await api("GET", `/api/projects/${S.pid}/builds`);
    const state = S.st.project.lifecycle_state;
    const canBuild = can("build.create") && ["scope_approved", "preview_ready", "changes_requested", "failed", "live", "monitoring", "approved_for_release"].includes(state);
    if (!S.builds.length) {
      el.innerHTML = `<div class="empty"><h2>No preview yet</h2><p>${canBuild ? "Your plan is approved. Generate a private preview to see your site." : "Approve your plan first, then come back here to generate a preview."}</p>
        ${canBuild ? `<button class="btn btn-primary" id="gen">Generate preview</button>` : ""}</div>`;
      if ($("#gen")) $("#gen").onclick = startBuild;
      return;
    }
    if (!S.buildSel || !S.builds.find(b => b.id === S.buildSel)) S.buildSel = S.builds[0].id;
    const b = S.builds.find(x => x.id === S.buildSel);
    const running = S.builds.some(x => x.status === "running");
    const qa = b.test_report || {}, sec = b.security_report || {};
    const fails = (qa.results || []).filter(r => !r.passed);
    const sfind = sec.findings || [];
    const latest = S.builds[0].id === b.id;
    const canApprove = latest && b.status === "passed" && can("release.approve") && !b.approval && ["preview_ready", "changes_requested"].includes(state);
    const statusB = { running: `<span class="badge info"><span class="spin"></span> Building & testing</span>`, passed: `<span class="badge ok">Checks passed</span>`,
                      blocked: `<span class="badge bad">Blocked by checks</span>`, failed: `<span class="badge bad">Build failed</span>` };
    el.innerHTML = `<div class="builds"><div>
        ${canBuild && !running ? `<button class="btn btn-primary" id="gen">Generate new preview</button>` : ""}
        <h3 class="small muted">Versions</h3>
        ${S.builds.map(x => `<button class="build-item" data-b="${x.id}" aria-pressed="${x.id === b.id}"><span><strong>Preview ${x.number}</strong><br><span class="small muted">${fmtDate(x.created_at)}</span></span>
          ${x.approval ? `<span class="badge ${x.approval.decision === "approved" ? "ok" : "warn"}">${x.approval.decision === "approved" ? "Approved" : "Changes asked"}</span>` : statusB[x.status] || ""}</button>`).join("")}
      </div><div>
        <div class="card"><div class="card-head"><div><h2>Preview ${b.number}</h2><p class="small muted">${statusB[b.status] || ""} ${S.adv ? `<span class="mono">${h(b.branch)} @ ${h((b.commit_sha || "").slice(0, 12))}</span>` : ""}</p></div>
          <div class="row">${b.status !== "running" ? `<div class="tabs-inline" role="group" aria-label="Device"><button data-dev="desktop" aria-selected="${S.device === "desktop"}">Desktop</button><button data-dev="mobile" aria-selected="${S.device === "mobile"}">Phone</button></div>` : ""}</div></div>
          ${S.lastChange && latest ? `<div class="callout ok"><strong>Applied:</strong> ${S.lastChange.applied.map(h).join(" · ")}${S.lastChange.not_understood.length ? `<br><span class="small">Couldn't do: ${S.lastChange.not_understood.map(h).join("; ")}</span>` : ""}</div>` : ""}
          ${b.status === "running" ? `<p><span class="spin"></span> Generating pages, then running accessibility, layout and security checks in a real browser. This usually takes under a minute.</p>` : `
          <div class="checks">
            <div class="stat"><div class="n">${(qa.total || 0) - (qa.failed || 0) - (qa.warnings || 0)}/${qa.total || 0}</div><div class="l">Quality & accessibility checks passed</div></div>
            <div class="stat"><div class="n">${sec.blocking || 0}</div><div class="l">Blocking security findings (${sec.files_scanned || 0} files scanned)</div></div>
            <div class="stat"><div class="n">${(b.pages || []).length}</div><div class="l">Pages</div></div>
            <div class="stat"><div class="n">${qa.browser && qa.browser.ran ? "✓" : "—"}</div><div class="l">${qa.browser && qa.browser.ran ? "Tested on phone + desktop in Chromium" : "Browser test skipped"}</div></div>
          </div>
          ${fails.length || sfind.length ? `<details class="box" ${b.status === "blocked" ? "open" : ""}><summary>${fails.length + sfind.length} item(s) to look at</summary><ul>
            ${fails.map(r => `<li><span class="badge ${["critical", "high"].includes(r.severity) ? "bad" : "warn"}">${h(r.severity)}</span> ${h(r.check)} — ${h(r.file || "")} ${h(r.detail || "")}</li>`).join("")}
            ${sfind.map(f => `<li><span class="badge ${["critical", "high"].includes(f.severity) ? "bad" : "warn"}">${h(f.severity)}</span> ${h(f.id)} — ${h(f.file)}: ${h(f.detail)}</li>`).join("")}</ul></details>` : ""}
          <div class="frame-bar"><span>Private preview · not indexed by search engines</span><span class="url">${h(b.preview_url)}</span><a class="btn btn-sm" href="${h(b.preview_url)}" target="_blank" rel="noopener">Open</a></div>
          <div class="frame-wrap ${S.device}"><iframe title="Website preview" src="${h(b.preview_url)}" sandbox="allow-same-origin allow-scripts allow-forms allow-popups"></iframe></div>
          <p class="small muted">Try the form — enquiries from the preview are marked as tests in your inbox.</p>
          ${latest && can("build.create") ? `<form id="chg" class="change-box"><label for="chgt" class="q-prompt">Want something different? Just say it.</label>
            <div class="answer-row"><textarea id="chgt" class="input" maxlength="1000" placeholder="e.g. Make the colours darker, move the FAQ above services and use rounder buttons"></textarea>
            <button class="btn btn-primary" type="submit">Apply &amp; rebuild</button></div>
            <div class="helpers">${["Make it darker", "Show services as a list", "Move the FAQ above services", "Rounder buttons", "Make it look more elegant", "Bigger headings"].map(x => `<button type="button" class="btn btn-sm btn-quiet" data-ex="${h(x)}">${h(x)}</button>`).join("")}</div>
            <div id="chgout" aria-live="polite"></div>
            <div class="row mt"><span class="small muted">Or choose directly:</span><button type="button" class="btn btn-sm" id="chgpal">Change colours</button><button type="button" class="btn btn-sm" id="chgtpl">Try another design</button></div></form>` : ""}
          ${critic(b)}
          ${b.diff && b.diff.summary ? `<details class="box"><summary>What changed: ${h(b.diff.summary)}</summary>
            ${b.diff.changed.length ? `<p class="small">Changed: ${b.diff.changed.map(h).join(", ")}</p>` : ""}${b.diff.added.length && b.number > 1 ? `<p class="small">Added: ${b.diff.added.map(h).join(", ")}</p>` : ""}
            ${S.adv ? Object.entries(b.diff.patches || {}).map(([f, p]) => `<pre class="diff">${h(p)}</pre>`).join("") : ""}</details>` : ""}
          ${qa.browser && qa.browser.screenshots ? `<details class="box"><summary>Automated screenshots</summary><div class="shots">${qa.browser.screenshots.map(s => `<img loading="lazy" alt="Screenshot ${h(s)}" src="/api/projects/${S.pid}/builds/${b.id}/screenshots/${h(s)}">`).join("")}</div></details>` : ""}
          ${canApprove ? `<div class="row end mt"><button class="btn btn-primary" id="apr">Approve this version for launch</button></div>` : ""}
          ${b.approval && b.approval.decision === "approved" && latest ? `<div class="callout ok">Approved for launch. <a href="#/p/${S.pid}/launch">Go to launch →</a></div>` : ""}`}
        </div></div></div>`;
    if ($("#gen")) $("#gen").onclick = startBuild;
    $$("[data-b]").forEach(x => (x.onclick = () => { S.buildSel = x.dataset.b; tabBuild(el); }));
    $$("[data-dev]").forEach(x => (x.onclick = () => { S.device = x.dataset.dev; $(".frame-wrap").className = "frame-wrap " + S.device; $$("[data-dev]").forEach(y => y.setAttribute("aria-selected", y === x)); fitFrame(); }));
    fitFrame();
    if ($("#apr")) $("#apr").onclick = () => decideRelease(b, "approved");
    const designDialog = (title, kind) => {
      const d = dialog(`<div class="row"><h2>${h(title)}</h2><span class="spacer"></span><button type="button" class="btn btn-sm" id="dx">Close</button></div><div id="dpick"></div>`);
      d.classList.add("wide");
      $("#dx", d).onclick = () => d.close();
      const pick = async v => {
        d.close();
        try { const r = await api("POST", `/api/projects/${S.pid}/design`, kind === "palette" ? { palette: v } : { template: v });
          S.lastChange = { applied: r.applied, not_understood: [] }; toast("Applied — building a new preview…"); S.buildSel = null; loadProject(); }
        catch (x) { toast(x.message, true); }
      };
      if (kind === "palette") palettePicker($("#dpick", d), pick); else templatePicker($("#dpick", d), pick);
    };
    if ($("#chgpal")) $("#chgpal").onclick = () => designDialog("Choose colours", "palette");
    if ($("#chgtpl")) $("#chgtpl").onclick = () => designDialog("Try another design", "template");
    $$("[data-ex]").forEach(x => (x.onclick = () => { $("#chgt").value = x.dataset.ex; $("#chgt").focus(); }));
    if ($("#chg")) $("#chg").onsubmit = async e => {
      e.preventDefault();
      const text = $("#chgt").value.trim(); if (!text) return;
      const btn = $("#chg button[type=submit]"); btn.disabled = true;
      $("#chgout").innerHTML = `<p class="small"><span class="spin"></span> The editor is working on it…</p>`;
      try {
        const r = await api("POST", `/api/projects/${S.pid}/change-requests`, { text });
        if (!r.build) { $("#chgout").innerHTML = `<div class="callout">${h(r.message)}${r.not_understood.length ? `<br><span class="small">Couldn't do: ${r.not_understood.map(h).join("; ")}</span>` : ""}</div>`; btn.disabled = false; return; }
        toast("Changes applied — building a new preview…");
        S.lastChange = r; S.buildSel = null; await loadProject();
      } catch (x) { $("#chgout").innerHTML = `<div class="callout bad">${h(x.message)}</div>`; btn.disabled = false; }
    };
    if (running) S.poll = setInterval(async () => {
      const bs = await api("GET", `/api/projects/${S.pid}/builds`);
      if (!bs.some(x => x.status === "running")) { clearInterval(S.poll); await loadProject(); }
    }, 2500);
  }

  // Desktop preview renders at a real 1280px and is scaled to fit; phone preview is a true 390px.
  function fitFrame() {
    const wrap = $(".frame-wrap"), fr = wrap && $("iframe", wrap);
    if (!fr) return;
    if (S.device === "mobile") { fr.style.width = "390px"; fr.style.height = "720px"; fr.style.transform = "none"; wrap.style.height = "720px"; return; }
    const scale = Math.min(1, wrap.clientWidth / 1280);
    fr.style.width = "1280px"; fr.style.height = Math.round(720 / scale) + "px";
    fr.style.transform = `scale(${scale})`; fr.style.transformOrigin = "top left"; wrap.style.height = "720px";
  }
  window.addEventListener("resize", () => { if (S.tab === "build") fitFrame(); });

  function critic(b) {
    const rounds = b.critic || [];
    if (!rounds.length) return "";
    const found = rounds.reduce((n, r) => n + r.issues.length, 0);
    const fixes = rounds.flatMap(r => r.fixes.filter(f => f.fix));
    const advice = rounds.flatMap(r => r.fixes.filter(f => f.advice));
    return `<details class="box" open><summary>Design review: ${rounds.length} round${rounds.length > 1 ? "s" : ""} on phone & desktop — ${found ? `${fixes.length} adjustment${fixes.length === 1 ? "" : "s"} made` : "no problems found"}</summary>
      ${fixes.length ? `<ol class="review">${fixes.map(f => `<li><span class="badge">${h(f.where)}</span> <strong>${h(f.fix)}</strong>${f.detail ? `<br><span class="small muted">Measured: ${h(f.detail)}</span>` : ""}</li>`).join("")}</ol>` : `<p class="small muted">Checked overflow, clipped text, headline length, button visibility, card layout, contrast and repetition.</p>`}
      ${advice.map(a => `<div class="callout">${h(a.advice)}</div>`).join("")}</details>`;
  }

  async function startBuild() {
    try { await api("POST", `/api/projects/${S.pid}/builds`); S.buildSel = null; await loadProject(); }
    catch (e) { toast(e.message, true); }
  }
  async function decideRelease(b, decision, comment) {
    try {
      await api("POST", `/api/projects/${S.pid}/builds/${b.id}/decision`, { checksum: b.checksum, decision, comment: comment || null });
      toast(decision === "approved" ? "Approved. You can publish from the Launch tab." : "Noted. Update your answers to make changes.");
      if (decision === "approved") location.hash = `#/p/${S.pid}/launch`; else loadProject();
    } catch (e) { toast(e.message, true); if (e.status === 409) loadProject(); }
  }

  // ------------------------------------------------------------------ Launch tab
  async function tabLaunch(el) {
    const [deps, builds] = await Promise.all([api("GET", `/api/projects/${S.pid}/deployments`), api("GET", `/api/projects/${S.pid}/builds`)]);
    const approved = builds.find(b => b.approval && b.approval.decision === "approved" && b.gates_passed);
    const liveDep = deps.deployments.find(d => d.status === "live");
    const state = S.st.project.lifecycle_state;
    const canPublish = approved && can("deploy") && state === "approved_for_release";
    const bp = S.st.blueprint && S.st.blueprint.content.business;
    el.innerHTML = `
      ${deps.live_url ? `<div class="card hero-card"><div class="card-head"><div><h1>Your site is live</h1><p><a href="${h(deps.live_url)}" target="_blank" rel="noopener">${h(location.origin + deps.live_url)}</a></p></div>
        <div class="row"><button class="btn" id="hc">Run health check</button>${can("deploy") && liveDep && liveDep.rollback_target ? `<button class="btn btn-danger" id="rb">Roll back to previous version</button>` : ""}</div></div><div id="hcout"></div></div>` : ""}
      ${canPublish ? `<div class="card"><h2>Ready to publish preview ${approved.number}</h2>
        <p>Here's exactly what happens when you press publish:</p>
        <ul class="list"><li>The approved preview (fingerprint <span class="mono">${h(approved.checksum.slice(0, 12))}</span>) is copied to your live address — nothing else.</li>
          <li>We check the live copy matches what you approved and that every page loads.</li>
          <li>If a check fails, the previous version stays live automatically.</li>
          <li>You can roll back to the previous version at any time.</li>
          ${bp && bp.domain_status === "have" ? `<li>Your domain <strong>${h(bp.domain_name || "")}</strong> is not changed. Connecting it is a separate step you'll approve.</li>` : `<li>Your site uses a free address. Connecting your own domain later is a separate step — we never buy domains or change DNS automatically.</li>`}</ul>
        <div class="row"><span class="spacer"></span><button class="btn btn-primary" id="pub">Publish to live site</button></div></div>`
        : !deps.live_url ? `<div class="empty"><h2>Not ready to publish yet</h2><p>${approved ? "Only an owner or admin can publish." : "Approve a preview on the Build & preview tab first."}</p></div>` : ""}
      <div class="card"><h2>Release history</h2>${deps.deployments.length ? `<div class="table-wrap"><table><tr><th>Release</th><th>Status</th><th>When</th><th>Health</th></tr>
        ${deps.deployments.map(d => `<tr><td class="mono">${h(d.release_id)}</td><td>${stateBadge(d.status === "live" ? "live" : d.status === "rolled_back" ? "failed" : "", d.status.replace("_", " "))}</td>
          <td>${fmtDate(d.deployed_at || d.created_at)}</td><td>${d.health && d.health.healthy ? "✓ healthy" : "✗ " + h(((d.health || {}).checks || []).filter(c => !c.passed).map(c => c.check).join(", "))}</td></tr>`).join("")}</table></div>` : `<p class="muted">No releases yet.</p>`}</div>`;
    if ($("#pub")) $("#pub").onclick = async () => {
      const pw = await confirmPassword("Publish your website?", `<p>Preview ${approved.number} will become your live site.</p>`, "Publish");
      if (pw == null) return;
      try {
        const r = await api("POST", `/api/projects/${S.pid}/deployments`, { build_id: approved.id, idempotency_key: uid(), password: pw });
        toast(r.status === "live" ? "Your site is live!" : "Health check failed — your previous version is still live.", r.status !== "live");
        loadProject();
      } catch (e) { toast(e.message, true); }
    };
    if ($("#rb")) $("#rb").onclick = async () => {
      const pw = await confirmPassword("Roll back?", "<p>Your live site will switch back to the previous version immediately.</p>", "Roll back");
      if (pw == null) return;
      try { await api("POST", `/api/projects/${S.pid}/deployments/rollback`, { password: pw }); toast("Rolled back to the previous version."); loadProject(); }
      catch (e) { toast(e.message, true); }
    };
    if ($("#hc")) $("#hc").onclick = async () => {
      const r = await api("GET", `/api/projects/${S.pid}/health`);
      $("#hcout").innerHTML = `<div class="callout ${r.healthy ? "ok" : "bad"}">${r.healthy ? "All checks passed" : "Problems found"}: ${r.checks.map(c => (c.passed ? "✓ " : "✗ ") + h(c.check)).join(" · ")}</div>`;
    };
  }

  // ------------------------------------------------------------------ Enquiries tab
  async function tabLeads(el) {
    if (!can("leads.read")) { el.innerHTML = `<div class="empty"><p>Only owners and admins can see enquiries — they contain people's contact details.</p></div>`; return; }
    const leads = await api("GET", `/api/projects/${S.pid}/leads`);
    el.innerHTML = `<div class="card"><div class="card-head"><div><h2>Enquiries</h2><p class="small muted">Deleted automatically when their retention period ends. Every view is recorded in the activity log.</p></div></div>
      ${leads.length ? `<div class="table-wrap"><table><tr><th>Received</th><th>Details</th><th>Email sent</th><th></th></tr>${leads.map(l => `<tr>
        <td>${fmtDate(l.created_at)}${l.is_test ? ' <span class="badge">Test</span>' : ""}</td>
        <td>${Object.entries(l.fields).map(([k, v]) => `<div><span class="muted small">${h(k)}:</span> ${h(v)}</div>`).join("")}</td>
        <td>${h((l.notification || "—").replace(/_/g, " "))}</td><td><button class="btn btn-sm btn-danger" data-del="${l.id}">Delete</button></td></tr>`).join("")}</table></div>`
        : `<p class="muted">No enquiries yet. Submit the form on your preview to send a test.</p>`}</div>`;
    $$("[data-del]").forEach(b => (b.onclick = async () => { await api("DELETE", `/api/projects/${S.pid}/leads/${b.dataset.del}`); tabLeads(el); }));
  }

  // ------------------------------------------------------------------ Insights tab
  async function tabInsights(el) {
    const a = await api("GET", `/api/projects/${S.pid}/analytics`);
    const labels = { page_view: "Page views", cta_click: "Button clicks", form_submit: "Form submissions", call_click: "Call taps", whatsapp_click: "WhatsApp taps" };
    const evs = (a.plan.events || Object.keys(labels));
    const max = Math.max(1, ...evs.map(e => a.totals[e] || 0));
    el.innerHTML = `<div class="card"><h2>Last 30 days</h2><p class="small muted">Counted without cookies or personal data.</p>
      <div class="checks">${evs.map(e => `<div class="stat"><div class="n">${a.totals[e] || 0}</div><div class="l">${h(labels[e] || e)}${e === a.plan.primary_metric ? " · main measure" : ""}</div><div class="meter"><span data-w="${Math.round(100 * (a.totals[e] || 0) / max)}"></span></div></div>`).join("")}
      <div class="stat"><div class="n">${a.leads}</div><div class="l">Real enquiries stored</div></div></div>
      ${a.plan.target ? `<p>Your goal: about <strong>${h(a.plan.target)}</strong> enquiries a month.</p>` : ""}
      <h3>How to read this</h3><ul class="list">${(a.plan.principles || []).map(p => `<li>${h(p)}</li>`).join("")}</ul></div>`;
    $$("[data-w]", el).forEach(s => (s.style.width = s.dataset.w + "%"));
  }

  // ------------------------------------------------------------------ Settings tab
  async function tabSettings(el) {
    const [members, ints, audit, runs] = await Promise.all([
      api("GET", `/api/projects/${S.pid}/members`), api("GET", `/api/projects/${S.pid}/integrations`),
      can("audit.read") ? api("GET", `/api/projects/${S.pid}/audit`) : Promise.resolve([]),
      api("GET", `/api/projects/${S.pid}/agent-runs`)]);
    const smtp = ints.find(i => i.provider === "smtp");
    el.innerHTML = `<div class="grid2">
      <div class="card"><h2>Team</h2><table>${members.map(m => `<tr><td>${h(m.email)}</td><td><span class="badge">${h(m.role)}</span></td></tr>`).join("")}</table>
        ${can("members.manage") ? `<form id="inv" class="row mt"><label class="sr" for="ie">Email</label><input class="input" id="ie" type="email" placeholder="colleague@example.com" required>
          <label class="sr" for="ir">Role</label><select class="input" id="ir"><option value="editor">Editor — answer questions, build previews</option><option value="reviewer">Reviewer — approve plans</option><option value="admin">Admin — publish & see enquiries</option><option value="viewer">Viewer — read only</option></select>
          <button class="btn">Add</button></form>` : ""}</div>
      <div class="card"><h2>Email delivery</h2>${smtp ? `<p>Sending through <strong>${h(smtp.config.host)}</strong> as ${h(smtp.config.from_address)}. Password: <span class="badge ok">stored in vault</span></p>
          <p class="small muted">Access granted: ${smtp.scopes.map(h).join(", ")}</p>${can("integrations.manage") ? `<button class="btn btn-danger btn-sm" id="smtpx">Disconnect & delete password</button>` : ""}`
        : `<p class="small muted">Enquiry emails currently go to the platform outbox. Connect your own mail server to send them from your address.</p>
          ${can("integrations.manage") ? `<form id="smtpf"><div class="grid2"><div class="field"><label for="sh">Mail server</label><input class="input" id="sh" placeholder="smtp.example.com" required></div>
          <div class="field"><label for="sp">Port</label><input class="input" id="sp" type="number" value="587"></div>
          <div class="field"><label for="su">Username</label><input class="input" id="su" autocomplete="off" required></div>
          <div class="field"><label for="sf">Send from</label><input class="input" id="sf" type="email" required></div></div>
          <div class="field"><label for="spw">Password or app password</label><input class="input" id="spw" type="password" autocomplete="new-password" required>
          <span class="small muted">Encrypted in our vault. It's never shown again, never sent to the AI, and never written to logs.</span></div>
          <button class="btn">Connect</button></form>` : ""}`}</div>
    </div>
    ${can("audit.read") ? `<div class="card"><h2>Activity log</h2><div class="table-wrap"><table><tr><th>When</th><th>Who</th><th>What</th><th>Result</th></tr>${audit.slice(0, 60).map(a => `<tr><td class="small">${fmtDate(a.created_at)}</td><td class="small">${h(a.actor)}</td><td><span class="mono">${h(a.action)}</span></td><td>${h(a.outcome)}</td></tr>`).join("")}</table></div></div>` : ""}
    <div class="card"><h2>Behind the scenes</h2><p class="small muted">Each specialist assistant returns a structured, validated result. Only the guide talks to you.</p>
      <div class="table-wrap"><table><tr><th>Assistant</th><th>Result</th><th>Status</th>${S.adv ? "<th>Provider</th><th>ms</th>" : ""}</tr>${runs.slice(0, 40).map(r => `<tr><td class="mono">${h(r.agent)}</td><td class="small">${h(r.summary || "")}</td><td>${h(r.status)}</td>${S.adv ? `<td class="small">${h(r.provider)}</td><td class="small">${r.duration_ms}</td>` : ""}</tr>`).join("")}</table></div></div>
    <div class="card"><h2>Your data</h2><p>Download everything about this project — answers, plans, releases${can("leads.read") ? " and enquiries" : ""} — as a file you can keep or take elsewhere.</p>
      <div class="row"><a class="btn" href="/api/projects/${S.pid}/export" download>Export project data</a>${can("project.delete") ? `<span class="spacer"></span><button class="btn btn-danger" id="delp">Delete project…</button>` : ""}</div></div>`;
    if ($("#inv")) $("#inv").onsubmit = async e => { e.preventDefault(); try { await api("POST", `/api/projects/${S.pid}/members`, { email: $("#ie").value, role: $("#ir").value }); toast("Added."); tabSettings(el); } catch (x) { toast(x.message, true); } };
    if ($("#smtpf")) $("#smtpf").onsubmit = async e => {
      e.preventDefault();
      try { await api("POST", `/api/projects/${S.pid}/integrations/smtp`, { host: $("#sh").value, port: +$("#sp").value, username: $("#su").value, from_address: $("#sf").value, password: $("#spw").value }); toast("Connected."); tabSettings(el); }
      catch (x) { toast(x.message, true); }
    };
    if ($("#smtpx")) $("#smtpx").onclick = async () => { await api("DELETE", `/api/projects/${S.pid}/integrations/${smtp.id}`); toast("Disconnected."); tabSettings(el); };
    if ($("#delp")) $("#delp").onclick = async () => {
      const pw = await confirmPassword("Delete this project?", "<p>This permanently removes the plan, previews, the live site and all stored enquiries. Export first if you want a copy.</p>", "Delete permanently");
      if (pw == null) return;
      try { await api("POST", `/api/projects/${S.pid}/delete`, { password: pw }); toast("Project deleted."); location.hash = "#/"; }
      catch (x) { toast(x.message, true); }
    };
  }

  route();
})();
