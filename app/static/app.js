/* global monaco: false */
"use strict";

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------
const $view = () => document.getElementById("view");

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "html") el.innerHTML = v;
    else el.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    el.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return el;
}

// auth state lives in site.js as window.AUTH

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  let data = null;
  const text = await res.text();
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (res.status === 401) {
    AUTH.required = true;
    AUTH.ok = false;
    renderLogin();
    const err = new Error("unauthorized");
    err.status = 401;
    throw err;
  }
  if (!res.ok) {
    const msg = (data && data.detail) ? data.detail : `HTTP ${res.status}`;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

function renderLogin() {
  runCleanup();
  const input = h("input", { type: "password", placeholder: "access token", style: "width:260px" });
  async function submit() {
    const token = input.value.trim();
    if (!token) return;
    const res = await fetch("api/auth/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    if (res.ok) {
      AUTH.ok = true;
      updateAuthBadge();
      toast("unlocked — welcome");
      route();
    } else {
      toast("wrong token", "error");
      input.select();
    }
  }
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); });
  $view().replaceChildren(
    h("div", { class: "card", style: "max-width:460px; margin:90px auto; text-align:center" },
      h("h2", null, "🔒 shared-bot console"),
      h("p", { class: "hint" }, "This console is protected. Enter the access token (ask the server owner)."),
      h("div", { class: "form-row", style: "justify-content:center" },
        input, h("button", { onclick: submit }, "Unlock"))));
  setTimeout(() => input.focus(), 50);
}

let toastTimer = null;
function toast(msg, type = "ok") {
  const root = document.getElementById("toast-root");
  const el = h("div", { class: `toast ${type}` }, msg);
  root.append(el);
  setTimeout(() => el.remove(), 4500);
}

const esc = (s) => String(s).replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function badge(text, kind) { return h("span", { class: `badge badge-${kind}` }, text); }

function stateBadge(state) {
  const map = {
    running: "green", broken: "red", exited: "red",
    starting: "yellow", sleeping: "muted", stopped: "muted",
  };
  return badge(state || "sleeping", map[state] || "muted");
}

let cleanupFns = [];
function onCleanup(fn) { cleanupFns.push(fn); }
function runCleanup() { cleanupFns.forEach((f) => { try { f(); } catch {} }); cleanupFns = []; }


// ---------------------------------------------------------------------------
// views: dashboard
// ---------------------------------------------------------------------------
async function workerAction(slug, action) {
  try {
    const r = await api("POST", `api/projects/${slug}/worker`, { action });
    toast(`${slug}: ${action} → ${r.state || "?"}`, r.state === "broken" ? "error" : "ok");
    return r;
  } catch (e) { toast(e.message, "error"); }
}

function serviceCell(p) {
  const stopped = p.runtime && (p.runtime.disabled || p.runtime.state === "stopped");
  return stopped
    ? h("button", { class: "small", onclick: async () => { await workerAction(p.slug, "start"); renderDashboard(); } }, "start")
    : h("button", { class: "secondary small", onclick: async () => { await workerAction(p.slug, "stop"); renderDashboard(); } }, "stop");
}

async function renderDashboard() {
  const [status, projects] = await Promise.all([
    api("GET", "api/status"), api("GET", "api/projects"),
  ]);

  const statusCard = h("div", { class: "card" },
    h("h2", null, "Status"),
    h("div", { class: "form-row" },
      h("span", null, "Access: ", AUTH.required
        ? badge("token-protected", "green")
        : badge("OPEN — set access_token in config.json!", "yellow")),
      h("span", null, "Bot: ", status.bot_configured
        ? (status.bot.online
          ? badge(`online as ${status.bot.user}`, "green")
          : badge("offline", "red"))
        : badge("no token configured", "yellow")),
      h("span", { class: "dim" }, `domain: ${status.domain} · projects: ${status.projects} · deployed: ${status.deployed}`),
      (status.bot.guilds || []).length
        ? h("span", { class: "dim" }, `servers: ${status.bot.guilds.join(", ")}`) : null,
    ),
  );

  const rows = projects.map((p) => h("tr", null,
    h("td", null, h("a", { class: "link", href: `#/project/${p.slug}` }, p.nickname),
      " ", h("span", { class: "dim mono" }, `(${p.slug})`)),
    h("td", null, p.author),
    h("td", null, p.deployed ? badge("deployed", "green") : badge("draft only", "muted")),
    h("td", null, stateBadge(p.runtime && p.runtime.state)),
    h("td", { class: "dim" }, `timeout ${p.timeout}s · tolerance ${p.tolerance}s`),
    h("td", { class: "row-actions" }, serviceCell(p)),
  ));

  const projectsCard = h("div", { class: "card" },
    h("h2", null, "Projects"),
    projects.length
      ? h("div", { class: "tablewrap" }, h("table", null,
          h("tr", null, h("th", null, "Project"), h("th", null, "Author"),
            h("th", null, "Code"), h("th", null, "Worker"), h("th", null, "Limits"), h("th", null, "Service")),
          rows))
      : h("p", { class: "hint" }, "No projects yet. Create one below."),
  );

  const slugIn = h("input", { placeholder: "slug (e.g. dice-roller)" });
  const nickIn = h("input", { placeholder: "nickname (shown in aggregated messages)" });
  const authIn = h("input", { placeholder: "author name", value: localStorage.getItem("author") || "" });

  const createCard = h("div", { class: "card" },
    h("h2", null, "New project"),
    h("div", { class: "form-row" },
      h("label", { class: "field" }, "Slug", slugIn),
      h("label", { class: "field" }, "Nickname", nickIn),
      h("label", { class: "field" }, "Author", authIn),
      h("button", {
        onclick: async (e) => {
          e.preventDefault();
          try {
            localStorage.setItem("author", authIn.value);
            const p = await api("POST", "api/projects", {
              slug: slugIn.value.trim(), nickname: nickIn.value.trim(), author: authIn.value.trim(),
            });
            toast(`project '${p.slug}' created`);
            location.hash = `#/project/${p.slug}`;
          } catch (err) { toast(err.message, "error"); }
        },
      }, "Create"),
    ),
  );

  $view().replaceChildren(h("h1", null, "Dashboard"), statusCard, projectsCard, createCard);
}

// ---------------------------------------------------------------------------
// views: packages
// ---------------------------------------------------------------------------
async function renderPackages() {
  const specIn = h("input", { placeholder: "package spec, e.g. 'pillow' or 'requests>=2'", style: "width:280px" });
  const outPre = h("pre", { class: "console", style: "display:none" });
  const listCard = h("div", { class: "card" }, h("h2", null, "Installed"), h("p", null, "loading…"));

  async function renderList() {
    const pkgs = await api("GET", "api/packages");
    listCard.replaceChildren(
      h("h2", null, `Installed (${pkgs.length})`),
      h("div", { class: "tablewrap" },
      h("table", null,
        h("tr", null, h("th", null, "Package"), h("th", null, "Version"), h("th", null, "")),
        pkgs.map((p) => h("tr", null,
          h("td", { class: "mono" }, p.name),
          h("td", { class: "mono dim" }, p.version),
          h("td", { class: "row-actions" }, p.protected
            ? h("button", { class: "core small", disabled: "", title: "platform dependency — the bot platform needs it" }, "core")
            : h("button", {
              class: "danger small",
              onclick: async () => {
                if (!confirm(`Uninstall ${p.name}? Workers currently using it may crash.`)) return;
                await showResult(await api("POST", "api/packages/uninstall", { name: p.name }));
                renderList();
              },
            }, "uninstall")),
        )))),
    );
  }

  async function showResult(r) {
    outPre.style.display = "block";
    outPre.textContent = r.output || JSON.stringify(r);
    outPre.className = "console " + (r.ok ? "result-ok" : "result-err");
  }

  async function install() {
    const spec = specIn.value.trim();
    if (!spec) return;
    outPre.style.display = "block";
    outPre.className = "console";
    outPre.textContent = `installing ${spec} …`;
    try { await showResult(await api("POST", "api/packages/install", { spec })); }
    catch (e) { outPre.textContent = e.message; outPre.className = "console result-err"; }
    renderList();
  }
  specIn.addEventListener("keydown", (e) => { if (e.key === "Enter") install(); });

  await renderList();
  $view().replaceChildren(
    h("h1", null, "Python packages"),
    h("div", { class: "card" },
      h("p", { class: "hint" },
        "One shared environment (the platform venv) is used by every project worker. " +
        "Installs are importable right away by new/restarted workers; restart a project's " +
        "worker (project settings) if it had already imported an older copy."),
      h("div", { class: "form-row" },
        specIn, h("button", { onclick: install }, "pip install")),
      outPre,
    ),
    listCard,
  );
}

// ---------------------------------------------------------------------------
// views: project
// ---------------------------------------------------------------------------
const FAUX_EVENTS = {
  on_message: {
    id: 1001, content: "hello bot",
    author: { id: 42, name: "tester", display_name: "Tester" },
    channel_id: 555, channel_name: "general",
    guild_id: 999, guild_name: "Test Guild", attachments: [],
  },
  on_message_edit: {
    id: 1001, content: "hello bot (edited)", old_content: "hello bot",
    author: { id: 42, name: "tester", display_name: "Tester" },
    channel_id: 555, channel_name: "general",
    guild_id: 999, guild_name: "Test Guild", attachments: [],
  },
  on_message_delete: {
    id: 1001, content: "hello bot", old_content: null,
    author: { id: 42, name: "tester", display_name: "Tester" },
    channel_id: 555, channel_name: "general",
    guild_id: 999, guild_name: "Test Guild", attachments: [],
  },
  on_reaction_add: {
    emoji: "👍", message_id: 1001, channel_id: 555, channel_name: "general",
    user: { id: 42, name: "tester", display_name: "Tester" },
    message_author: { id: 7, name: "friend", display_name: "Friend" },
    guild_id: 999, guild_name: "Test Guild",
  },
  on_reaction_remove: {
    emoji: "👍", message_id: 1001, channel_id: 555, channel_name: "general",
    user: { id: 42, name: "tester", display_name: "Tester" },
    message_author: { id: 7, name: "friend", display_name: "Friend" },
    guild_id: 999, guild_name: "Test Guild",
  },
};

async function renderProject(slug, tab = "code") {
  const info = await api("GET", `api/projects/${slug}`);
  const tabs = ["code", "cheatsheet", "test", "kv", "console", "settings"];
  if (!tabs.includes(tab)) tab = "code";

  const tabBar = h("div", { class: "tabs" },
    tabs.map((t) => h("button", {
      class: t === tab ? "active" : "",
      onclick: () => { location.hash = `#/project/${slug}/${t}`; },
    }, t.toUpperCase())),
  );

  const header = h("div", { class: "header-line" },
    h("h1", null, info.nickname, " ", h("span", { class: "dim mono", style: "font-size:14px" }, `(${slug})`)),
    info.deployed ? badge("deployed", "green") : badge("draft only", "muted"),
    stateBadge(info.runtime && info.runtime.state),
    h("span", { class: "dim" }, `by ${info.author || "?"} · timeout ${info.timeout}s · tolerance ${info.tolerance}s`),
  );

  const body = h("div", null);
  $view().replaceChildren(header, tabBar, body);

  if (tab === "code") await projectCodeTab(body, info);
  else if (tab === "cheatsheet") await projectCheatsheetTab(body, info);
  else if (tab === "test") await projectTestTab(body, info);
  else if (tab === "kv") await projectKvTab(body, info);
  else if (tab === "console") await projectConsoleTab(body, info);
  else if (tab === "settings") await projectSettingsTab(body, info);
}

// --- code tab ---------------------------------------------------------------
let monacoPromise = null;
function loadMonaco() {
  if (!monacoPromise) {
    monacoPromise = new Promise((resolve) => {
      require.config({ paths: { vs: "static/monaco/vs" } });
      require(["vs/editor/editor.main"], () => resolve(window.monaco));
    });
  }
  return monacoPromise;
}

let editorInstance = null;

async function projectCodeTab(root, info) {
  const slug = info.slug;
  const statusLine = h("span", { class: "hint" });
  const compileOut = h("pre", { class: "console", style: "display:none; max-height:200px" });
  const viewBtn = h("button", { class: "secondary small" }, "view deployed (read-only)");
  const editorWrap = h("div", { id: "editor-wrap" });

  const saveBtn = h("button", null, "Save draft (ctrl+s)");
  const compileBtn = h("button", { class: "secondary" }, "py-compile");
  const deployBtn = h("button", null, "Deploy → live");

  root.replaceChildren(
    h("div", { class: "toolbar" }, saveBtn, compileBtn, deployBtn, h("span", { class: "spacer" }), viewBtn),
    statusLine, compileOut, editorWrap,
  );

  const { code } = await api("GET", `api/projects/${slug}/code?which=draft`);
  const m = await loadMonaco();
  if (editorInstance) { editorInstance.dispose(); editorInstance = null; }
  editorInstance = m.editor.create(editorWrap, {
    value: code, language: "python", theme: "vs-dark",
    automaticLayout: true, fontSize: 13, tabSize: 4,
    fontFamily: (window.SITE_CONFIG && SITE_CONFIG.font_code) || undefined,
    minimap: { enabled: false }, scrollBeyondLastLine: false,
  });
  onCleanup(() => { if (editorInstance) { editorInstance.dispose(); editorInstance = null; } });

  let readOnlyDeployed = false;

  async function save() {
    if (readOnlyDeployed) return;
    await api("PUT", `api/projects/${slug}/code`, { code: editorInstance.getValue() });
    statusLine.textContent = `saved draft at ${new Date().toLocaleTimeString()}`;
  }
  editorInstance.addCommand(m.KeyMod.CtrlCmd | m.KeyCode.KeyS, () => { save().catch((e) => toast(e.message, "error")); });
  saveBtn.onclick = () => save().then(() => toast("draft saved")).catch((e) => toast(e.message, "error"));

  compileBtn.onclick = async () => {
    await save();
    compileOut.style.display = "block";
    const r = await api("POST", `api/projects/${slug}/compile`);
    if (r.ok) { compileOut.textContent = "✓ compiles OK"; compileOut.className = "console result-ok"; }
    else { compileOut.textContent = r.error; compileOut.className = "console result-err"; }
  };

  deployBtn.onclick = async () => {
    await save();
    try {
      const r = await api("POST", `api/projects/${slug}/deploy`);
      const ws = r.worker || {};
      if (ws.state === "broken") {
        toast("deployed, but the worker failed to load the code — see console tab", "error");
      } else {
        toast(`deployed (worker ${ws.state || "sleeping"})`);
      }
      renderProject(slug, "code");
    } catch (e) { toast("deploy refused: " + e.message, "error"); }
  };

  viewBtn.onclick = async () => {
    readOnlyDeployed = !readOnlyDeployed;
    if (readOnlyDeployed) {
      const r = await api("GET", `api/projects/${slug}/code?which=active`);
      editorInstance.setValue(r.code || "(not deployed yet)");
      editorInstance.updateOptions({ readOnly: true });
      viewBtn.textContent = "back to draft";
    } else {
      const r = await api("GET", `api/projects/${slug}/code?which=draft`);
      editorInstance.setValue(r.code);
      editorInstance.updateOptions({ readOnly: false });
      viewBtn.textContent = "view deployed (read-only)";
    }
  };
}

// --- cheatsheet tab -----------------------------------------------------------
async function projectCheatsheetTab(root, info) {
  const lang = (window.getLang && getLang()) || "en";
  const sections = (window.CHEATSHEET && CHEATSHEET[lang]) || CHEATSHEET.en;
  root.replaceChildren(...sections.map((sec) => {
    const card = h("div", { class: "card" }, h("h2", null, sec.title));
    if (sec.rows) {
      card.append(h("div", { class: "tablewrap" }, h("table", { class: "tools" },
        h("tbody", null, sec.rows.map(([sig, desc]) => h("tr", null,
          h("td", { class: "mono", style: "white-space:nowrap; vertical-align:top; padding-right:14px" }, sig),
          h("td", { class: "dim" }, desc)))))));
    }
    if (sec.note) card.append(h("p", { class: "hint" }, sec.note));
    if (sec.bullets) card.append(h("ul", null, sec.bullets.map((b) => h("li", null, b))));
    return card;
  }));
}

// --- test tab ----------------------------------------------------------------

// recursively build form fields for the payload object; every leaf input
// carries data-path + data-kind so the object can be reassembled losslessly
function uiField(label, value, path) {
  if (value !== null && typeof value === "object" && !Array.isArray(value)) {
    const fs = h("fieldset", { class: "ui-fieldset" }, h("legend", null, label));
    Object.entries(value).forEach(([k, v]) => fs.append(uiField(k, v, path + "." + k)));
    return fs;
  }
  let input;
  if (Array.isArray(value)) {
    input = h("input", { class: "mono", value: JSON.stringify(value),
                         "data-path": path, "data-kind": "json", title: "JSON value" });
  } else if (typeof value === "boolean") {
    input = h("input", { type: "checkbox", "data-path": path, "data-kind": "bool" });
    input.checked = value;
  } else if (typeof value === "number") {
    input = h("input", { type: "number", step: "any", value: String(value),
                         "data-path": path, "data-kind": "number" });
  } else if (value === null) {
    input = h("input", { placeholder: "null", "data-path": path, "data-kind": "nullish" });
  } else if (label === "content" || label === "old_content") {
    // message text can be multiline
    input = h("textarea", { rows: 2, style: "width:100%", "data-path": path, "data-kind": "string" });
    input.value = String(value);
  } else {
    input = h("input", { value: String(value), "data-path": path, "data-kind": "string" });
  }
  return h("label", { class: "field" }, label, input);
}

function serializeUI(container) {
  const obj = {};
  const inputs = container.querySelectorAll("[data-path]");
  for (const inp of inputs) {
    const parts = inp.dataset.path.split(".");
    let cur = obj;
    for (let i = 0; i < parts.length - 1; i++) {
      cur[parts[i]] = cur[parts[i]] || {};
      cur = cur[parts[i]];
    }
    const kind = inp.dataset.kind;
    let val;
    if (kind === "bool") val = inp.checked;
    else if (kind === "number") val = inp.value === "" ? null : Number(inp.value);
    else if (kind === "nullish") {
      if (inp.value === "") val = null;
      else { try { val = JSON.parse(inp.value); } catch { val = inp.value; } }
    } else if (kind === "json") {
      try { val = JSON.parse(inp.value); }
      catch { throw new Error(`field "${inp.dataset.path}" is not valid JSON`); }
    } else val = inp.value;
    cur[parts[parts.length - 1]] = val;
  }
  return obj;
}

// render one framework action as discord-ish UI; every field stays visible.
function actionCard(a, nickname) {
  const metaParts = [];
  const meta = (skip) => Object.entries(a)
    .filter(([k]) => !["action", ...skip].includes(k))
    .map(([k, v]) => `${k}: ${k === "data_b64" ? `<${String(v).length} chars>` : JSON.stringify(v)}`)
    .join("  ·  ");

  if (a.action === "send" && a.embed) {
    const e = a.embed;
    const color = typeof e.color === "number" ? "#" + e.color.toString(16).padStart(6, "0") : "#5865f2";
    return h("div", { class: "action-card" },
      h("div", { class: "action-kind" }, `${nickname} · send_embed`),
      h("div", { class: "embed-card", style: `border-left-color:${color}` },
        e.title ? h("div", { class: "embed-title" }, e.title) : null,
        e.description ? h("div", { class: "embed-desc" }, e.description) : null,
        (e.fields || []).length ? h("div", { class: "embed-fields" },
          e.fields.map((f) => h("div", { class: "embed-field",
            style: f.inline === false ? "grid-column: 1 / -1" : "" },
            h("div", { class: "fname" }, f.name), h("div", { class: "fvalue" }, f.value)))) : null),
      h("div", { class: "action-meta" }, `color: ${color}` + (meta(["embed"]) ? "  ·  " + meta(["embed"]) : "")));
  }

  if (a.action === "send" && a.file) {
    const bytes = Uint8Array.from(atob(a.file.data_b64), (c) => c.charCodeAt(0));
    const url = URL.createObjectURL(new Blob([bytes]));
    onCleanup(() => URL.revokeObjectURL(url));
    return h("div", { class: "action-card" },
      h("div", { class: "action-kind" }, `${nickname} · send_file`),
      h("div", { class: "file-chip" }, "📎 ",
        h("a", { class: "link", href: url, download: a.file.filename, target: "_blank" },
          a.file.filename),
        h("span", { class: "dim" }, `${bytes.length} bytes`)),
      h("div", { class: "action-meta" }, meta(["file"])));
  }

  if (a.action === "send") {
    return h("div", { class: "action-card" },
      h("div", { class: "action-kind" }, `${nickname} · send`),
      h("div", { class: "bubble" }, a.content),
      h("div", { class: "action-meta" }, meta(["content"])));
  }

  if (a.action === "reply") {
    return h("div", { class: "action-card" },
      h("div", { class: "action-kind" }, `${nickname} · reply`),
      h("div", { class: "reply-marker" }, `↩ reply to message #${a.message_id}`),
      h("div", { class: "bubble" }, a.content),
      h("div", { class: "action-meta" }, meta(["content"])));
  }

  if (a.action === "react") {
    return h("div", { class: "action-card" },
      h("div", { class: "action-kind" }, `${nickname} · add_reaction`),
      h("span", { class: "react-chip" }, a.emoji),
      h("div", { class: "action-meta" }, meta(["emoji"])));
  }

  // unknown action kinds still render fully, generically
  return h("div", { class: "action-card" },
    h("div", { class: "action-kind" }, `${nickname} · ${a.action || "?"}`),
    h("pre", { class: "console", style: "margin:0" }, JSON.stringify(a, null, 2)));
}

async function projectTestTab(root, info) {
  const slug = info.slug;
  let mode = "ui"; // "ui" (default) | "json"

  const eventSel = h("select", null,
    Object.keys(FAUX_EVENTS).map((e) => h("option", { value: e }, e)));
  const payloadTA = h("textarea", { rows: 12, style: "width:100%; display:none" });
  const uiWrap = h("div", null);
  const jsonBtn = h("button", { class: "small secondary" }, "JSON");
  const uiBtn = h("button", { class: "small" }, "UI");
  const resultDiv = h("div", null, h("p", { class: "hint" }, "Run a test to see what your code would do."));
  const runBtn = h("button", null, "Run test (draft + cloned KV)");

  const storageKey = () => `test-payload:${slug}:${eventSel.value}`;

  function tryParse(s) { try { return JSON.parse(s); } catch { return undefined; } }

  function loadPayload() {
    const saved = localStorage.getItem(storageKey());
    const obj = tryParse(saved) ?? structuredClone(FAUX_EVENTS[eventSel.value]);
    payloadTA.value = JSON.stringify(obj, null, 2);
    if (mode === "ui") { uiWrap.replaceChildren(); Object.entries(obj).forEach(([k, v]) => uiWrap.append(uiField(k, v, k))); }
  }

  function showMode() {
    jsonBtn.className = mode === "json" ? "small" : "small secondary";
    uiBtn.className = mode === "ui" ? "small" : "small secondary";
    payloadTA.style.display = mode === "json" ? "" : "none";
    uiWrap.style.display = mode === "ui" ? "" : "none";
  }

  jsonBtn.onclick = () => {
    if (mode === "ui") {
      let obj;
      try { obj = serializeUI(uiWrap); } catch (e) { toast(e.message, "error"); return; }
      payloadTA.value = JSON.stringify(obj, null, 2);
    }
    mode = "json"; showMode(); persist();
  };
  uiBtn.onclick = () => {
    if (mode === "json") {
      const obj = tryParse(payloadTA.value);
      if (obj === undefined) { toast("payload is not valid JSON — fix it before switching to the UI form", "error"); return; }
      if (obj === null || typeof obj !== "object" || Array.isArray(obj)) {
        toast("payload must be a JSON object", "error"); return;
      }
      uiWrap.replaceChildren();
      Object.entries(obj).forEach(([k, v]) => uiWrap.append(uiField(k, v, k)));
    }
    mode = "ui"; showMode(); persist();
  };

  function currentPayload() {
    if (mode === "ui") return serializeUI(uiWrap);
    const obj = tryParse(payloadTA.value);
    if (obj === undefined) throw new Error("payload is not valid JSON");
    return obj;
  }
  function persist() {
    try { localStorage.setItem(storageKey(), JSON.stringify(currentPayload())); } catch {}
  }

  eventSel.onchange = () => { loadPayload(); };
  payloadTA.addEventListener("input", () => persist());
  uiWrap.addEventListener("input", () => persist());

  runBtn.onclick = async () => {
    let data;
    try { data = currentPayload(); } catch (e) { toast(e.message, "error"); return; }
    persist();
    runBtn.disabled = true;
    resultDiv.replaceChildren(h("p", { class: "hint" },
      `running ${eventSel.value} against draft.py with a fresh copy of the real KV store…`));
    try {
      const r = await api("POST", `api/projects/${slug}/test`, { event: eventSel.value, data });
      const parts = [];
      if (r.phase === "compile") {
        parts.push(h("pre", { class: "console result-err" }, "COMPILE FAILED:\n" + r.error));
      } else {
        parts.push(h("p", null,
          r.ok ? h("span", { class: "result-ok" }, "✓ handler ran") : h("span", { class: "result-err" }, "✗ handler failed"),
          ` (${r.duration}s)`));
        if (r.error) {
          parts.push(h("h3", null, "Error"),
            h("pre", { class: "console result-err" }, r.error.traceback || r.error.message));
        }
        parts.push(h("h3", null, `What would happen on Discord (${(r.actions || []).length})`));
        if ((r.actions || []).length) {
          r.actions.forEach((a) => parts.push(actionCard(a, info.nickname)));
        } else {
          parts.push(h("p", { class: "hint" }, "no actions (nothing would be sent)"));
        }
        parts.push(h("h3", null, `Logs (${(r.logs || []).length})`),
          (r.logs || []).length
            ? h("pre", { class: "console" }, (r.logs || []).join("\n"))
            : h("p", { class: "hint" }, "no log output"));
      }
      resultDiv.replaceChildren(...parts);
    } catch (e) {
      resultDiv.replaceChildren(h("pre", { class: "console result-err" }, e.message));
    } finally {
      runBtn.disabled = false;
    }
  };

  loadPayload();
  showMode();
  root.replaceChildren(
    h("div", { class: "card" },
      h("h2", null, "Fake event"),
      h("div", { class: "form-row" },
        h("label", { class: "field" }, "Event type", eventSel),
        h("label", { class: "field" }, "Payload editor", h("div", { class: "row-actions" }, jsonBtn, uiBtn))),
      h("div", { style: "height:8px" }),
      payloadTA, uiWrap,
      h("div", { style: "height:10px" }),
      runBtn, " ",
      h("span", { class: "hint" },
        "Both editors edit the same object — switch freely. Test runs use a fresh copy of the real KV; nothing is sent to Discord.")),
    h("div", { class: "card" }, h("h2", null, "Result"), resultDiv),
  );
}

// --- kv tab ------------------------------------------------------------------
async function projectKvTab(root, info) {
  const slug = info.slug;
  let which = "real";
  const tableWrap = h("div", null);
  const fileInput = h("input", { type: "file", accept: ".json,application/json", style: "display:none" });
  const realBtn = h("button", { class: "small" }, "real KV");
  const testBtn = h("button", { class: "small secondary" }, "test KV");
  const resetBtn = h("button", { class: "small secondary", style: "display:none" },
    "reset test KV := real KV");

  function setWhich(w) {
    which = w;
    realBtn.className = w === "real" ? "small" : "small secondary";
    testBtn.className = w === "test" ? "small" : "small secondary";
    resetBtn.style.display = w === "test" ? "" : "none";
    renderTable();
  }
  realBtn.onclick = () => setWhich("real");
  testBtn.onclick = () => setWhich("test");
  resetBtn.onclick = async () => {
    const real = await api("GET", `api/projects/${slug}/kv?which=real`);
    await api("PUT", `api/projects/${slug}/kv?which=test`, real);
    toast("test KV reset from real KV");
    renderTable();
  };

  fileInput.onchange = async () => {
    const f = fileInput.files[0];
    if (!f) return;
    let data;
    try { data = JSON.parse(await f.text()); }
    catch (e) { toast("not valid JSON: " + e.message, "error"); return; }
    if (typeof data !== "object" || data === null || Array.isArray(data)) {
      toast("KV import must be a JSON object", "error"); return;
    }
    await api("PUT", `api/projects/${slug}/kv?which=${which}`, data);
    toast(`imported ${Object.keys(data).length} keys into ${which} KV`);
    renderTable();
  };

  async function setKey(key, rawValue) {
    let value;
    try { value = JSON.parse(rawValue); } catch { value = rawValue; }
    await api("PUT", `api/projects/${slug}/kv/${encodeURIComponent(key)}?which=${which}`, { value });
    toast(`saved "${key}" = ${JSON.stringify(value)}`);
  }

  async function renderTable() {
    const data = await api("GET", `api/projects/${slug}/kv?which=${which}`);
    const keys = Object.keys(data);
    const newKey = h("input", { placeholder: "new key" });
    const newVal = h("input", { placeholder: 'value (JSON, e.g. 42, "text", [1,2])' });
    tableWrap.replaceChildren(
      h("div", { class: "tablewrap" }, h("table", { class: "kv-table" },
        h("tr", null, h("th", null, "Key"), h("th", null, "Value (JSON)"), h("th", null, "")),
        h("tr", null,
          h("td", null, newKey),
          h("td", null, newVal),
          h("td", null, h("button", {
            class: "small",
            onclick: async () => {
              if (!newKey.value.trim()) return;
              await setKey(newKey.value.trim(), newVal.value);
              renderTable();
            },
          }, "add"))),
        keys.map((k) => {
          const valIn = h("input", { value: JSON.stringify(data[k]) });
          let dirty = false;
          valIn.addEventListener("input", () => { dirty = true; });
          const saveIfDirty = () => { if (dirty) setKey(k, valIn.value); };
          valIn.addEventListener("blur", saveIfDirty);
          valIn.addEventListener("keydown", (e) => { if (e.key === "Enter") { saveIfDirty(); valIn.blur(); } });
          return h("tr", null,
            h("td", null, k),
            h("td", null, valIn),
            h("td", null, h("button", {
              class: "danger small",
              onclick: async () => {
                if (!confirm(`delete key "${k}" from ${which} KV?`)) return;
                await api("DELETE", `api/projects/${slug}/kv/${encodeURIComponent(k)}?which=${which}`);
                renderTable();
              },
            }, "del")));
        }))),
      keys.length === 0 ? h("p", { class: "hint" }, "store is empty") : null,
    );
  }

  root.replaceChildren(
    h("div", { class: "card" },
      h("div", { class: "toolbar" },
        realBtn, testBtn, resetBtn, h("span", { class: "spacer" }),
        h("button", {
          class: "secondary small",
          onclick: () => { location.href = `api/projects/${slug}/kv?which=${which}&download=1`; },
        }, "⬇ export JSON"),
        h("button", { class: "secondary small", onclick: () => fileInput.click() }, "⬆ import JSON (replaces store)"),
        fileInput),
      tableWrap),
  );
  await renderTable();
}

// --- console tab ---------------------------------------------------------------
async function projectConsoleTab(root, info) {
  const slug = info.slug;
  const pre = h("pre", { class: "console", style: "height: calc(100vh - 260px); min-height: 380px; max-height:none" }, "loading…");
  let stop = false;
  onCleanup(() => { stop = true; timer && clearTimeout(timer); });
  let timer = null;
  async function poll() {
    if (stop) return;
    try {
      const r = await api("GET", `api/projects/${slug}/console?tail=400`);
      const nearBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 60;
      pre.textContent = r.lines.join("\n") || "(console is empty)";
      if (nearBottom) pre.scrollTop = pre.scrollHeight;
    } catch (e) {
      if (!stop) pre.textContent = "failed to load console: " + e.message;
    }
    timer = setTimeout(poll, 2500);
  }
  poll();
  root.replaceChildren(
    h("div", { class: "card" },
      h("div", { class: "toolbar" },
        h("h2", null, "Console"),
        h("span", { class: "hint" }, "log(), print(), errors and worker lifecycle",
        )),
      pre),
  );
}

// --- settings tab --------------------------------------------------------------
async function projectSettingsTab(root, info) {
  const slug = info.slug;
  const nick = h("input", { value: info.nickname });
  const author = h("input", { value: info.author });
  const timeout = h("input", { type: "number", step: "0.5", min: "0.2", max: "600", value: info.timeout });
  const tolerance = h("input", { type: "number", step: "0.5", min: "0.2", max: "600", value: info.tolerance });

  const saveBtn = h("button", null, "Save metadata");
  saveBtn.onclick = async () => {
    try {
      await api("PATCH", `api/projects/${slug}`, {
        nickname: nick.value, author: author.value,
        timeout: parseFloat(timeout.value), tolerance: parseFloat(tolerance.value),
      });
      toast("metadata saved");
      renderProject(slug, "settings");
    } catch (e) { toast(e.message, "error"); }
  };

  const restartBtn = h("button", { class: "secondary" }, "Restart worker");
  restartBtn.onclick = async () => {
    const r = await api("POST", `api/projects/${slug}/restart`);
    toast("worker state: " + (r.state || "?"), r.state === "broken" ? "error" : "ok");
    renderProject(slug, "settings");
  };
  const startBtn = h("button", null, "Start");
  startBtn.onclick = async () => { await workerAction(slug, "start"); renderProject(slug, "settings"); };
  const stopBtn = h("button", { class: "secondary" }, "Stop");
  stopBtn.onclick = async () => { await workerAction(slug, "stop"); renderProject(slug, "settings"); };

  const delBtn = h("button", { class: "danger" }, "Delete project");
  delBtn.onclick = async () => {
    if (!confirm(`Delete project '${slug}'? This removes code, KV stores and console.`)) return;
    if (!confirm("Really delete? There is no undo (except git).")) return;
    await api("DELETE", `api/projects/${slug}`);
    toast(`project '${slug}' deleted`);
    location.hash = "#/";
  };

  root.replaceChildren(
    h("div", { class: "card" },
      h("h2", null, "Metadata"),
      h("div", { class: "form-row" },
        h("label", { class: "field" }, "Nickname (prefix in aggregated messages)", nick),
        h("label", { class: "field" }, "Author", author)),
      h("div", { style: "height:10px" }),
      h("div", { class: "form-row" },
        h("label", { class: "field" }, "Max handler time (s) — worker is killed after this", timeout),
        h("label", { class: "field" }, "Max tolerance (s) — aggregated messages go out no later than this", tolerance),
        saveBtn)),
    h("div", { class: "card" },
      h("h2", null, "Maintenance"),
      h("div", { class: "form-row" }, startBtn, stopBtn, restartBtn,
        h("span", { class: "hint" }, "Stop pauses this project's event handling (it stays stopped until started). Restart reloads handler.py and picks up newly installed packages."))),
    h("div", { class: "card" },
      h("h2", null, "Danger zone"),
      h("div", { class: "form-row" }, delBtn)),
  );
}

// ---------------------------------------------------------------------------
// router
// ---------------------------------------------------------------------------
async function route() {
  runCleanup();
  if (AUTH.required && !AUTH.ok) { renderLogin(); return; }
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  document.querySelectorAll("#topbar nav a").forEach((a) => {
    a.classList.toggle("active",
      a.dataset.nav === (parts[0] === "packages" ? "packages" : "dashboard"));
  });
  try {
    if (parts[0] === "packages") await renderPackages();
    else if (parts[0] === "project" && parts[1]) await renderProject(parts[1], parts[2]);
    else await renderDashboard();
  } catch (e) {
    $view().replaceChildren(h("div", { class: "card" },
      h("h2", null, "Error"), h("pre", { class: "console result-err" }, e.message),
      h("p", null, h("a", { class: "link", href: "#/" }, "← back to dashboard"))));
  }
}

async function boot() {
  await initAuth();
  route();
}

window.addEventListener("hashchange", route);
window.addEventListener("langchange", () => {
  if (/^#\/project\/[^/]+\/cheatsheet/.test(location.hash)) route();
});
boot();
