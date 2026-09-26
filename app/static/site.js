/* Shared site behavior: language switch (EN/中文), topbar badges, auth state.
   Loaded by both the landing page and the console (before app.js).
   All fetches are relative, so the site works under any path prefix. */
"use strict";

window.AUTH = window.AUTH || { required: false, ok: true };

// ---------------------------------------------------------------------------
// language
// ---------------------------------------------------------------------------
function getLang() {
  return localStorage.getItem("lang") || "en";
}

function setLang(l) {
  localStorage.setItem("lang", l);
  document.documentElement.lang = l === "zh" ? "zh-CN" : "en";
  updateLangButton();
  window.dispatchEvent(new CustomEvent("langchange"));
}

function updateLangButton() {
  const b = document.getElementById("lang-switch");
  if (b) b.textContent = getLang() === "en" ? "中文" : "EN";
}

function injectLangSwitch() {
  const anchor = document.getElementById("auth-badge");
  if (!anchor || document.getElementById("lang-switch")) return;
  const b = document.createElement("button");
  b.id = "lang-switch";
  b.className = "badge badge-muted";
  b.style.cursor = "pointer";
  b.title = "switch language / 切换语言";
  b.onclick = () => setLang(getLang() === "en" ? "zh" : "en");
  anchor.parentNode.insertBefore(b, anchor);
  updateLangButton();
}

function applyI18n(root) {
  if (!window.I18N) return;
  const lang = getLang();
  const dict = I18N[lang] || {};
  const en = I18N.en || {};
  (root || document).querySelectorAll("[data-i18n]").forEach((el) => {
    const k = el.dataset.i18n;
    el.textContent = dict[k] ?? en[k] ?? k;
  });
  (root || document).querySelectorAll("[data-i18n-html]").forEach((el) => {
    const k = el.dataset.i18nHtml;
    el.innerHTML = dict[k] ?? en[k] ?? k;
  });
}

// ---------------------------------------------------------------------------
// topbar badges
// ---------------------------------------------------------------------------
function updateAuthBadge() {
  const el = document.getElementById("auth-badge");
  if (!el) return;
  if (!AUTH.required) {
    el.className = "badge badge-yellow";
    el.textContent = "⚠ open access";
    el.title = "No access_token is configured — anyone can use this console.";
    el.style.cursor = "default";
    el.onclick = null;
    return;
  }
  el.className = "badge badge-green";
  el.textContent = "🔒 protected";
  el.style.cursor = "pointer";
  el.title = "protected — click to log out";
  el.onclick = async () => {
    await fetch("api/auth/logout", { method: "POST" });
    AUTH.ok = false;
    updateAuthBadge();
    if (window.renderLogin) window.renderLogin();
  };
}

async function refreshBotStatus() {
  const el = document.getElementById("bot-status");
  if (!el) return;
  try {
    const res = await fetch("api/status");
    if (res.status === 401) {
      el.className = "badge badge-muted";
      el.textContent = "bot: 🔒";
      el.title = "unlock to see bot status";
      return;
    }
    const s = await res.json();
    if (!s.bot_configured) {
      el.className = "badge badge-muted";
      el.textContent = "bot: no token";
      el.title = "Set token in config.json or DISCORD_TOKEN";
    } else if (s.bot.online) {
      el.className = "badge badge-green";
      el.textContent = `online: ${s.bot.user || ""}`;
      el.title = `guilds: ${(s.bot.guilds || []).join(", ")}`;
    } else {
      el.className = "badge badge-red";
      el.textContent = "bot: offline";
      el.title = "bot is configured but not connected";
    }
  } catch {
    el.className = "badge badge-red";
    el.textContent = "backend unreachable";
  }
}

async function initAuth() {
  try {
    const c = await (await fetch("api/auth/check")).json();
    AUTH.required = !!c.required;
    AUTH.ok = !!c.ok;
  } catch { /* backend not up */ }
  updateAuthBadge();
}

function initSite() {
  injectLangSwitch();
  document.documentElement.lang = getLang() === "zh" ? "zh-CN" : "en";
  refreshBotStatus();
  setInterval(refreshBotStatus, 15000);
}

initSite();
