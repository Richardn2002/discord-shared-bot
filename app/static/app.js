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
    reply_to: null, mentions: [], mention_everyone: false, pinned: false,
    created_at: "2026-01-01T12:00:00+00:00", edited_at: null,
    jump_url: "https://discord.com/channels/999/555/1001", old_content: null,
  },
  on_message_edit: {
    id: 1001, content: "hello bot (edited)", old_content: "hello bot",
    author: { id: 42, name: "tester", display_name: "Tester" },
    channel_id: 555, channel_name: "general",
    guild_id: 999, guild_name: "Test Guild", attachments: [],
    reply_to: null, mentions: [], mention_everyone: false, pinned: false,
    created_at: "2026-01-01T12:00:00+00:00", edited_at: "2026-01-01T12:05:00+00:00",
    jump_url: "https://discord.com/channels/999/555/1001",
  },
  on_message_delete: {
    id: 1001, content: "hello bot", old_content: null,
    author: { id: 42, name: "tester", display_name: "Tester" },
    channel_id: 555, channel_name: "general",
    guild_id: 999, guild_name: "Test Guild", attachments: [],
    reply_to: null, mentions: [], mention_everyone: false, pinned: false,
    created_at: "2026-01-01T12:00:00+00:00", edited_at: null,
    jump_url: "https://discord.com/channels/999/555/1001",
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
    const author = typeof e.author === "string" ? { name: e.author } : e.author;
    const footer = typeof e.footer === "string" ? { text: e.footer } : e.footer;
    return h("div", { class: "action-card" },
      h("div", { class: "action-kind" }, `${nickname} · send_embed`),
      h("div", { class: "embed-card", style: `border-left-color:${color}` },
        h("div", { class: "embed-body" },
          h("div", { class: "embed-left" },
            author && author.name ? h("div", { class: "embed-author" },
              author.icon_url ? h("img", { src: author.icon_url, alt: "" }) : null,
              author.url ? h("a", { href: author.url, target: "_blank" }, author.name) : h("span", null, author.name)) : null,
            e.title ? h("div", { class: "embed-title" },
              e.url ? h("a", { href: e.url, target: "_blank" }, e.title) : e.title) : null,
            e.description ? h("div", { class: "embed-desc" }, e.description) : null,
            (e.fields || []).length ? h("div", { class: "embed-fields" },
              e.fields.map((f) => h("div", { class: "embed-field",
                style: f.inline === false ? "grid-column: 1 / -1" : "" },
                h("div", { class: "fname" }, f.name), h("div", { class: "fvalue" }, f.value)))) : null),
          e.thumbnail ? h("img", { class: "embed-thumb", src: e.thumbnail, alt: "thumbnail" }) : null),
        e.image ? h("img", { class: "embed-image", src: e.image, alt: "embed image" }) : null,
        ((footer && footer.text) || e.timestamp) ? h("div", { class: "embed-footer" },
          footer && footer.icon_url ? h("img", { src: footer.icon_url, alt: "" }) : null,
          h("span", null, [footer && footer.text, e.timestamp].filter(Boolean).join(" · "))) : null),
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
    const isReply = a.reply_to_message_id != null;
    return h("div", { class: "action-card" },
      h("div", { class: "action-kind" }, `${nickname} · ${isReply ? "reply" : "send"}`),
      isReply ? h("div", { class: "reply-marker" }, `↩ quotes message #${a.reply_to_message_id}`) : null,
      h("div", { class: "bubble" }, a.content),
      h("div", { class: "action-meta" }, meta(["content", "reply_to_message_id"])));
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

// one object edited two ways (UI form ⇄ JSON), switchable without data loss
function tryParse(s) { try { return JSON.parse(s); } catch { return undefined; } }

function makeDualEditor(onChange) {
  let mode = "ui"; // "ui" (default) | "json"
  const ta = h("textarea", { rows: 10, style: "width:100%; display:none" });
  const uiWrap = h("div", null);
  const uiBtn = h("button", { class: "small", type: "button" }, "UI");
  const jsonBtn = h("button", { class: "small secondary", type: "button" }, "JSON");

  function buildUI(obj) {
    uiWrap.replaceChildren(...Object.entries(obj).map(([k, v]) => uiField(k, v, k)));
  }
  function show() {
    uiBtn.className = mode === "ui" ? "small" : "small secondary";
    jsonBtn.className = mode === "json" ? "small" : "small secondary";
    ta.style.display = mode === "json" ? "" : "none";
    uiWrap.style.display = mode === "ui" ? "" : "none";
  }
  function setObj(obj) {
    ta.value = JSON.stringify(obj, null, 2);
    if (mode === "ui") buildUI(obj);
  }
  function getObj() {
    if (mode === "ui") return serializeUI(uiWrap);
    const v = tryParse(ta.value);
    if (v === undefined) throw new Error("not valid JSON");
    return v;
  }

  uiBtn.onclick = () => {
    if (mode === "json") {
      const obj = tryParse(ta.value);
      if (obj === undefined) { toast("not valid JSON — fix it before switching to UI", "error"); return; }
      if (obj === null || typeof obj !== "object" || Array.isArray(obj)) {
        toast("must be a JSON object", "error"); return;
      }
      buildUI(obj);
    }
    mode = "ui"; show(); onChange();
  };
  jsonBtn.onclick = () => {
    if (mode === "ui") {
      let obj;
      try { obj = serializeUI(uiWrap); } catch (e) { toast(e.message, "error"); return; }
      ta.value = JSON.stringify(obj, null, 2);
    }
    mode = "json"; show(); onChange();
  };
  ta.addEventListener("input", onChange);
  uiWrap.addEventListener("input", onChange);

  const toggleRow = h("div", { class: "row-actions" }, uiBtn, jsonBtn);
  const body = h("div", null, ta, uiWrap);
  const el = h("div", null, toggleRow, h("div", { style: "height:6px" }), body);
  return { el, toggleRow, body, getObj, setObj };
}

async function projectTestTab(root, info) {
  const slug = info.slug;

  const eventSel = h("select", null,
    Object.keys(FAUX_EVENTS).map((e) => h("option", { value: e }, e)));
  const resultDiv = h("div", null, h("p", { class: "hint" }, "Run a test to see what your code would do."));
  const runBtn = h("button", null, "Run test (draft + cloned KV)");

  const payloadEditor = makeDualEditor(() => persistPayload());
  const fakeEditor = makeDualEditor(() => persistFake());
  const fakeEnable = h("input", { type: "checkbox" });

  const storageKey = () => `test-payload:${slug}:${eventSel.value}`;
  const fakeKey = `test-fake-msg:${slug}`;
  const fakeOnKey = `test-fake-on:${slug}`;

  function loadPayload() {
    payloadEditor.setObj(tryParse(localStorage.getItem(storageKey()))
      ?? structuredClone(FAUX_EVENTS[eventSel.value]));
  }
  function persistPayload() {
    try { localStorage.setItem(storageKey(), JSON.stringify(payloadEditor.getObj())); } catch {}
  }

  function loadFake() {
    fakeEnable.checked = localStorage.getItem(fakeOnKey) === "1";
    const tpl = structuredClone(FAUX_EVENTS.on_message);
    delete tpl.old_content;
    tpl.id = 2002;
    tpl.content = "the message being replied to";
    fakeEditor.setObj(tryParse(localStorage.getItem(fakeKey)) ?? tpl);
    updateFakeVisibility();
  }
  function persistFake() {
    try { localStorage.setItem(fakeKey, JSON.stringify(fakeEditor.getObj())); } catch {}
  }
  function updateFakeVisibility() {
    fakeEditor.el.style.display = fakeEnable.checked ? "" : "none";
  }
  fakeEnable.onchange = () => {
    localStorage.setItem(fakeOnKey, fakeEnable.checked ? "1" : "0");
    updateFakeVisibility();
  };

  eventSel.onchange = () => { loadPayload(); };

  runBtn.onclick = async () => {
    let data, fake = null;
    try {
      data = payloadEditor.getObj();
      if (fakeEnable.checked) fake = fakeEditor.getObj();
    } catch (e) { toast(e.message, "error"); return; }
    persistPayload();
    persistFake();
    runBtn.disabled = true;
    resultDiv.replaceChildren(h("p", { class: "hint" },
      `running ${eventSel.value} against draft.py with a fresh copy of the real KV store…`));
    resultDiv.replaceChildren(h("p", { class: "hint" },
      `running ${eventSel.value} against draft.py with a fresh copy of the real KV store…`));
    try {
      const r = await api("POST", `api/projects/${slug}/test`, { event: eventSel.value, data, fake_message: fake });
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
        if ((r.queries || []).length) {
          parts.push(h("h3", null, `Lookups (${r.queries.length})`));
          r.queries.forEach((q) => {
            parts.push(h("div", { class: "action-card" },
              h("div", { class: "action-kind" }, `${q.query}()`),
              h("div", { class: "action-meta" }, `args: ${JSON.stringify(q.args)}`),
              h("div", { class: "action-meta" }, q.error
                ? `→ None (${q.error})`
                : q.result
                  ? `→ message ${q.result.id}: ${JSON.stringify(q.result.content)}` +
                    (q.result.author ? ` · by ${q.result.author.name}` : "")
                  : "→ None (not found)")));
          });
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
  loadFake();
  root.replaceChildren(
    h("div", { class: "card" },
      h("h2", null, "Fake event"),
      h("div", { class: "form-row" },
        h("label", { class: "field" }, "Event type", eventSel),
        h("label", { class: "field" }, "Payload editor", payloadEditor.toggleRow)),
      payloadEditor.body,
      h("div", { style: "height:10px" }),
      runBtn, " ",
      h("span", { class: "hint" },
        "UI and JSON edit the same object — switch freely. Test runs use a fresh copy of the real KV; nothing is sent to Discord.")),
    h("div", { class: "card" },
      h("h2", null, "Fake lookup (optional)"),
      h("label", { class: "row-actions", style: "gap:8px; margin-bottom:8px" }, fakeEnable,
        h("span", null, "answer get_message() with this message")),
      h("p", { class: "hint" },
        "During tests get_message() never touches Discord: asking for this message's id returns it; any other id returns None. Lookups appear under Result → Lookups."),
      fakeEditor.el),
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
      ...(keys.length === 0 ? [h("p", { class: "hint" }, "store is empty")] : []),
    );
  }

  // --- secrets section (single .env store, read-only for handlers via secret_get)
  const secWrap = h("div", null);
  async function renderSecrets() {
    const data = await api("GET", `api/projects/${slug}/secrets`);
    const keys = Object.keys(data);
    const newKey = h("input", { placeholder: "KEY_NAME" });
    const newVal = h("input", { placeholder: "secret value (plain text)" });
    async function setSecret(k, v) {
      await api("PUT", `api/projects/${slug}/secrets/${encodeURIComponent(k)}`, { value: v });
      toast(`saved secret ${k}`);
    }
    secWrap.replaceChildren(
      h("div", { class: "tablewrap" }, h("table", { class: "kv-table" },
        h("tr", null, h("th", null, "Key"), h("th", null, "Value"), h("th", null, "")),
        h("tr", null,
          h("td", null, newKey),
          h("td", null, newVal),
          h("td", null, h("button", {
            class: "small",
            onclick: async () => {
              if (!newKey.value.trim()) return;
              await setSecret(newKey.value.trim(), newVal.value);
              renderSecrets();
            },
          }, "add"))),
        keys.map((k) => {
          const valIn = h("input", { value: data[k] });
          let dirty = false;
          valIn.addEventListener("input", () => { dirty = true; });
          const saveIfDirty = () => { if (dirty) setSecret(k, valIn.value); };
          valIn.addEventListener("blur", saveIfDirty);
          valIn.addEventListener("keydown", (e) => { if (e.key === "Enter") { saveIfDirty(); valIn.blur(); } });
          return h("tr", null,
            h("td", { class: "mono" }, k),
            h("td", null, valIn),
            h("td", null, h("button", {
              class: "danger small",
              onclick: async () => {
                if (!confirm(`delete secret "${k}"?`)) return;
                await api("DELETE", `api/projects/${slug}/secrets/${encodeURIComponent(k)}`);
                renderSecrets();
              },
            }, "del")));
        }))),
      ...(keys.length === 0 ? [h("p", { class: "hint" }, "no secrets stored")] : []),
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
    h("div", { class: "card" },
      h("h2", null, "Secrets"),
      h("p", { class: "hint" },
        "Backed by a gitignored .env file in this project's folder. Read in code with ",
        h("code", null, 'secret_get("KEY")'),
        " — handlers can only read. Edits apply immediately, no restart needed."),
      secWrap),
  );
  await renderTable();
  await renderSecrets();
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
// archive explorer
// ---------------------------------------------------------------------------
const ARCHIVE_FIELDS = [
  { id: "content", label: "Message content", type: "text" },
  { id: "author_display_name", label: "Author display name", type: "text" },
  { id: "author_name", label: "Author username", type: "text" },
  { id: "author_id", label: "Author", type: "id" },
  { id: "author_is_bot", label: "Author is a bot", type: "bool" },
  { id: "channel_name", label: "Channel name", type: "text" },
  { id: "channel_id", label: "Channel", type: "id" },
  { id: "guild_name", label: "Server name", type: "text" },
  { id: "guild_id", label: "Server", type: "id" },
  { id: "created_ts", label: "Message date", type: "date" },
  { id: "edited_ts", label: "Edited date", type: "date" },
  { id: "pinned", label: "Pinned", type: "bool" },
  { id: "has_attachments", label: "Has attachments", type: "bool" },
  { id: "has_embeds", label: "Has embeds", type: "bool" },
  { id: "attachment_filename", label: "Attachment filename", type: "text" },
  { id: "attachment_content_type", label: "Attachment type", type: "text" },
  { id: "attachment_size_bytes", label: "Attachment size (bytes)", type: "number" },
  { id: "attachment_kind", label: "Attachment kind", type: "text" },
  { id: "attachment_count", label: "Attachment count", type: "number" },
  { id: "content_length", label: "Message length", type: "number" },
  { id: "message_type", label: "Message type", type: "number" },
  { id: "id", label: "Message ID", type: "id" },
  { id: "reference_message_id", label: "Reply target ID", type: "id" },
  { id: "search_text", label: "Full searchable text", type: "text" },
];
const ARCHIVE_OPS = {
  text: ["contains", "not_contains", "equals", "not_equals", "starts_with", "ends_with", "regex", "is_empty", "is_not_empty"],
  number: ["equals", "not_equals", "gt", "gte", "lt", "lte", "between", "is_null", "not_null"],
  id: ["equals", "not_equals", "gt", "gte", "lt", "lte", "between", "is_null", "not_null"],
  date: ["equals", "not_equals", "gt", "gte", "lt", "lte", "between", "is_null", "not_null"],
  bool: ["is_true", "is_false"],
};
const ARCHIVE_OP_LABEL = {
  contains: "contains", not_contains: "does not contain", equals: "is", not_equals: "is not",
  starts_with: "starts with", ends_with: "ends with", regex: "matches regex",
  in: "is one of", not_in: "is not one of",
  gt: "after / greater than", gte: "on or after / at least",
  lt: "before / less than", lte: "on or before / at most",
  between: "between", is_empty: "is empty", is_not_empty: "is not empty",
  is_true: "is true", is_false: "is false", is_null: "is empty (null)", not_null: "is not empty",
};
const ARCHIVE_NO_VALUE = new Set(["is_empty", "is_not_empty", "is_true", "is_false", "is_null", "not_null"]);
const ARCHIVE_ID_KEYS = new Set(["id", "guild_id", "channel_id", "author_id", "reference_message_id", "reference_channel_id", "parent_id"]);

let archiveState = null;
let archiveUi = {};

function archiveT(key, fallback) {
  const lang = (window.getLang && getLang()) || "en";
  const dict = (window.I18N && (I18N[lang] || {})) || {};
  return dict[key] ?? ((window.I18N && I18N.en && I18N.en[key]) || fallback || key);
}

function archiveNewState() {
  return {
    guildId: null, channels: [], guilds: [], users: [], stats: null,
    channelId: null, allChannels: false, channelQuery: "",
    threadOpen: new Set(),
    resultMode: "browse", activeFilter: null, timeWindow: null,
    anchorMessageId: null, newerCursor: null,
    includeBots: true, queryOrder: "desc",
    messages: [], cursor: null, total: null, loading: false, loadingMore: false,
    builder: { kind: "group", op: "AND", children: [
      { kind: "condition", field: "content", op: "contains", value: "" },
    ] },
    heat: { bucket: "hour", days: 90, metric: "heat", cell: 10, data: null, loading: false },
    quickSearch: "",
  };
}

function archiveTzOffset() { return -new Date().getTimezoneOffset(); }
function archiveLocalBucket(ts, seconds) {
  const off = archiveTzOffset() * 60;
  return Math.floor((ts + off) / seconds) * seconds - off;
}
function archiveField(id) { return ARCHIVE_FIELDS.find((f) => f.id === id) || ARCHIVE_FIELDS[0]; }
function archiveFmtDate(ts) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleDateString(undefined,
    { year: "numeric", month: "short", day: "numeric" });
}
function archiveFmtDateTime(ts) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString(undefined,
    { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
function archiveFmtTime(ts) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}
function archiveFmtBucket(ts, seconds) {
  return seconds >= 86400 ? archiveFmtDate(ts) : archiveFmtDateTime(ts);
}
function archiveFmtSize(n) {
  if (n == null) return "—";
  n = Number(n);
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}
function archiveAuthorColor(id) {
  let h = 0;
  for (const ch of String(id || "?")) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return `hsl(${h % 360}, 52%, 42%)`;
}
function archiveInitial(name) {
  return String(name || "?").trim().slice(0, 1).toUpperCase() || "?";
}
function archiveEsc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function archiveEscapeRe(s) {
  return String(s).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
function archiveFmtDiscordTimestamp(ts, style) {
  const n = Number(ts);
  if (!Number.isFinite(n)) return String(ts);
  const d = new Date(n * 1000);
  if (style === "t") return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  if (style === "T") return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  if (style === "d") return d.toLocaleDateString();
  if (style === "D") return d.toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" });
  if (style === "R") {
    const diff = Math.round((d.getTime() - Date.now()) / 1000);
    const abs = Math.abs(diff);
    const units = [["year", 31536000], ["month", 2592000], ["day", 86400],
      ["hour", 3600], ["minute", 60], ["second", 1]];
    for (const [unit, sec] of units) {
      if (abs >= sec || unit === "second") {
        return new Intl.RelativeTimeFormat(undefined, { numeric: "auto" })
          .format(Math.round(diff / sec), unit);
      }
    }
  }
  return archiveFmtDateTime(n);
}

function archiveDiscordLinkHtml(url, label) {
  const text = label || url;
  const m = url.match(/^https?:\/\/(?:www\.)?(?:discord\.com|discordapp\.com)\/channels\/(\d+)\/(\d+)(?:\/(\d+))?\/?$/);
  if (m && (!archiveState.guildId || String(m[1]) === String(archiveState.guildId))) {
    const href = m[3]
      ? `#/archive?channel=${encodeURIComponent(m[2])}&around=${encodeURIComponent(m[3])}`
      : `#/archive?channel=${encodeURIComponent(m[2])}`;
    return `<a class="discord-link" href="${href}">${text}</a>`;
  }
  return `<a href="${url}" target="_blank" rel="noopener">${text}</a>`;
}

function archiveFormatText(text, terms, msg) {
  let s = archiveEsc(text || "");
  const safeTerms = (terms || []).filter((t) => String(t).trim()).map((t) => archiveEsc(t));
  for (const term of safeTerms) {
    s = s.replace(new RegExp(archiveEscapeRe(term), "gi"), (m) => `\u0001${m}\u0002`);
  }
  const blocks = [];
  s = s.replace(/```([\s\S]*?)```/g, (_, code) => {
    blocks.push(`<pre class="msg-code">${code.replace(/^\n|\n$/g, "")}</pre>`);
    return `\u0003${blocks.length - 1}\u0004`;
  });
  s = s.replace(/`([^`\n]+)`/g, "<code>$1</code>");
  s = s.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/~~([^~\n]+)~~/g, "<del>$1</del>");
  s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");

  // Stash generated links so the generic URL pass cannot re-link their hrefs.
  const links = [];
  const stashLink = (html) => {
    links.push(html);
    return `\u0005${links.length - 1}\u0006`;
  };
  s = s.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g,
    (_, label, url) => stashLink(archiveDiscordLinkHtml(url, label)));

  // Discord entities. Resolution maps come from the archived raw payload.
  const mentions = new Map((msg && msg.mentions || []).map((u) => [String(u.id), u]));
  s = s.replace(/&lt;@!?(\d+)&gt;/g, (_, id) => {
    const u = mentions.get(String(id));
    const name = (u && (u.display_name || u.name)) || String(id);
    return `<span class="discord-mention" title="user ${id}">@${archiveEsc(name)}</span>`;
  });
  s = s.replace(/&lt;@everyone&gt;/g, '<span class="discord-mention">@everyone</span>');
  s = s.replace(/&lt;@here&gt;/g, '<span class="discord-mention">@here</span>');
  s = s.replace(/&lt;@&amp;(\d+)&gt;/g,
    '<span class="discord-mention discord-role-mention">@role</span>');
  s = s.replace(/&lt;#(\d+)&gt;/g, (_, id) => {
    const ch = (archiveState.channels || []).find((c) => String(c.id) === String(id));
    return `<a class="discord-mention discord-channel-mention" ` +
      `href="#/archive?channel=${encodeURIComponent(id)}">#${archiveEsc(ch && ch.name || id)}</a>`;
  });
  s = s.replace(/&lt;t:(\d+)(?::([tTdDfFR]))?&gt;/g, (_, ts, style) =>
    `<span class="discord-time" title="${archiveEsc(archiveFmtDateTime(Number(ts)))}">` +
    `${archiveEsc(archiveFmtDiscordTimestamp(ts, style || "f"))}</span>`);

  // Angle-bracket links suppress Discord embeds; keeping the link is enough.
  s = s.replace(/&lt;(https?:\/\/[^\s<]+)&gt;/g, "$1");
  s = s.replace(/(https?:\/\/[^\s<]+)/g, (url) => stashLink(archiveDiscordLinkHtml(url)));
  s = s.replace(/\n/g, "<br>");
  s = s.replace(/\u0005(\d+)\u0006/g, (_, i) => links[Number(i)] || "");
  s = s.replace(/\u0001([\s\S]*?)\u0002/g, "<mark>$1</mark>");
  s = s.replace(/\u0003(\d+)\u0004/g, (_, i) => blocks[Number(i)] || "");
  return s;
}
function archiveLinkifyText(text) { return archiveFormatText(text, []); }

function archiveQueryTerms(filter) {
  const out = [];
  if (!filter) return out;
  if (filter.kind === "condition" &&
      ["content", "search_text"].includes(filter.field) &&
      ["contains", "starts_with", "ends_with", "equals"].includes(filter.op) &&
      typeof filter.value === "string") out.push(filter.value);
  for (const c of filter.children || []) out.push(...archiveQueryTerms(c));
  return out;
}

function archiveConditionSqlSummary(node) {
  if (!node) return "";
  if (node.kind === "group" || node.children) {
    const op = node.op || "AND";
    const parts = (node.children || []).map(archiveConditionSqlSummary).filter(Boolean);
    if (!parts.length) return "";
    if (op === "NOT") return `NOT (${parts.join(" AND ")})`;
    if (parts.length === 1) return parts[0];
    return `(${parts.join(` ${op} `)})`;
  }
  const f = archiveField(node.field);
  const op = ARCHIVE_OP_LABEL[node.op] || node.op;
  if (ARCHIVE_NO_VALUE.has(node.op)) return `${f.label} ${op}`;
  let val = node.value;
  if (f.type === "date" && typeof val === "number") val = archiveFmtDateTime(val);
  if (Array.isArray(val)) val = val.map((v) => f.type === "date" && typeof v === "number" ? archiveFmtDateTime(v) : v).join(" – ");
  return `${f.label} ${op} ${val ?? ""}`.trim();
}

function archiveCleanFilter(node) {
  if (!node) return null;
  if (node.kind === "group" || node.children) {
    const children = (node.children || []).map(archiveCleanFilter).filter(Boolean);
    if (!children.length) return null;
    return { kind: "group", op: node.op || "AND", children };
  }
  if (ARCHIVE_NO_VALUE.has(node.op)) return { ...node, value: undefined };
  const spec = archiveField(node.field);
  if (spec.type === "bool") return node;
  if (node.value === undefined || node.value === null ||
      (typeof node.value === "string" && !node.value.trim())) return null;
  if (node.op === "between" && (!Array.isArray(node.value) || node.value.length !== 2 ||
      node.value.some((v) => v === "" || v == null))) return null;
  return node;
}

function archiveUpdateHash() {
  if (!archiveState) return;
  const q = new URLSearchParams();
  if (archiveState.channelId && !archiveState.allChannels) q.set("channel", archiveState.channelId);
  if (archiveState.anchorMessageId) q.set("around", archiveState.anchorMessageId);
  if (archiveState.timeWindow) {
    q.set("after", archiveState.timeWindow.after);
    q.set("before", archiveState.timeWindow.before);
  }
  const target = "#/archive" + (q.toString() ? `?${q.toString()}` : "");
  history.replaceState(null, "", target);
}

async function renderArchive(params) {
  document.body.classList.add("archive-mode");
  onCleanup(() => document.body.classList.remove("archive-mode"));
  const p = params || new URLSearchParams((location.hash.split("?")[1] || ""));
  if (!archiveState) archiveState = archiveNewState();
  if (p.has("channel")) {
    archiveState.channelId = p.get("channel") || null;
    archiveState.allChannels = !archiveState.channelId;
  }
  archiveState.anchorMessageId = p.has("around") ? (p.get("around") || null) : null;
  archiveState.newerCursor = null;
  if (p.has("after") || p.has("before")) {
    archiveState.timeWindow = {
      after: Number(p.get("after") || 0),
      before: Number(p.get("before") || 0),
    };
  } else {
    archiveState.timeWindow = null;
  }

  if (!archiveState.stats) {
    const [stats, guilds, channels, users] = await Promise.all([
      api("GET", "api/archive/stats"),
      api("GET", "api/archive/guilds"),
      api("GET", "api/archive/channels"),
      api("GET", "api/archive/users?limit=200"),
    ]);
    archiveState.stats = stats;
    archiveState.guilds = guilds;
    archiveState.channels = channels;
    archiveState.users = users;
    if (!archiveState.guildId && guilds.length) archiveState.guildId = guilds[0].id;
    if (!archiveState.channelId && !archiveState.allChannels) {
      const scoped = channels.filter((c) => !c.is_thread && c.message_count > 0);
      const general = scoped.find((c) => c.name === "general") ||
        scoped.find((c) => !c.is_thread) || scoped[0];
      if (general) archiveState.channelId = general.id;
    }
  }

  buildArchiveShell();
  archiveUpdateHash();
  refreshChannelList();
  refreshBuilder();
  if (archiveState.anchorMessageId) {
    await Promise.all([loadArchiveContext(archiveState.anchorMessageId), refreshHeatmap()]);
  } else {
    await Promise.all([refreshMessages(true), refreshHeatmap()]);
  }
}

function buildArchiveShell() {
  const stats = archiveState.stats || {};
  const statsCard = h("div", { class: "card archive-stats-card" },
    h("h2", null, "Archive"),
    h("div", { class: "archive-stat-grid" },
      h("div", null, h("b", null, (stats.messages || 0).toLocaleString()), h("span", null, " messages")),
      h("div", null, h("b", null, (stats.channels || 0).toLocaleString()), h("span", null, " channels")),
      h("div", null, h("b", null, (stats.threads || 0).toLocaleString()), h("span", null, " threads")),
      h("div", null, h("b", null, (stats.users || 0).toLocaleString()), h("span", null, " people")),
      h("div", null, h("b", null, (stats.attachments || 0).toLocaleString()), h("span", null, " files")),
    ),
    h("p", { class: "hint" },
      `${archiveFmtDate(stats.oldest_ts)} → ${archiveFmtDate(stats.newest_ts)} · ${archiveFmtSize(stats.blob_bytes)} archived`),
  );

  const channelSearch = h("input", {
    class: "archive-channel-search", placeholder: archiveT("archive_filter_channels", "Filter channels…"),
    value: archiveState.channelQuery,
    oninput: (e) => { archiveState.channelQuery = e.target.value; refreshChannelList(); },
  });
  const channelList = h("div", { class: "archive-channel-list" });
  const guildSelect = h("select", {
    class: "archive-guild-select",
    onchange: (e) => {
      archiveState.guildId = e.target.value || null;
      archiveState.channelId = null;
      archiveState.allChannels = false;
      refreshChannelList(); refreshMessages(true); refreshHeatmap();
    },
  }, archiveState.guilds.map((g) => h("option", { value: g.id, selected: g.id === archiveState.guildId ? "" : null }, g.name || g.id)));

  const channelPane = h("aside", { class: "archive-channel-pane" },
    h("div", { class: "archive-guild" },
      h("div", { class: "archive-guild-name" }, guildSelect),
      h("div", { class: "hint" }, "Browse by channel or thread")),
    channelSearch, channelList,
  );

  const msgHeader = h("div", { class: "archive-msg-head" });
  const msgList = h("div", { class: "archive-msgs" });
  const messagePane = h("section", { class: "archive-message-pane" }, msgHeader, msgList);

  const queryCard = h("div", { class: "card archive-query-card" },
    h("h2", null, archiveT("archive_query", "Query builder")),
    (() => {
      const input = h("input", {
        class: "archive-quick-search", placeholder: archiveT("archive_search_placeholder", "Search messages…"),
        value: archiveState.quickSearch,
        oninput: (e) => { archiveState.quickSearch = e.target.value; },
        onkeydown: (e) => { if (e.key === "Enter") runArchiveQuickSearch(); },
      });
      return h("div", { class: "archive-quick-row" }, input,
        h("button", { onclick: runArchiveQuickSearch }, archiveT("archive_search", "Search")));
    })(),
    h("div", { class: "archive-builder" }),
    h("div", { class: "archive-query-actions" },
      h("label", { class: "archive-check" },
        (() => { const cb = h("input", { type: "checkbox" }); cb.checked = archiveState.includeBots; cb.onchange = () => { archiveState.includeBots = cb.checked; }; return cb; })(),
        " include bots"),
      h("select", {
        onchange: (e) => { archiveState.queryOrder = e.target.value; },
      }, h("option", { value: "desc", selected: archiveState.queryOrder === "desc" ? "" : null }, "newest first"),
         h("option", { value: "asc", selected: archiveState.queryOrder === "asc" ? "" : null }, "oldest first")),
      h("span", { class: "spacer" }),
      h("button", { class: "secondary small", onclick: resetArchiveBuilder }, "Reset"),
      h("button", { onclick: runArchiveQuery }, archiveT("archive_run", "Run query")),
    ),
    h("pre", { class: "archive-query-preview" }),
  );

  const heatCard = h("div", { class: "card archive-heat-card" },
    h("h2", null, archiveT("archive_heatmap", "Activity heatmap")),
    h("p", { class: "hint" }, archiveT("archive_heat_hint", "Click a cell or hot moment to jump to that time.")),
    h("div", { class: "archive-heat-controls" },
      (() => {
        const s = h("select", { onchange: (e) => { archiveState.heat.bucket = e.target.value; refreshHeatmap(); } },
          h("option", { value: "hour", selected: archiveState.heat.bucket === "hour" ? "" : null }, "hourly"),
          h("option", { value: "day", selected: archiveState.heat.bucket === "day" ? "" : null }, "daily"));
        return s;
      })(),
      (() => {
        const s = h("select", { onchange: (e) => { archiveState.heat.days = Number(e.target.value); refreshHeatmap(); } });
        for (const [v, label] of [[7, "last 7 days"], [30, "last 30 days"], [90, "last 90 days"], [365, "last year"], [0, "all time"]]) {
          const o = h("option", { value: v }, label); if (archiveState.heat.days === v) o.selected = true; s.append(o);
        }
        return s;
      })(),
      (() => {
        const s = h("select", { onchange: (e) => { archiveState.heat.metric = e.target.value; refreshHeatmap(); } });
        for (const [v, label] of [["heat", "heat"], ["messages", "messages"], ["authors", "people"]]) {
          const o = h("option", { value: v }, label); if (archiveState.heat.metric === v) o.selected = true; s.append(o);
        }
        return s;
      })(),
      h("span", { class: "spacer" }),
      h("button", { class: "secondary small", title: "smaller cells", onclick: () => { archiveState.heat.cell = Math.max(5, archiveState.heat.cell - 2); refreshHeatmap(); } }, "−"),
      h("button", { class: "secondary small", title: "larger cells", onclick: () => { archiveState.heat.cell = Math.min(20, archiveState.heat.cell + 2); refreshHeatmap(); } }, "+"),
    ),
    h("div", { class: "archive-heat-body" }),
  );

  const queryPane = h("aside", { class: "archive-query-pane" }, statsCard, queryCard, heatCard);
  const shell = h("div", { class: "archive-shell" },
    h("section", { class: "archive-browser" }, channelPane, messagePane),
    queryPane,
    h("div", { class: "archive-mobile-tabs" },
      h("button", { onclick: () => { shell.classList.remove("show-query"); shell.classList.add("show-browser"); } }, "Browser"),
      h("button", { onclick: () => { shell.classList.add("show-query"); shell.classList.remove("show-browser"); } }, "Query & heatmap"),
    ),
  );
  shell.classList.add("show-browser");
  archiveUi = { shell, msgHeader, msgList, channelList, queryCard, heatCard, queryPane };
  $view().replaceChildren(shell);
}

function refreshChannelList() {
  if (!archiveUi.channelList) return;
  const q = archiveState.channelQuery.trim().toLowerCase();
  const channels = archiveState.channels.filter((c) => c.guild_id === archiveState.guildId);
  const visible = (c) => !q || String(c.name || "").toLowerCase().includes(q) || String(c.id).includes(q);
  const cats = channels.filter((c) => c.type === 4).sort((a, b) => (a.position ?? 999) - (b.position ?? 999));
  const catIds = new Set(cats.map((c) => c.id));
  const top = channels.filter((c) => !c.is_thread && c.type !== 4);
  const threads = channels.filter((c) => c.is_thread);
  const byParent = {};
  for (const t of threads) (byParent[t.parent_id] ||= []).push(t);

  function row(c, depth) {
    const active = !archiveState.allChannels && archiveState.channelId === c.id;
    const icon = c.type === 2 ? "🔊" : c.type === 5 ? "📢" : c.type === 15 ? "🗂" : c.is_thread ? "🧵" : "#";
    const hasThreads = (byParent[c.id] || []).length > 0;
    const open = archiveState.threadOpen.has(c.id);
    const r = h("div", {
      class: `archive-channel-row${active ? " active" : ""}`, style: `padding-left:${8 + depth * 14}px`,
      onclick: () => {
        archiveState.channelId = c.id; archiveState.allChannels = false;
        archiveState.resultMode = "browse"; archiveState.activeFilter = null; archiveState.timeWindow = null;
        archiveState.anchorMessageId = null; archiveState.newerCursor = null;
        archiveUpdateHash(); refreshChannelList(); refreshMessages(true); refreshHeatmap();
      },
      title: `${c.name || c.id} · ${c.message_count || 0} messages`,
    },
      h("span", { class: "archive-ch-icon" }, icon),
      h("span", { class: "archive-ch-name" }, c.name || c.id),
      c.message_count ? h("span", { class: "archive-ch-count" }, c.message_count) : null,
      hasThreads ? h("button", {
        class: "archive-thread-toggle", title: "toggle threads",
        onclick: (e) => {
          e.stopPropagation();
          if (open) archiveState.threadOpen.delete(c.id); else archiveState.threadOpen.add(c.id);
          refreshChannelList();
        },
      }, `${open ? "▾" : "▸"}${byParent[c.id].length}`) : null,
    );
    return r;
  }

  const children = [];
  children.push(h("div", {
    class: `archive-channel-row archive-all-channels${archiveState.allChannels ? " active" : ""}`,
    onclick: () => {
      archiveState.channelId = null; archiveState.allChannels = true;
      archiveState.resultMode = "browse"; archiveState.activeFilter = null; archiveState.timeWindow = null;
      archiveState.anchorMessageId = null; archiveState.newerCursor = null;
      archiveUpdateHash(); refreshChannelList(); refreshMessages(true); refreshHeatmap();
    },
  }, h("span", { class: "archive-ch-icon" }, "🌐"), h("span", { class: "archive-ch-name" }, "All channels")));

  const groups = cats.map((cat) => ({ cat, chans: top.filter((c) => c.parent_id === cat.id) }))
    .filter((g) => g.chans.some(visible) || threads.some((t) => visible(t) && g.chans.some((c) => c.id === t.parent_id)));
  const orphan = top.filter((c) => !c.parent_id || !catIds.has(c.parent_id));
  for (const { cat, chans } of groups) {
    children.push(h("div", { class: "archive-channel-cat" }, cat.name || "Channels"));
    for (const c of chans.filter(visible)) {
      children.push(row(c, 0));
      if (archiveState.threadOpen.has(c.id)) {
        for (const t of (byParent[c.id] || []).filter(visible)) children.push(row(t, 1));
      }
    }
  }
  if (orphan.filter(visible).length) {
    children.push(h("div", { class: "archive-channel-cat" }, "Other"));
    for (const c of orphan.filter(visible)) {
      children.push(row(c, 0));
      if (archiveState.threadOpen.has(c.id)) {
        for (const t of (byParent[c.id] || []).filter(visible)) children.push(row(t, 1));
      }
    }
  }
  if (!children.length) children.push(h("p", { class: "hint" }, "No matching channels."));
  archiveUi.channelList.replaceChildren(...children);
}

function refreshMessageHeader() {
  if (!archiveUi.msgHeader) return;
  const st = archiveState;
  const channel = st.channels.find((c) => c.id === st.channelId);
  let title, sub;
  if (st.resultMode === "query") {
    title = "Query results";
    sub = `${st.total == null ? "…" : st.total.toLocaleString()} matching messages`;
  } else if (st.allChannels || !channel) {
    title = "All channels";
    sub = `${st.total == null ? "…" : st.total.toLocaleString()} messages`;
  } else {
    title = `${channel.is_thread ? "🧵" : "#"} ${channel.name || channel.id}`;
    sub = `${st.total == null ? "…" : st.total.toLocaleString()} messages archived`;
  }
  const chips = [];
  if (st.resultMode === "query") {
    const summary = archiveConditionSqlSummary(st.activeFilter);
    if (summary) chips.push(h("span", { class: "archive-chip", title: summary }, summary));
  }
  if (st.timeWindow) {
    chips.push(h("span", { class: "archive-chip archive-chip-time" },
      `${archiveFmtDateTime(st.timeWindow.after)} → ${archiveFmtDateTime(st.timeWindow.before)} `,
      h("button", { class: "archive-chip-x", title: "clear time filter", onclick: () => {
        st.timeWindow = null; st.anchorMessageId = null; st.newerCursor = null;
        archiveUpdateHash(); refreshMessages(true);
      } }, "×")));
  }
  const headNodes = [
    h("div", { class: "archive-msg-head-main" },
      h("div", null, h("h2", null, title), h("div", { class: "hint" }, sub)),
      h("div", { class: "archive-msg-tools" },
        h("button", { class: "secondary small", onclick: () => {
          if (st.anchorMessageId) loadArchiveContext(st.anchorMessageId);
          else refreshMessages(true);
        } }, "↻ refresh"),
        st.resultMode === "query" || st.timeWindow || st.anchorMessageId
          ? h("button", { class: "secondary small", onclick: () => {
              st.resultMode = "browse"; st.activeFilter = null; st.timeWindow = null;
              st.anchorMessageId = null; st.newerCursor = null;
              archiveUpdateHash(); refreshMessages(true);
            } }, "latest")
          : null,
      ),
    ),
  ];
  if (chips.length) headNodes.push(h("div", { class: "archive-chips" }, chips));
  archiveUi.msgHeader.replaceChildren(...headNodes);
}

async function refreshMessages(reset) {
  if (!archiveUi.msgList) return;
  const st = archiveState;
  if (reset) {
    st.messages = []; st.cursor = null; st.newerCursor = null;
    st.total = null; st.loading = true;
    archiveUi.msgList.replaceChildren(h("div", { class: "archive-loading" }, "loading messages…"));
    refreshMessageHeader();
  } else if (st.loadingMore) return;
  else { st.loadingMore = true; }
  try {
    let res;
    if (st.resultMode === "query") {
      res = await api("POST", "api/archive/query", {
        filter: st.activeFilter, guild_id: st.guildId,
        channel_id: st.allChannels ? null : st.channelId,
        before: st.timeWindow ? st.timeWindow.before : null,
        after: st.timeWindow ? st.timeWindow.after : null,
        limit: 50, cursor: reset ? null : st.cursor,
        order: st.queryOrder, include_bots: st.includeBots,
      });
    } else {
      const q = new URLSearchParams({ limit: "50", order: "desc", include_bots: String(st.includeBots) });
      if (st.channelId && !st.allChannels) q.set("channel_id", st.channelId);
      if (st.guildId) q.set("guild_id", st.guildId);
      if (st.timeWindow) { q.set("after", st.timeWindow.after); q.set("before", st.timeWindow.before); }
      if (!reset && st.cursor) q.set("cursor", st.cursor);
      res = await api("GET", `api/archive/messages?${q.toString()}`);
    }
    st.messages = reset ? res.items : st.messages.concat(res.items);
    st.cursor = res.next_cursor;
    st.total = res.total;
  } catch (e) {
    st.loading = false; st.loadingMore = false;
    archiveUi.msgList.replaceChildren(h("div", { class: "card result-err" }, e.message));
    return;
  }
  st.loading = false; st.loadingMore = false;
  renderMessageList(reset);
  refreshMessageHeader();
}

async function jumpToOriginalMessage(m) {
  const st = archiveState;
  st.guildId = m.guild_id || st.guildId;
  st.channelId = m.channel_id;
  st.allChannels = false;
  st.resultMode = "browse";
  st.activeFilter = null;
  st.timeWindow = null;
  st.anchorMessageId = String(m.id);
  st.newerCursor = null;
  resetArchiveBuilder();
  archiveUpdateHash();
  refreshChannelList();
  await loadArchiveContext(m.id);
}

async function loadArchiveContext(messageId) {
  if (!archiveUi.msgList) return;
  const st = archiveState;
  st.loading = true;
  st.messages = [];
  st.cursor = null;
  st.newerCursor = null;
  st.anchorMessageId = String(messageId);
  archiveUi.msgList.replaceChildren(h("div", { class: "archive-loading" }, "loading message context…"));
  refreshMessageHeader();
  try {
    const q = new URLSearchParams({ message_id: messageId, before: "50", after: "50" });
    if (st.channelId) q.set("channel_id", st.channelId);
    const res = await api("GET", `api/archive/context?${q.toString()}`);
    const asc = res.items || [];
    st.messages = asc.slice().reverse();
    const oldest = asc[0];
    const newest = asc[asc.length - 1];
    st.cursor = res.has_older && oldest ? `${oldest.created_ts}:${oldest.id}` : null;
    st.newerCursor = res.has_newer && newest ? `${newest.created_ts}:${newest.id}` : null;
    const channel = st.channels.find((c) => c.id === st.channelId);
    st.total = channel ? channel.message_count : null;
  } catch (e) {
    st.loading = false;
    archiveUi.msgList.replaceChildren(h("div", { class: "card result-err" }, e.message));
    return;
  }
  st.loading = false;
  renderMessageList(true);
  refreshMessageHeader();
}

async function loadOlderMessages() {
  if (!archiveUi.msgList || archiveState.loadingMore) return;
  const before = Array.from(archiveUi.msgList.querySelectorAll(".msg-row[data-mid]"))
    .find((el) => el.offsetTop + el.offsetHeight > archiveUi.msgList.scrollTop);
  const offset = before ? before.offsetTop - archiveUi.msgList.scrollTop : 0;
  const mid = before ? before.dataset.mid : null;
  await refreshMessages(false);
  if (!mid) return;
  const after = Array.from(archiveUi.msgList.querySelectorAll(".msg-row[data-mid]"))
    .find((el) => el.dataset.mid === mid);
  if (after) archiveUi.msgList.scrollTop = after.offsetTop - offset;
}

async function loadNewerMessages() {
  if (!archiveUi.msgList || archiveState.loadingMore || !archiveState.newerCursor) return;
  const st = archiveState;
  st.loadingMore = true;
  try {
    const q = new URLSearchParams({
      limit: "50", order: "asc", include_bots: String(st.includeBots),
      cursor: st.newerCursor,
    });
    if (st.channelId && !st.allChannels) q.set("channel_id", st.channelId);
    if (st.guildId) q.set("guild_id", st.guildId);
    const res = await api("GET", `api/archive/messages?${q.toString()}`);
    st.messages = (res.items || []).slice().reverse().concat(st.messages);
    st.newerCursor = res.next_cursor;
  } catch (e) {
    st.loadingMore = false;
    toast(e.message, "error");
    return;
  }
  st.loadingMore = false;
  const top = archiveUi.msgList.scrollTop;
  renderMessageList(false);
  archiveUi.msgList.scrollTop = top;
  refreshMessageHeader();
}

function archiveThreadTimelineEvents(items) {
  const st = archiveState;
  if (st.resultMode === "query" || st.allChannels || !st.channelId) return [];
  const represented = new Set();
  for (const m of items) {
    if (m.thread && m.thread.id) represented.add(String(m.thread.id));
  }
  const events = [];
  for (const c of st.channels || []) {
    if (!c.is_thread || String(c.parent_id) !== String(st.channelId)) continue;
    if (!c.created_ts || represented.has(String(c.id))) continue;
    if (st.timeWindow &&
        (c.created_ts < st.timeWindow.after || c.created_ts > st.timeWindow.before)) continue;
    events.push({ ts: c.created_ts, kind: "thread", thread: c });
  }
  return events;
}

function archiveThreadHint(t, cls) {
  const owner = (archiveState.users || []).find((u) => String(u.id) === String(t.owner_id));
  const name = owner ? (owner.display_name || owner.name) : "Someone";
  return h("div", { class: cls || "system-message" },
    h("span", { class: "system-icon" }, "🧵"),
    h("span", null, `${name} started a thread `,
      h("a", {
        class: "system-link",
        href: `#/archive?channel=${encodeURIComponent(t.id)}`,
      }, `#${t.name || t.id}`)),
    h("span", { class: "dim" }, ` · ${t.message_count || 0} messages`));
}

function archiveThreadEvent(t) {
  return archiveThreadHint(t, "archive-thread-event");
}

function renderMessageList(reset) {
  if (!archiveUi.msgList) return;
  const st = archiveState;
  const terms = st.resultMode === "query" ? archiveQueryTerms(st.activeFilter) : [];
  const chronological = st.resultMode !== "query";
  const items = chronological ? st.messages.slice().reverse() : st.messages.slice();
  const nodes = [];
  if (st.cursor) {
    nodes.push(h("div", { class: "archive-load-more" },
      h("button", { class: "secondary small", onclick: async () => {
        if (chronological) await loadOlderMessages();
        else await refreshMessages(false);
      } }, chronological ? "↑ load older messages" : "↓ load more results")));
  }
  const timeline = chronological
    ? items.map((m) => ({ ts: m.created_ts || 0, kind: "message", data: m }))
        .concat(archiveThreadTimelineEvents(items))
        .sort((a, b) => (a.ts - b.ts) ||
          String(a.kind === "thread" ? a.thread.id : a.data.id)
            .localeCompare(String(b.kind === "thread" ? b.thread.id : b.data.id)))
    : items.map((m) => ({ ts: m.created_ts || 0, kind: "message", data: m }));
  let prev = null;
  let prevDate = null;
  for (const event of timeline) {
    const ts = event.kind === "thread" ? event.thread.created_ts : event.data.created_ts;
    const day = new Date((ts || 0) * 1000).toDateString();
    if (day !== prevDate) {
      nodes.push(h("div", { class: "archive-date-sep" }, archiveFmtDate(ts)));
      prevDate = day; prev = null;
    }
    if (event.kind === "thread") {
      nodes.push(archiveThreadEvent(event.thread));
      prev = null;
      continue;
    }
    const m = event.data;
    const same = prev && prev.author && m.author && prev.author.id === m.author.id &&
      Math.abs((m.created_ts || 0) - (prev.created_ts || 0)) < 7 * 60 &&
      !m.reply_to && !(m.attachments || []).length;
    nodes.push(archiveMessageRow(m, same, terms));
    prev = m;
  }
  if (!items.length) nodes.push(h("div", { class: "archive-empty" }, "No messages found."));
  if (chronological && st.newerCursor) {
    nodes.push(h("div", { class: "archive-load-more" },
      h("button", { class: "secondary small", onclick: loadNewerMessages },
        "↓ load newer messages")));
  }
  archiveUi.msgList.replaceChildren(...nodes);
  if (reset) {
    requestAnimationFrame(() => {
      if (st.anchorMessageId) {
        const anchor = Array.from(archiveUi.msgList.querySelectorAll(".msg-row[data-mid]"))
          .find((el) => el.dataset.mid === String(st.anchorMessageId));
        if (anchor) {
          archiveUi.msgList.scrollTop = Math.max(0, anchor.offsetTop -
            archiveUi.msgList.clientHeight / 2 + anchor.offsetHeight / 2);
          return;
        }
      }
      if (chronological) archiveUi.msgList.scrollTop = archiveUi.msgList.scrollHeight;
      else archiveUi.msgList.scrollTop = 0;
    });
  }
}

function archiveMessageRow(m, grouped, terms) {
  const author = m.author || {};
  const name = author.display_name || author.name || "unknown";
  const avatar = h("div", { class: "msg-avatar", style: `background:${archiveAuthorColor(author.id)}` }, archiveInitial(name));
  const header = grouped ? null : h("div", { class: "msg-headline" },
    h("span", { class: "msg-author", title: `user id ${author.id || "?"}` }, name),
    author.is_bot ? badge("bot", "muted") : null,
    h("span", { class: "msg-time", title: archiveFmtDateTime(m.created_ts) }, archiveFmtTime(m.created_ts)),
    m.edited_ts ? h("span", { class: "msg-edited" }, "(edited)") : null,
    m.pinned ? h("span", { class: "msg-pin", title: "pinned" }, "📌") : null,
    h("button", {
      class: "msg-jump msg-jump-original", title: "jump to original message",
      onclick: (e) => { e.stopPropagation(); jumpToOriginalMessage(m); },
    }, "↩"),
    m.guild_id && m.channel_id
      ? h("a", { class: "msg-jump", href: `https://discord.com/channels/${m.guild_id}/${m.channel_id}/${m.id}`, target: "_blank", rel: "noopener", title: "open in Discord" }, "↗")
      : null,
  );
  const body = h("div", { class: "msg-body" });
  if (header) body.append(header);
  if (archiveState.allChannels || archiveState.resultMode === "query") {
    body.append(h("div", { class: "msg-channel" }, `# ${m.channel_name || m.channel_id}`));
  }
  if (m.reply_to && m.reference_type !== 1) {
    const target = {
      id: m.reference_message_id || m.reply_to.id,
      channel_id: m.reference_channel_id || m.channel_id,
      guild_id: m.reference_guild_id || m.guild_id,
    };
    body.append(h("div", {
      class: "reply-preview clickable",
      role: "button", tabindex: "0",
      title: "jump to original message",
      onclick: () => jumpToOriginalMessage(target),
      onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") jumpToOriginalMessage(target); },
    },
      h("span", { class: "dim" }, `↪ ${m.reply_to.author_name || "unknown"}: `),
      h("span", { html: archiveFormatText(m.reply_to.content || "(no text)", terms, m) })));
  }
  const system = archiveSystemMessage(m, terms);
  if (system) body.append(system);
  const hideSystemContent = new Set([4, 6, 7, 18, 21, 46]);
  if (m.content && (!system || !hideSystemContent.has(m.message_type))) {
    body.append(h("div", { class: "msg-content", html: archiveFormatText(m.content, terms, m) }));
  }
  if (m.forward) {
    const card = archiveForwardCard(m, terms);
    if (card) body.append(card);
  }
  const atts = m.attachments || [];
  if (atts.length) {
    const grid = h("div", { class: "msg-attach-grid" });
    for (const a of atts) {
      const href = `api/archive/attachments/${encodeURIComponent(a.id)}`;
      const isImg = a.available && String(a.content_type || "").startsWith("image/");
      if (isImg) {
        grid.append(h("a", { class: "msg-image-link", href: `${href}?download=1`, target: "_blank", rel: "noopener", title: a.filename || "" },
          h("img", { class: "msg-image", src: href, loading: "lazy", alt: a.filename || "attachment" })));
      } else {
        grid.append(a.available
          ? h("a", { class: "file-chip", href: `${href}?download=1`, target: "_blank", rel: "noopener", title: a.filename || "" },
              "📎 ", h("span", null, a.filename || "file"), h("span", { class: "dim" }, ` ${archiveFmtSize(a.size_bytes)}`))
          : h("span", { class: "file-chip", title: "bytes are not available in the archive" },
              "📎 ", h("span", null, a.filename || "file"), badge("not archived", "yellow")));
      }
    }
    body.append(grid);
  }
  for (const e of m.embeds || []) {
    const card = archiveEmbedCard(e);
    if (card) body.append(card);
  }
  if (m.thread && m.message_type !== 18 &&
      String(m.channel_id) === String(m.thread.parent_id)) {
    body.append(archiveThreadHint({
      ...m.thread,
      owner_id: m.thread.owner_id || (m.author && m.author.id),
    }, "system-message thread-attached"));
  }
  const isAnchor = String(m.id) === String(archiveState.anchorMessageId);
  return h("div", { class: `msg-row${grouped ? " grouped" : ""}${m.pinned ? " pinned" : ""}${isAnchor ? " anchor" : ""}`,
    "data-mid": m.id }, avatar, body);
}

function archiveSystemMessage(m, terms) {
  const type = m.message_type;
  const name = (m.author && (m.author.display_name || m.author.name)) || "someone";
  const parent = m.thread && archiveState.channels.find((c) => String(c.id) === String(m.thread.parent_id));
  const channelName = parent ? `#${parent.name}` : "this channel";
  if (type === 18 && m.thread) {
    const threadName = m.thread.name || m.content || "thread";
    return h("div", { class: "system-message" },
      h("span", { class: "system-icon" }, "🧵"),
      h("span", null, `${name} started a thread `,
        h("a", {
          class: "system-link", href: `#/archive?channel=${encodeURIComponent(m.thread.id)}`,
        }, `#${threadName}`),
        ` in ${channelName}`),
      m.thread.message_count != null ? h("span", { class: "dim" }, ` · ${m.thread.message_count} messages`) : null);
  }
  if (type === 21) {
    const target = m.reply_to ? {
      id: m.reference_message_id || m.reply_to.id,
      channel_id: m.reference_channel_id || m.channel_id,
      guild_id: m.reference_guild_id || m.guild_id,
    } : null;
    return h("div", { class: "system-message" },
      h("span", { class: "system-icon" }, "🧵"),
      h("span", null, "Thread started from this message"),
      target ? h("button", {
        class: "system-link", onclick: () => jumpToOriginalMessage(target),
      }, "jump to original") : null);
  }
  if (type === 7) {
    return h("div", { class: "system-message" },
      h("span", { class: "system-icon" }, "👋"),
      h("span", null, `${name} joined the server.`));
  }
  if (type === 6) {
    return h("div", { class: "system-message" },
      h("span", { class: "system-icon" }, "📌"),
      h("span", null, `${name} pinned a message in ${channelName}.`));
  }
  if (type === 4) {
    return h("div", { class: "system-message" },
      h("span", { class: "system-icon" }, "✏️"),
      h("span", null, `${name} changed the channel name to `,
        h("strong", null, m.content || "(empty)")));
  }
  if (type === 20) {
    return h("div", { class: "system-message" },
      h("span", { class: "system-icon" }, "⚡"),
      h("span", null, `${name} used `,
        h("span", { class: "mono" }, m.interaction_name ? `/${m.interaction_name}` : "a command")));
  }
  if (type === 46) {
    return h("div", { class: "system-message" },
      h("span", { class: "system-icon" }, "📊"),
      h("span", null, "Poll ended."));
  }
  return null;
}

function archiveForwardCard(m, terms) {
  const f = m.forward;
  if (!f) return null;
  const card = h("div", { class: "forward-card" },
    h("div", { class: "forward-label" }, "↪ Forwarded message"));
  if (f.author && (f.author.display_name || f.author.name)) {
    card.append(h("div", { class: "forward-author" }, f.author.display_name || f.author.name));
  }
  if (f.content) {
    card.append(h("div", { class: "forward-content", html: archiveFormatText(f.content, terms, f) }));
  }
  if ((f.attachments || []).length) {
    card.append(h("div", { class: "msg-attach-grid" },
      f.attachments.map((a) => h("span", { class: "file-chip dim", title: "forwarded attachment metadata" },
        "📎 ", a.filename || "file", a.size_bytes ? ` · ${archiveFmtSize(a.size_bytes)}` : ""))));
  }
  const target = { id: f.message_id, channel_id: f.channel_id, guild_id: f.guild_id };
  const sameGuild = !f.guild_id || !archiveState.guildId ||
    String(f.guild_id) === String(archiveState.guildId);
  if (target.id && sameGuild && (m.reply_to || f.content)) {
    card.append(h("button", {
      class: "forward-jump", onclick: () => jumpToOriginalMessage(target),
    }, "jump to original message"));
  } else if (f.guild_id && f.channel_id && f.message_id) {
    card.append(h("a", {
      class: "forward-jump", target: "_blank", rel: "noopener",
      href: `https://discord.com/channels/${f.guild_id}/${f.channel_id}/${f.message_id}`,
    }, "open original in Discord"));
  }
  return card;
}

function archiveEmbedCard(e) {
  if (!e || (!e.title && !e.description && !e.image && !e.thumbnail &&
      !(e.fields || []).length)) return null;
  const color = typeof e.color === "number" ? `#${e.color.toString(16).padStart(6, "0")}` : null;
  const card = h("div", { class: "embed-card", style: color ? `border-left-color:${color}` : null });
  if (e.author && e.author.name) card.append(h("div", { class: "embed-author" },
    e.author.icon_url ? h("img", { src: e.author.icon_url, alt: "" }) : null,
    e.author.url ? h("a", { href: e.author.url, target: "_blank", rel: "noopener" }, e.author.name) : e.author.name));
  if (e.title) card.append(h("div", { class: "embed-title" },
    e.url ? h("a", { href: e.url, target: "_blank", rel: "noopener" }, e.title) : e.title));
  if (e.description) card.append(h("div", { class: "embed-desc", html: archiveLinkifyText(e.description) }));
  if ((e.fields || []).length) card.append(h("div", { class: "embed-fields" },
    e.fields.map((f) => h("div", { class: "embed-field" },
      h("div", { class: "fname" }, f.name || ""),
      h("div", { class: "fvalue", html: archiveLinkifyText(f.value || "") })))));
  if (e.image && e.image.url) {
    card.append(h("img", { class: "embed-image", src: e.image.url, loading: "lazy", alt: "" }));
  } else if (e.thumbnail && e.thumbnail.url) {
    const imageOnly = !e.title && !e.description && !(e.fields || []).length;
    card.append(h("img", {
      class: imageOnly || e.type === "image" ? "embed-image" : "embed-thumb",
      src: e.thumbnail.url, loading: "lazy", alt: "",
    }));
  }
  if (e.footer && e.footer.text) card.append(h("div", { class: "embed-footer" },
    e.footer.icon_url ? h("img", { src: e.footer.icon_url, alt: "" }) : null, e.footer.text));
  return card;
}

function refreshBuilder() {
  if (!archiveUi.queryCard) return;
  const wrap = archiveUi.queryCard.querySelector(".archive-builder");
  const preview = archiveUi.queryCard.querySelector(".archive-query-preview");
  if (!wrap) return;
  wrap.replaceChildren(archiveBuildGroup(archiveState.builder, null));
  if (preview) {
    const clean = archiveCleanFilter(archiveState.builder);
    preview.textContent = clean ? archiveConditionSqlSummary(clean) : "(no conditions — browse mode)";
  }
}

function archiveNewCondition() { return { kind: "condition", field: "content", op: "contains", value: "" }; }

function archiveBuildGroup(node, parent) {
  const group = h("div", { class: `qb-group${parent ? " qb-nested" : ""}` });
  const opSel = h("select", {
    class: "qb-group-op",
    onchange: (e) => { node.op = e.target.value; refreshBuilder(); },
  }, h("option", { value: "AND", selected: node.op === "AND" ? "" : null }, "ALL (AND)"),
     h("option", { value: "OR", selected: node.op === "OR" ? "" : null }, "ANY (OR)"),
     h("option", { value: "NOT", selected: node.op === "NOT" ? "" : null }, "NOT"));
  const head = h("div", { class: "qb-group-head" }, opSel,
    h("button", { class: "secondary small", onclick: () => { node.children.push(archiveNewCondition()); refreshBuilder(); } }, "+ condition"),
    h("button", { class: "secondary small", onclick: () => { node.children.push({ kind: "group", op: "AND", children: [archiveNewCondition()] }); refreshBuilder(); } }, "+ group"),
    parent ? h("button", { class: "secondary small danger-text", title: "remove group", onclick: () => {
      const i = parent.children.indexOf(node); if (i >= 0) parent.children.splice(i, 1); refreshBuilder();
    } }, "×") : null);
  group.append(head);
  for (const child of node.children) {
    group.append(child.kind === "group" || child.children
      ? archiveBuildGroup(child, node)
      : archiveBuildCondition(child, node));
  }
  return group;
}

function archiveBuildCondition(node, parent) {
  const spec = archiveField(node.field);
  const row = h("div", { class: "qb-row" });
  const fieldSel = h("select", { class: "qb-field", onchange: (e) => {
    node.field = e.target.value;
    const ns = archiveField(node.field);
    node.op = ARCHIVE_OPS[ns.type][0];
    node.value = ns.type === "bool" ? undefined : "";
    refreshBuilder();
  } }, ARCHIVE_FIELDS.map((f) => h("option", { value: f.id, selected: f.id === node.field ? "" : null }, f.label)));
  const ops = ARCHIVE_OPS[spec.type] || ARCHIVE_OPS.text;
  if (!ops.includes(node.op)) node.op = ops[0];
  const opSel = h("select", { class: "qb-op", onchange: (e) => {
    node.op = e.target.value;
    if (ARCHIVE_NO_VALUE.has(node.op)) {
      node.value = undefined;
    } else if (node.op === "between") {
      if (!Array.isArray(node.value)) node.value = [node.value ?? "", ""];
    } else if (Array.isArray(node.value)) {
      node.value = node.value[0] ?? "";
    } else if (node.value === undefined) {
      node.value = "";
    }
    refreshBuilder();
  } }, ops.map((op) => h("option", { value: op, selected: op === node.op ? "" : null }, ARCHIVE_OP_LABEL[op] || op)));
  const valueWrap = h("span", { class: "qb-value" }, archiveValueControl(node, spec));
  row.append(fieldSel, opSel, valueWrap,
    h("button", { class: "secondary small danger-text", title: "remove condition", onclick: () => {
      const i = parent.children.indexOf(node); if (i >= 0) parent.children.splice(i, 1);
      if (!parent.children.length) parent.children.push(archiveNewCondition());
      refreshBuilder();
    } }, "×"));
  return row;
}

function archiveValueControl(node, spec) {
  if (ARCHIVE_NO_VALUE.has(node.op)) return h("span", { class: "hint" }, "—");
  if (spec.type === "date") {
    const fromTs = (v) => {
      if (v == null || v === "") return "";
      const d = new Date(Number(v) * 1000 - new Date(Number(v) * 1000).getTimezoneOffset() * 60000);
      return d.toISOString().slice(0, 16);
    };
    const toTs = (v) => v ? new Date(v).getTime() / 1000 : "";
    if (node.op === "between") {
      const vals = Array.isArray(node.value) ? node.value : ["", ""];
      const a = h("input", { type: "datetime-local", value: fromTs(vals[0]), onchange: (e) => {
        node.value = [toTs(e.target.value), node.value && node.value[1] || ""];
      } });
      const b = h("input", { type: "datetime-local", value: fromTs(vals[1]), onchange: (e) => {
        node.value = [node.value && node.value[0] || "", toTs(e.target.value)];
      } });
      return h("span", { class: "qb-between" }, a, h("span", { class: "dim" }, "→"), b);
    }
    return h("input", { type: "datetime-local", value: fromTs(node.value), onchange: (e) => { node.value = toTs(e.target.value); } });
  }
  if (spec.type === "bool") return h("span", { class: "hint" }, "—");
  if (node.op === "between") {
    const vals = Array.isArray(node.value) ? node.value : ["", ""];
    const a = h("input", { type: spec.type === "number" ? "number" : "text", value: vals[0] ?? "", placeholder: "from", onchange: (e) => {
      node.value = [e.target.value, node.value && node.value[1] || ""];
    } });
    const b = h("input", { type: spec.type === "number" ? "number" : "text", value: vals[1] ?? "", placeholder: "to", onchange: (e) => {
      node.value = [node.value && node.value[0] || "", e.target.value];
    } });
    return h("span", { class: "qb-between" }, a, h("span", { class: "dim" }, "→"), b);
  }
  if (node.field === "author_id") {
    const sel = h("select", { onchange: (e) => { node.value = e.target.value; } },
      h("option", { value: "" }, "any author"),
      archiveState.users.map((u) => h("option", { value: u.id, selected: String(u.id) === String(node.value) ? "" : null },
        `${u.display_name || u.name || u.id} (${u.message_count})`)));
    return sel;
  }
  if (node.field === "channel_id") {
    return h("select", { onchange: (e) => { node.value = e.target.value; } },
      h("option", { value: "" }, "any channel"),
      archiveState.channels.map((c) => h("option", { value: c.id, selected: String(c.id) === String(node.value) ? "" : null },
        `${c.is_thread ? "🧵 " : "# "}${c.name || c.id}`)));
  }
  if (node.field === "guild_id") {
    return h("select", { onchange: (e) => { node.value = e.target.value; } },
      h("option", { value: "" }, "any server"),
      archiveState.guilds.map((g) => h("option", { value: g.id, selected: String(g.id) === String(node.value) ? "" : null }, g.name || g.id)));
  }
  return h("input", {
    type: spec.type === "number" ? "number" : "text",
    value: node.value ?? "",
    placeholder: spec.type === "number" ? "number" : spec.type === "id" ? "ID" : "value",
    oninput: (e) => { node.value = e.target.value; },
  });
}

function resetArchiveBuilder() {
  archiveState.builder = { kind: "group", op: "AND", children: [archiveNewCondition()] };
  archiveState.quickSearch = "";
  const input = archiveUi.queryCard && archiveUi.queryCard.querySelector(".archive-quick-search");
  if (input) input.value = "";
  refreshBuilder();
}

function runArchiveQuickSearch() {
  const q = (archiveState.quickSearch || "").trim();
  if (!q) { resetArchiveBuilder(); return; }
  const terms = q.split(/\s+/).filter(Boolean);
  archiveState.builder = { kind: "group", op: "AND", children: terms.map((term) => ({
    kind: "condition", field: "search_text", op: "contains", value: term,
  })) };
  refreshBuilder();
  runArchiveQuery();
}

async function runArchiveQuery() {
  const clean = archiveCleanFilter(archiveState.builder);
  archiveState.activeFilter = clean;
  archiveState.resultMode = "query";
  archiveState.timeWindow = null;
  archiveState.anchorMessageId = null;
  archiveState.newerCursor = null;
  archiveUpdateHash();
  await refreshMessages(true);
}

async function refreshHeatmap() {
  if (!archiveUi.heatCard) return;
  const body = archiveUi.heatCard.querySelector(".archive-heat-body");
  if (!body) return;
  const st = archiveState;
  st.heat.loading = true;
  body.replaceChildren(h("div", { class: "archive-loading" }, "loading heatmap…"));
  const stats = st.stats || {};
  const now = Date.now() / 1000;
  const before = Math.max(now, stats.newest_ts || 0);
  const after = st.heat.days > 0 ? before - st.heat.days * 86400 : (stats.oldest_ts || 0);
  const q = new URLSearchParams({
    after: String(after), before: String(before),
    bucket: st.heat.bucket, tz_offset: String(archiveTzOffset()),
    include_bots: String(st.includeBots),
  });
  if (st.guildId) q.set("guild_id", st.guildId);
  if (st.channelId && !st.allChannels) q.set("channel_id", st.channelId);
  const req = Symbol();
  st.heat.req = req;
  try {
    const data = await api("GET", `api/archive/heatmap?${q.toString()}`);
    if (st.heat.req !== req) return;
    st.heat.data = data;
    st.heat.loading = false;
    renderHeatmapBody(body, data, after, before);
  } catch (e) {
    st.heat.loading = false;
    body.replaceChildren(h("div", { class: "result-err" }, e.message));
  }
}

function archiveHeatValue(item, metric) {
  if (!item) return 0;
  if (metric === "messages") return item.messages;
  if (metric === "authors") return item.authors;
  return item.heat;
}

function archiveHeatColor(v, scale) {
  if (!v) return "#25262b";
  const t = Math.max(0, Math.min(1, scale <= 0 ? 0 : Math.log1p(v) / Math.log1p(scale)));
  return `hsl(${Math.round(220 - 220 * t)}, ${Math.round(45 + 30 * t)}%, ${Math.round(18 + 28 * t)}%)`;
}

function archiveHeatScale(items, metric) {
  const values = items.map((x) => archiveHeatValue(x, metric)).filter((v) => v > 0).sort((a, b) => a - b);
  if (!values.length) return 1;
  const idx = Math.min(values.length - 1, Math.floor(values.length * 0.94));
  return Math.max(1, values[idx]);
}

function renderHeatmapBody(body, data, after, before) {
  const st = archiveState;
  const items = data.items || [];
  const metric = st.heat.metric;
  const scale = archiveHeatScale(items, metric);
  const byBucket = new Map(items.map((x) => [x.bucket_start, x]));
  const cell = st.heat.cell;
  const seconds = data.bucket_seconds;
  const nodes = [];
  if (seconds >= 86400) {
    const start = archiveLocalBucket(after, 86400);
    const end = archiveLocalBucket(before, 86400);
    const days = [];
    for (let ts = start; ts <= end; ts += 86400) days.push(ts);
    const grid = h("div", { class: "archive-heat-daygrid", style: `grid-template-columns:repeat(${days.length},${Math.max(7, cell)}px)` });
    for (const ts of days) {
      const item = byBucket.get(ts);
      const v = archiveHeatValue(item, metric);
      grid.append(h("div", {
        class: "archive-heat-cell", style: `background:${archiveHeatColor(v, scale)}`,
        title: `${archiveFmtDate(ts)}\n${item ? `${item.messages} messages · ${item.authors} people · heat ${item.heat}` : "no messages"}`,
        onclick: () => applyHeatWindow(ts, seconds, item),
      }));
    }
    nodes.push(h("div", { class: "archive-heat-scroll" }, grid));
  } else {
    const dayStart = archiveLocalBucket(after, 86400);
    const dayEnd = archiveLocalBucket(before, 86400);
    const days = [];
    for (let ts = dayStart; ts <= dayEnd; ts += 86400) days.push(ts);
    const grid = h("div", {
      class: "archive-heat-grid",
      style: `grid-auto-flow:column;grid-template-rows:repeat(24,${cell}px);grid-template-columns:repeat(${days.length},${cell}px)`,
    });
    for (const day of days) {
      for (let hh = 0; hh < 24; hh++) {
        const ts = day + hh * 3600;
        const item = byBucket.get(ts);
        const v = archiveHeatValue(item, metric);
        grid.append(h("div", {
          class: "archive-heat-cell", style: `background:${archiveHeatColor(v, scale)}`,
          title: `${archiveFmtDateTime(ts)}\n${item ? `${item.messages} messages · ${item.authors} people · heat ${item.heat}` : "no messages"}`,
          onclick: () => applyHeatWindow(ts, seconds, item),
        }));
      }
    }
    const hours = h("div", { class: "archive-heat-hours", style: `grid-template-rows:repeat(24,${cell}px)` },
      Array.from({ length: 24 }, (_, i) => h("span", { style: `height:${cell}px` }, String(i).padStart(2, "0"))));
    nodes.push(h("div", { class: "archive-heat-wrap" }, hours,
      h("div", { class: "archive-heat-scroll" }, grid)));
  }
  const legend = h("div", { class: "archive-heat-legend" },
    h("span", null, "low"), ...[0.15, 0.35, 0.55, 0.75, 1].map((t) =>
      h("span", { class: "archive-heat-swatch", style: `background:${archiveHeatColor(1 + t * scale, scale)}` })),
    h("span", null, "high"));
  const top = items.filter((x) => archiveHeatValue(x, metric) > 0)
    .sort((a, b) => archiveHeatValue(b, metric) - archiveHeatValue(a, metric)).slice(0, 8);
  const hot = h("div", { class: "archive-hot-list" },
    h("div", { class: "archive-hot-title" }, "Hot moments"),
    top.length ? top.map((item) => h("button", {
      class: "archive-hot-item", onclick: () => applyHeatWindow(item.bucket_start, seconds, item),
    },
      h("span", null, archiveFmtBucket(item.bucket_start, seconds)),
      h("span", { class: "dim" }, `${item.messages} msg · ${item.authors} people`),
      h("span", { class: "archive-hot-score" }, metric === "heat" ? `heat ${item.heat}` : String(archiveHeatValue(item, metric))),
    )) : h("div", { class: "hint" }, "No activity in this range."));
  body.replaceChildren(
    h("div", { class: "hint", style: "margin-bottom:6px" },
      `${archiveFmtDate(after)} → ${archiveFmtDate(before)} · ${st.channelId && !st.allChannels ? (st.channels.find((c) => c.id === st.channelId) || {}).name || "channel" : "all channels"} · ${metric}`),
    ...nodes, legend, hot);
}

function applyHeatWindow(ts, seconds, item) {
  archiveState.timeWindow = { after: ts, before: ts + seconds - 0.001 };
  archiveState.resultMode = "browse";
  archiveState.activeFilter = null;
  archiveState.anchorMessageId = null;
  archiveState.newerCursor = null;
  archiveUpdateHash();
  refreshMessages(true);
}

// ---------------------------------------------------------------------------
// router
// ---------------------------------------------------------------------------
async function route() {
  runCleanup();
  document.body.classList.remove("archive-mode");
  if (AUTH.required && !AUTH.ok) { renderLogin(); return; }
  const raw = location.hash.replace(/^#\/?/, "");
  const [pathPart, queryPart] = raw.split("?");
  const parts = pathPart.split("/").filter(Boolean);
  const params = new URLSearchParams(queryPart || "");
  const section = parts[0] === "packages" ? "packages"
    : parts[0] === "archive" ? "archive" : "dashboard";
  document.querySelectorAll("#topbar nav a").forEach((a) => {
    a.classList.toggle("active", a.dataset.nav === section);
  });
  try {
    if (parts[0] === "packages") await renderPackages();
    else if (parts[0] === "archive") await renderArchive(params);
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
  if (/^#\/project\/[^/]+\/cheatsheet/.test(location.hash) ||
      /^#\/archive/.test(location.hash)) route();
});
boot();
