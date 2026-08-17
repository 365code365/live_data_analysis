/* 前台与后台共用的基础设施：主题、请求、提示、弹窗、鉴权。
   挂在 window.LDM 上，页面脚本直接用。无构建步骤，浏览器原生 ES 也能跑。 */
(() => {
  'use strict';

  // ── 主题 ────────────────────────────────────────────────────────────
  const THEMES = [
    { key: 'dark', label: '深色' },
    { key: 'midnight', label: '深蓝' },
    { key: 'light', label: '浅色' },
    { key: 'contrast', label: '高对比' },
  ];
  const THEME_KEY = 'ldm_theme';

  function applyTheme(key) {
    const theme = THEMES.some((t) => t.key === key) ? key : 'dark';
    document.documentElement.dataset.theme = theme;
    localStorage.setItem(THEME_KEY, theme);
    return theme;
  }
  function currentTheme() {
    return localStorage.getItem(THEME_KEY) || 'dark';
  }
  // 尽早应用，避免首屏闪一下默认色
  applyTheme(currentTheme());

  function mountThemePicker(selectEl) {
    if (!selectEl) return;
    selectEl.innerHTML = THEMES.map(
      (t) => `<option value="${t.key}">${t.label}</option>`,
    ).join('');
    selectEl.value = currentTheme();
    selectEl.addEventListener('change', () => applyTheme(selectEl.value));
  }

  // ── DOM ─────────────────────────────────────────────────────────────
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));

  // ── 管理员令牌 ──────────────────────────────────────────────────────
  const TOKEN_KEY = 'ldm_admin_token';
  const getToken = () => localStorage.getItem(TOKEN_KEY) || '';
  const setToken = (t) => (t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY));

  // ── 请求 ────────────────────────────────────────────────────────────
  async function api(path, { method = 'GET', body, form, admin = false, raw = false } = {}) {
    const headers = {};
    // 后台接口带令牌；前台请求不带，服务端会按 401 拒绝越权访问
    if (admin || getToken()) headers['X-Admin-Token'] = getToken();
    const opts = { method, headers };
    if (form) {
      opts.body = form;
    } else if (body !== undefined) {
      headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(path, opts);
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const j = await res.json();
        detail = j.detail || j.message || detail;
      } catch (_) { /* 非 JSON 错误体 */ }
      const err = new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
      err.status = res.status;
      throw err;
    }
    if (raw) return res;
    return res.status === 204 ? null : res.json();
  }

  // ── 提示 ────────────────────────────────────────────────────────────
  let toastTimer = null;
  function toast(msg, kind = '') {
    const el = $('#toast');
    if (!el) return;
    el.textContent = msg;
    el.className = `toast ${kind}`;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, kind === 'err' ? 6000 : 2800);
  }

  // ── 弹窗 ────────────────────────────────────────────────────────────
  const closeHooks = [];
  function modal(title, html, { wide = false } = {}) {
    const box = $('#modal');
    if (!box) return;
    $('#modalTitle').textContent = title;
    $('#modalBody').innerHTML = html;
    box.querySelector('.modal-box').classList.toggle('wide', wide);
    box.hidden = false;
    box.classList.add('open');
  }
  function closeModal() {
    const box = $('#modal');
    if (!box) return;
    box.hidden = true;
    box.classList.remove('open');
    $('#modalBody').innerHTML = '';
    closeHooks.forEach((fn) => { try { fn(); } catch (_) {} });
  }
  function onModalClose(fn) { closeHooks.push(fn); }

  function bindModal() {
    const close = $('#modalClose');
    if (close) close.addEventListener('click', closeModal);
    const box = $('#modal');
    if (box) box.addEventListener('click', (e) => { if (e.target.id === 'modal') closeModal(); });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });
  }

  // ── 格式化 ──────────────────────────────────────────────────────────
  const fmtTime = (v) => {
    if (!v) return '-';
    const s = String(v);
    const d = new Date(/[Z+]/.test(s.slice(10)) ? s : `${s}Z`);
    return isNaN(d) ? s : d.toLocaleString('zh-CN', { hour12: false });
  };
  const fmtNum = (v) => (v === null || v === undefined ? '-' : Number(v).toLocaleString('zh-CN'));
  const fmtDur = (s) => {
    if (!s) return '-';
    const t = Math.round(s);
    const p = (n) => String(n).padStart(2, '0');
    return `${p(Math.floor(t / 3600))}:${p(Math.floor((t % 3600) / 60))}:${p(t % 60)}`;
  };
  const fmtSize = (mb) => (mb === null || mb === undefined ? '-' : mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb} MB`);
  const yuan = (cents) => `¥${((cents || 0) / 100).toFixed(2)}`;

  function empty(icon, title, body, action = '') {
    return `<div class="empty"><div class="empty-ico">${icon}</div>
      <div class="empty-title">${esc(title)}</div>
      <div class="empty-body">${esc(body)}</div>${action}</div>`;
  }

  // ── 防抖 ────────────────────────────────────────────────────────────
  /** 内容没变就不动 DOM。轮询刷新最容易踩的坑就是无脑重写 innerHTML：
   *  节点被销毁重建，图片重新加载、滚动位置和输入焦点全丢，看起来就是页面在抖。 */
  function setHTML(el, html) {
    if (!el || el.innerHTML === html) return false;
    el.innerHTML = html;
    return true;
  }

  /** 下拉框同理：选项没变就别重写，否则用户正在选的值会被打回默认。 */
  function setOptions(el, html) {
    if (!el || el.innerHTML === html) return false;
    const keep = el.value;
    el.innerHTML = html;
    if (keep && Array.from(el.options).some((o) => o.value === keep)) el.value = keep;
    return true;
  }

  /** 合并高频触发（连续操作后只跑最后一次）。 */
  function debounce(fn, ms = 250) {
    let t = null;
    return (...args) => {
      clearTimeout(t);
      t = setTimeout(() => fn(...args), ms);
    };
  }

  /** 用户正在操作时（打开弹窗、在输入框里）不要做自动刷新，否则会打断他。 */
  function userBusy() {
    const box = $('#modal');
    if (box && !box.hidden) return true;
    const el = document.activeElement;
    return !!(el && /^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName) && el.type !== 'checkbox');
  }

  // ── 视图切换（左侧导航）────────────────────────────────────────────
  function mountNav({ onChange } = {}) {
    const items = $$('.nav-item[data-view]');
    const go = (view, push = true) => {
      items.forEach((i) => i.classList.toggle('active', i.dataset.view === view));
      $$('.view').forEach((v) => v.classList.toggle('active', v.id === `view-${view}`));
      const item = items.find((i) => i.dataset.view === view);
      if (item) {
        const title = $('#pageTitle');
        const sub = $('#pageSub');
        if (title) title.textContent = item.dataset.title || item.textContent.trim();
        if (sub) sub.textContent = item.dataset.sub || '';
      }
      if (push && location.hash.slice(1) !== view) history.replaceState(null, '', `#${view}`);
      if (onChange) onChange(view);
    };
    items.forEach((i) => i.addEventListener('click', () => go(i.dataset.view)));
    const initial = location.hash.slice(1) || items[0]?.dataset.view;
    if (initial) go(initial, false);
    return go;
  }

  window.LDM = {
    $, $$, esc, api, toast, modal, closeModal, onModalClose, bindModal,
    fmtTime, fmtNum, fmtDur, fmtSize, yuan, empty,
    setHTML, setOptions, debounce, userBusy,
    themes: THEMES, applyTheme, currentTheme, mountThemePicker,
    getToken, setToken, mountNav,
  };
})();
